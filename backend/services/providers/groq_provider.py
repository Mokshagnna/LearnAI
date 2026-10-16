import os
import time
import logging
from typing import Optional, List
from services.providers.base import (
    BaseAIProvider,
    ProviderUnavailableError,
    ProviderConfigError,
    ProviderError,
    get_quiz_prompt,
    get_analysis_prompt
)
from services.logger_utils import sanitize_error_text

logger = logging.getLogger("learnai.providers.groq")


class GroqProvider(BaseAIProvider):
    """Groq AI Provider for ultra-fast fallback and primary inference."""

    def __init__(self):
        self._name = "groq"

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return bool(os.getenv("GROQ_API_KEY") or os.getenv("GROQ_BACKUP_API_KEY"))

    def _get_client(self, timeout_sec: float, api_key: str):
        if not api_key:
            raise ProviderConfigError("GROQ_API_KEY is missing from environment.", category="MISSING_CREDENTIALS")
        try:
            from groq import Groq
            return Groq(api_key=api_key, timeout=max(timeout_sec, 2.0), max_retries=0)
        except ImportError as e:
            raise ProviderConfigError("groq python package is not installed.", category="DEPENDENCY_MISSING") from e

    def _execute(self, prompt: str, timeout_seconds: float, max_tokens: int = 3000) -> str:
        primary_key = os.getenv("GROQ_API_KEY", "")
        backup_key = os.getenv("GROQ_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if backup_key and backup_key != primary_key:
            keys_to_try.append((backup_key, "backup"))

        if not keys_to_try:
            raise ProviderConfigError("GROQ_API_KEY is not configured.", category="MISSING_CREDENTIALS")

        primary_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        candidate_models = [primary_model]
        for alt in ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]:
            if alt not in candidate_models:
                candidate_models.append(alt)

        last_error = None
        overall_start = time.perf_counter()

        for key_value, key_label in keys_to_try:
            time_left_for_key = timeout_seconds - (time.perf_counter() - overall_start)
            if time_left_for_key < 2.5:
                logger.warning(f"[GROQ] Budget exhausted ({time_left_for_key:.1f}s left). Cannot try key={key_label}.")
                break
            client = self._get_client(time_left_for_key, api_key=key_value)
            key_error = None

            for current_model in candidate_models:
                time_left = timeout_seconds - (time.perf_counter() - overall_start)
                if time_left < 2.5:
                    break
                call_start = time.perf_counter()
                try:
                    logger.info(f"[GROQ START] key={key_label} model={current_model} timeout={time_left:.1f}s")
                    extra_kwargs = {}
                    if "gpt-oss" in current_model:
                        extra_kwargs["extra_body"] = {"reasoning_effort": "low"}

                    chat_completion = client.chat.completions.create(
                        model=current_model,
                        messages=[
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        max_tokens=max_tokens,
                        **extra_kwargs
                    )
                    duration = time.perf_counter() - call_start
                    if not chat_completion.choices or not chat_completion.choices[0].message.content:
                        raise ValueError(f"Groq model {current_model} returned an empty response.")

                    result_text = chat_completion.choices[0].message.content
                    logger.info(f"[GROQ SUCCESS] key={key_label} model={current_model} duration={duration:.2f}s")
                    return result_text

                except Exception as e:
                    key_error = e
                    last_error = e
                    duration = time.perf_counter() - call_start
                    msg = str(e).lower()

                    # Auth errors cannot be solved by changing models or keys — fail immediately
                    if any(term in msg for term in ["invalid api key", "authentication", "unauthorized", "api_key", "401"]):
                        logger.error(f"[GROQ AUTH FAILED] key={key_label} duration={duration:.2f}s error={sanitize_error_text(e)}")
                        raise ProviderConfigError(
                            "Groq API key is invalid or unauthorized.", category="AUTH_CONFIG_ERROR"
                        ) from e

                    logger.warning(
                        f"[GROQ MODEL FALLBACK] key={key_label} model={current_model} "
                        f"failed (duration={duration:.2f}s): {sanitize_error_text(e)}. Attempting fallback model..."
                    )
                    continue

            # All models on this key exhausted — decide whether to try backup key
            if key_error is not None:
                msg = str(key_error).lower()
                is_recoverable = any(term in msg for term in [
                    "rate limit", "429", "too many requests",
                    "503", "500", "502", "504", "unavailable", "timeout", "timed out"
                ])
                if is_recoverable and key_label == "primary" and len(keys_to_try) > 1:
                    logger.warning("[GROQ KEY FALLBACK] Primary key failed with recoverable error. Trying backup key.")
                    continue
                # Non-recoverable error or no backup available — stop
                break

        if last_error:
            msg = str(last_error).lower()
            if any(term in msg for term in ["rate limit", "429", "too many requests"]):
                raise ProviderUnavailableError(
                    "Groq rate limit reached across all models and keys.", category="RATE_LIMIT_429"
                ) from last_error
            clean_err = sanitize_error_text(last_error)
            raise ProviderUnavailableError(
                f"Groq service unavailable: {clean_err}", category="SERVICE_UNAVAILABLE"
            ) from last_error

        raise ProviderUnavailableError("Groq timeout or all models exhausted.", category="SERVICE_UNAVAILABLE")

    def generate_quiz_raw(
        self,
        course_code: str,
        course_name: str,
        topic_name: str,
        syllabus_context: str,
        timeout_seconds: float,
        custom_prompt: Optional[str] = None,
        max_tokens: int = 2200,
        **kwargs
    ) -> str:
        if custom_prompt:
            prompt = custom_prompt
        else:
            prompt = get_quiz_prompt(course_code, course_name, topic_name, syllabus_context)
        return self._execute(prompt, timeout_seconds, max_tokens=max_tokens)

    def generate_analysis_raw(
        self,
        subject: str,
        topic: str,
        weak_concepts: List[str],
        score: int,
        total: int,
        timeout_seconds: float
    ) -> str:
        prompt = get_analysis_prompt(subject, topic, weak_concepts, score, total)
        return self._execute(prompt, timeout_seconds, max_tokens=600)
