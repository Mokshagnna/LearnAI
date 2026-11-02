"""
Test Suite: AI Provider Reliability, Fallback Sequence & Resilience
Verifies:
  Scenario 1: Successful response from the primary provider (e.g., Groq returns 10 questions).
  Scenario 2: Primary provider returns 429 -> successfully falls back to secondary provider without key rotation.
  Scenario 3: Primary provider returns 503 -> retries with backoff if sole provider, or falls back to next provider.
  Scenario 4: Primary provider times out -> falls back within reasonable time.
  Scenario 5: Primary provider encounters 500/502 and backup provider succeeds.
  Scenario 6: All providers fail -> raises AIServiceUnavailableError with the student-friendly message:
              "Quiz generation is temporarily unavailable. Please try again shortly."
  Scenario 7: Invalid API credentials -> fails cleanly with AUTH_CONFIG_ERROR without repeated retries.
  Scenario 8: No key rotation on 429 rate limit (anti-evasion safeguard).
  Scenario 9: Model 404 failover (provider tries fallback model before raising error).
  Scenario 10: Gemini Candidate Model Failover from 404.
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch

from services.providers.base import (
    BaseAIProvider,
    ProviderConfigError,
    ProviderUnavailableError,
    ProviderError
)
from services.providers.groq_provider import GroqProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.llm_service import (
    generate_quiz,
    AIServiceUnavailableError,
    AIRateLimitError,
    AITimeoutError,
    AIConfigError
)
from test_phase1_reliability import _make_valid_quiz_json


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1: Successful Primary Provider Response
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_1_successful_primary_provider_response():
    """Scenario 1: Primary provider (Groq) successfully generates 10 questions on the first attempt."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.return_value = _make_valid_quiz_json("Neural Networks", "Deep Learning")

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
        with patch("services.llm_service.get_or_create_student", return_value="student_001"):
            quiz = generate_quiz(
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name="Neural Networks",
                syllabus_context="Neural networks, perceptrons, activations",
                student_id="student_001",
                concepts=["Neural Networks Core Principles"]
            )

    assert quiz["provider_used"] == "groq"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 1
    assert mock_nvidia.generate_quiz_raw.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2: Primary Returns 429 -> Falls Back to Secondary Provider
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_2_primary_429_falls_back_to_secondary():
    """Scenario 2: Primary provider returns 429 (rate limit) and system seamlessly falls back to secondary provider."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Groq rate limit exceeded (429)", category="RATE_LIMIT_429"
    )

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.return_value = _make_valid_quiz_json("Neural Networks", "Deep Learning")

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
        with patch("services.llm_service.get_or_create_student", return_value="student_001"):
            quiz = generate_quiz(
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name="Neural Networks",
                syllabus_context="Neural networks, perceptrons, activations",
                student_id="student_001",
                concepts=["Neural Networks Core Principles"]
            )

    assert quiz["provider_used"] == "nvidia"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 1  # No duplicate request to rate-limited provider
    assert mock_nvidia.generate_quiz_raw.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3: Primary Returns 503 -> Bounded Retry on Sole Provider OR Fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_3_primary_503_fallback_in_multiprovider():
    """Scenario 3A: When multiple providers exist, 503 on primary immediately fails over to secondary."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "503 Service Unavailable: High demand", category="SERVICE_UNAVAILABLE_503"
    )

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.return_value = _make_valid_quiz_json("Neural Networks", "Deep Learning")

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
        with patch("services.llm_service.get_or_create_student", return_value="student_001"):
            quiz = generate_quiz(
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name="Neural Networks",
                syllabus_context="Neural networks, perceptrons, activations",
                student_id="student_001",
                concepts=["Neural Networks Core Principles"]
            )

    assert quiz["provider_used"] == "nvidia"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 1
    assert mock_nvidia.generate_quiz_raw.call_count == 1


def test_scenario_3_sole_provider_503_retries_with_backoff():
    """Scenario 3B: When only one provider is configured, transient 503 is retried with backoff."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    # First attempt fails with 503, second attempt succeeds
    mock_groq.generate_quiz_raw.side_effect = [
        ProviderUnavailableError("503 Service Unavailable", category="SERVICE_UNAVAILABLE_503"),
        _make_valid_quiz_json("Neural Networks", "Deep Learning")
    ]

    with patch("services.llm_service._get_providers", return_value=[mock_groq]):
        with patch("services.llm_service.get_or_create_student", return_value="student_001"):
            with patch("time.sleep") as mock_sleep:
                quiz = generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="Neural Networks",
                    syllabus_context="Neural networks, perceptrons, activations",
                    student_id="student_001",
                    concepts=["Neural Networks Core Principles"]
                )

    assert quiz["provider_used"] == "groq"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 2
    assert mock_sleep.called


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4: Primary Times Out -> Falls Back to Next Provider
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_4_primary_timeout_falls_back():
    """Scenario 4: Primary provider times out and orchestrator falls back to next provider."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Groq request timed out", category="TIMEOUT"
    )

    mock_gemini = MagicMock(spec=BaseAIProvider)
    mock_gemini.name = "gemini"
    mock_gemini.is_configured.return_value = True
    mock_gemini.generate_quiz_raw.return_value = _make_valid_quiz_json("Neural Networks", "Deep Learning")

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_gemini]):
        with patch("services.llm_service.get_or_create_student", return_value="student_001"):
            quiz = generate_quiz(
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name="Neural Networks",
                syllabus_context="Neural networks, perceptrons, activations",
                student_id="student_001",
                concepts=["Neural Networks Core Principles"]
            )

    assert quiz["provider_used"] == "gemini"
    assert len(quiz["questions"]) == 10


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 5: Primary Fails with 500/502 and Backup Provider Succeeds
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_5_primary_gateway_error_backup_succeeds():
    """Scenario 5: Primary provider encounters 502 Bad Gateway / Network error and backup provider succeeds."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "502 Bad Gateway: upstream server error", category="SERVER_ERROR_502"
    )

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.return_value = _make_valid_quiz_json("Neural Networks", "Deep Learning")

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
        with patch("services.llm_service.get_or_create_student", return_value="student_001"):
            quiz = generate_quiz(
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name="Neural Networks",
                syllabus_context="Neural networks, perceptrons, activations",
                student_id="student_001",
                concepts=["Neural Networks Core Principles"]
            )

    assert quiz["provider_used"] == "nvidia"
    assert len(quiz["questions"]) == 10


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 6: All Providers Fail -> Student-Friendly Message
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_6_all_providers_fail_returns_student_friendly_error():
    """Scenario 6: When all providers are exhausted with temporary 503/429 errors and no demo bank matches,
    system raises AIServiceUnavailableError with the exact student-friendly message:
    'Quiz generation is temporarily unavailable. Please try again shortly.'"""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Groq 503 Service Unavailable", category="SERVICE_UNAVAILABLE_503"
    )

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "NVIDIA 503 Service Unavailable: High demand", category="SERVICE_UNAVAILABLE_503"
    )

    with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
        with patch("services.llm_service._load_local_demo_questions", return_value=None):
            with patch("services.llm_service.get_or_create_student", return_value="student_001"):
                with pytest.raises(AIServiceUnavailableError) as exc_info:
                    generate_quiz(
                        course_code="22AM301",
                        course_name="Deep Learning",
                        topic_name="Unseen Non Demo Topic",
                        syllabus_context="Context",
                        student_id="student_001"
                    )

    assert "Quiz generation is temporarily unavailable. Please try again shortly." in str(exc_info.value)
    assert exc_info.value.status_code in (503, 429)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 7: Invalid API Credentials -> Clean Failure Without Retries
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_7_invalid_credentials_fail_cleanly_without_retries():
    """Scenario 7: Invalid API key raises ProviderConfigError / AIConfigError with AUTH_CONFIG_ERROR immediately."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderConfigError(
        "Groq API key is invalid or unauthorized.", category="AUTH_CONFIG_ERROR"
    )

    with patch("services.llm_service._get_providers", return_value=[mock_groq]):
        with patch("services.llm_service._load_local_demo_questions", return_value=None):
            with patch("services.llm_service.get_or_create_student", return_value="student_001"):
                with pytest.raises(AIConfigError) as exc_info:
                    generate_quiz(
                        course_code="22AM301",
                        course_name="Deep Learning",
                        topic_name="Unseen Non Demo Topic",
                        syllabus_context="Context",
                        student_id="student_001"
                    )

    assert exc_info.value.category == "AUTH_CONFIG_ERROR"
    assert mock_groq.generate_quiz_raw.call_count == 1  # Not retried


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 8: Anti-Key-Rotation Safeguard on 429
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_8_no_dynamic_key_pool_or_unbounded_retries(monkeypatch):
    """Scenario 8: Providers do NOT loop through arbitrary key pools or unbounded retries on 429."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_primary_key_001")
    monkeypatch.delenv("GROQ_BACKUP_API_KEY", raising=False)

    provider = GroqProvider()
    assert provider.is_configured()

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Client chat completion raises 429
        mock_client.chat.completions.create.side_effect = Exception("429 Too Many Requests: Rate limit reached")

        with pytest.raises(ProviderUnavailableError) as exc_info:
            provider._execute("test prompt", timeout_seconds=5.0)

        assert exc_info.value.category == "RATE_LIMIT_429"
        # Verify only the configured key is attempted
        assert mock_get_client.call_count == 1
        call_key = mock_get_client.call_args[1].get("api_key")
        assert call_key == "gsk_primary_key_001"


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 9: Candidate Model 404 Failover
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_9_candidate_model_404_failover(monkeypatch):
    """Scenario 9: If a candidate model returns 404 (e.g. decommissioned or not found),
    provider fails over to the next candidate model instead of crashing immediately."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_primary_key_001")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")

    provider = GroqProvider()
    assert provider.is_configured()

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # First call (70B model) raises 404 model not found; second call (8B fallback) succeeds
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=_make_valid_quiz_json("Neural Networks", "Deep Learning")))]

        mock_client.chat.completions.create.side_effect = [
            Exception("404 The model `llama-3.3-70b-versatile` does not exist"),
            mock_resp
        ]

        result = provider._execute("test prompt", timeout_seconds=10.0)
        assert result is not None
        assert mock_client.chat.completions.create.call_count == 2
        # Verify second call used the 8B fallback model
        second_call_model = mock_client.chat.completions.create.call_args_list[1][1]["model"]
        assert second_call_model == "llama-3.1-8b-instant"


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 10: Gemini Candidate Model Failover from 404
# ─────────────────────────────────────────────────────────────────────────────

def test_scenario_10_gemini_invalid_model_fails_fast_without_retries(monkeypatch):
    """Scenario 10: When an invalid Gemini model ID returns 404, provider fails fast immediately
    with ProviderConfigError(MODEL_NOT_FOUND_404) without retrying invalid model repeatedly."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key_001")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-nonexistent-model")

    provider = GeminiProvider()
    assert provider.is_configured()

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.models.generate_content.side_effect = Exception("404 Not Found: models/gemini-nonexistent-model is not found")

        with pytest.raises(ProviderConfigError) as exc_info:
            provider._execute_with_models("test prompt", timeout_seconds=15.0)

        assert exc_info.value.category == "MODEL_NOT_FOUND_404"
        # Fails immediately on first attempt without redundant retries of the invalid model
        assert mock_client.models.generate_content.call_count == 1
