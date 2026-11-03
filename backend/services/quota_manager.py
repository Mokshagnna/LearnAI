"""
LearnAI - Intelligent Provider Quota & Rate-Limit Manager
Provides dual-tier (in-memory + persistent) quota, rate-limit, and cooldown management
across AI providers (Groq, NVIDIA, Gemini, Cloudflare).

Differentiates:
  - TPM (Tokens Per Minute) / RPM (Requests Per Minute): transient cooldowns (seconds).
  - TPD (Tokens Per Day) / RPD (Requests Per Day): daily exhaustion cooldowns.
  - Concurrency Limits: short throttle.
  - Unknown 429s: conservative transient cooldown (NEVER daily lockout).
"""

import os
import re
import time
import json
import hashlib
import logging
import threading
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("learnai.quota_manager")


class RateLimitType(str, Enum):
    TPM = "TPM"                      # Tokens Per Minute
    RPM = "RPM"                      # Requests Per Minute
    TPD = "TPD"                      # Tokens Per Day (Daily Quota)
    RPD = "RPD"                      # Requests Per Day (Daily Quota)
    CONCURRENCY = "CONCURRENCY"      # Concurrency limit exceeded
    UNKNOWN_429 = "UNKNOWN_429"      # Unknown 429 (Transient only, never daily)


class RateLimitInfo:
    """Parsed rate limit details from a provider exception or response."""

    def __init__(
        self,
        limit_type: RateLimitType,
        retry_after_seconds: Optional[float] = None,
        is_daily_exhaustion: bool = False,
        explicit_reset_time: bool = False,
        raw_message: str = ""
    ):
        self.limit_type = limit_type
        self.retry_after_seconds = retry_after_seconds
        self.is_daily_exhaustion = is_daily_exhaustion
        self.explicit_reset_time = explicit_reset_time
        self.raw_message = raw_message

    def __repr__(self) -> str:
        return (
            f"RateLimitInfo(type={self.limit_type.value}, retry_after={self.retry_after_seconds}s, "
            f"daily={self.is_daily_exhaustion}, explicit={self.explicit_reset_time})"
        )


def _hash_key(api_key: Optional[str]) -> str:
    """Returns a short, non-sensitive hash of the API key for tracking without logging secrets."""
    if not api_key:
        return "default"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def parse_rate_limit(
    provider_name: str,
    error: Exception,
    response_headers: Optional[Dict[str, Any]] = None
) -> RateLimitInfo:
    """
    Parses provider-specific rate limit errors, HTTP headers, and message bodies.
    Never assumes every 429 is TPM or TPD. Unknown 429 errors are strictly classified
    as UNKNOWN_429 to avoid unjustified daily lockouts.
    """
    msg = str(error).lower()
    raw_msg_snippet = str(error)[:300]
    headers = response_headers or {}
    if not headers and hasattr(error, "response"):
        resp = getattr(error, "response", None)
        if resp is not None and hasattr(resp, "headers"):
            headers = dict(resp.headers)

    # 1. Parse Retry-After / Reset headers if available
    retry_after_header_sec: Optional[float] = None
    explicit_reset = False

    # Standard Retry-After
    for h_name in ["retry-after", "Retry-After"]:
        if h_name in headers:
            try:
                retry_after_header_sec = float(headers[h_name])
                explicit_reset = True
                break
            except (ValueError, TypeError):
                pass

    # Provider-specific headers
    if retry_after_header_sec is None:
        for h_reset in [
            "x-ratelimit-reset-tokens",
            "x-ratelimit-reset-requests",
            "retry-after-ms"
        ]:
            if h_reset in headers:
                try:
                    val = float(headers[h_reset])
                    if "ms" in h_reset:
                        retry_after_header_sec = max(0.5, val / 1000.0)
                    else:
                        retry_after_header_sec = max(0.5, val)
                    explicit_reset = True
                    break
                except (ValueError, TypeError):
                    pass

    # 2. Parse inline duration patterns from error message text (e.g. "try again in 14m32s", "try again in 1.2s")
    inline_seconds: Optional[float] = None
    # Pattern: "try again in 1h2m3s" or "try again in 14m32.5s" or "try again in 2.5s"
    m_hms = re.search(r'try again in\s+(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+(?:\.\d+)?)\s*s)?', msg)
    if m_hms and any(m_hms.groups()):
        h = float(m_hms.group(1) or 0)
        m = float(m_hms.group(2) or 0)
        s = float(m_hms.group(3) or 0)
        total_sec = h * 3600.0 + m * 60.0 + s
        if total_sec > 0:
            inline_seconds = total_sec
            explicit_reset = True

    # Pattern: "retry after (\d+) seconds"
    if inline_seconds is None:
        m_sec = re.search(r'retry\s+(?:after|in)\s+(\d+(?:\.\d+)?)\s*(?:seconds|sec|s\b)', msg)
        if m_sec:
            inline_seconds = float(m_sec.group(1))
            explicit_reset = True

    parsed_retry_after = retry_after_header_sec or inline_seconds

    # 3. Classify RateLimitType based on explicit provider keywords
    is_daily = False

    # Groq / OpenAI style daily tokens or requests
    is_groq_tpd = any(t in msg for t in [
        "tokens per day",
        "(tpd)",
        "token per day",
        "requests per day",
        "(rpd)",
        "daily limit",
        "daily quota",
        "quota exceeded for the day"
    ])

    # Gemini style daily resource exhaustion
    is_gemini_daily = (
        "generate_content_requests_per_day" in msg
        or "requests_per_day" in msg
        or ("resource_exhausted" in msg and "per day" in msg)
    )

    if is_groq_tpd or is_gemini_daily:
        is_daily = True
        limit_type = RateLimitType.TPD if ("token" in msg or "tpd" in msg) else RateLimitType.RPD
    elif "tokens per minute" in msg or "(tpm)" in msg or "token per minute" in msg or bool(re.search(r'\btpm\b', msg)):
        limit_type = RateLimitType.TPM
    elif "requests per minute" in msg or "(rpm)" in msg or "request per minute" in msg or bool(re.search(r'\brpm\b', msg)):
        limit_type = RateLimitType.RPM
    elif "concurrent" in msg or "concurrency" in msg:
        limit_type = RateLimitType.CONCURRENCY
    else:
        # Crucial safeguard: unknown rate limit errors MUST NOT trigger daily lockout
        limit_type = RateLimitType.UNKNOWN_429

    return RateLimitInfo(
        limit_type=limit_type,
        retry_after_seconds=parsed_retry_after,
        is_daily_exhaustion=is_daily,
        explicit_reset_time=explicit_reset,
        raw_message=raw_msg_snippet
    )


class ProviderQuotaManager:
    """
    Thread-safe dual-tier quota & cooldown manager.
    Keeps state in memory for zero-overhead hot-path checks, and optionally persists
    to disk (`backend/data/provider_cooldowns.json`) to survive worker recycles.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, state_file_path: Optional[str] = None, *args, **kwargs):
        if state_file_path is not None:
            obj = super().__new__(cls)
            obj._initialized = False
            return obj
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, state_file_path: Optional[str] = None):
        if state_file_path is None and getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._state_lock = threading.Lock()
        self._file_lock = threading.Lock()
        # Cooldown store: key = (provider_name, model_id, key_hash) -> dict(cooldown_until, reason, is_daily)
        self._cooldowns: Dict[str, Dict[str, Any]] = {}

        if state_file_path:
            self._state_file = Path(state_file_path)
        else:
            backend_dir = Path(__file__).resolve().parent.parent
            self._state_file = backend_dir / "data" / "provider_cooldowns.json"

        self._load_persisted_state()

    def _make_key(self, provider_name: str, model_id: str, key_hash: str) -> str:
        return f"{provider_name.lower().strip()}:{model_id.lower().strip()}:{key_hash.strip()}"

    def _load_persisted_state(self):
        """Loads non-expired cooldowns from persistent disk store on startup."""
        if not self._state_file.is_file():
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            with self._state_lock:
                for k, v in data.items():
                    if isinstance(v, dict) and v.get("cooldown_until", 0) > now:
                        self._cooldowns[k] = v
            active_count = len(self._cooldowns)
            if active_count > 0:
                logger.info(f"[QUOTA] Loaded {active_count} active provider cooldowns from {self._state_file.name}")
        except Exception as e:
            logger.warning(f"[QUOTA] Could not load persisted cooldowns: {e}")

    def _persist_state(self):
        """Flushes non-expired cooldowns to persistent storage safely."""
        with self._file_lock:
            temp_file = None
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                now = time.time()
                with self._state_lock:
                    active = {k: v for k, v in self._cooldowns.items() if v.get("cooldown_until", 0) > now}
                temp_file = self._state_file.with_name(
                    f"{self._state_file.stem}_{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp"
                )
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(active, f, indent=2)
                replaced = False
                for attempt in range(5):
                    try:
                        temp_file.replace(self._state_file)
                        replaced = True
                        break
                    except (PermissionError, OSError):
                        time.sleep(0.01 * (2 ** attempt))
                if not replaced:
                    temp_file.replace(self._state_file)
            except Exception as e:
                logger.warning(f"[QUOTA] Failed to persist cooldown state: {e}")
            finally:
                if temp_file is not None and temp_file.is_file():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass

    def is_model_available(
        self,
        provider_name: str,
        model_id: str,
        api_key: Optional[str] = None
    ) -> Tuple[bool, Optional[float]]:
        """
        Fast non-blocking check whether a model is available.
        Returns:
            (True, None) if model is available.
            (False, remaining_seconds) if model is on cooldown.
        """
        k_hash = _hash_key(api_key)
        key = self._make_key(provider_name, model_id, k_hash)
        now = time.time()

        with self._state_lock:
            entry = self._cooldowns.get(key)
            if not entry:
                return True, 0.0

            cooldown_until = entry.get("cooldown_until", 0)
            if now >= cooldown_until:
                # Expired
                del self._cooldowns[key]
                return True, 0.0

            remaining = cooldown_until - now
            return False, remaining

    def record_rate_limit(
        self,
        provider_name: str,
        model_id: str,
        api_key: Optional[str],
        info: RateLimitInfo,
        **kwargs
    ) -> float:
        """
        Records a rate-limit event and calculates the appropriate cooldown duration.
        - Uses explicit reset time when available.
        - If daily exhaustion without explicit reset: applies configurable conservative cooldown (default 1800s)
          and reports the uncertainty in logs.
        - If transient (TPM / RPM / CONCURRENCY / UNKNOWN_429): applies explicit retry-after or conservative 5s.
        """
        k_hash = _hash_key(api_key)
        key = self._make_key(provider_name, model_id, k_hash)
        now = time.time()

        # Configurable defaults
        default_daily_cooldown = float(os.getenv("DAILY_QUOTA_COOLDOWN_SECONDS", "1800.0"))
        default_transient_cooldown = float(os.getenv("TRANSIENT_RATE_LIMIT_COOLDOWN_SECONDS", "5.0"))

        if info.is_daily_exhaustion:
            if info.explicit_reset_time and info.retry_after_seconds:
                cooldown_sec = info.retry_after_seconds
                logger.warning(
                    f"[QUOTA DAILY EXHAUSTED] provider={provider_name} model={model_id} key={k_hash} "
                    f"type={info.limit_type.value}. Explicit provider reset in {cooldown_sec:.1f}s. Cooldown applied."
                )
            else:
                cooldown_sec = default_daily_cooldown
                logger.warning(
                    f"[QUOTA DAILY EXHAUSTED] provider={provider_name} model={model_id} key={k_hash} "
                    f"type={info.limit_type.value}. No explicit reset time returned by provider. "
                    f"Applying conservative cooldown of {cooldown_sec:.0f}s (configurable via DAILY_QUOTA_COOLDOWN_SECONDS)."
                )
        else:
            # Transient rate limit (TPM, RPM, Concurrency, or Unknown 429)
            if info.explicit_reset_time and info.retry_after_seconds:
                cooldown_sec = max(1.0, info.retry_after_seconds)
            else:
                cooldown_sec = default_transient_cooldown

            if info.limit_type == RateLimitType.UNKNOWN_429:
                logger.info(
                    f"[QUOTA TRANSIENT 429] provider={provider_name} model={model_id} key={k_hash} "
                    f"type=UNKNOWN_429. Applying short transient cooldown of {cooldown_sec:.1f}s (never daily lockout)."
                )
            else:
                logger.info(
                    f"[QUOTA TRANSIENT THROTTLE] provider={provider_name} model={model_id} key={k_hash} "
                    f"type={info.limit_type.value}. Cooldown {cooldown_sec:.1f}s."
                )

        cooldown_until = now + cooldown_sec
        with self._state_lock:
            self._cooldowns[key] = {
                "cooldown_until": cooldown_until,
                "reason": info.limit_type.value,
                "is_daily": info.is_daily_exhaustion,
                "model_id": model_id,
                "provider": provider_name,
                "set_at": now
            }

        # Background flush
        if kwargs.get("sync", False):
            self._persist_state()
        else:
            threading.Thread(target=self._persist_state, daemon=True).start()
        return cooldown_sec

    def record_success(self, provider_name: str, model_id: str, api_key: Optional[str] = None):
        """Clears any non-daily cooldowns upon confirmed successful completion."""
        k_hash = _hash_key(api_key)
        key = self._make_key(provider_name, model_id, k_hash)
        with self._state_lock:
            if key in self._cooldowns and not self._cooldowns[key].get("is_daily", False):
                del self._cooldowns[key]

    def reset_all(self):
        """Clears all cooldowns (useful for test suites)."""
        with self._state_lock:
            self._cooldowns.clear()
        if self._state_file.is_file():
            try:
                self._state_file.unlink()
            except Exception:
                pass


# Global singleton
quota_manager = ProviderQuotaManager()
