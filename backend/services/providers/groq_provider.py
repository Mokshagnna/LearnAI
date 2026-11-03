import os
import time
import random
import logging
import threading
import json
from typing import Optional, List
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

logger = logging.getLogger("learnai.providers.groq")


class GroqProvider(BaseAIProvider):
    """Groq AI Provider for ultra-fast fallback and primary inference."""

    _client_cache: dict = {}
    _client_lock: threading.Lock = threading.Lock()

    def __init__(self):
        self._name = "groq"

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return bool(
            os.getenv("GROQ_API_KEY")
            or os.getenv("GROQ_SECONDARY_API_KEY")
            or os.getenv("GROQ_BACKUP_API_KEY")
        )

    def _get_client(self, timeout_sec: float, api_key: str):
        if not api_key:
            raise ProviderConfigError("GROQ_API_KEY is missing from environment.", category="MISSING_CREDENTIALS")
        with self._client_lock:
            client = self._client_cache.get(api_key)
            if client is None:
                try:
                    from groq import Groq
                    # Client connection reuse across requests; per-request timeout passed in create()
                    client = Groq(api_key=api_key, timeout=max(float(timeout_sec), 30.0), max_retries=0)
                    self._client_cache[api_key] = client
                except ImportError as e:
                    raise ProviderConfigError("groq python package is not installed.", category="DEPENDENCY_MISSING") from e
            return client

    def _execute(self, prompt: str, timeout_seconds: float, max_tokens: int = 4000) -> str:
        primary_key = os.getenv("GROQ_API_KEY", "")
        secondary_key = os.getenv("GROQ_SECONDARY_API_KEY") or os.getenv("GROQ_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if secondary_key and secondary_key != primary_key:
            keys_to_try.append((secondary_key, "secondary"))

        if not keys_to_try:
            raise ProviderConfigError("GROQ_API_KEY is not configured.", category="MISSING_CREDENTIALS")

        # Verified active Groq models: llama-3.3-70b-versatile (primary, 12k TPM), llama-3.1-8b-instant (fallback, 6k TPM)
        DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
        DEFAULT_GROQ_FALLBACK = "llama-3.1-8b-instant"
        KNOWN_INVALID_GROQ_MODELS = {
            "llama3-70b-8192",
            "llama3-8b-8192",
            "llama-3.2-90b-text-preview",
            "mixtral-8x7b-32768",
        }

        configured_model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip()
        fallback_model = (os.getenv("GROQ_FALLBACK_MODEL") or DEFAULT_GROQ_FALLBACK).strip()

        if configured_model in KNOWN_INVALID_GROQ_MODELS:
            logger.warning(
                "[GROQ CONFIG WARNING] Configured GROQ_MODEL '%s' is a known-deprecated or decommissioned model. "
                "Adding verified active model '%s' to candidate models.",
                configured_model, DEFAULT_GROQ_MODEL
            )

        if fallback_model in KNOWN_INVALID_GROQ_MODELS:
            logger.warning(
                "[GROQ CONFIG WARNING] Configured GROQ_FALLBACK_MODEL '%s' is a known-deprecated or decommissioned model. "
                "Adding verified active model '%s' to candidate models.",
                fallback_model, DEFAULT_GROQ_FALLBACK
            )

        candidate_models = [configured_model]
        if fallback_model and fallback_model not in candidate_models:
            candidate_models.append(fallback_model)

        for active_model in [DEFAULT_GROQ_MODEL, DEFAULT_GROQ_FALLBACK, "openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
            if active_model not in candidate_models and active_model not in KNOWN_INVALID_GROQ_MODELS:
                candidate_models.append(active_model)

        last_error = None
        overall_start = time.perf_counter()

        for key_value, key_label in keys_to_try:
            time_left_for_key = timeout_seconds - (time.perf_counter() - overall_start)
            if time_left_for_key < 2.5:
                logger.warning(f"[GROQ] Budget exhausted ({time_left_for_key:.1f}s left). Cannot try key={key_label}.")
                break
            client = self._get_client(time_left_for_key, api_key=key_value)
            key_error = None
            key_had_auth_error = False

            for m_idx, current_model in enumerate(candidate_models):
                time_left = timeout_seconds - (time.perf_counter() - overall_start)
                if time_left < 2.5:
                    break
                call_start = time.perf_counter()
                try:
                    logger.info(f"[GROQ START] key={key_label} model={current_model} timeout={time_left:.1f}s")
                    create_kwargs = {
                        "model": current_model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.3,
                        "max_tokens": max_tokens,
                        "timeout": time_left
                    }
                    if "json" in prompt.lower():
                        create_kwargs["response_format"] = {"type": "json_object"}

                    try:
                        chat_completion = client.chat.completions.create(**create_kwargs)
                    except Exception as call_err:
                        err_str = str(call_err).lower()
                        if "response_format" in err_str and "response_format" in create_kwargs:
                            logger.warning(
                                f"[GROQ] response_format parameter not supported by {current_model}: {call_err}. "
                                "Retrying call without response_format."
                            )
                            create_kwargs.pop("response_format", None)
                            chat_completion = client.chat.completions.create(**create_kwargs)
                        else:
                            raise call_err

                    duration = time.perf_counter() - call_start

                    if not chat_completion or not getattr(chat_completion, "choices", None):
                        raise ValueError(f"Groq model {current_model} returned no choices in response.")

                    choice = chat_completion.choices[0]
                    message = getattr(choice, "message", None) if not isinstance(choice, dict) else choice.get("message")
                    if message is None:
                        raise ValueError(f"Groq model {current_model} returned a choice with no message.")

                    # Primary extraction: message.content only.
                    # Per architectural directive: message.reasoning or reasoning_content must NOT
                    # be used as a source of quiz answers, even if it contains JSON-like text.
                    raw_content = getattr(message, "content", None) if not isinstance(message, dict) else message.get("content")
                    result_text = None

                    if raw_content is not None and isinstance(raw_content, str) and raw_content.strip():
                        result_text = raw_content.strip()

                    if not result_text:
                        raw_fr = getattr(choice, "finish_reason", None) or (choice.get("finish_reason") if isinstance(choice, dict) else "unknown")
                        finish_reason = raw_fr if isinstance(raw_fr, (str, int)) else ("mock_finish" if "Mock" in type(raw_fr).__name__ else "unknown")
                        raise ValueError(
                            f"Groq model {current_model} returned an empty response "
                            f"(content is empty, finish_reason='{finish_reason}')."
                        )

                    logger.info(f"[GROQ SUCCESS] key={key_label} model={current_model} duration={duration:.2f}s")
                    return result_text

                except Exception as e:
                    key_error = e
                    last_error = e
                    duration = time.perf_counter() - call_start
                    msg = str(e).lower()

                    # Auth errors on primary key: fail immediately without trying secondary key
                    if any(term in msg for term in ["invalid api key", "authentication", "unauthorized", "api_key", "401"]):
                        logger.error(f"[GROQ AUTH FAILED] key={key_label} duration={duration:.2f}s error={sanitize_error_text(e)}")
                        raise ProviderConfigError("Groq API key is invalid or unauthorized.", category="AUTH_CONFIG_ERROR") from None

                    if any(term in msg for term in ["model_not_found", "model not found", "does not exist", " 404", "404 ", "404:", "/404"]):
                        logger.warning(
                            f"[GROQ MODEL NOT FOUND] key={key_label} model={current_model} "
                            f"duration={duration:.2f}s: {sanitize_error_text(e)}. Attempting fallback model..."
                        )
                        continue

                    logger.warning(
                        f"[GROQ MODEL FALLBACK] key={key_label} model={current_model} "
                        f"failed (duration={duration:.2f}s): {sanitize_error_text(e)}. Attempting fallback model..."
                    )

                    # Bounded backoff/jitter for 429 and 503 if time budget allows
                    is_rate_limit_or_unavailable = any(term in msg for term in ["rate limit", "429", "503", "too many requests", "unavailable", "overloaded"])
                    time_left_now = timeout_seconds - (time.perf_counter() - overall_start)
                    has_more_models = (m_idx + 1) < len(candidate_models)
                    if not has_more_models and is_rate_limit_or_unavailable and time_left_now > 3.0:
                        backoff = min(1.0, 0.2 * (2 ** m_idx)) + random.uniform(0.05, 0.15)
                        if time_left_now > backoff + 2.0:
                            logger.info(f"[GROQ BACKOFF] Retrying after {backoff:.2f}s backoff due to transient status (429/503)...")
                            time.sleep(backoff)
                    continue


            # All models on this key exhausted — decide whether to try secondary key
            if key_error is not None:
                msg = str(key_error).lower()
                is_recoverable = any(term in msg for term in [
                    "rate limit", "429", "too many requests",
                    "503", "500", "502", "504", "unavailable", "timeout", "timed out"
                ])
                if is_recoverable and key_label == "primary" and len(keys_to_try) > 1:
                    logger.warning("[GROQ KEY FALLBACK] Primary key failed with recoverable error. Trying secondary key.")
                    continue
                # Non-recoverable error or no secondary available — stop
                break

        if last_error:
            msg = str(last_error).lower()
            clean_err = sanitize_error_text(last_error)
            if any(term in msg for term in ["model_not_found", "model not found", "does not exist", " 404", "404 ", "404:", "/404"]):
                raise ProviderConfigError(
                    f"Groq model not found or invalid model identifier: {clean_err}", category="MODEL_NOT_FOUND_404"
                ) from last_error
            if any(term in msg for term in ["rate limit", "429", "too many requests"]):
                raise ProviderUnavailableError(
                    "Groq rate limit reached across all models and keys.", category="RATE_LIMIT_429"
                ) from last_error
            if any(term in msg for term in ["timeout", "timed out", "deadline"]) or "timeouterror" in type(last_error).__name__.lower():
                raise ProviderUnavailableError(
                    f"Groq request timed out: {clean_err}", category="TIMEOUT"
                ) from last_error
            raise ProviderUnavailableError(
                f"Groq service unavailable: {clean_err}", category="SERVICE_UNAVAILABLE"
            ) from last_error

        raise ProviderUnavailableError("Groq timeout or all models exhausted.", category="TIMEOUT")

    def generate_quiz_raw(
        self,
        course_code: str,
        course_name: str,
        topic_name: str,
        syllabus_context: str,
        timeout_seconds: float,
        custom_prompt: Optional[str] = None,
        max_tokens: int = 4000,
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
        return self._execute(prompt, timeout_seconds, max_tokens=1500)
