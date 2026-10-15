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
        # Ensure timeout_ms respects caller timeout_sec, bounded by at least 1000ms
        timeout_ms = max(int(timeout_sec * 1000), 1000)
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
        if timeout_seconds < 4.0:
            logger.warning(f"[GEMINI] Timeout budget {timeout_seconds:.1f}s is too small for Gemini call.")
            raise ProviderUnavailableError(f"Gemini: timeout budget {timeout_seconds:.1f}s too small for call.", category="TIMEOUT")

        primary_key = os.getenv("GEMINI_API_KEY", "")
        backup_key = os.getenv("GEMINI_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if backup_key and backup_key != primary_key:
            keys_to_try.append((backup_key, "backup"))

        if not keys_to_try:
            raise ProviderConfigError("GEMINI_API_KEY is not configured.", category="MISSING_CREDENTIALS")

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
                if time_remaining <= 3.0:
                    logger.warning(f"[GEMINI] Time budget exhausted ({time_remaining:.1f}s left). Cannot attempt model={model}.")
                    break

                per_call_timeout = min(time_remaining, max(time_remaining * 0.7, 4.0) if len(models_to_try) > 1 else time_remaining)
                client = self._get_client(per_call_timeout, api_key=key_value)

                # Max 1 retry per model if budget permits (>6s remaining)
                attempts = 2 if time_remaining > 6.0 else 1

                for attempt in range(1, attempts + 1):
                    cur_remaining = timeout_seconds - (time.perf_counter() - start_time)
                    if cur_remaining <= 3.0:
                        logger.warning(f"[GEMINI] Time budget exhausted ({cur_remaining:.1f}s left). Skipping attempt {attempt} on model={model}.")
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
                            logger.error(f"[GEMINI AUTH FAILED] key={key_label} model={model} duration={duration:.2f}s category={cat}")
                            raise ProviderConfigError("Gemini API key is invalid or unauthorized.", category=cat) from e

                        if not self._is_retryable(e):
                            logger.error(f"[GEMINI PERMANENT ERROR] key={key_label} model={model} duration={duration:.2f}s category={cat}")
                            raise ProviderError(f"Gemini error: {e}", category=cat) from e

                        key_had_recoverable_error = True
                        logger.warning(f"[GEMINI RETRYABLE ERROR] key={key_label} model={model} attempt={attempt}/{attempts} duration={duration:.2f}s category={cat}")

                        # If another attempt is budgeted on this model
                        elapsed_now = time.perf_counter() - start_time
                        if attempt < attempts and (timeout_seconds - elapsed_now) > 4.0:
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
        raise ProviderUnavailableError(f"Gemini service unavailable ({category}).", category=category)

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
