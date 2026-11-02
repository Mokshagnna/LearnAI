import os
import time
import random
import logging
import threading
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

    _client_cache: dict = {}
    _client_lock: threading.Lock = threading.Lock()

    def __init__(self):
        self._name = "nvidia"

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return bool(
            os.getenv("NVIDIA_API_KEY")
            or os.getenv("NVIDIA_SECONDARY_API_KEY")
            or os.getenv("NVIDIA_BACKUP_API_KEY")
        )

    def _get_client(self, timeout_sec: float, api_key: str):
        if not api_key:
            raise ProviderConfigError("NVIDIA_API_KEY is missing from environment.", category="MISSING_CREDENTIALS")
        with self._client_lock:
            client = self._client_cache.get(api_key)
            if client is None:
                try:
                    from openai import OpenAI
                    # Persistent client connection reuse; per-request timeout passed in create()
                    client = OpenAI(
                        base_url="https://integrate.api.nvidia.com/v1",
                        api_key=api_key,
                        timeout=max(float(timeout_sec), 0.5),
                        max_retries=0
                    )
                    self._client_cache[api_key] = client
                except ImportError as e:
                    raise ProviderConfigError("openai python package is not installed.", category="DEPENDENCY_MISSING") from e
            else:
                client.timeout = max(float(timeout_sec), 0.5)
            return client

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
        if status_code in {400, 401, 403, 404, 410, 422}:
            return False
        if status_code in {408, 429, 500, 502, 503, 504}:
            return True

        exc_name = type(exc).__name__
        if any(term in exc_name for term in ["RateLimitError", "InternalServerError", "APITimeoutError", "APIConnectionError", "Timeout", "NetworkError", "ConnectError"]):
            return True
        if any(term in exc_name for term in ["BadRequestError", "NotFoundError", "UnprocessableEntityError"]):
            return False

        msg = str(exc).lower()
        if any(term in msg for term in ["model not found", "model does not exist", "404", "410", "gone", "bad request", "400", "invalid_request_error"]):
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
        if status_code == 503:
            return "SERVICE_UNAVAILABLE_503"
        if status_code == 410:
            return "MODEL_DEPRECATED_410"
        if status_code == 404:
            return "MODEL_NOT_FOUND_404"
        if status_code == 400:
            return "MALFORMED_REQUEST_400"
        if status_code in (500, 502, 504):
            return f"SERVER_ERROR_{status_code}"

        exc_name = type(exc).__name__
        if "RateLimitError" in exc_name:
            return "RATE_LIMIT_429"
        if "InternalServerError" in exc_name:
            return "SERVER_ERROR_500"
        if "APITimeoutError" in exc_name or "Timeout" in exc_name:
            return "CLIENT_TIMEOUT"
        if "APIConnectionError" in exc_name or "Connection" in exc_name:
            return "NETWORK_ERROR"

        msg = str(exc).lower()
        if "410" in msg or "gone" in msg:
            return "MODEL_DEPRECATED_410"
        if "400" in msg or "bad request" in msg:
            return "MALFORMED_REQUEST_400"
        if "rate limit" in msg or "429" in msg or "quota" in msg or "too many requests" in msg:
            return "RATE_LIMIT_429"
        if "model not found" in msg or "does not exist" in msg or "404" in msg:
            return "MODEL_NOT_FOUND_404"
        if "503" in msg or "service unavailable" in msg:
            return "SERVICE_UNAVAILABLE_503"
        if "500" in msg or "internal server error" in msg:
            return "SERVER_ERROR_500"
        if "timeout" in msg or "timed out" in msg:
            return "CLIENT_TIMEOUT"
        if "connection" in msg:
            return "NETWORK_ERROR"
        return "UNKNOWN_ERROR"

    def _execute(self, prompt: str, timeout_seconds: float) -> str:
        primary_key = os.getenv("NVIDIA_API_KEY", "")
        secondary_key = os.getenv("NVIDIA_SECONDARY_API_KEY") or os.getenv("NVIDIA_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if secondary_key and secondary_key != primary_key:
            keys_to_try.append((secondary_key, "secondary"))

        if not keys_to_try:
            raise ProviderConfigError("NVIDIA_API_KEY is not configured.", category="MISSING_CREDENTIALS") from None

        # Verified active NVIDIA models: meta/llama-3.1-8b-instruct (primary), meta/llama-3.1-70b-instruct (fallback)
        DEFAULT_NVIDIA_MODEL = "meta/llama-3.1-8b-instruct"
        KNOWN_INVALID_NVIDIA_MODELS = {"meta/llama-3.3-70b-instruct"}

        configured_model = os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL).strip()
        fallback_model = (os.getenv("NVIDIA_FALLBACK_MODEL") or "meta/llama-3.1-70b-instruct").strip()

        if configured_model in KNOWN_INVALID_NVIDIA_MODELS:
            logger.warning(
                "[NVIDIA CONFIG WARNING] Configured NVIDIA_MODEL '%s' returned 410 Gone (endpoint deprecated) in production. "
                "Adding verified active model '%s' to candidate models.",
                configured_model, DEFAULT_NVIDIA_MODEL
            )

        candidate_models = [configured_model]
        for alt in [fallback_model, DEFAULT_NVIDIA_MODEL, "meta/llama-3.1-70b-instruct", "mistralai/mixtral-8x7b-instruct-v0.1"]:
            if alt and alt not in candidate_models:
                candidate_models.append(alt)

        last_error = None
        overall_start = time.perf_counter()

        for key_value, key_label in keys_to_try:
            time_left_for_key = timeout_seconds - (time.perf_counter() - overall_start)
            if time_left_for_key < 2.5:
                logger.warning(f"[NVIDIA] Budget exhausted ({time_left_for_key:.1f}s left). Cannot try key={key_label}.")
                break
            client = self._get_client(time_left_for_key, api_key=key_value)
            key_error = None
            key_had_auth_error = False

            for m_idx, current_model in enumerate(candidate_models):
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

                    # 2. Model 410 Gone (Deprecated): attempt next candidate model!
                    if cat == "MODEL_DEPRECATED_410":
                        logger.warning(
                            f"[NVIDIA MODEL DEPRECATED] key={key_label} model={current_model} "
                            f"duration={duration:.2f}s category={cat}: {sanitize_error_text(e)}. Attempting fallback model..."
                        )
                        continue

                    # 3. Permanent non-retryable errors: do NOT attempt model fallback or backup key
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

                    # 4. Retryable error (rate-limit, temporary service, timeout, connection)
                    logger.warning(
                        f"[NVIDIA MODEL FALLBACK] key={key_label} model={current_model} "
                        f"failed with retryable error (duration={duration:.2f}s, category={cat}): {sanitize_error_text(e)}. "
                        f"Attempting fallback model..."
                    )

                    # Bounded backoff/jitter for 429 and 503 if time budget allows
                    is_rate_limit_or_unavailable = any(term in cat for term in ["RATE_LIMIT", "SERVICE_UNAVAILABLE", "SERVER_ERROR"])
                    time_left_now = timeout_seconds - (time.perf_counter() - overall_start)
                    if is_rate_limit_or_unavailable and time_left_now > 3.0:
                        backoff = min(1.0, 0.2 * (2 ** m_idx)) + random.uniform(0.05, 0.15)
                        if time_left_now > backoff + 2.0:
                            logger.info(f"[NVIDIA BACKOFF] Retrying after {backoff:.2f}s backoff due to transient status (429/503)...")
                            time.sleep(backoff)
                    continue


            # Check if recoverable error allows secondary key attempt
            if key_error is not None:
                is_recoverable = self._is_retryable(key_error)
                if is_recoverable and key_label == "primary" and len(keys_to_try) > 1:
                    logger.warning(
                        f"[NVIDIA KEY FALLBACK] key={key_label} recoverable error (duration={duration:.2f}s): {sanitize_error_text(key_error)}. "
                        f"Trying secondary key."
                    )
                    continue

                break

        # Both keys exhausted
        if last_error:
            cat = self._categorize(last_error)
            clean_err = sanitize_error_text(last_error)
            if cat == "RATE_LIMIT_429":
                raise ProviderUnavailableError("NVIDIA rate limit reached.", category="RATE_LIMIT_429") from None
            if cat == "CLIENT_TIMEOUT" or "timeout" in clean_err.lower():
                raise ProviderUnavailableError(f"NVIDIA request timed out: {clean_err}", category="TIMEOUT") from None
            if cat in ("SERVICE_UNAVAILABLE_503", "NETWORK_ERROR") or cat.startswith("SERVER_ERROR"):
                raise ProviderUnavailableError(f"NVIDIA service unavailable: {clean_err}", category=cat) from None

            raise ProviderError(f"NVIDIA call failed ({cat}): {clean_err}", category="UNKNOWN_ERROR") from None

        raise ProviderUnavailableError("NVIDIA: budget exhausted or all models/keys exhausted.", category="TIMEOUT") from None

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
