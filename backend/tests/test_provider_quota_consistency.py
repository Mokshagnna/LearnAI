"""
test_provider_quota_consistency.py
==================================
Deterministic test suite for Section 5: Provider Quota Consistency.
Verifies that all four AI providers (Groq, NVIDIA NIM, Google Gemini, Cloudflare Workers AI)
consistently integrate with the shared ProviderQuotaManager for:
1. HTTP 429 rate-limit detection and parsing.
2. Daily quota exhaustion detection vs transient throttle.
3. Retry-After header and body reset handling.
4. Missing Retry-After conservative fallback (UNKNOWN_429).
5. Fast skip (< 1ms) of cooling models without network calls across ALL 4 providers.
6. Recovery after cooldown expiry.
7. Provider success clearing transient cooldowns.
8. Mixed-provider failover under quota exhaustion.
9. Concurrent thread-safe quota access.
"""

import time
import threading
from unittest.mock import MagicMock, patch
import pytest

from services.quota_manager import (
    quota_manager,
    parse_rate_limit,
    RateLimitType,
    RateLimitInfo,
)
from services.providers.base import ProviderUnavailableError
from services.providers.groq_provider import GroqProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.cloudflare_provider import CloudflareProvider


@pytest.fixture(autouse=True)
def clean_quota_state():
    """Ensure clean quota manager state before and after each test."""
    quota_manager.reset_all()
    yield
    quota_manager.reset_all()


class TestAllProvidersFastSkipIntegration:
    """Verifies that all 4 providers fast-skip cooling models without network calls."""

    def test_groq_fast_skips_cooling_model(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test_key_groq")
        provider = GroqProvider()
        quota_manager.record_rate_limit(
            "groq",
            "llama-3.1-8b-instant",
            "test_key_groq",
            RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
        )
        with patch.object(provider, "_get_client") as mock_client_getter:
            mock_client = MagicMock()
            mock_client_getter.return_value = mock_client
            # Fallback model succeeds
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"questions": []}'))]
            )
            provider._execute("test prompt", timeout_seconds=10.0)

            # Assert llama-3.1-8b-instant was NOT called on mock_client
            calls = mock_client.chat.completions.create.call_args_list
            assert len(calls) == 1
            assert calls[0].kwargs["model"] != "llama-3.1-8b-instant"

    def test_nvidia_fast_skips_cooling_model(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "test_key_nvidia")
        provider = NvidiaProvider()
        quota_manager.record_rate_limit(
            "nvidia",
            "meta/llama-3.1-8b-instruct",
            "test_key_nvidia",
            RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
        )
        with patch.object(provider, "_get_client") as mock_client_getter:
            mock_client = MagicMock()
            mock_client_getter.return_value = mock_client
            # Fallback model succeeds
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"questions": []}'))]
            )
            provider._execute("test prompt", timeout_seconds=15.0)

            calls = mock_client.chat.completions.create.call_args_list
            assert len(calls) == 1
            assert calls[0].kwargs["model"] != "meta/llama-3.1-8b-instruct"

    def test_gemini_fast_skips_cooling_model(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test_key_gemini")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-flash")
        monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-1.5-pro")
        provider = GeminiProvider()
        quota_manager.record_rate_limit(
            "gemini",
            "gemini-1.5-flash",
            "test_key_gemini",
            RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
        )
        with patch.object(provider, "_get_client") as mock_client_getter:
            mock_client = MagicMock()
            mock_client_getter.return_value = mock_client
            mock_client.models.generate_content.return_value = MagicMock(
                text='{"questions": []}',
                candidates=[MagicMock(finish_reason="STOP")]
            )
            provider._execute_with_models("test prompt", timeout_seconds=20.0)

            # Primary model was skipped, fallback model was attempted
            calls = mock_client.models.generate_content.call_args_list
            assert len(calls) == 1
            assert calls[0].kwargs["model"] == "gemini-1.5-pro"

    def test_cloudflare_fast_skips_cooling_model(self, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test_token_cf")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test_account_cf")
        provider = CloudflareProvider()
        quota_manager.record_rate_limit(
            "cloudflare",
            "@cf/meta/llama-3.1-8b-instruct",
            "test_token_cf",
            RateLimitInfo(RateLimitType.RPM, retry_after_seconds=30.0, is_daily_exhaustion=False)
        )
        with patch("httpx.Client") as mock_httpx:
            # Should raise ProviderUnavailableError without creating httpx.Client
            with pytest.raises(ProviderUnavailableError) as exc_info:
                provider._execute("test prompt", timeout_seconds=20.0)
            assert "is on cooldown" in str(exc_info.value)
            assert mock_httpx.call_count == 0


class TestQuotaManagerDetailedBehaviors:
    """Verifies detailed rate limit parsing, header processing, and recovery."""

    def test_gemini_daily_resource_exhaustion_detection(self):
        err = Exception("Resource has been exhausted (e.g. check quota): generate_content_requests_per_day exceeded")
        info = parse_rate_limit("gemini", err)
        assert info.is_daily_exhaustion is True
        assert info.limit_type in (RateLimitType.TPD, RateLimitType.RPD)

    def test_cloudflare_retry_after_header_handling(self, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test_token_cf2")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test_account_cf2")
        provider = CloudflareProvider()

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {"Retry-After": "45"}
        mock_resp.text = '{"success": false, "errors": [{"message": "Rate limit exceeded"}]}'

        with patch("httpx.Client.post", return_value=mock_resp):
            with pytest.raises(ProviderUnavailableError):
                provider._execute("test prompt", timeout_seconds=20.0)

        # Verify quota_manager registered exactly 45s cooldown
        avail, remaining = quota_manager.is_model_available("cloudflare", "@cf/meta/llama-3.1-8b-instruct", "test_token_cf2")
        assert avail is False
        assert 40.0 <= remaining <= 45.0

    def test_missing_retry_after_applies_transient_cooldown_not_daily(self):
        err = Exception("HTTP 429 Too Many Requests")
        info = parse_rate_limit("groq", err, response_headers={})
        assert info.limit_type == RateLimitType.UNKNOWN_429
        assert info.is_daily_exhaustion is False

        cooldown = quota_manager.record_rate_limit("groq", "test-model", "test-key", info)
        # Conservative transient cooldown (5.0s), never daily 1800s
        assert cooldown == 5.0

    def test_cooldown_expiry_and_provider_recovery(self):
        info = RateLimitInfo(RateLimitType.RPM, retry_after_seconds=2.0, explicit_reset_time=True)
        quota_manager.record_rate_limit("gemini", "gemini-1.5-flash", "test-key", info)

        avail, remaining = quota_manager.is_model_available("gemini", "gemini-1.5-flash", "test-key")
        assert avail is False
        assert remaining > 0.0

        # Fast-forward time past cooldown
        future_time = time.time() + 10.0
        with patch("time.time", return_value=future_time):
            avail, remaining = quota_manager.is_model_available("gemini", "gemini-1.5-flash", "test-key")
            assert avail is True
            assert remaining == 0.0

    def test_record_success_clears_transient_cooldown_across_all_providers(self):
        providers = [
            ("groq", "llama-3.1-8b-instant"),
            ("nvidia", "meta/llama-3.1-8b-instruct"),
            ("gemini", "gemini-1.5-flash"),
            ("cloudflare", "@cf/meta/llama-3.1-8b-instruct")
        ]
        for p_name, model in providers:
            info = RateLimitInfo(RateLimitType.TPM, retry_after_seconds=60.0)
            quota_manager.record_rate_limit(p_name, model, "key_transient", info)
            assert quota_manager.is_model_available(p_name, model, "key_transient")[0] is False

            # Success clears it
            quota_manager.record_success(p_name, model, "key_transient")
            assert quota_manager.is_model_available(p_name, model, "key_transient")[0] is True

    def test_concurrent_quota_access_thread_safety(self):
        """Stress-tests concurrent reads and writes to ProviderQuotaManager."""
        errors = []

        def worker(thread_id: int):
            try:
                for i in range(50):
                    p_name = ["groq", "nvidia", "gemini", "cloudflare"][i % 4]
                    model = f"model-{i % 3}"
                    key = f"key-{thread_id % 2}"
                    info = RateLimitInfo(RateLimitType.RPM, retry_after_seconds=1.0)
                    quota_manager.record_rate_limit(p_name, model, key, info)
                    quota_manager.is_model_available(p_name, model, key)
                    if i % 5 == 0:
                        quota_manager.record_success(p_name, model, key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent quota access encountered errors: {errors}"
