"""
Unit tests for ProviderQuotaManager, parse_rate_limit, and calculate_max_output_tokens.

Verifies:
1. Rate limit classification: TPM/RPM vs TPD/RPD vs UNKNOWN_429.
2. Retry-After header parsing (seconds and reset headers).
3. Text regex parsing for retry hints ("try again in 14m32s", "retry after 5s").
4. Unknown 429 safety: never triggers daily lockout, only transient 5s cooldown.
5. Daily exhaustion cooldown calculation: uses explicit reset time or configurable conservative fallback.
6. Dual-tier persistence: survives cross-process/worker reload via provider_cooldowns.json.
7. Model availability fast-skip: skips exhausted models with zero network overhead.
8. Dynamic token budgeting: calculates safe output token budgets for 10, 11, 12 questions within model ceilings.
"""

import os
import time
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.quota_manager import (
    ProviderQuotaManager,
    RateLimitType,
    RateLimitInfo,
    parse_rate_limit,
    quota_manager
)
from services.providers.base import calculate_max_output_tokens
from services.providers.groq_provider import GroqProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.providers.base import ProviderUnavailableError


class TestRateLimitParsing:
    """Tests rate limit header and message extraction."""

    def test_parse_retry_after_dict_headers(self):
        headers = {"retry-after": "7.5"}
        info = parse_rate_limit("groq", Exception("Too Many Requests"), response_headers=headers)
        assert info.retry_after_seconds == 7.5
        assert info.explicit_reset_time is True

    def test_parse_ratelimit_reset_tokens_header(self):
        headers = {"x-ratelimit-reset-tokens": "12.0"}
        info = parse_rate_limit("groq", Exception("Too Many Requests"), response_headers=headers)
        assert info.retry_after_seconds == 12.0
        assert info.explicit_reset_time is True

    def test_parse_reset_hint_from_text_seconds(self):
        err = Exception("Rate limit reached. Please try again in 5.2s.")
        info = parse_rate_limit("groq", err)
        assert info.retry_after_seconds == 5.2
        assert info.explicit_reset_time is True

    def test_parse_reset_hint_from_text_minutes_seconds(self):
        err = Exception("Daily limit exceeded. Please try again in 14m30s.")
        info = parse_rate_limit("groq", err)
        assert info.retry_after_seconds == 14 * 60 + 30
        assert info.explicit_reset_time is True
        assert info.is_daily_exhaustion is True

    def test_parse_reset_hint_from_text_hours_minutes(self):
        err = Exception("Tokens per day exhausted. Try again in 3h15m.")
        info = parse_rate_limit("groq", err)
        assert info.retry_after_seconds == 3 * 3600 + 15 * 60
        assert info.explicit_reset_time is True
        assert info.limit_type == RateLimitType.TPD

    def test_tpm_rate_limit_classification(self):
        err = Exception("Rate limit reached for model llama-3.1-8b-instant: 6000 TPM limit exceeded. Please try again in 2.1s.")
        info = parse_rate_limit("groq", err)
        assert info.limit_type == RateLimitType.TPM
        assert info.is_daily_exhaustion is False
        assert info.retry_after_seconds == 2.1
        assert info.explicit_reset_time is True

    def test_rpm_rate_limit_classification(self):
        err = Exception("Requests per minute limit exceeded. Try again in 4s.")
        info = parse_rate_limit("groq", err)
        assert info.limit_type == RateLimitType.RPM
        assert info.is_daily_exhaustion is False
        assert info.retry_after_seconds == 4.0

    def test_tpd_daily_limit_with_explicit_reset(self):
        err = Exception("Rate limit reached: tokens per day (TPD) exhausted for model llama-3.3-70b-versatile. Try again in 5h30m.")
        info = parse_rate_limit("groq", err)
        assert info.limit_type == RateLimitType.TPD
        assert info.is_daily_exhaustion is True
        assert info.retry_after_seconds == 5 * 3600 + 30 * 60
        assert info.explicit_reset_time is True

    def test_tpd_daily_limit_without_explicit_reset_applies_conservative_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DAILY_QUOTA_COOLDOWN_SECONDS", "1200")
        err = Exception("Tokens per day (TPD) limit exceeded.")
        info = parse_rate_limit("groq", err)
        assert info.limit_type == RateLimitType.TPD
        assert info.is_daily_exhaustion is True
        assert info.explicit_reset_time is False

        # When recorded, conservative fallback of 1200s is used
        state_file = str(tmp_path / "test_cooldowns_tpd.json")
        mgr = ProviderQuotaManager(state_file_path=state_file)
        mgr.reset_all()
        try:
            cd = mgr.record_rate_limit("groq", "llama-3.3-70b-versatile", "key1", info)
            assert cd == 1200.0
            avail, remaining = mgr.is_model_available("groq", "llama-3.3-70b-versatile", "key1")
            assert avail is False
            assert 1195.0 <= remaining <= 1200.0
        finally:
            mgr.reset_all()

    def test_unknown_429_never_triggers_daily_lockout(self, tmp_path):
        """Unknown 429 with no TPM/TPD keywords must strictly trigger a transient 5s cooldown."""
        err = Exception("429 Too Many Requests: Rate limit exceeded")
        info = parse_rate_limit("groq", err)
        assert info.limit_type == RateLimitType.UNKNOWN_429
        assert info.is_daily_exhaustion is False

        state_file = str(tmp_path / "test_cooldowns_unk.json")
        mgr = ProviderQuotaManager(state_file_path=state_file)
        mgr.reset_all()
        try:
            cd = mgr.record_rate_limit("groq", "llama-3.1-8b-instant", "key_unk", info)
            assert cd == 5.0  # strictly transient
            avail, remaining = mgr.is_model_available("groq", "llama-3.1-8b-instant", "key_unk")
            assert avail is False
            assert remaining <= 5.0
        finally:
            mgr.reset_all()



    def test_parse_rate_limit_with_httpx_response_object(self):
        import httpx
        resp = httpx.Response(429, headers={"retry-after": "7.5"})
        info = parse_rate_limit("groq", Exception("Too Many Requests"), response_headers=resp)
        assert info.retry_after_seconds == 7.5
        assert info.explicit_reset_time is True

    def test_parse_rate_limit_with_error_containing_httpx_response(self):
        import httpx
        err = Exception("Rate limit reached (429)")
        err.response = httpx.Response(429, headers={"Retry-After": "4.2"})
        info = parse_rate_limit("groq", err, getattr(err, "response", None))
        assert info.retry_after_seconds == 4.2
        assert info.explicit_reset_time is True

    def test_parse_rate_limit_case_insensitive_reset_tokens_with_response(self):
        import httpx
        resp = httpx.Response(429, headers={"X-RateLimit-Reset-Tokens": "15.0"})
        info = parse_rate_limit("nvidia", Exception("TPM limit exceeded"), response_headers=resp)
        assert info.retry_after_seconds == 15.0
        assert info.explicit_reset_time is True


class TestProviderQuotaManagerPersistence:
    """Tests dual-tier memory + disk state persistence across instances."""

    def test_persistence_across_manager_instances(self, tmp_path):
        state_file = str(tmp_path / "cooldowns.json")
        mgr1 = ProviderQuotaManager(state_file_path=state_file)
        mgr1.reset_all()

        info = RateLimitInfo(
            limit_type=RateLimitType.TPD,
            retry_after_seconds=3600.0,
            is_daily_exhaustion=True,
            explicit_reset_time=True
        )
        mgr1.record_rate_limit("groq", "llama-3.3-70b-versatile", "key123", info, sync=True)

        # Ensure background save writes to disk
        time.sleep(0.1)
        assert Path(state_file).exists()

        # Instantiate second manager (simulating worker restart or separate worker process)
        mgr2 = ProviderQuotaManager(state_file_path=state_file)
        avail, remaining = mgr2.is_model_available("groq", "llama-3.3-70b-versatile", "key123")
        assert avail is False
        assert 3590.0 <= remaining <= 3600.0

    def test_record_success_clears_transient_cooldown(self, tmp_path):
        state_file = str(tmp_path / "cooldowns_success.json")
        mgr = ProviderQuotaManager(state_file_path=state_file)
        mgr.reset_all()

        # Record a transient UNKNOWN_429
        info = RateLimitInfo(limit_type=RateLimitType.UNKNOWN_429, retry_after_seconds=5.0)
        mgr.record_rate_limit("groq", "llama-3.1-8b-instant", "key_success", info)

        avail, _ = mgr.is_model_available("groq", "llama-3.1-8b-instant", "key_success")
        assert avail is False

        # Record success -> clears transient throttle immediately
        mgr.record_success("groq", "llama-3.1-8b-instant", "key_success")
        avail, rem = mgr.is_model_available("groq", "llama-3.1-8b-instant", "key_success")
        assert avail is True
        assert rem == 0.0


class TestDynamicTokenBudgeting:
    """Tests calculate_max_output_tokens formula and model limits."""

    def test_default_10_questions_token_budget(self):
        tokens = calculate_max_output_tokens("llama-3.1-8b-instant", 10)
        # Formula: 210 * 10 + 300 = 2400
        assert tokens == 2400

    def test_retake_12_questions_token_budget(self):
        tokens = calculate_max_output_tokens("llama-3.1-8b-instant", 12)
        # Formula: 210 * 12 + 300 = 2820
        assert tokens == 2820

    def test_cloudflare_model_ceiling(self):
        tokens = calculate_max_output_tokens("@cf/meta/llama-3.1-8b-instruct", 15)
        # Calculated: 210 * 15 + 300 = 3450, but CF model limit is 3000
        assert tokens == 3000

    def test_gpt_oss_model_ceiling(self):
        tokens = calculate_max_output_tokens("openai/gpt-oss-20b", 20)
        # Calculated: 210 * 20 + 300 = 4500, but limit is 4096
        assert tokens == 4096

    def test_explicit_env_override(self, monkeypatch):
        monkeypatch.setenv("MAX_GEN_TOKENS", "3500")
        tokens = calculate_max_output_tokens("llama-3.1-8b-instant", 10)
        assert tokens == 3500


class TestProviderFastSkipIntegration:
    """Verifies that providers skip exhausted models immediately without issuing network calls."""

    def test_groq_skips_exhausted_model_without_network_call(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test_key_fast_skip")
        provider = GroqProvider()

        # Put llama-3.1-8b-instant on cooldown
        quota_manager.record_rate_limit(
            "groq",
            "llama-3.1-8b-instant",
            "test_key_fast_skip",
            RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
        )

        with patch.object(provider, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            # Setup fallback model (llama-3.3-70b-versatile) to succeed
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content='{"questions": []}'))]
            mock_client.chat.completions.create.return_value = mock_resp

            result = provider._execute("generate quiz", timeout_seconds=10.0)
            assert result == '{"questions": []}'

            # Verify chat.completions.create was called for fallback model, NEVER for llama-3.1-8b-instant
            called_models = [call.kwargs.get("model") for call in mock_client.chat.completions.create.call_args_list]
            assert "llama-3.1-8b-instant" not in called_models
            assert "llama-3.3-70b-versatile" in called_models

    def test_nvidia_skips_exhausted_model_without_network_call(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "nv_key_fast_skip")
        provider = NvidiaProvider()

        # Put meta/llama-3.1-8b-instruct on cooldown
        quota_manager.record_rate_limit(
            "nvidia",
            "meta/llama-3.1-8b-instruct",
            "nv_key_fast_skip",
            RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
        )

        with patch.object(provider, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content='{"questions": []}'))]
            mock_client.chat.completions.create.return_value = mock_resp

            result = provider._execute("generate quiz", timeout_seconds=10.0)
            assert result == '{"questions": []}'

            called_models = [call.kwargs.get("model") for call in mock_client.chat.completions.create.call_args_list]
            assert "meta/llama-3.1-8b-instruct" not in called_models
            assert "meta/llama-3.1-70b-instruct" in called_models
