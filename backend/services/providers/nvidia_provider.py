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
            # Ensure client timeout respects caller timeout_sec without exceeding it
            effective_timeout = max(float(timeout_sec), 0.5)
            return OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=api_key,
                timeout=effective_timeout,
                max_retries=0
            )
        except ImportError as e:
            raise ProviderConfigError("openai python package is not installed.", category="DEPENDENCY_MISSING") from e

    def _is_auth_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        exc_name = type(exc).__name__
        if "AuthenticationError" in exc_name or "PermissionDeniedError" in exc_name:
            return True
        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status_code in {401, 403}:
            return True
        auth_terms = [
            "invalid api key", "authentication", "unauthorized", "api_key",
            "forbidden", "permission_denied", "api key not valid"
        ]
        return any(term in msg for term in auth_terms)

    def _is_retryable(self, exc: Exception) -> bool:
        """
        Restricts model and key retry/fallback to transient, retryable failures:
        - Rate limits (429, too many requests)
        - Temporary service errors (503, 502, 504, 500, unavailable, overloaded)
        - Timeouts (client/server timeout, deadline exceeded)
        - Connection and network errors
        Permanent errors (400 Bad Request, 401/403 Auth, 404 Model Not Found, 422 Unprocessable) return False.
        """
        if self._is_auth_error(exc):
            return False

        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status_code in {400, 401, 403, 404, 422}:
            return False
        if status_code in {408, 429, 500, 502, 503, 504}:
            return True

        exc_name = type(exc).__name__
        if any(term in exc_name for term in ["RateLimitError", "InternalServerError", "APITimeoutError", "APIConnectionError", "Timeout", "NetworkError", "ConnectError"]):
            return True
        if any(term in exc_name for term in ["BadRequestError", "NotFoundError", "UnprocessableEntityError"]):
            return False

        msg = str(exc).lower()
        if any(term in msg for term in ["model not found", "model does not exist", "404", "bad request", "400", "invalid_request_error"]):
            return False

        retryable_terms = [
            "rate limit", "429", "too many requests",
            "503", "500", "502", "504", "unavailable", "timeout", "timed out",
            "connection", "overloaded", "temporarily", "server error"
        ]
        return any(term in msg for term in retryable_terms)

    def _categorize(self, exc: Exception) -> str:
        if self._is_auth_error(exc):
            return "AUTH_CONFIG_ERROR"
        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status_code == 429:
            return "RATE_LIMIT_429"
        elif status_code == 503:
            return "SERVICE_UNAVAILABLE_503"
        elif status_code == 404:
            return "MODEL_NOT_FOUND_404"
        elif status_code == 400:
            return "MALFORMED_REQUEST_400"
        elif status_code in {500, 502, 504}:
            return f"SERVER_ERROR_{status_code}"

        msg = str(exc).lower()
        if "429" in msg or "rate limit" in msg:
            return "RATE_LIMIT_429"
        if "404" in msg or "model not found" in msg or "does not exist" in msg:
            return "MODEL_NOT_FOUND_404"
        if "400" in msg or "bad request" in msg:
            return "MALFORMED_REQUEST_400"
        if any(t in msg for t in ["503", "502", "504", "unavailable"]):
            return "SERVICE_UNAVAILABLE_503"
        if "timeout" in msg or "timed out" in msg:
            return "CLIENT_TIMEOUT"
        if "connection" in msg:
            return "NETWORK_ERROR"
        return "UNKNOWN_ERROR"

    def _execute(self, prompt: str, timeout_seconds: float) -> str:
        primary_key = os.getenv("NVIDIA_API_KEY", "")
        backup_key = os.getenv("NVIDIA_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if backup_key and backup_key != primary_key:
            keys_to_try.append((backup_key, "backup"))

        if not keys_to_try:
            raise ProviderConfigError("NVIDIA_API_KEY is not configured.", category="MISSING_CREDENTIALS") from None

        primary_model = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
        fallback_model = os.getenv(
            "NVIDIA_FALLBACK_MODEL",
            "meta/llama-3.1-70b-instruct" if primary_model != "meta/llama-3.1-70b-instruct" else "meta/llama-3.3-70b-instruct"
        )
        candidate_models = [primary_model]
        if fallback_model and fallback_model.lower() not in ("none", "false", "") and fallback_model not in candidate_models:
            candidate_models.append(fallback_model)

        last_error = None
        overall_start = time.perf_counter()

        for key_value, key_label in keys_to_try:
            time_left_for_key = timeout_seconds - (time.perf_counter() - overall_start)
            if time_left_for_key < 2.5:
                logger.warning(f"[NVIDIA] Budget exhausted ({time_left_for_key:.1f}s left). Cannot try key={key_label}.")
                break
            client = self._get_client(time_left_for_key, api_key=key_value)
            key_error = None

            for current_model in candidate_models:
                time_left = timeout_seconds - (time.perf_counter() - overall_start)
                if time_left < 2.5:
                    logger.warning(f"[NVIDIA] Budget exhausted ({time_left:.1f}s left). Skipping model={current_model}.")
                    break

                call_start = time.perf_counter()
                logger.info(f"[NVIDIA START] key={key_label} model={current_model} timeout={time_left:.1f}s")

                try:
                    completion = client.chat.completions.create(
                        model=current_model,
                        messages=[
                            {
                                "role": "system",
                                "content": "You are a professional educational quiz generator. Always output valid JSON conforming strictly to the requested schema. Do not include markdown formatting or code fences."
                            },
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.4,
                        max_tokens=2500,
                        timeout=time_left
                    )
                    duration = time.perf_counter() - call_start

                    if not completion.choices or not completion.choices[0].message.content:
                        raise ValueError(f"NVIDIA model {current_model} returned an empty response.")

                    result_text = completion.choices[0].message.content.strip()
                    logger.info(f"[NVIDIA SUCCESS] key={key_label} model={current_model} duration={duration:.2f}s len={len(result_text)}")
                    return result_text

                except Exception as e:
                    key_error = e
                    last_error = e
                    duration = time.perf_counter() - call_start
                    cat = self._categorize(e)

                    # 1. Authentication / Permission error: fail immediately without trying other models or keys
                    if self._is_auth_error(e):
                        logger.error(f"[NVIDIA AUTH FAILED] key={key_label} duration={duration:.2f}s category={cat} error={sanitize_error_text(e)}")
                        raise ProviderConfigError(
                            "NVIDIA API key is invalid or unauthorized.", category="AUTH_CONFIG_ERROR"
                        ) from None

                    # 2. Permanent non-retryable errors: do NOT attempt model fallback or backup key
                    if not self._is_retryable(e):
                        clean_err = sanitize_error_text(e)
                        if cat == "MODEL_NOT_FOUND_404":
                            logger.error(f"[NVIDIA MODEL NOT FOUND] key={key_label} model={current_model} duration={duration:.2f}s category={cat}")
                            raise ProviderConfigError(
                                f"NVIDIA model '{current_model}' not found or invalid model ID.", category="MODEL_NOT_FOUND_404"
                            ) from None
                        if cat == "MALFORMED_REQUEST_400":
                            logger.error(f"[NVIDIA MALFORMED REQUEST] key={key_label} model={current_model} duration={duration:.2f}s category={cat}")
                            raise ProviderError(
                                "NVIDIA malformed request or invalid prompt schema.", category="MALFORMED_REQUEST_400"
                            ) from None

                        logger.error(f"[NVIDIA PERMANENT ERROR] key={key_label} model={current_model} duration={duration:.2f}s category={cat}: {clean_err}")
                        raise ProviderError(f"NVIDIA error ({cat}): {clean_err}", category=cat) from None

                    # 3. Retryable error (rate-limit, temporary service, timeout, connection)
                    logger.warning(
                        f"[NVIDIA MODEL FALLBACK] key={key_label} model={current_model} "
                        f"failed with retryable error (duration={duration:.2f}s, category={cat}): {sanitize_error_text(e)}. "
                        f"Attempting fallback model..."
                    )
                    continue

            # Check if recoverable error allows backup key attempt
            if key_error is not None:
                is_recoverable = self._is_retryable(key_error)
                if is_recoverable and key_label == "primary" and len(keys_to_try) > 1:
                    logger.warning(
                        f"[NVIDIA KEY FALLBACK] key={key_label} recoverable error (duration={duration:.2f}s): {sanitize_error_text(key_error)}. "
                        f"Trying backup key."
                    )
                    continue

                break

        # Both keys exhausted
        if last_error:
            cat = self._categorize(last_error)
            if cat == "RATE_LIMIT_429":
                raise ProviderUnavailableError("NVIDIA rate limit reached.", category="RATE_LIMIT_429") from None

            clean_err = sanitize_error_text(last_error)
            if cat in ("SERVICE_UNAVAILABLE_503", "CLIENT_TIMEOUT", "NETWORK_ERROR") or cat.startswith("SERVER_ERROR"):
                raise ProviderUnavailableError(f"NVIDIA service unavailable: {clean_err}", category="SERVICE_UNAVAILABLE") from None

            raise ProviderError(f"NVIDIA call failed ({cat}): {clean_err}", category="UNKNOWN_ERROR") from None

        raise ProviderUnavailableError("NVIDIA: budget exhausted or all models/keys exhausted.", category="SERVICE_UNAVAILABLE") from None

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
