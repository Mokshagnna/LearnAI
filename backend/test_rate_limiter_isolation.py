"""
test_rate_limiter_isolation.py
==============================
Regression tests verifying test-isolation of in-memory rate limiters:
1. Direct reset functionality clears tracking dictionaries.
2. An earlier test that exhausts the client rate limit does not poison subsequent tests.
3. Actual rate limit enforcement remains active within a single test execution.
"""
import io
import pytest
from starlette.testclient import TestClient

from main import app
from services.rate_limiter import (
    quiz_gen_limiter,
    quiz_submit_limiter,
    reset_all_rate_limiters,
    SlidingWindowRateLimiter
)


def test_reset_all_rate_limiters_clears_global_state():
    """Verify reset_all_rate_limiters purges client timestamps from both limiters."""
    # Seed requests into both limiters
    allowed_gen, _, _ = quiz_gen_limiter.check_rate_limit("client_alpha")
    allowed_sub, _, _ = quiz_submit_limiter.check_rate_limit("client_alpha")
    assert allowed_gen is True
    assert allowed_sub is True

    with quiz_gen_limiter._lock:
        assert "client_alpha" in quiz_gen_limiter._requests
    with quiz_submit_limiter._lock:
        assert "client_alpha" in quiz_submit_limiter._requests

    # Reset
    reset_all_rate_limiters()

    with quiz_gen_limiter._lock:
        assert len(quiz_gen_limiter._requests) == 0
    with quiz_submit_limiter._lock:
        assert len(quiz_submit_limiter._requests) == 0


class TestRateLimiterTestIsolationSequence:
    """
    Demonstrates that two consecutive test cases run with full isolation:
    Step 1 exhausts the client's rate limit.
    Step 2 immediately receives a full fresh quota without inheriting 429.
    """

    def test_step_1_exhausts_quiz_gen_limit_to_trigger_429(self):
        client = TestClient(app)
        # Default quiz_gen_limiter limit is 10 requests per minute
        for i in range(10):
            allowed, _, _ = quiz_gen_limiter.check_rate_limit("testclient")
            assert allowed is True, f"Request {i+1} should be allowed"

        # The 11th request must be rejected with allowed=False
        allowed, retry_after, remaining = quiz_gen_limiter.check_rate_limit("testclient")
        assert allowed is False
        assert retry_after > 0
        assert remaining == 0

    def test_step_2_subsequent_test_starts_with_clean_quota(self):
        # Even though step_1 completely exhausted 'testclient' quota, autouse fixture
        # guarantees this test starts with an empty sliding window.
        client = TestClient(app)
        allowed, retry_after, remaining = quiz_gen_limiter.check_rate_limit("testclient")
        assert allowed is True, "Subsequent test must not inherit exhausted quota from previous test"
        assert remaining == 9


def test_rate_limit_enforcement_remains_active_within_single_test():
    """Verifies that autouse isolation does NOT disable rate limiting during a test run."""
    limiter = SlidingWindowRateLimiter(default_limit=3, window_seconds=60, name="test_limiter")

    assert limiter.check_rate_limit("10.0.0.1")[0] is True
    assert limiter.check_rate_limit("10.0.0.1")[0] is True
    assert limiter.check_rate_limit("10.0.0.1")[0] is True

    # 4th request exceeds limit
    allowed, retry_after, remaining = limiter.check_rate_limit("10.0.0.1")
    assert allowed is False
    assert retry_after > 0
    assert remaining == 0
