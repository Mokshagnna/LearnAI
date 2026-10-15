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

logger = logging.getLogger("learnai.providers.nvidia")


class NvidiaProvider(BaseAIProvider):
    """NVIDIA Build AI Provider via OpenAI-compatible API."""

    def __init__(self):
        self._name = "nvidia"

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return bool(os.getenv("NVIDIA_API_KEY") or os.getenv("NVIDIA_BACKUP_API_KEY"))

    def _get_client(self, timeout_sec: float, api_key: str):
        if not api_key:
            raise ProviderConfigError("NVIDIA_API_KEY is missing from environment.", category="MISSING_CREDENTIALS")
        try:
            from openai import OpenAI
            return OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=api_key,
                timeout=max(timeout_sec, 4.0),
                max_retries=0
            )
        except ImportError as e:
            raise ProviderConfigError("openai python package is not installed.", category="DEPENDENCY_MISSING") from e

    def _execute(self, prompt: str, timeout_seconds: float) -> str:
        primary_key = os.getenv("NVIDIA_API_KEY", "")
        backup_key = os.getenv("NVIDIA_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if backup_key and backup_key != primary_key:
            keys_to_try.append((backup_key, "backup"))

        if not keys_to_try:
            raise ProviderConfigError("NVIDIA_API_KEY is not configured.", category="MISSING_CREDENTIALS")

        model = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
        last_error = None
        overall_start = time.perf_counter()

        for key_value, key_label in keys_to_try:
            time_left_for_key = timeout_seconds - (time.perf_counter() - overall_start)
            if time_left_for_key < 3.0:
                logger.warning(f"[NVIDIA] Budget exhausted ({time_left_for_key:.1f}s left). Cannot try key={key_label}.")
                break
            client = self._get_client(time_left_for_key, api_key=key_value)
            call_start = time.perf_counter()
            logger.info(f"[NVIDIA START] key={key_label} model={model} timeout={time_left_for_key:.1f}s")

            try:
                completion = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a professional educational quiz generator. Always output valid JSON conforming strictly to the requested schema. Do not include markdown formatting or code fences."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.4,
                    max_tokens=2500
                )
                duration = time.perf_counter() - call_start

                if not completion.choices or not completion.choices[0].message.content:
                    raise ValueError("NVIDIA returned an empty response.")

                result_text = completion.choices[0].message.content.strip()
                logger.info(f"[NVIDIA SUCCESS] key={key_label} model={model} duration={duration:.2f}s len={len(result_text)}")
                return result_text

            except Exception as e:
                last_error = e
                duration = time.perf_counter() - call_start
                msg = str(e).lower()

                if any(term in msg for term in ["invalid api key", "authentication", "unauthorized", "401", "api_key"]):
                    logger.error(f"[NVIDIA AUTH FAILED] key={key_label} duration={duration:.2f}s error={e}")
                    raise ProviderConfigError(
                        f"NVIDIA API key is invalid or unauthorized: {e}", category="AUTH_CONFIG_ERROR"
                    ) from e

                is_recoverable = any(term in msg for term in [
                    "rate limit", "429", "too many requests",
                    "503", "500", "502", "504", "unavailable", "timeout", "timed out", "connection"
                ])

                if is_recoverable and key_label == "primary" and len(keys_to_try) > 1:
                    logger.warning(
                        f"[NVIDIA KEY FALLBACK] key={key_label} recoverable error (duration={duration:.2f}s): {e}. "
                        f"Trying backup key."
                    )
                    continue

                # Non-recoverable or no backup
                if any(term in msg for term in ["rate limit", "429", "too many requests"]):
                    logger.warning(f"[NVIDIA RATE LIMIT] key={key_label} duration={duration:.2f}s error={e}")
                    raise ProviderUnavailableError("NVIDIA rate limit reached.", category="RATE_LIMIT_429") from e

                if any(term in msg for term in ["503", "500", "502", "504", "unavailable", "timeout", "timed out", "connection"]):
                    logger.warning(f"[NVIDIA SERVICE UNAVAILABLE] key={key_label} duration={duration:.2f}s error={e}")
                    raise ProviderUnavailableError(f"NVIDIA service unavailable: {e}", category="SERVICE_UNAVAILABLE") from e

                logger.error(f"[NVIDIA UNEXPECTED ERROR] key={key_label} duration={duration:.2f}s error={e}")
                raise ProviderError(f"NVIDIA call failed: {e}", category="UNKNOWN_ERROR") from e

        # Both keys exhausted
        if last_error:
            msg = str(last_error).lower()
            if any(term in msg for term in ["rate limit", "429", "too many requests"]):
                raise ProviderUnavailableError("NVIDIA rate limit reached on all keys.", category="RATE_LIMIT_429") from last_error
            raise ProviderUnavailableError(f"NVIDIA service unavailable: {last_error}", category="SERVICE_UNAVAILABLE") from last_error

        raise ProviderUnavailableError("NVIDIA: all keys exhausted.", category="SERVICE_UNAVAILABLE")

    def generate_quiz_raw(
        self,
        course_code: str,
        course_name: str,
        topic_name: str,
        syllabus_context: str,
        timeout_seconds: float,
        custom_prompt: Optional[str] = None,
        **kwargs
    ) -> str:
        prompt = custom_prompt if custom_prompt else get_quiz_prompt(course_code, course_name, topic_name, syllabus_context)
        return self._execute(prompt, timeout_seconds)

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
        return self._execute(prompt, timeout_seconds)
