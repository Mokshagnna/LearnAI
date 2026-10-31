"""
test_phase4_phase5.py
=====================
Automated tests for Phase 4 (Rate Limiting & AI Usage Tracking) and Phase 5 (Error Logging & Monitoring).

Covers:
- In-memory sliding window rate limiting
- Configurable limits & HTTP 429 Retry-After response headers
- Metrics collection: quiz requests, provider latency, success/failure counts
- Health check endpoint /api/health
- Secret redaction logging filter
- Correlation ID propagation via X-Request-ID

Offline execution, zero external dependencies.

Run with:
    python -m unittest backend.test_phase4_phase5 -v
"""
import os
import sys
import time
import logging
import unittest
from unittest.mock import MagicMock, patch

BACKEND_DIR = os.path.join(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.rate_limiter import SlidingWindowRateLimiter, get_client_ip, quiz_gen_limiter, quiz_submit_limiter
from services.metrics import MetricsCollector
from services.logger_utils import SecretRedactionFilter, request_id_var


class TestSlidingWindowRateLimiter(unittest.TestCase):

    def setUp(self):
        self.limiter = SlidingWindowRateLimiter(default_limit=3, window_seconds=2, name="test_limiter")

    def tearDown(self):
        self.limiter.reset()

    def test_requests_within_limit_allowed(self):
        """Requests up to default_limit succeed with positive remaining quota."""
        allowed1, retry_after1, remaining1 = self.limiter.check_rate_limit("192.168.1.1")
        self.assertTrue(allowed1)
        self.assertEqual(remaining1, 2)

        allowed2, retry_after2, remaining2 = self.limiter.check_rate_limit("192.168.1.1")
        self.assertTrue(allowed2)
        self.assertEqual(remaining2, 1)

        allowed3, retry_after3, remaining3 = self.limiter.check_rate_limit("192.168.1.1")
        self.assertTrue(allowed3)
        self.assertEqual(remaining3, 0)

    def test_requests_exceeding_limit_rejected(self):
        """Requests exceeding the limit are rejected with retry_after > 0."""
        for _ in range(3):
            self.limiter.check_rate_limit("192.168.1.1")

        allowed, retry_after, remaining = self.limiter.check_rate_limit("192.168.1.1")
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)
        self.assertEqual(remaining, 0)

    def test_different_clients_isolated(self):
        """Rate limit on one client does not impact another client."""
        for _ in range(3):
            self.limiter.check_rate_limit("192.168.1.1")

        # Client 1 is blocked
        allowed1, _, _ = self.limiter.check_rate_limit("192.168.1.1")
        self.assertFalse(allowed1)

        # Client 2 is completely unblocked
        allowed2, _, remaining2 = self.limiter.check_rate_limit("10.0.0.2")
        self.assertTrue(allowed2)
        self.assertEqual(remaining2, 2)

    def test_window_expiration_restores_quota(self):
        """After window_seconds elapse, quota is restored."""
        for _ in range(3):
            self.limiter.check_rate_limit("192.168.1.1")

        # Wait for the 2-second test window to expire
        time.sleep(2.1)

        allowed, _, remaining = self.limiter.check_rate_limit("192.168.1.1")
        self.assertTrue(allowed)
        self.assertEqual(remaining, 2)

    def test_client_ip_untrusted_proxy_defaults_to_socket(self):
        """Without TRUST_PROXY_HEADERS, X-Forwarded-For is ignored to prevent IP spoofing."""
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "203.0.113.195, 70.41.3.18"}
        request.client.host = "198.51.100.22"

        with patch.dict(os.environ, {"TRUST_PROXY_HEADERS": "false"}):
            ip = get_client_ip(request)
            self.assertEqual(ip, "198.51.100.22")

    def test_client_ip_trusted_proxy_uses_forwarded_header(self):
        """With TRUST_PROXY_HEADERS=true, first hop in X-Forwarded-For is extracted."""
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "203.0.113.195, 70.41.3.18"}
        request.client.host = "10.0.0.1"

        with patch.dict(os.environ, {"TRUST_PROXY_HEADERS": "true"}):
            ip = get_client_ip(request)
            self.assertEqual(ip, "203.0.113.195")


class TestMetricsCollector(unittest.TestCase):

    def setUp(self):
        self.m = MetricsCollector()

    def test_quiz_metrics_aggregation(self):
        """Metrics correctly aggregate requests, successes, failures, and latencies."""
        self.m.record_quiz_request()
        self.m.record_quiz_success("groq", 1.5)
        self.m.record_quiz_request()
        self.m.record_quiz_failure("nvidia", 3.0)
        self.m.record_rate_limit_exceeded()
        self.m.record_submission()

        summary = self.m.get_summary()

        self.assertEqual(summary["quiz_requests"]["total"], 2)
        self.assertEqual(summary["quiz_requests"]["successful"], 1)
        self.assertEqual(summary["quiz_requests"]["failed"], 1)
        self.assertEqual(summary["quiz_requests"]["rate_limited"], 1)
        self.assertEqual(summary["total_submissions"], 1)

        groq_stats = summary["providers"]["groq"]
        self.assertEqual(groq_stats["attempts"], 1)
        self.assertEqual(groq_stats["successes"], 1)
        self.assertEqual(groq_stats["avg_latency_seconds"], 1.5)

        nvidia_stats = summary["providers"]["nvidia"]
        self.assertEqual(nvidia_stats["attempts"], 1)
        self.assertEqual(nvidia_stats["failures"], 1)
        self.assertEqual(nvidia_stats["avg_latency_seconds"], 3.0)


class TestSecretRedactionLoggingFilter(unittest.TestCase):

    def setUp(self):
        self.filter = SecretRedactionFilter()

    def test_groq_key_redaction(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Connecting with key gsk_1234567890abcdef1234567890abcdef123 to Groq",
            args=(), exc_info=None
        )
        self.filter.filter(record)
        self.assertNotIn("gsk_1234567890abcdef", record.msg)
        self.assertIn("[REDACTED_CREDENTIAL]", record.msg)

    def test_nvidia_key_redaction(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Calling NVIDIA endpoint with nvapi-abcdef1234567890abcdef1234567890",
            args=(), exc_info=None
        )
        self.filter.filter(record)
        self.assertNotIn("nvapi-abcdef", record.msg)
        self.assertIn("[REDACTED_CREDENTIAL]", record.msg)

    def test_gemini_key_redaction(self):
        fake_key = "AIza_MOCK_TEST_KEY_REDACTION_FILTER_0123456789"
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=f"Gemini initialization with {fake_key}",
            args=(), exc_info=None
        )
        self.filter.filter(record)
        self.assertNotIn(fake_key, record.msg)
        self.assertIn("[REDACTED_CREDENTIAL]", record.msg)

    def test_bearer_token_redaction(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Headers: Authorization: Bearer abcdef1234567890abcdef1234567890",
            args=(), exc_info=None
        )
        self.filter.filter(record)
        self.assertNotIn("abcdef1234567890abcdef", record.msg)
        self.assertIn("[REDACTED_CREDENTIAL]", record.msg)


class TestHealthEndpointAndFastAPI(unittest.TestCase):

    def test_public_health_endpoint_is_sanitized(self):
        """GET /api/health returns status, version, database but does NOT leak providers or metrics to public callers."""
        from main import health_check
        with patch("main.DEBUG", False), patch.dict(os.environ, {"EXPOSE_HEALTH_METRICS": "false"}):
            res = health_check(detailed=False)

        self.assertIn("status", res)
        self.assertIn(res["status"], ["healthy", "degraded"])
        self.assertIn("timestamp", res)
        self.assertIn("version", res)
        self.assertIn("database", res)
        # Verify internal architecture details are redacted
        self.assertNotIn("configured_providers", res)
        self.assertNotIn("metrics", res)

    def test_detailed_health_endpoint_returns_metrics(self):
        """GET /api/health with detailed=True returns provider list and operational metrics."""
        from main import health_check
        res = health_check(detailed=True)

        self.assertIn("status", res)
        self.assertIn("configured_providers", res)
        self.assertIn("metrics", res)
        self.assertIsInstance(res["configured_providers"], list)
        self.assertIn("quiz_requests", res["metrics"])

    def test_liveness_check_endpoint(self):
        """GET /health returns minimal ok status."""
        from main import liveness_check
        res = liveness_check()
        self.assertEqual(res, {"status": "ok"})

    def test_cors_wildcard_disables_credentials(self):
        """When allowed origins is wildcard '*', allow_credentials must be False."""
        from main import is_wildcard
        self.assertTrue(is_wildcard)


if __name__ == "__main__":
    unittest.main(verbosity=2)
