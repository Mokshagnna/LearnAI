import os
import time
import random
import logging
import threading
import json
from typing import Optional, List, Any
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
    _models_cache: dict = {}
    _unsupported_json_format_models: set = set()
    _client_lock: threading.Lock = threading.Lock()

    def get_available_models(self, client: Optional[Any] = None, api_key: Optional[str] = None) -> List[str]:
        target_key = api_key or os.getenv("GROQ_API_KEY", "")
        if not target_key and not client:
            return []
        with self._client_lock:
            if target_key:
                cached = self._models_cache.get(target_key)
                if cached and (time.time() - cached[0] < 3600):
                    return cached[1]
        try:
            target_client = client if client is not None else self._get_client(5.0, api_key=target_key)
            if not hasattr(target_client, "models") or not hasattr(target_client.models, "list"):
                return []
            resp = target_client.models.list(timeout=4.0)
            data = getattr(resp, "data", [])
            model_ids = [getattr(m, "id", "") for m in data if getattr(m, "id", "")]
            if model_ids and target_key:
                with self._client_lock:
                    self._models_cache[target_key] = (time.time(), model_ids)
                logger.info(f"[GROQ DISCOVERY] Discovered {len(model_ids)} active models on Groq account: {model_ids[:5]}...")
            return model_ids
        except Exception as e:
            logger.warning(f"[GROQ DISCOVERY] Failed to query active models: {e}")
            return []

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

    def _execute(self, prompt: str, timeout_seconds: float, max_tokens: int = 1800) -> str:
        primary_key = os.getenv("GROQ_API_KEY", "")
        secondary_key = os.getenv("GROQ_SECONDARY_API_KEY") or os.getenv("GROQ_BACKUP_API_KEY", "")

        keys_to_try = []
        if primary_key:
            keys_to_try.append((primary_key, "primary"))
        if secondary_key and secondary_key != primary_key:
            keys_to_try.append((secondary_key, "secondary"))

        if not keys_to_try:
            raise ProviderConfigError("GROQ_API_KEY is not configured.", category="MISSING_CREDENTIALS")

        # Verified active Groq models: llama-3.1-8b-instant (primary ultra-fast, 560 tok/s), llama-3.3-70b-versatile (fallback, 12k TPM)
        DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
        DEFAULT_GROQ_FALLBACK = "llama-3.3-70b-versatile"
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

        raw_candidates = [configured_model]
        if fallback_model and fallback_model not in raw_candidates:
            raw_candidates.append(fallback_model)

        for active_model in [DEFAULT_GROQ_MODEL, DEFAULT_GROQ_FALLBACK, "openai/gpt-oss-20b", "openai/gpt-oss-120b", "llama-3.3-70b-versatile"]:
            if active_model not in raw_candidates and active_model not in KNOWN_INVALID_GROQ_MODELS:
                raw_candidates.append(active_model)

        last_error = None
        overall_start = time.perf_counter()

        for key_value, key_label in keys_to_try:
            time_left_for_key = timeout_seconds - (time.perf_counter() - overall_start)
            if time_left_for_key < 2.0:
                logger.warning(f"[GROQ] Budget exhausted ({time_left_for_key:.1f}s left). Cannot try key={key_label}.")
                break
            client = self._get_client(time_left_for_key, api_key=key_value)
            key_error = None
            key_had_auth_error = False

            # Dynamic model discovery: prune candidates not supported by this API key
            available = self.get_available_models(client=client, api_key=key_value)
            if available:
                candidate_models = [m for m in raw_candidates if m in available]
                if not candidate_models:
                    text_models = [
                        m for m in available
                        if not any(x in m.lower() for x in ["whisper", "guard", "orpheus", "vision", "safeguard"])
                    ]
                    candidate_models = text_models[:3] if text_models else raw_candidates
            else:
                candidate_models = raw_candidates

            for m_idx, current_model in enumerate(candidate_models):
                time_left = timeout_seconds - (time.perf_counter() - overall_start)
                if time_left < 2.0:
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
                    if "json" in prompt.lower() and current_model not in self._unsupported_json_format_models:
                        create_kwargs["response_format"] = {"type": "json_object"}

                    try:
                        chat_completion = client.chat.completions.create(**create_kwargs)
                    except Exception as call_err:
                        err_str = str(call_err).lower()
                        is_json_format_err = (
                            "response_format" in err_str
                            or "failed to validate json" in err_str
                            or "validate json" in err_str
                            or "adjust your prompt" in err_str
                            or "json_validate_failed" in err_str
                            or "json object" in err_str
                        )
                        if is_json_format_err and "response_format" in create_kwargs:
                            self._unsupported_json_format_models.add(current_model)
                            logger.warning(
                                f"[GROQ] response_format parameter failed for {current_model}: {call_err}. "
                                "Retrying call immediately without response_format."
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
        max_tokens: int = 1800,
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
