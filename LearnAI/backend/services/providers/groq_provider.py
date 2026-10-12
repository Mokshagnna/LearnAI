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

logger = logging.getLogger("learnai.providers.groq")


class GroqProvider(BaseAIProvider):
    """Groq AI Provider for ultra-fast fallback and primary inference."""

    def __init__(self):
        self._name = "groq"

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return bool(os.getenv("GROQ_API_KEY"))

    def _get_client(self, timeout_sec: float):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ProviderConfigError("GROQ_API_KEY is missing from environment.", category="MISSING_CREDENTIALS")
        try:
            from groq import Groq
            return Groq(api_key=api_key, timeout=max(timeout_sec, 2.0), max_retries=0)
        except ImportError as e:
            raise ProviderConfigError("groq python package is not installed.", category="DEPENDENCY_MISSING") from e

    def _execute(self, prompt: str, timeout_seconds: float, max_tokens: int = 3000) -> str:
        if not self.is_configured():
            raise ProviderConfigError("GROQ_API_KEY is not configured.", category="MISSING_CREDENTIALS")

        primary_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        candidate_models = [primary_model]
        for alt in ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]:
            if alt not in candidate_models:
                candidate_models.append(alt)

        client = self._get_client(timeout_seconds)
        call_start = time.perf_counter()
        last_error = None

        for current_model in candidate_models:
            time_left = timeout_seconds - (time.perf_counter() - call_start)
            if time_left < 3.0:
                break
            try:
                logger.info(f"[GROQ START] model={current_model} timeout={time_left:.1f}s")
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
                logger.info(f"[GROQ SUCCESS] model={current_model} duration={duration:.2f}s")
                return result_text

            except Exception as e:
                last_error = e
                duration = time.perf_counter() - call_start
                msg = str(e).lower()

                # Auth errors cannot be solved by changing models
                if any(term in msg for term in ["invalid api key", "authentication", "unauthorized", "api_key", "401"]):
                    logger.error(f"[GROQ AUTH FAILED] duration={duration:.2f}s error={e}")
                    raise ProviderConfigError(f"Groq API key is invalid or unauthorized: {e}", category="AUTH_CONFIG_ERROR") from e

                logger.warning(
                    f"[GROQ MODEL FALLBACK] model={current_model} failed (duration={duration:.2f}s): {e}. "
                    f"Attempting fallback model..."
                )
                continue

        if last_error:
            msg = str(last_error).lower()
            if any(term in msg for term in ["rate limit", "429", "too many requests"]):
                raise ProviderUnavailableError("Groq rate limit reached across all models.", category="RATE_LIMIT_429") from last_error
            raise ProviderUnavailableError(f"Groq service unavailable: {last_error}", category="SERVICE_UNAVAILABLE") from last_error

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
