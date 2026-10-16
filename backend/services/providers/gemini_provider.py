import os
import time
import random
import logging
from typing import Optional, List
from google import genai
from google.genai import types, errors

from services.providers.base import (
    BaseAIProvider,
    ProviderUnavailableError,
    ProviderConfigError,
    ProviderError,
    get_quiz_prompt,
    get_analysis_prompt
)
from services.logger_utils import sanitize_error_text

logger = logging.getLogger("learnai.providers.gemini")


class GeminiProvider(BaseAIProvider):
    """Google Gemini AI Provider with bounded retries and fast model failover."""

    def __init__(self):
        self._name = "gemini"

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_BACKUP_API_KEY"))

    def _get_client(self, timeout_sec: float, api_key: str) -> genai.Client:
        if not api_key:
            raise ProviderConfigError("GEMINI_API_KEY is missing from environment.", category="MISSING_CREDENTIALS")
        # Google GenAI requires a minimum allowed deadline of 10.0 seconds (10,000 ms) if manually set.
        # Ensure timeout_ms is at least 10,000ms to prevent HTTP 400 INVALID_ARGUMENT from Google.
        timeout_ms = max(int(timeout_sec * 1000), 10000)
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=timeout_ms,
                retry_options=types.HttpRetryOptions(attempts=1)
            )
        )

    def _is_auth_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        auth_terms = [
            "api key", "api_key", "permission", "credential", "unregistered_callers",
            "forbidden", "unauthorized", "api_key_invalid"
        ]
        if isinstance(exc, errors.APIError):
            code = getattr(exc, "code", None)
            if code in {400, 401, 403}:
                if any(t in msg for t in auth_terms):
                    return True
        return any(t in msg for t in ["api key not valid", "api_key_invalid", "permission_denied", "invalid api key"])

    def _is_retryable(self, exc: Exception) -> bool:
        if self._is_auth_error(exc):
            return False
        if isinstance(exc, errors.APIError):
            code = getattr(exc, "code", None)
            status = getattr(exc, "status", "") or ""
            if code in {400, 401, 403, 404}:
                return False
            if code in {408, 429, 500, 502, 503, 504}:
                return True
            if status in {"UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE_EXCEEDED"}:
                return True
        exc_name = type(exc).__name__
        if any(k in exc_name for k in ["Timeout", "NetworkError", "ConnectError", "ConnectionError"]):
            return True
        msg = str(exc).lower()
        if any(w in msg for w in ["503", "unavailable", "high demand", "429", "rate limit", "timed out", "temporarily"]):
            if not any(t in msg for t in ["api_key", "permission", "forbidden", "unauthorized"]):
                return True
        return False

    def _categorize(self, exc: Exception) -> str:
        if self._is_auth_error(exc):
            return "AUTH_CONFIG_ERROR"
        if isinstance(exc, errors.APIError):
            code = getattr(exc, "code", None)
            if code == 503:
                return "SERVICE_UNAVAILABLE_503"
            elif code == 429:
                return "RATE_LIMIT_429"
            elif code == 408:
                return "REQUEST_TIMEOUT_408"
            elif code in {500, 502, 504}:
                return f"SERVER_ERROR_{code}"
            elif code == 400:
                return "MALFORMED_REQUEST_400"
            elif code == 404:
                return "MODEL_NOT_FOUND_404"
        exc_name = type(exc).__name__
        if "Timeout" in exc_name:
            return "CLIENT_TIMEOUT"
        if "Connection" in exc_name or "Network" in exc_name:
            return "NETWORK_ERROR"
        return "UNKNOWN_ERROR"

    def _execute_with_models(self, prompt: str, timeout_seconds: float, response_mime_type: Optional[str] = None) -> str:
        MIN_GEMINI_DEADLINE_SECONDS = 10.0
        if timeout_seconds < MIN_GEMINI_DEADLINE_SECONDS:
            logger.warning(
                f"[GEMINI] Timeout budget {timeout_seconds:.1f}s is below Gemini's minimum allowed deadline ({MIN_GEMINI_DEADLINE_SECONDS:.1f}s)."
            )
            raise ProviderUnavailableError(
                f"Gemini: timeout budget {timeout_seconds:.1f}s is below minimum allowed deadline of {MIN_GEMINI_DEADLINE_SECONDS:.1f}s.",
                category="TIMEOUT"
            )

        primary_key = os.getenv("GEMINI_API_KEY", "")
        backup_key = os.getenv("GEMINI_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if backup_key and backup_key != primary_key:
            keys_to_try.append((backup_key, "backup"))

        if not keys_to_try:
            raise ProviderConfigError("GEMINI_API_KEY is not configured.", category="MISSING_CREDENTIALS") from None

        primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        fallback_model = os.getenv(
            "GEMINI_FALLBACK_MODEL",
            "gemini-3.5-flash" if primary_model != "gemini-3.5-flash" else "gemini-3.6-flash"
        )

        models_to_try = [primary_model]
        if fallback_model and fallback_model != primary_model:
            models_to_try.append(fallback_model)

        last_error = None
        start_time = time.perf_counter()

        for key_value, key_label in keys_to_try:
            key_had_recoverable_error = False

            for model_idx, model in enumerate(models_to_try):
                elapsed = time.perf_counter() - start_time
                time_remaining = timeout_seconds - elapsed
                if time_remaining < MIN_GEMINI_DEADLINE_SECONDS:
                    logger.warning(
                        f"[GEMINI] Time budget exhausted ({time_remaining:.1f}s left). "
                        f"Cannot attempt model={model} (min {MIN_GEMINI_DEADLINE_SECONDS:.1f}s required)."
                    )
                    break

                # We must never send a deadline below 10.0s to Google GenAI.
                # If time_remaining >= 20.0s and multiple models exist, allocate a split;
                # otherwise allocate full time_remaining without creating a sub-10s deadline.
                if len(models_to_try) > 1 and time_remaining >= 20.0:
                    per_call_timeout = min(time_remaining, max(time_remaining * 0.6, MIN_GEMINI_DEADLINE_SECONDS))
                else:
                    per_call_timeout = time_remaining

                client = self._get_client(per_call_timeout, api_key=key_value)

                # Max 1 retry per model only if budget permits at least another full 10.0s attempt
                attempts = 2 if time_remaining >= 20.0 else 1

                for attempt in range(1, attempts + 1):
                    cur_remaining = timeout_seconds - (time.perf_counter() - start_time)
                    if cur_remaining < MIN_GEMINI_DEADLINE_SECONDS:
                        logger.warning(
                            f"[GEMINI] Time budget exhausted ({cur_remaining:.1f}s left). "
                            f"Skipping attempt {attempt} on model={model} (min {MIN_GEMINI_DEADLINE_SECONDS:.1f}s required)."
                        )
                        break

                    call_start = time.perf_counter()
                    logger.info(f"[GEMINI START] key={key_label} model={model} attempt={attempt}/{attempts} budget={cur_remaining:.1f}s")
                    try:
                        config = {}
                        if response_mime_type:
                            config["response_mime_type"] = response_mime_type
                        resp = client.models.generate_content(model=model, contents=prompt, config=config if config else None)
                        if not resp or not resp.text:
                            raise ValueError("Gemini returned empty text response.")
                        duration = time.perf_counter() - call_start
                        logger.info(f"[GEMINI SUCCESS] key={key_label} model={model} attempt={attempt} duration={duration:.2f}s")
                        return resp.text
                    except Exception as e:
                        duration = time.perf_counter() - call_start
                        last_error = e
                        cat = self._categorize(e)

                        if self._is_auth_error(e):
                            logger.error(f"[GEMINI AUTH FAILED] key={key_label} model={model} duration={duration:.2f}s category={cat} error={sanitize_error_text(e)}")
                            raise ProviderConfigError("Gemini API key is invalid or unauthorized.", category="AUTH_CONFIG_ERROR") from None

                        if not self._is_retryable(e):
                            clean_err = sanitize_error_text(e)
                            logger.error(f"[GEMINI PERMANENT ERROR] key={key_label} model={model} duration={duration:.2f}s category={cat}: {clean_err}")
                            if cat == "MODEL_NOT_FOUND_404":
                                raise ProviderConfigError(f"Gemini model '{model}' not found or invalid model ID.", category="MODEL_NOT_FOUND_404") from None
                            if cat == "MALFORMED_REQUEST_400":
                                raise ProviderError(f"Gemini malformed request: {clean_err}", category="MALFORMED_REQUEST_400", retryable=False) from None
                            raise ProviderError(f"Gemini permanent error ({cat}): {clean_err}", category=cat, retryable=False) from None

                        key_had_recoverable_error = True
                        logger.warning(f"[GEMINI RETRYABLE ERROR] key={key_label} model={model} attempt={attempt}/{attempts} duration={duration:.2f}s category={cat}: {sanitize_error_text(e)}")

                        # If another attempt is budgeted on this model
                        elapsed_now = time.perf_counter() - start_time
                        if attempt < attempts and (timeout_seconds - elapsed_now) >= MIN_GEMINI_DEADLINE_SECONDS:
                            delay = random.uniform(0.8, 1.5)
                            time.sleep(delay)
                        else:
                            break

                # If primary model failed, log failover
                if model_idx < len(models_to_try) - 1:
                    logger.info(f"[GEMINI MODEL FAILOVER] key={key_label} Moving to next model due to {model} unavailability.")

            # After exhausting all models on this key, try backup if failure was recoverable
            if key_had_recoverable_error and key_label == "primary" and len(keys_to_try) > 1:
                logger.warning("[GEMINI KEY FALLBACK] Primary key exhausted with recoverable errors. Trying backup key.")
                continue
            elif key_had_recoverable_error:
                break  # Backup also failed or no backup available
            else:
                break  # No error tracked — shouldn't reach here normally

        category = self._categorize(last_error) if last_error else "TIMEOUT"
        clean_err = sanitize_error_text(last_error) if last_error else "Budget exhausted"
        raise ProviderUnavailableError(f"Gemini service unavailable ({category}): {clean_err}", category=category) from None

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
        if custom_prompt:
            prompt = custom_prompt
        else:
            prompt = get_quiz_prompt(course_code, course_name, topic_name, syllabus_context)
        return self._execute_with_models(prompt, timeout_seconds, response_mime_type="application/json")

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
        return self._execute_with_models(prompt, timeout_seconds, response_mime_type="application/json")
