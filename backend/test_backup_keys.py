"""
test_backup_keys.py
===================
Unit tests for backup API key fallback logic in GroqProvider, NvidiaProvider, and GeminiProvider.

All tests use unittest.mock — no real API calls are made.

Run with:
    python -m pytest backend/test_backup_keys.py -v
or:
    python -m unittest backend.test_backup_keys
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure the backend directory is on the path so provider imports work
BACKEND_DIR = os.path.join(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_groq_response(text: str):
    """Build a minimal mock Groq chat completion response."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_openai_response(text: str):
    """Build a minimal mock OpenAI chat completion response (NVIDIA uses OpenAI client)."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_gemini_response(text: str):
    """Build a minimal mock Gemini generate_content response."""
    resp = MagicMock()
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# GroqProvider Tests
# ---------------------------------------------------------------------------

class TestGroqProviderBackupKey(unittest.TestCase):

    def _get_provider(self):
        from services.providers.groq_provider import GroqProvider
        return GroqProvider()

    @patch.dict(os.environ, {"GROQ_API_KEY": "primary_key", "GROQ_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_primary_succeeds_backup_never_called(self):
        """When primary key succeeds, backup is never used."""
        provider = self._get_provider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_groq_response('{"ok": true}')

        with patch.object(provider, "_get_client", return_value=mock_client) as mock_get_client:
            result = provider._execute("test prompt", timeout_seconds=10.0)

        self.assertEqual(result, '{"ok": true}')
        # _get_client should only be called once (for primary key)
        self.assertEqual(mock_get_client.call_count, 1)
        first_call_key = mock_get_client.call_args_list[0][1].get("api_key") or mock_get_client.call_args_list[0][0][1]
        self.assertEqual(first_call_key, "primary_key")

    @patch.dict(os.environ, {"GROQ_API_KEY": "primary_key", "GROQ_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_primary_rate_limit_backup_succeeds(self):
        """On 429 rate-limit from primary, backup key is tried and succeeds."""
        provider = self._get_provider()

        primary_client = MagicMock()
        primary_client.chat.completions.create.side_effect = Exception("rate limit 429 too many requests")

        backup_client = MagicMock()
        backup_client.chat.completions.create.return_value = _make_groq_response('{"backup": true}')

        call_count = {"n": 0}

        def fake_get_client(timeout_sec, api_key):
            call_count["n"] += 1
            if api_key == "primary_key":
                return primary_client
            return backup_client

        with patch.object(provider, "_get_client", side_effect=fake_get_client):
            result = provider._execute("test prompt", timeout_seconds=10.0)

        self.assertEqual(result, '{"backup": true}')
        self.assertEqual(call_count["n"], 2)  # primary + backup

    @patch.dict(os.environ, {"GROQ_API_KEY": "bad_key", "GROQ_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_auth_error_does_not_try_backup(self):
        """Auth errors (401 / invalid api key) must NOT trigger backup key attempt."""
        from services.providers.base import ProviderConfigError
        provider = self._get_provider()

        primary_client = MagicMock()
        primary_client.chat.completions.create.side_effect = Exception("invalid api key 401 unauthorized")

        with patch.object(provider, "_get_client", return_value=primary_client) as mock_get_client:
            with self.assertRaises(ProviderConfigError):
                provider._execute("test prompt", timeout_seconds=10.0)

        # Should only call _get_client once (for the primary key that caused auth error)
        self.assertEqual(mock_get_client.call_count, 1)

    @patch.dict(os.environ, {"GROQ_API_KEY": "primary_key", "GROQ_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_both_keys_fail_raises_unavailable(self):
        """When both primary and backup fail with recoverable errors, ProviderUnavailableError is raised."""
        from services.providers.base import ProviderUnavailableError
        provider = self._get_provider()

        failing_client = MagicMock()
        failing_client.chat.completions.create.side_effect = Exception("503 service unavailable")

        with patch.object(provider, "_get_client", return_value=failing_client):
            with self.assertRaises(ProviderUnavailableError):
                provider._execute("test prompt", timeout_seconds=10.0)

    @patch.dict(os.environ, {"GROQ_API_KEY": "primary_key"}, clear=False)
    def test_no_backup_key_single_attempt(self):
        """When GROQ_BACKUP_API_KEY is not set, only one key is tried."""
        # Remove backup key if it happens to be set
        os.environ.pop("GROQ_BACKUP_API_KEY", None)
        from services.providers.base import ProviderUnavailableError
        provider = self._get_provider()

        primary_client = MagicMock()
        primary_client.chat.completions.create.side_effect = Exception("429 rate limit")

        with patch.object(provider, "_get_client", return_value=primary_client) as mock_get_client:
            with self.assertRaises(ProviderUnavailableError):
                provider._execute("test prompt", timeout_seconds=10.0)

        # Only one client created (no backup)
        self.assertEqual(mock_get_client.call_count, 1)

    @patch.dict(os.environ, {"GROQ_API_KEY": "primary_key", "GROQ_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_no_key_in_error_message(self):
        """API keys must never appear in raised exception messages."""
        from services.providers.base import ProviderUnavailableError
        provider = self._get_provider()

        failing_client = MagicMock()
        failing_client.chat.completions.create.side_effect = Exception("503 service down")

        with patch.object(provider, "_get_client", return_value=failing_client):
            try:
                provider._execute("test prompt", timeout_seconds=10.0)
            except ProviderUnavailableError as exc:
                self.assertNotIn("primary_key", str(exc))
                self.assertNotIn("backup_key", str(exc))

    @patch.dict(os.environ, {"GROQ_API_KEY": "primary_key", "GROQ_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_is_configured_true_when_primary_set(self):
        provider = self._get_provider()
        self.assertTrue(provider.is_configured())

    @patch.dict(os.environ, {"GROQ_API_KEY": "primary_key"}, clear=False)
    def test_groq_auth_error_sanitizes_synthetic_credential(self):
        """Synthetic Groq API key in auth exception must NEVER appear in raised exception or logs."""
        from services.providers.base import ProviderConfigError
        provider = self._get_provider()
        synthetic_key = "gsk_SYNTHETIC_TEST_LEAK_CHECK_KEY_0123456789"
        raw_sdk_error = f"401 Unauthorized: Invalid API key {synthetic_key} rejected"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception(raw_sdk_error)

        with patch.object(provider, "_get_client", return_value=mock_client):
            with self.assertLogs("learnai.providers.groq", level="ERROR") as cm:
                with self.assertRaises(ProviderConfigError) as ctx:
                    provider._execute("test prompt", timeout_seconds=10.0)

        self.assertNotIn(synthetic_key, str(ctx.exception))
        for log_entry in cm.output:
            self.assertNotIn(synthetic_key, log_entry)
            self.assertIn("[REDACTED_CREDENTIAL]", log_entry)

    @patch.dict(os.environ, {}, clear=False)
    def test_is_configured_with_only_backup_key(self):
        os.environ.pop("GROQ_API_KEY", None)
        os.environ["GROQ_BACKUP_API_KEY"] = "only_backup"
        try:
            provider = self._get_provider()
            self.assertTrue(provider.is_configured())
        finally:
            os.environ.pop("GROQ_BACKUP_API_KEY", None)


# ---------------------------------------------------------------------------
# NvidiaProvider Tests
# ---------------------------------------------------------------------------

class TestNvidiaProviderBackupKey(unittest.TestCase):

    def _get_provider(self):
        from services.providers.nvidia_provider import NvidiaProvider
        return NvidiaProvider()

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key", "NVIDIA_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_primary_succeeds_backup_never_called(self):
        provider = self._get_provider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_openai_response('{"ok": true}')

        with patch.object(provider, "_get_client", return_value=mock_client) as mock_get_client:
            result = provider._execute("test prompt", timeout_seconds=10.0)

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(mock_get_client.call_count, 1)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key", "NVIDIA_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_primary_rate_limit_backup_succeeds(self):
        provider = self._get_provider()

        primary_client = MagicMock()
        primary_client.chat.completions.create.side_effect = Exception("rate limit 429 too many requests")

        backup_client = MagicMock()
        backup_client.chat.completions.create.return_value = _make_openai_response('{"backup": true}')

        def fake_get_client(timeout_sec, api_key):
            if api_key == "primary_key":
                return primary_client
            return backup_client

        with patch.object(provider, "_get_client", side_effect=fake_get_client):
            result = provider._execute("test prompt", timeout_seconds=10.0)

        self.assertEqual(result, '{"backup": true}')

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "bad_key", "NVIDIA_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_auth_error_does_not_try_backup(self):
        from services.providers.base import ProviderConfigError
        provider = self._get_provider()

        primary_client = MagicMock()
        primary_client.chat.completions.create.side_effect = Exception("invalid api key 401 unauthorized")

        with patch.object(provider, "_get_client", return_value=primary_client) as mock_get_client:
            with self.assertRaises(ProviderConfigError):
                provider._execute("test prompt", timeout_seconds=10.0)

        self.assertEqual(mock_get_client.call_count, 1)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key", "NVIDIA_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_both_keys_fail_raises_unavailable(self):
        from services.providers.base import ProviderUnavailableError
        provider = self._get_provider()

        failing_client = MagicMock()
        failing_client.chat.completions.create.side_effect = Exception("503 service unavailable")

        with patch.object(provider, "_get_client", return_value=failing_client):
            with self.assertRaises(ProviderUnavailableError):
                provider._execute("test prompt", timeout_seconds=10.0)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key", "NVIDIA_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_primary_503_backup_succeeds(self):
        provider = self._get_provider()

        primary_client = MagicMock()
        primary_client.chat.completions.create.side_effect = Exception("503 temporarily unavailable")

        backup_client = MagicMock()
        backup_client.chat.completions.create.return_value = _make_openai_response('{"recovered": true}')

        def fake_get_client(timeout_sec, api_key):
            if api_key == "primary_key":
                return primary_client
            return backup_client

        with patch.object(provider, "_get_client", side_effect=fake_get_client):
            result = provider._execute("test prompt", timeout_seconds=10.0)

        self.assertEqual(result, '{"recovered": true}')

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key", "NVIDIA_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_is_configured_true(self):
        provider = self._get_provider()
        self.assertTrue(provider.is_configured())

    @patch.dict(os.environ, {}, clear=False)
    def test_is_configured_with_only_backup_key(self):
        os.environ.pop("NVIDIA_API_KEY", None)
        os.environ["NVIDIA_BACKUP_API_KEY"] = "only_backup"
        try:
            provider = self._get_provider()
            self.assertTrue(provider.is_configured())
        finally:
            os.environ.pop("NVIDIA_BACKUP_API_KEY", None)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key"}, clear=False)
    def test_auth_error_sanitizes_synthetic_credential(self):
        """Synthetic NVIDIA API key in auth exception must NEVER appear in raised exception or logs."""
        from services.providers.base import ProviderConfigError
        provider = self._get_provider()
        synthetic_key = "nvapi-SYNTHETIC_TEST_LEAK_CHECK_KEY_0123456789"
        raw_sdk_error = f"401 Unauthorized: Invalid API key {synthetic_key} provided"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception(raw_sdk_error)

        with patch.object(provider, "_get_client", return_value=mock_client):
            with self.assertLogs("learnai.providers.nvidia", level="ERROR") as cm:
                with self.assertRaises(ProviderConfigError) as ctx:
                    provider._execute("test prompt", timeout_seconds=10.0)

        # Assert synthetic key not in raised exception
        self.assertNotIn(synthetic_key, str(ctx.exception))
        # Assert synthetic key not in log records
        for log_entry in cm.output:
            self.assertNotIn(synthetic_key, log_entry)
            self.assertIn("[REDACTED_CREDENTIAL]", log_entry)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key"}, clear=False)
    def test_transient_error_sanitizes_synthetic_credential(self):
        """Synthetic key in transient 503 error message must be redacted in exception and logs."""
        from services.providers.base import ProviderUnavailableError
        provider = self._get_provider()
        synthetic_key = "nvapi-SYNTHETIC_TEST_LEAK_CHECK_KEY_9876543210"
        raw_sdk_error = f"503 Service Unavailable: connection failed for {synthetic_key}"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception(raw_sdk_error)

        with patch.object(provider, "_get_client", return_value=mock_client):
            with self.assertRaises(ProviderUnavailableError) as ctx:
                provider._execute("test prompt", timeout_seconds=10.0)

        self.assertNotIn(synthetic_key, str(ctx.exception))
        self.assertIn("[REDACTED_CREDENTIAL]", str(ctx.exception))

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key", "NVIDIA_BACKUP_API_KEY": "backup_key", "NVIDIA_FALLBACK_MODEL": "meta/llama-3.1-70b-instruct"}, clear=False)
    def test_model_not_found_404_does_not_try_model_fallback_or_backup(self):
        """Permanent 404 Model Not Found error must fail immediately without trying fallback model or backup key."""
        from services.providers.base import ProviderConfigError
        provider = self._get_provider()

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("404 Not Found: model 'unknown-model' does not exist")

        with patch.object(provider, "_get_client", return_value=mock_client) as mock_get_client:
            with self.assertRaises(ProviderConfigError) as ctx:
                provider._execute("test prompt", timeout_seconds=10.0)

        # Must fail on the first attempt without fallback
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)
        self.assertEqual(mock_get_client.call_count, 1)
        self.assertEqual(ctx.exception.category, "MODEL_NOT_FOUND_404")
        # Ensure no chained cause
        self.assertIsNone(ctx.exception.__cause__)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key", "NVIDIA_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_malformed_request_400_does_not_try_model_fallback_or_backup(self):
        """Permanent 400 Bad Request error must fail immediately without trying fallback model or backup key."""
        from services.providers.base import ProviderError
        provider = self._get_provider()

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("400 Bad Request: prompt exceeds max context length")

        with patch.object(provider, "_get_client", return_value=mock_client) as mock_get_client:
            with self.assertRaises(ProviderError) as ctx:
                provider._execute("test prompt", timeout_seconds=10.0)

        # Must fail on the first attempt without fallback
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)
        self.assertEqual(mock_get_client.call_count, 1)
        self.assertEqual(ctx.exception.category, "MALFORMED_REQUEST_400")
        # Ensure no chained cause
        self.assertIsNone(ctx.exception.__cause__)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "primary_key"}, clear=False)
    def test_chained_exception_suppression_prevents_cause_leak(self):
        """Verify that raise ... from None suppresses __cause__ on raw SDK errors containing secrets."""
        from services.providers.base import ProviderConfigError
        provider = self._get_provider()
        secret_token = "nvapi-SUPER_SECRET_TOKEN_DO_NOT_LEAK"
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception(f"401 Unauthorized for token {secret_token}")

        with patch.object(provider, "_get_client", return_value=mock_client):
            with self.assertRaises(ProviderConfigError) as ctx:
                provider._execute("test prompt", timeout_seconds=10.0)

        # __cause__ must be explicitly None due to 'from None'
        self.assertIsNone(ctx.exception.__cause__)
        # String representation must not have secret
        self.assertNotIn(secret_token, str(ctx.exception))




# ---------------------------------------------------------------------------
# GeminiProvider Tests
# ---------------------------------------------------------------------------

class TestGeminiProviderBackupKey(unittest.TestCase):

    def _get_provider(self):
        from services.providers.gemini_provider import GeminiProvider
        return GeminiProvider()

    def _make_gemini_client(self, response_text: str):
        client = MagicMock()
        client.models.generate_content.return_value = _make_gemini_response(response_text)
        return client

    def _make_failing_gemini_client(self, error_msg: str):
        client = MagicMock()
        client.models.generate_content.side_effect = Exception(error_msg)
        return client

    @patch.dict(os.environ, {"GEMINI_API_KEY": "primary_key", "GEMINI_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_primary_succeeds_backup_never_called(self):
        provider = self._get_provider()
        primary_client = self._make_gemini_client('{"ok": true}')

        with patch.object(provider, "_get_client", return_value=primary_client) as mock_get_client:
            result = provider._execute_with_models("test prompt", timeout_seconds=12.0)

        self.assertEqual(result, '{"ok": true}')
        # Only one client instantiated (primary)
        self.assertEqual(mock_get_client.call_count, 1)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "primary_key", "GEMINI_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_primary_rate_limit_backup_succeeds(self):
        provider = self._get_provider()
        primary_client = self._make_failing_gemini_client("429 rate limit resource exhausted")
        backup_client = self._make_gemini_client('{"backup": true}')

        def fake_get_client(timeout_sec, api_key):
            if api_key == "primary_key":
                return primary_client
            return backup_client

        with patch.object(provider, "_get_client", side_effect=fake_get_client):
            result = provider._execute_with_models("test prompt", timeout_seconds=12.0)

        self.assertEqual(result, '{"backup": true}')

    @patch.dict(os.environ, {"GEMINI_API_KEY": "bad_key", "GEMINI_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_auth_error_does_not_try_backup(self):
        from services.providers.base import ProviderConfigError
        provider = self._get_provider()

        # Patch _is_auth_error to return True for our test exception
        auth_exc = Exception("api key not valid please pass a valid api key")
        primary_client = self._make_failing_gemini_client(str(auth_exc))

        with patch.object(provider, "_get_client", return_value=primary_client) as mock_get_client:
            with patch.object(provider, "_is_auth_error", return_value=True):
                with self.assertRaises(ProviderConfigError):
                    provider._execute_with_models("test prompt", timeout_seconds=12.0)

        # Only one client instantiated (auth fails immediately)
        self.assertEqual(mock_get_client.call_count, 1)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "primary_key", "GEMINI_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_both_keys_fail_raises_unavailable(self):
        from services.providers.base import ProviderUnavailableError
        provider = self._get_provider()

        failing_client = self._make_failing_gemini_client("503 service unavailable temporarily")

        with patch.object(provider, "_get_client", return_value=failing_client):
            with patch.object(provider, "_is_auth_error", return_value=False):
                with patch.object(provider, "_is_retryable", return_value=True):
                    with self.assertRaises(ProviderUnavailableError):
                        provider._execute_with_models("test prompt", timeout_seconds=12.0)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "primary_key", "GEMINI_BACKUP_API_KEY": "backup_key"}, clear=False)
    def test_is_configured_true(self):
        provider = self._get_provider()
        self.assertTrue(provider.is_configured())

    @patch.dict(os.environ, {}, clear=False)
    def test_is_configured_with_only_backup_key(self):
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ["GEMINI_BACKUP_API_KEY"] = "only_backup"
        try:
            provider = self._get_provider()
            self.assertTrue(provider.is_configured())
        finally:
            os.environ.pop("GEMINI_BACKUP_API_KEY", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
