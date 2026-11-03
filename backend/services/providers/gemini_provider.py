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
    get_analysis_prompt,
    extract_json_from_text
)
from services.logger_utils import sanitize_error_text
from services.quota_manager import quota_manager, parse_rate_limit, RateLimitType

logger = logging.getLogger("learnai.providers.gemini")


class GeminiProvider(BaseAIProvider):
    """Google Gemini AI Provider with bounded retries and fast model failover."""

    def __init__(self):
        self._name = "gemini"

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return bool(
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GEMINI_SECONDARY_API_KEY")
            or os.getenv("GEMINI_BACKUP_API_KEY")
        )

    def _get_client(self, timeout_sec: float, api_key: str) -> genai.Client:
        if not api_key:
            raise ProviderConfigError("GEMINI_API_KEY is missing from environment.", category="MISSING_CREDENTIALS")
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
        if "empty response" in msg or "malformed json" in msg:
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
            elif code == 504:
                return "DEADLINE_EXCEEDED_504"
            elif code in {500, 502}:
                return f"SERVER_ERROR_{code}"
            elif code == 400:
                return "MALFORMED_REQUEST_400"
            elif code == 404:
                return "MODEL_NOT_FOUND_404"
        msg = str(exc).lower()
        status = (getattr(exc, "status", "") or "").lower()
        if "not found" in msg or "404" in msg:
            return "MODEL_NOT_FOUND_404"
        if "503" in msg or "service unavailable" in msg or "high demand" in msg:
            return "SERVICE_UNAVAILABLE_503"
        if "429" in msg or "rate limit" in msg or "resource exhausted" in msg or "quota" in msg:
            return "RATE_LIMIT_429"
        if "deadline" in msg or "deadline_exceeded" in status or "504" in msg:
            return "DEADLINE_EXCEEDED_504"
        if "empty response" in msg:
            return "EMPTY_RESPONSE"
        if "malformed json" in msg:
            return "MALFORMED_JSON_PARSE_ERROR"
        exc_name = type(exc).__name__
        if "Timeout" in exc_name:
            return "CLIENT_TIMEOUT"
        if "Connection" in exc_name or "Network" in exc_name:
            return "NETWORK_ERROR"
        return "UNKNOWN_ERROR"

    def _execute_with_models(
        self,
        prompt: str,
        timeout_seconds: float,
        response_mime_type: Optional[str] = None,
        expected_schema_key: Optional[str] = None
    ) -> str:
        MIN_GEMINI_DEADLINE_SECONDS = 10.0
        # Google GenAI strictly requires an API server deadline of at least 10.0s (10,000ms).
        # Any request with X-Server-Timeout < 10 returns HTTP 400 INVALID_ARGUMENT.
        # If GEMINI_SAFETY_MARGIN_SECONDS is explicitly configured in environment, require the budget
        # to accommodate both Google's minimum deadline and the safety margin.
        # Otherwise, the absolute minimum floor is Google's required 10.0s deadline.
        margin_env = os.getenv("GEMINI_SAFETY_MARGIN_SECONDS")
        if margin_env is not None:
            GEMINI_SAFETY_MARGIN_SECONDS = float(margin_env)
            MIN_SAFE_GEMINI_BUDGET = MIN_GEMINI_DEADLINE_SECONDS + GEMINI_SAFETY_MARGIN_SECONDS
        else:
            GEMINI_SAFETY_MARGIN_SECONDS = 1.5
            MIN_SAFE_GEMINI_BUDGET = float(os.getenv("MIN_GEMINI_REQUEST_BUDGET", str(MIN_GEMINI_DEADLINE_SECONDS)))

        # Microsecond jitter tolerance (50ms) to prevent microsecond budget starvation on exact-deadline allocations
        BUDGET_JITTER_EPSILON = 0.05

        if timeout_seconds < (MIN_SAFE_GEMINI_BUDGET - BUDGET_JITTER_EPSILON):
            logger.warning(
                f"[GEMINI TIMEOUT] Initial budget {timeout_seconds:.1f}s is below minimum safe budget ({MIN_SAFE_GEMINI_BUDGET:.1f}s). "
                "Yielding without issuing network request."
            )
            raise ProviderUnavailableError(
                f"Gemini: initial timeout budget {timeout_seconds:.1f}s is below minimum safe budget of {MIN_SAFE_GEMINI_BUDGET:.1f}s.",
                category="TIMEOUT"
            )

        primary_key = os.getenv("GEMINI_API_KEY", "")
        secondary_key = os.getenv("GEMINI_SECONDARY_API_KEY") or os.getenv("GEMINI_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if secondary_key and secondary_key != primary_key:
            keys_to_try.append((secondary_key, "secondary"))

        if not keys_to_try:
            raise ProviderConfigError("GEMINI_API_KEY is not configured.", category="MISSING_CREDENTIALS") from None

        # Verified active Gemini models: gemini-1.5-flash (primary), gemini-1.5-pro (fallback)
        DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"
        KNOWN_INVALID_GEMINI_MODELS = {"gemini-2.0-flash", "gemini-2.0-flash-exp", "gemini-1.0-pro"}

        configured_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        fallback_model = (os.getenv("GEMINI_FALLBACK_MODEL") or "").strip()

        models_to_try = [configured_model]
        if fallback_model and fallback_model.lower() not in ("none", "false", "") and fallback_model not in models_to_try:
            models_to_try.append(fallback_model)
        elif configured_model in KNOWN_INVALID_GEMINI_MODELS:
            logger.warning(
                "[GEMINI CONFIG WARNING] Configured GEMINI_MODEL '%s' is a known-invalid / deprecated model "
                "(reported 404 on API v1beta). Adding verified active model '%s' to candidate models.",
                configured_model, DEFAULT_GEMINI_MODEL
            )
            if DEFAULT_GEMINI_MODEL not in models_to_try:
                models_to_try.append(DEFAULT_GEMINI_MODEL)
        elif "GEMINI_MODEL" not in os.environ:
            if DEFAULT_GEMINI_MODEL not in models_to_try:
                models_to_try.append(DEFAULT_GEMINI_MODEL)
            if "gemini-1.5-pro" not in models_to_try:
                models_to_try.append("gemini-1.5-pro")

        last_error = None
        start_time = time.perf_counter()

        for key_value, key_label in keys_to_try:
            key_had_recoverable_error = False

            for model_idx, model in enumerate(models_to_try):
                elapsed = time.perf_counter() - start_time
                time_remaining = timeout_seconds - elapsed
                if time_remaining < (MIN_SAFE_GEMINI_BUDGET - BUDGET_JITTER_EPSILON):
                    logger.warning(
                        f"[GEMINI TIMEOUT] Time budget exhausted ({time_remaining:.1f}s left). "
                        f"Cannot attempt model={model} (min safe budget {MIN_SAFE_GEMINI_BUDGET:.1f}s required)."
                    )
                    break

                usable_budget = max(time_remaining - GEMINI_SAFETY_MARGIN_SECONDS, MIN_GEMINI_DEADLINE_SECONDS)
                if len(models_to_try) > 1 and time_remaining >= 24.0:
                    per_call_timeout = min(time_remaining, max(time_remaining * 0.5, MIN_GEMINI_DEADLINE_SECONDS))
                else:
                    per_call_timeout = max(time_remaining, MIN_GEMINI_DEADLINE_SECONDS)

                is_avail, remaining = quota_manager.is_model_available('gemini', model, key_value)
                if not is_avail:
                    logger.info(
                        f'[GEMINI QUOTA SKIP] model={model} on cooldown ({remaining:.1f}s remaining). Skipping.'
                    )
                    continue

                client = self._get_client(per_call_timeout, api_key=key_value)
                attempts = 2 if time_remaining >= 24.0 else 1

                for attempt in range(1, attempts + 1):
                    cur_remaining = timeout_seconds - (time.perf_counter() - start_time)
                    if cur_remaining < (MIN_SAFE_GEMINI_BUDGET - BUDGET_JITTER_EPSILON):
                        logger.warning(
                            f"[GEMINI TIMEOUT] Time budget exhausted ({cur_remaining:.1f}s left). "
                            f"Skipping attempt {attempt} on model={model} (min safe budget {MIN_SAFE_GEMINI_BUDGET:.1f}s required)."
                        )
                        break

                    call_start = time.perf_counter()
                    # Calculate per-call HTTP timeout:
                    # 1. Deduct safety margin when remaining time permits (cur_remaining >= 10.0 + margin).
                    # 2. When budget is at exact Google deadline boundary (within jitter epsilon), allocate exactly 10.0s.
                    # 3. Defensively ensure call_timeout_sec is at least Google's required 10.0s deadline when within boundary.
                    if cur_remaining >= MIN_GEMINI_DEADLINE_SECONDS + GEMINI_SAFETY_MARGIN_SECONDS:
                        call_timeout_sec = cur_remaining - GEMINI_SAFETY_MARGIN_SECONDS
                    elif cur_remaining >= MIN_GEMINI_DEADLINE_SECONDS - BUDGET_JITTER_EPSILON:
                        call_timeout_sec = MIN_GEMINI_DEADLINE_SECONDS
                    else:
                        call_timeout_sec = cur_remaining

                    call_timeout_ms = int(call_timeout_sec * 1000)

                    logger.info(
                        f"[GEMINI START] key={key_label} model={model} attempt={attempt}/{attempts} "
                        f"budget={cur_remaining:.1f}s (call_timeout={call_timeout_sec:.1f}s)"
                    )
                    try:
                        # Explicitly disable AFC and set per-call http timeout in config
                        config = types.GenerateContentConfig(
                            response_mime_type=response_mime_type,
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                            http_options=types.HttpOptions(timeout=call_timeout_ms)
                        )
                        resp = client.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=config
                        )
                        duration = time.perf_counter() - call_start

                        finish_reason = "unknown"
                        raw_text = None
                        if resp and getattr(resp, "candidates", None) and len(resp.candidates) > 0:
                            cand = resp.candidates[0]
                            fr = getattr(cand, "finish_reason", "unknown")
                            finish_reason = getattr(fr, "name", str(fr))

                        # 1. Primary text extraction
                        try:
                            raw_text = resp.text
                        except Exception:
                            raw_text = None

                        # 2. Candidate content parts extraction fallback
                        if not raw_text and resp and getattr(resp, "candidates", None) and len(resp.candidates) > 0:
                            cand = resp.candidates[0]
                            if hasattr(cand, "content") and hasattr(cand.content, "parts") and cand.content.parts:
                                text_parts = []
                                for part in cand.content.parts:
                                    if getattr(part, "thought", False):
                                        continue
                                    p_txt = getattr(part, "text", None)
                                    if p_txt and isinstance(p_txt, str):
                                        text_parts.append(p_txt)
                                if text_parts:
                                    raw_text = "".join(text_parts)

                        if not raw_text or not raw_text.strip():
                            logger.warning(
                                f"[GEMINI EMPTY_RESPONSE] key={key_label} model={model} attempt={attempt} "
                                f"duration={duration:.2f}s finish_reason='{finish_reason}': content is empty or absent."
                            )
                            raise ValueError(f"Gemini model {model} returned an empty response (finish_reason='{finish_reason}').")

                        # 3. JSON shape validation if json response requested
                        if response_mime_type == "application/json":
                            from services.providers.base import extract_json_from_text
                            try:
                                parsed_json = extract_json_from_text(raw_text)
                                if not isinstance(parsed_json, dict):
                                    raise ValueError("Response is not a valid JSON dictionary.")
                                if expected_schema_key and expected_schema_key not in parsed_json:
                                    raise ValueError(f"Response missing required root key '{expected_schema_key}'.")
                            except Exception as parse_err:
                                logger.warning(
                                    f"[GEMINI PARSE_ERROR] key={key_label} model={model} attempt={attempt} "
                                    f"duration={duration:.2f}s: response failed JSON schema validation: {sanitize_error_text(parse_err)}"
                                )
                                raise ValueError(f"Gemini model {model} returned malformed JSON: {sanitize_error_text(parse_err)}") from parse_err

                        budget_left = timeout_seconds - (time.perf_counter() - start_time)
                        quota_manager.record_success("gemini", model, key_value)
                        logger.info(
                            f"[GEMINI SUCCESS] key={key_label} model={model} attempt={attempt} "
                            f"duration={duration:.2f}s budget_left={budget_left:.1f}s"
                        )
                        return raw_text

                    except Exception as e:
                        duration = time.perf_counter() - call_start
                        last_error = e
                        cat = self._categorize(e)

                        if self._is_auth_error(e):
                            logger.error(
                                f"[GEMINI API_ERROR] key={key_label} model={model} attempt={attempt} "
                                f"duration={duration:.2f}s category={cat} error={sanitize_error_text(e)}"
                            )
                            raise ProviderConfigError(
                                "Gemini API key is invalid or unauthorized.", category="AUTH_CONFIG_ERROR"
                            ) from None

                        if not self._is_retryable(e):
                            clean_err = sanitize_error_text(e)
                            logger.error(
                                f"[GEMINI API_ERROR] key={key_label} model={model} attempt={attempt} "
                                f"duration={duration:.2f}s category={cat}: {clean_err}"
                            )
                            if cat in ("MODEL_NOT_FOUND_404", "MODEL_DEPRECATED_410"):
                                if model_idx < len(models_to_try) - 1:
                                    logger.warning(
                                        f"[GEMINI MODEL FAILOVER] key={key_label} model={model} returned {cat}. Trying next candidate model..."
                                    )
                                    break
                                raise ProviderConfigError(
                                    f"Gemini model '{model}' not found or invalid model ID.", category="MODEL_NOT_FOUND_404"
                                ) from None
                            if cat == "MALFORMED_REQUEST_400":
                                raise ProviderError(
                                    f"Gemini malformed request: {clean_err}", category="MALFORMED_REQUEST_400", retryable=False
                                ) from None
                            raise ProviderError(
                                f"Gemini permanent error ({cat}): {clean_err}", category=cat, retryable=False
                            ) from None

                        # Retryable transient errors, timeouts, empty responses, or malformed JSON
                        key_had_recoverable_error = True

                        if cat == "RATE_LIMIT_429":
                            logger.warning(
                                f"[GEMINI API_ERROR] key={key_label} model={model} attempt={attempt}/{attempts} "
                                f"duration={duration:.2f}s category={cat}: {sanitize_error_text(e)}"
                            )
                            rl_info = parse_rate_limit("gemini", e)
                            cooldown = quota_manager.record_rate_limit("gemini", model, key_value, rl_info)
                            logger.info(
                                f"[GEMINI RATE LIMIT] model={model} key={key_label} type={rl_info.limit_type.value} cooldown={cooldown:.1f}s"
                            )
                            break

                        if cat in ("DEADLINE_EXCEEDED_504", "CLIENT_TIMEOUT", "REQUEST_TIMEOUT_408") or "deadline" in str(e).lower() or "timeout" in str(e).lower():
                            logger.warning(
                                f"[GEMINI TIMEOUT] key={key_label} model={model} attempt={attempt} "
                                f"duration={duration:.2f}s category={cat}: {sanitize_error_text(e)}. Yielding without redundant retries of this model."
                            )
                            break

                        if cat in ("EMPTY_RESPONSE", "MALFORMED_JSON_PARSE_ERROR"):
                            # Empty response or malformed JSON: break to try fallback model
                            logger.warning(
                                f"[GEMINI API_ERROR] key={key_label} model={model} attempt={attempt} "
                                f"duration={duration:.2f}s category={cat}: {sanitize_error_text(e)}. Falling over to next model..."
                            )
                            break

                        logger.warning(
                            f"[GEMINI API_ERROR] key={key_label} model={model} attempt={attempt}/{attempts} "
                            f"duration={duration:.2f}s category={cat}: {sanitize_error_text(e)}"
                        )

                        elapsed_now = time.perf_counter() - start_time
                        if attempt < attempts and (timeout_seconds - elapsed_now) >= (MIN_SAFE_GEMINI_BUDGET - BUDGET_JITTER_EPSILON):
                            delay = random.uniform(0.5, 1.0)
                            time.sleep(delay)
                        else:
                            break

                if model_idx < len(models_to_try) - 1:
                    logger.info(f"[GEMINI MODEL FAILOVER] key={key_label} Moving to next model due to {model} unavailability.")

            if key_had_recoverable_error and key_label == "primary" and len(keys_to_try) > 1:
                logger.warning("[GEMINI KEY FALLBACK] Primary key exhausted with recoverable errors. Trying secondary key.")
                continue
            elif key_had_recoverable_error:
                break
            else:
                if last_error and self._categorize(last_error) in ("MODEL_NOT_FOUND_404", "MODEL_DEPRECATED_410"):
                    raise ProviderConfigError(f"Gemini model not found across candidate models: {models_to_try}.", category="MODEL_NOT_FOUND_404") from None
                break

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
        return self._execute_with_models(
            prompt,
            timeout_seconds,
            response_mime_type="application/json",
            expected_schema_key="questions"
        )

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
        return self._execute_with_models(
            prompt,
            timeout_seconds,
            response_mime_type="application/json",
            expected_schema_key="summary"
        )
