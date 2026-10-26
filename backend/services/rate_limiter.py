"""
rate_limiter.py
===============
Thread-safe, in-memory sliding window rate limiter for LearnAI endpoints.

Features:
- Configurable window size and max requests via environment variables
- Client identification by IP (supporting X-Forwarded-For when behind proxy)
- Automatic pruning of expired timestamps to keep memory bounded
- Returns remaining quota and Retry-After countdown

Limitations:
- In-memory state is local to the running Python process (single-instance).
  It does not synchronize across distributed multi-container or multi-process deployments.
"""
import os
import time
import threading
from typing import Dict, List, Tuple, Optional
from fastapi import Request, HTTPException


class SlidingWindowRateLimiter:
    def __init__(self, default_limit: int = 10, window_seconds: int = 60, name: str = "rate_limit"):
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self.name = name
        self._lock = threading.Lock()
        # client_id -> list of float timestamps
        self._requests: Dict[str, List[float]] = {}
        self._last_prune = time.time()

    def _get_limit(self, env_var: Optional[str] = None) -> int:
        if env_var:
            val = os.getenv(env_var)
            if val:
                try:
                    return max(1, int(val))
                except ValueError:
                    pass
        return self.default_limit

    def _prune_expired(self, now: float):
        """Periodically prune stale client entries to prevent unbounded memory growth."""
        if now - self._last_prune < 60:
            return
        cutoff = now - self.window_seconds
        keys_to_remove = []
        for client_id, timestamps in self._requests.items():
            self._requests[client_id] = [t for t in timestamps if t > cutoff]
            if not self._requests[client_id]:
                keys_to_remove.append(client_id)
        for k in keys_to_remove:
            self._requests.pop(k, None)
        self._last_prune = now

    def check_rate_limit(self, client_id: str, limit: Optional[int] = None) -> Tuple[bool, int, int]:
        """
        Evaluates whether client_id is allowed to make a request.
        Returns:
            (allowed: bool, retry_after: int, remaining: int)
        """
        now = time.time()
        effective_limit = limit if limit is not None else self.default_limit
        cutoff = now - self.window_seconds

        with self._lock:
            self._prune_expired(now)

            if client_id not in self._requests:
                self._requests[client_id] = []

            # Keep only timestamps within sliding window
            valid_timestamps = [t for t in self._requests[client_id] if t > cutoff]
            self._requests[client_id] = valid_timestamps

            if len(valid_timestamps) >= effective_limit:
                oldest_in_window = valid_timestamps[0]
                retry_after = max(1, int(self.window_seconds - (now - oldest_in_window)))
                return False, retry_after, 0

            self._requests[client_id].append(now)
            remaining = effective_limit - len(self._requests[client_id])
            return True, 0, remaining

    def reset(self):
        """Clears all stored client timestamps (useful for tests)."""
        with self._lock:
            self._requests.clear()


def get_client_ip(request: Request) -> str:
    """
    Extracts client IP.
    Only inspects X-Forwarded-For if TRUST_PROXY_HEADERS is explicitly enabled (e.g. behind nginx/Cloudflare/ALB).
    Otherwise defaults to direct TCP socket IP (request.client.host) to prevent IP spoofing attacks.
    """
    trust_proxy = os.getenv("TRUST_PROXY_HEADERS", "false").lower() in ("true", "1", "yes")
    if trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",")]
            if parts and parts[0]:
                return parts[0]
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


# Global instances configured from environment variables
quiz_gen_limiter = SlidingWindowRateLimiter(
    default_limit=int(os.getenv("RATE_LIMIT_QUIZ_GEN_PER_MIN", "10")),
    window_seconds=60,
    name="quiz_generation"
)

quiz_submit_limiter = SlidingWindowRateLimiter(
    default_limit=int(os.getenv("RATE_LIMIT_QUIZ_SUBMIT_PER_MIN", "30")),
    window_seconds=60,
    name="quiz_submit"
)


def reset_all_rate_limiters() -> None:
    """
    Clears all in-memory rate-limiter state across all global limiter instances.
    Designed for test isolation and fixture teardown without modifying production
    limits or application behavior.
    """
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()
