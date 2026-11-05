"""
backend/test_nvidia_provider.py
================================
Unit tests for NvidiaProvider model availability, catalog discovery,
HTTP 410 deprecation handling, and time budget preservation.
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch

from services.providers.base import (
    ProviderConfigError,
    ProviderUnavailableError,
    ProviderError
)
from services.providers.nvidia_provider import NvidiaProvider
from services.quota_manager import quota_manager


def _make_dummy_quiz_json() -> str:
    return json.dumps({
        "subject": "Deep Learning",
        "topic": "Loss Functions",
        "questions": [
            {
                "question": "What is MSE?",
                "options": {
                    "A": "Mean Squared Error",
                    "B": "Maximum Squared Error",
                    "C": "Minimum Standard Error",
                    "D": "Mean Standard Error"
                },
                "correct_answer": "A",
                "difficulty": "Easy",
                "concept": "Loss Functions",
                "explanation": "MSE measures average squared difference between predictions and actuals."
            }
        ]
    })


@pytest.fixture(autouse=True)
def reset_provider_state():
    """Ensure in-memory caches and quotas are clean before each test."""
    NvidiaProvider.reset_runtime_caches()
    quota_manager.reset_all()
    yield
    NvidiaProvider.reset_runtime_caches()
    quota_manager.reset_all()


def test_nvidia_deprecated_primary_model_skipped(monkeypatch):
    """Verifies that if NVIDIA_MODEL is set to deprecated 'meta/llama-3.1-8b-instruct',

    it is skipped upfront and the active fallback model is selected.
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test_key")
    monkeypatch.setenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")
    monkeypatch.delenv("NVIDIA_FALLBACK_MODEL", raising=False)

    provider = NvidiaProvider()

    with patch.object(provider, "_get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client

        # Catalog lookup disabled by returning None
        mock_client.models.list.side_effect = Exception("Catalog disabled")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=_make_dummy_quiz_json()))]
        mock_client.chat.completions.create.return_value = mock_resp

        result = provider._execute("Test prompt", timeout_seconds=10.0)
        assert result is not None
        assert mock_client.chat.completions.create.called

        # Confirm deprecated meta/llama-3.1-8b-instruct was NEVER called
        called_models = [c[1]["model"] for c in mock_client.chat.completions.create.call_args_list]
        assert "meta/llama-3.1-8b-instruct" not in called_models
        # Confirm active model was called
        assert called_models[0] in [provider.DEFAULT_FALLBACK_MODEL, 'nvidia/nemotron-3.5-lightning-30b-a3b']


def test_nvidia_active_fallback_selected_from_catalog(monkeypatch):
    """Verifies that active models from dynamic catalog discovery are prioritized."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test_key")
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_FALLBACK_MODEL", raising=False)

    provider = NvidiaProvider()

    with patch.object(provider, "_get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client

        # Mock catalog returning only mixtral
        mock_model_obj = MagicMock()
        mock_model_obj.id = "mistralai/mixtral-8x7b-instruct-v0.1"
        mock_client.models.list.return_value = MagicMock(data=[mock_model_obj])

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=_make_dummy_quiz_json()))]
        mock_client.chat.completions.create.return_value = mock_resp

        result = provider._execute("Test prompt", timeout_seconds=10.0)
        assert result is not None
        called_models = [c[1]["model"] for c in mock_client.chat.completions.create.call_args_list]
        assert called_models[0] == "mistralai/mixtral-8x7b-instruct-v0.1"


def test_nvidia_catalog_lookup_failure_falls_back_safely(monkeypatch):
    """Verifies that if catalog discovery fails (e.g.

    timeout or 503), it safely falls back to verified static candidate models.
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test_key")
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_FALLBACK_MODEL", raising=False)

    provider = NvidiaProvider()

    with patch.object(provider, "_get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client

        # Catalog lookup fails with an exception
        mock_client.models.list.side_effect = Exception("503 Service Unavailable: Catalog unreachable")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=_make_dummy_quiz_json()))]
        mock_client.chat.completions.create.return_value = mock_resp

        result = provider._execute("Test prompt", timeout_seconds=10.0)
        assert result is not None
        assert mock_client.chat.completions.create.called
        called_models = [c[1]["model"] for c in mock_client.chat.completions.create.call_args_list]
        # Fell back safely to static candidate models
        assert called_models[0] in [NvidiaProvider.DEFAULT_FALLBACK_MODEL, 'nvidia/nemotron-3.5-lightning-30b-a3b']


def test_nvidia_http_410_handling_preserves_budget(monkeypatch):
    """Verifies HTTP 410 Gone dynamically records model deprecation, does not sleep,

    and attempts the active fallback with remaining budget.
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test_key")
    monkeypatch.setenv("NVIDIA_MODEL", "custom-experimental-model")
    monkeypatch.setenv("NVIDIA_FALLBACK_MODEL", NvidiaProvider.DEFAULT_NVIDIA_MODEL)

    provider = NvidiaProvider()

    with patch.object(provider, "_get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client

        mock_client.models.list.side_effect = Exception("Catalog disabled")

        # First call fails with 410 Gone; second call succeeds with fallback model
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=_make_dummy_quiz_json()))]
        mock_client.chat.completions.create.side_effect = [
            Exception("410 Gone: The requested model endpoint is no longer active."),
            mock_resp
        ]

        result = provider._execute("Test prompt", timeout_seconds=12.0)
        assert result is not None
        assert mock_client.chat.completions.create.call_count == 2

        calls = mock_client.chat.completions.create.call_args_list
        assert calls[0][1]["model"] == "custom-experimental-model"
        # Bounded timeout reserved at least 2.5s for the second model
        assert calls[0][1]["timeout"] <= 7.5
        assert calls[1][1]["model"] == NvidiaProvider.DEFAULT_NVIDIA_MODEL

        # Confirm model was recorded as runtime deprecated
        assert "custom-experimental-model" in provider._runtime_deprecated_models

        # A subsequent call should now skip 'custom-experimental-model' upfront!
        mock_client.chat.completions.create.side_effect = [mock_resp]
        result2 = provider._execute("Second call", timeout_seconds=10.0)
        assert result2 is not None
        second_run_models = [c[1]["model"] for c in mock_client.chat.completions.create.call_args_list[2:]]
        assert "custom-experimental-model" not in second_run_models


def test_nvidia_insufficient_remaining_time_skips_gracefully(monkeypatch):
    """Verifies that when remaining time is less than minimum budget (< 2.0s),

    NvidiaProvider skips attempts and raises TIMEOUT ProviderUnavailableError.
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test_key")
    provider = NvidiaProvider()

    with patch.object(provider, "_get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client

        with pytest.raises(ProviderUnavailableError) as exc_info:
            provider._execute("Test prompt", timeout_seconds=1.2)

        assert exc_info.value.category == "TIMEOUT"
        assert not mock_client.chat.completions.create.called


def test_nvidia_no_available_models_raises_config_error(monkeypatch):
    """Verifies that if all configured models are deprecated and catalog provides no alternatives,

    ProviderConfigError(MODEL_NOT_FOUND_404) is raised.
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test_key")
    monkeypatch.setenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")

    provider = NvidiaProvider()
    # Mark all static candidate models as runtime deprecated for this test
    provider._runtime_deprecated_models.add("meta/llama-3.1-8b-instruct")
    provider._runtime_deprecated_models.add(NvidiaProvider.DEFAULT_NVIDIA_MODEL)
    provider._runtime_deprecated_models.add(NvidiaProvider.DEFAULT_FALLBACK_MODEL)
    provider._runtime_deprecated_models.add("nvidia/nemotron-3.5-lightning-30b-a3b")
    provider._runtime_deprecated_models.add("mistralai/mixtral-8x7b-instruct-v0.1")

    with patch.object(provider, "_get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client
        mock_client.models.list.side_effect = Exception("Catalog unreachable")

        with pytest.raises(ProviderConfigError) as exc_info:
            provider._execute("Test prompt", timeout_seconds=10.0)

        assert exc_info.value.category == "MODEL_NOT_FOUND_404"
