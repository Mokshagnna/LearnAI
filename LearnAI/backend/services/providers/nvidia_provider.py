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
        return bool(os.getenv("NVIDIA_API_KEY"))

    def _get_client(self, timeout_sec: float):
        api_key = os.getenv("NVIDIA_API_KEY")
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
        if not self.is_configured():
            raise ProviderConfigError("NVIDIA_API_KEY is not configured.", category="MISSING_CREDENTIALS")

        model = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
        client = self._get_client(timeout_seconds)

        call_start = time.perf_counter()
        logger.info(f"[NVIDIA START] model={model} timeout={timeout_seconds:.1f}s")

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
            logger.info(f"[NVIDIA SUCCESS] model={model} duration={duration:.2f}s len={len(result_text)}")
            return result_text

        except Exception as e:
            duration = time.perf_counter() - call_start
            msg = str(e).lower()

            if any(term in msg for term in ["invalid api key", "authentication", "unauthorized", "401", "api_key"]):
                logger.error(f"[NVIDIA AUTH FAILED] duration={duration:.2f}s error={e}")
                raise ProviderConfigError(f"NVIDIA API key is invalid or unauthorized: {e}", category="AUTH_CONFIG_ERROR") from e

            if any(term in msg for term in ["rate limit", "429", "too many requests"]):
                logger.warning(f"[NVIDIA RATE LIMIT] duration={duration:.2f}s error={e}")
                raise ProviderUnavailableError("NVIDIA rate limit reached.", category="RATE_LIMIT_429") from e

            if any(term in msg for term in ["503", "500", "502", "504", "unavailable", "timeout", "timed out", "connection"]):
                logger.warning(f"[NVIDIA SERVICE UNAVAILABLE] duration={duration:.2f}s error={e}")
                raise ProviderUnavailableError(f"NVIDIA service unavailable: {e}", category="SERVICE_UNAVAILABLE") from e

            logger.error(f"[NVIDIA UNEXPECTED ERROR] duration={duration:.2f}s error={e}")
            raise ProviderError(f"NVIDIA call failed: {e}", category="UNKNOWN_ERROR") from e

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
