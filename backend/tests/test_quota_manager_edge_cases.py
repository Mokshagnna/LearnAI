"""
Targeted verification tests for quota_manager edge cases:
- Corrupt JSON file recovery.
- Missing JSON file initialization.
- Concurrent thread writes and atomic replacement.
- Interrupted persistence (.tmp cleanup/tolerance).
- Key scoping across accounts and models (no raw credential leakage).
- Record success immunity for genuine TPD.
"""

import os
import json
import time
import threading
from pathlib import Path
import pytest

from services.quota_manager import (
    ProviderQuotaManager,
    RateLimitInfo,
    RateLimitType,
    _hash_key,
    parse_rate_limit,
)


class TestQuotaManagerEdgeCases:
    """Rigorous verification of edge cases and failure modes."""

    def test_missing_json_file_initializes_cleanly(self, tmp_path):
        non_existent = str(tmp_path / "does_not_exist" / "cooldowns.json")
        mgr = ProviderQuotaManager(state_file_path=non_existent)
        avail, rem = mgr.is_model_available("groq", "llama-3.1-8b-instant", "k1")
        assert avail is True
        assert rem == 0.0

    def test_corrupt_json_file_handles_gracefully(self, tmp_path):
        corrupt_file = tmp_path / "corrupt_cooldowns.json"
        corrupt_file.write_text("{{INVALID_JSON_CORRUPT_BYTES", encoding="utf-8")

        mgr = ProviderQuotaManager(state_file_path=str(corrupt_file))
        # Must not crash, should start with empty state
        avail, rem = mgr.is_model_available("groq", "llama-3.1-8b-instant", "k1")
        assert avail is True
        assert rem == 0.0

    def test_interrupted_persistence_tolerance(self, tmp_path):
        state_file = tmp_path / "cooldowns.json"
        tmp_file = tmp_path / "cooldowns.json.tmp"
        # Simulate leftover .tmp from aborted previous process
        tmp_file.write_text('{"partial": "abort"}', encoding="utf-8")
        state_file.write_text('{}', encoding="utf-8")

        mgr = ProviderQuotaManager(state_file_path=str(state_file))
        # Record new cooldown
        info = RateLimitInfo(RateLimitType.TPM, retry_after_seconds=10.0)
        mgr.record_rate_limit("groq", "llama-3.1-8b-instant", "k1", info, sync=True)

        assert state_file.exists()
        # Ensure state file contains valid JSON with the new cooldown
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert any("llama-3.1-8b-instant" in k for k in data.keys())

    def test_concurrent_writes_do_not_corrupt_file(self, tmp_path):
        state_file = str(tmp_path / "concurrent_cooldowns.json")
        mgr = ProviderQuotaManager(state_file_path=state_file)

        errors = []

        def worker(idx):
            try:
                for i in range(20):
                    info = RateLimitInfo(
                        RateLimitType.TPM,
                        retry_after_seconds=float(idx * 10 + i + 1)
                    )
                    mgr.record_rate_limit("groq", f"model_{idx}_{i}", f"key_{idx}", info, sync=True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent write errors: {errors}"
        # Validate that the resulting file is valid JSON
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_key_scoping_and_no_secret_leak(self, tmp_path):
        secret_key_1 = "gsk_live_secret_key_abcdef1234567890"
        secret_key_2 = "gsk_live_secret_key_9999999999999999"

        h1 = _hash_key(secret_key_1)
        h2 = _hash_key(secret_key_2)

        # 1. Non-reversible 12-char SHA256 hex digest
        assert len(h1) == 12
        assert len(h2) == 12
        assert h1 != h2
        assert secret_key_1 not in h1

        state_file = str(tmp_path / "cooldowns_scope.json")
        mgr = ProviderQuotaManager(state_file_path=state_file)

        # Lock key 1
        info = RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
        mgr.record_rate_limit("groq", "llama-3.1-8b-instant", secret_key_1, info, sync=True)

        # Key 1 on llama-3.1-8b is unavailable
        avail1, _ = mgr.is_model_available("groq", "llama-3.1-8b-instant", secret_key_1)
        assert avail1 is False

        # Key 2 on llama-3.1-8b is STILL available (independent identity)
        avail2, _ = mgr.is_model_available("groq", "llama-3.1-8b-instant", secret_key_2)
        assert avail2 is True

        # Key 1 on a DIFFERENT model is STILL available (model scoping)
        avail_diff_model, _ = mgr.is_model_available("groq", "llama-3.3-70b-versatile", secret_key_1)
        assert avail_diff_model is True

        # Ensure raw secret is not in the JSON file
        content = Path(state_file).read_text(encoding="utf-8")
        assert secret_key_1 not in content
        assert secret_key_2 not in content

    def test_record_success_cannot_clear_genuine_tpd(self, tmp_path):
        state_file = str(tmp_path / "cooldowns_tpd_lock.json")
        mgr = ProviderQuotaManager(state_file_path=state_file)

        # Put model on genuine daily TPD
        tpd_info = RateLimitInfo(
            limit_type=RateLimitType.TPD,
            retry_after_seconds=1800.0,
            is_daily_exhaustion=True
        )
        mgr.record_rate_limit("groq", "llama-3.3-70b-versatile", "k_daily", tpd_info, sync=True)

        avail, remaining = mgr.is_model_available("groq", "llama-3.3-70b-versatile", "k_daily")
        assert avail is False

        # Call record_success -> must NOT clear TPD
        mgr.record_success("groq", "llama-3.3-70b-versatile", "k_daily")

        avail_after, remaining_after = mgr.is_model_available("groq", "llama-3.3-70b-versatile", "k_daily")
        assert avail_after is False
        assert remaining_after > 1700.0

    def test_record_success_clears_unknown_429(self, tmp_path):
        state_file = str(tmp_path / "cooldowns_unk_clear.json")
        mgr = ProviderQuotaManager(state_file_path=state_file)

        # Put model on transient UNKNOWN_429
        unk_info = RateLimitInfo(
            limit_type=RateLimitType.UNKNOWN_429,
            retry_after_seconds=5.0,
            is_daily_exhaustion=False
        )
        mgr.record_rate_limit("groq", "llama-3.1-8b-instant", "k_transient", unk_info, sync=True)

        avail, _ = mgr.is_model_available("groq", "llama-3.1-8b-instant", "k_transient")
        assert avail is False

        # Call record_success -> MUST clear transient UNKNOWN_429 immediately
        mgr.record_success("groq", "llama-3.1-8b-instant", "k_transient")

        avail_after, remaining_after = mgr.is_model_available("groq", "llama-3.1-8b-instant", "k_transient")
        assert avail_after is True
        assert remaining_after == 0.0
