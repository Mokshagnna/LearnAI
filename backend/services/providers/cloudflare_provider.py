"""
Cloudflare Workers AI provider for LearnAI quiz generation.

Calls the Cloudflare Workers AI REST API:

    POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL_ID}
    Authorization: Bearer {API_TOKEN}
    Content-Type: application/json
    Body: { "prompt": "..." }

Successful response:
    { "result": { "response": "<generated text>" }, "success": true, "errors": [], "messages": [] }

Official documentation:
    https://developers.cloudflare.com/workers-ai/get-started/rest-api/
    https://developers.cloudflare.com/workers-ai/models/

Credentials (read exclusively from environment variables; never hardcoded, never logged):
    CLOUDFLARE_ACCOUNT_ID      - Cloudflare account ID
    CLOUDFLARE_API_TOKEN       - Workers AI API token (needs Workers AI - Read + Edit)
    CLOUDFLARE_MODEL           - model identifier (default: @cf/meta/llama-3.1-8b-instruct)
    CLOUDFLARE_TIMEOUT_SECONDS - per-request timeout budget (default: 20.0)
    CLOUDFLARE_ENABLED         - "false" disables this provider cleanly (default: "true")
    CLOUDFLARE_MAX_RETRIES     - placeholder for future retry policy (default: 0)

This provider is an ADDITION to, not a replacement for, Groq / NVIDIA / Gemini.
When credentials are missing or CLOUDFLARE_ENABLED=false, is_configured() returns False
and the orchestrator skips it; application startup is unaffected.
"""
import logging
import os
from typing import Optional

import httpx

from services.providers.base import (
    BaseAIProvider,
    ProviderConfigError,
    ProviderError,
    ProviderUnavailableError,
    get_analysis_prompt,
    get_quiz_prompt,
)
from services.logger_utils import sanitize_error_text

logger = logging.getLogger("learnai.providers.cloudflare")

# Officially documented Workers AI text-generation (instruction-tuned) model.
# Verified against the Cloudflare Workers AI models catalog.
DEFAULT_CLOUDFLARE_MODEL = "@cf/meta/llama-3.1-8b-instruct"

# Never issue a Cloudflare request unless at least this many seconds remain in the
# overall quiz-generation deadline, so a failing provider cannot starve the chain.
_MIN_CLOUDFLARE_REQUEST_SECONDS = 3.0


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def cloudflare_enabled() -> bool:
    """Return False when CLOUDFLARE_ENABLED is explicitly disabled; True otherwise."""
    return _env("CLOUDFLARE_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")


class CloudflareProvider(BaseAIProvider):
    """Cloudflare Workers AI text-generation provider."""

    def __init__(self):
        self._name = "cloudflare"

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        """Cloudflare is usable only when explicitly enabled and both credentials exist."""
        if not cloudflare_enabled():
            return False
        return bool(_env("CLOUDFLARE_ACCOUNT_ID") and _env("CLOUDFLARE_API_TOKEN"))

    # ── configuration helpers ───────────────────────────────────────────

    def _model(self) -> str:
        return _env("CLOUDFLARE_MODEL", DEFAULT_CLOUDFLARE_MODEL).strip()

    def _account_id(self) -> str:
        return _env("CLOUDFLARE_ACCOUNT_ID", "").strip()

    def _api_token(self) -> str:
        return _env("CLOUDFLARE_API_TOKEN", "").strip()

    def _endpoint(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self._account_id()}/ai/run/{self._model()}"
        )

    def _headers(self) -> dict:
        # The Authorization header contains a secret; it is never logged.
        return {
            "Authorization": f"Bearer {self._api_token()}",
            "Content-Type": "application/json",
        }

    # ── request execution ───────────────────────────────────────────────

    def _execute(self, prompt: str, timeout_seconds: float) -> str:
        if not self.is_configured():
            raise ProviderConfigError(
                "Cloudflare Workers AI is not configured. Set CLOUDFLARE_ACCOUNT_ID "
                "and CLOUDFLARE_API_TOKEN (or set CLOUDFLARE_ENABLED=false).",
                category="MISSING_CREDENTIALS",
            )

        # Guard: never start a request the remaining deadline cannot honor.
        if timeout_seconds < _MIN_CLOUDFLARE_REQUEST_SECONDS:
            raise ProviderUnavailableError(
                f"Cloudflare timeout budget {timeout_seconds:.1f}s is below minimum "
                f"{_MIN_CLOUDFLARE_REQUEST_SECONDS:.1f}s.",
                category="TIMEOUT",
            )

        payload = {
            "prompt": prompt,
            # Cloudflare text-generation models accept max_tokens; pass it through.
            "max_tokens": 1800,
        }

        request_timeout = httpx.Timeout(
            timeout=timeout_seconds,
            connect=min(timeout_seconds, 5.0),
            read=timeout_seconds,
        )

        try:
            with httpx.Client(timeout=request_timeout) as client:
                response = client.post(
                    self._endpoint(),
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                f"Cloudflare request timed out: {sanitize_error_text(exc)}",
                category="TIMEOUT",
            ) from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Cloudflare connection error: {sanitize_error_text(exc)}",
                category="NETWORK_ERROR",
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderUnavailableError(
                f"Cloudflare network error: {sanitize_error_text(exc)}",
                category="NETWORK_ERROR",
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Cloudflare request failed: {sanitize_error_text(exc)}",
                category="NETWORK_ERROR",
            ) from exc

        return self._interpret_response(response)

    def _interpret_response(self, response: httpx.Response) -> str:
        status = response.status_code
        body_text = response.text or ""

        parsed = None
        try:
            parsed = response.json()
        except ValueError:
            parsed = None

        errors = parsed.get("errors") if isinstance(parsed, dict) else None
        if not isinstance(errors, list):
            errors = []

        def first_error_message() -> str:
            if errors and isinstance(errors[0], dict):
                return str(errors[0].get("message", errors[0]))
            if errors:
                return str(errors[0])
            return ""

        # ---- HTTP status categorization ----
        if status == 401:
            raise ProviderConfigError(
                "Cloudflare API token is invalid or unauthorized.",
                category="AUTH_CONFIG_ERROR",
            )
        if status == 403:
            raise ProviderConfigError(
                "Cloudflare API token lacks Workers AI inference permissions.",
                category="AUTH_CONFIG_ERROR",
            )
        if status == 404:
            raise ProviderConfigError(
                f"Cloudflare model '{self._model()}' not found or unsupported "
                "(or invalid Account ID).",
                category="MODEL_NOT_FOUND_404",
            )
        if status == 429:
            raise ProviderUnavailableError(
                "Cloudflare Workers AI rate limit reached.",
                category="RATE_LIMIT_429",
            )
        if status == 400:
            msg = first_error_message() or body_text
            raise ProviderError(
                f"Cloudflare malformed request: {sanitize_error_text(msg)}",
                category="MALFORMED_REQUEST_400",
            )
        if 500 <= status < 600:
            raise ProviderUnavailableError(
                f"Cloudflare server error (HTTP {status}).",
                category=f"SERVER_ERROR_{status}",
            )
        if status != 200:
            raise ProviderUnavailableError(
                f"Cloudflare unexpected HTTP status {status}.",
                category="UNKNOWN_ERROR",
            )

        # ---- HTTP 200: inspect the Workers AI response envelope ----
        if status == 200 and parsed is None:
            # Non-JSON body despite HTTP 200.
            raise ProviderError(
                "Cloudflare returned a non-JSON response.",
                category="MALFORMED_JSON_PARSE_ERROR",
            )

        if not isinstance(parsed, dict) or not parsed.get("success", False):
            msg = first_error_message() or body_text or (
                "Cloudflare API returned an unsuccessful response."
            )
            msg_lower = msg.lower()
            if "rate limit" in msg_lower or "quota" in msg_lower or "429" in body_text:
                raise ProviderUnavailableError(
                    "Cloudflare Workers AI rate limit or quota exceeded.",
                    category="RATE_LIMIT_429",
                )
            if "unauthorized" in msg_lower or "401" in msg_lower:
                raise ProviderConfigError(
                    "Cloudflare API token is invalid or unauthorized.",
                    category="AUTH_CONFIG_ERROR",
                )
            if "not found" in msg_lower or "404" in msg_lower:
                raise ProviderConfigError(
                    f"Cloudflare model '{self._model()}' not found or unsupported.",
                    category="MODEL_NOT_FOUND_404",
                )
            raise ProviderUnavailableError(
                f"Cloudflare API error: {sanitize_error_text(msg)}",
                category="UNKNOWN_ERROR",
            )

        result = parsed.get("result")
        if not isinstance(result, dict):
            raise ProviderError(
                "Cloudflare response missing 'result' object.",
                category="MALFORMED_JSON_PARSE_ERROR",
            )

        response_text = result.get("response")
        if not response_text or not isinstance(response_text, str) or not response_text.strip():
            raise ProviderError(
                "Cloudflare returned an empty response.",
                category="EMPTY_RESPONSE",
            )

        logger.info(
            "[CLOUDFLARE SUCCESS] model=%s status=200 response_len=%d",
            self._model(),
            len(response_text),
        )
        return response_text.strip()

    # ── BaseAIProvider interface ────────────────────────────────────────

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
            prompt = get_quiz_prompt(
                course_code=course_code,
                course_name=course_name,
                topic_name=topic_name,
                syllabus_context=syllabus_context,
            )
        return self._execute(prompt, timeout_seconds)

    def generate_analysis_raw(
        self,
        subject: str,
        topic: str,
        weak_concepts: list,
        score: int,
        total: int,
        timeout_seconds: float,
    ) -> str:
        prompt = get_analysis_prompt(subject, topic, weak_concepts, score, total)
        return self._execute(prompt, timeout_seconds)
