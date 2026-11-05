"""
Test Suite: AI Provider Reliability - Cloudflare Fallback & Missing Scenarios
Verifies:
  Scenario A: Cloudflare successfully generating questions after another provider fails.
  Scenario B: Groq → Cloudflare fallback using the Render configuration (PROVIDER_ORDER=groq,cloudflare).
  Scenario C: Complete four-provider fallback: Groq → NVIDIA → Gemini → Cloudflare.
  Scenario D: Partial question aggregation across multiple providers.
  Scenario E: A provider in cooldown being skipped during fallback.
  Scenario F: A provider being skipped when insufficient deadline remains.
  Scenario G: Mixed failures across all four providers.
  Scenario H: Invalid credentials followed by successful fallback.
  Scenario I: All providers failing and returning the correct controlled error.

Uses mocks. Does not make real paid API calls.
"""
import os
import json
import pytest
from unittest.mock import MagicMock, patch

from services.providers.base import (
    BaseAIProvider,
    ProviderConfigError,
    ProviderUnavailableError,
    ProviderError,
)
from services.providers.groq_provider import GroqProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.providers.cloudflare_provider import CloudflareProvider
from services.llm_service import (
    generate_quiz,
    AIServiceUnavailableError,
    AIRateLimitError,
    AITimeoutError,
    AIConfigError,
    _get_providers,
)
from test_phase1_reliability import _make_valid_quiz_json


def _make_valid_quiz_json_partial(topic: str = "Test Topic", count: int = 4):
    """Creates a partial quiz JSON payload with fewer than 10 questions."""
    questions = []
    for i in range(count):
        questions.append({
            "question": f"{topic}: Partial question {i + 1}",
            "options": {
                "A": f"Option A for question {i + 1}",
                "B": f"Option B for question {i + 1}",
                "C": f"Option C for question {i + 1}",
                "D": f"Option D for question {i + 1}",
            },
            "correct_answer": "A",
            "difficulty": "Easy",
            "concept": f"{topic} Concept {i + 1}",
            "explanation": f"Explanation for question {i + 1}",
        })
    return json.dumps({
        "subject": "Test Subject",
        "topic": topic,
        "questions": questions,
    })


# ── Scenario A: Cloudflare successfully generating questions after another provider fails ──
@patch("services.llm_service._get_providers")
def test_cloudflare_success_after_groq_failure(mock_get_providers):
    """Scenario A: Groq fails with 429, Cloudflare successfully generates the quiz."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Groq rate limit exceeded (429)", category="RATE_LIMIT_429"
    )

    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.return_value = _make_valid_quiz_json("Neural Networks")

    mock_get_providers.return_value = [mock_groq, mock_cf]

    with patch("services.llm_service.get_or_create_student", return_value="student_001"):
        quiz = generate_quiz(
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Neural Networks",
            syllabus_context="Neural networks, perceptrons, activations",
            student_id="student_001",
            concepts=["Neural Networks Core Principles"]
        )

    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 1
    assert mock_cf.generate_quiz_raw.call_count == 1
    # Groq failed, Cloudflare succeeded - provider_used is Cloudflare
    assert quiz["provider_used"] == "cloudflare"


# ── Scenario B: Groq → Cloudflare fallback using Render configuration ──
@patch("services.llm_service._get_providers")
@patch.dict(os.environ, {"PROVIDER_ORDER": "groq,cloudflare"})
def test_groq_to_cloudflare_render_configuration(mock_get_providers):
    """Scenario B: With PROVIDER_ORDER=groq,cloudflare (Render config), Groq failure falls back to Cloudflare."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Groq service unavailable (503)", category="SERVICE_UNAVAILABLE_503"
    )

    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.return_value = _make_valid_quiz_json("Linear Algebra")

    mock_get_providers.return_value = [mock_groq, mock_cf]

    with patch("services.llm_service.get_or_create_student", return_value="student_001"):
        quiz = generate_quiz(
            course_code="22MT103",
            course_name="Linear Algebra",
            topic_name="Matrices",
            syllabus_context="Matrix algebra, types of matrices",
            student_id="student_001",
            concepts=["Matrix algebra"]
        )

    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 1
    assert mock_cf.generate_quiz_raw.call_count == 1
    # Groq failed, Cloudflare succeeded - provider_used is Cloudflare
    assert quiz["provider_used"] == "cloudflare"


# ── Scenario C: Complete four-provider fallback ──
@patch("services.llm_service._get_providers")
def test_four_provider_fallback_groq_to_nvidia_to_gemini_to_cloudflare(mock_get_providers):
    """Scenario C: Groq→NVIDIA→Gemini fail, Cloudflare succeeds."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq 429", category="RATE_LIMIT_429")

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.side_effect = ProviderUnavailableError("NVIDIA 503", category="SERVICE_UNAVAILABLE_503")

    mock_gemini = MagicMock(spec=BaseAIProvider)
    mock_gemini.name = "gemini"
    mock_gemini.is_configured.return_value = True
    mock_gemini.generate_quiz_raw.side_effect = ProviderUnavailableError("Gemini 503", category="SERVICE_UNAVAILABLE_503")

    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.return_value = _make_valid_quiz_json("Quantum Computing")

    mock_get_providers.return_value = [mock_groq, mock_nvidia, mock_gemini, mock_cf]

    with patch("services.llm_service.get_or_create_student", return_value="student_001"):
        quiz = generate_quiz(
            course_code="22PH101",
            course_name="Quantum Physics",
            topic_name="Quantum Computing",
            syllabus_context="Quantum mechanics, superposition, entanglement",
            student_id="student_001",
            concepts=["Quantum Computing Core Principles"]
        )

    assert quiz["provider_used"] == "cloudflare"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 1
    assert mock_nvidia.generate_quiz_raw.call_count == 1
    assert mock_gemini.generate_quiz_raw.call_count == 1
    assert mock_cf.generate_quiz_raw.call_count == 1


# ── Scenario D: Partial question aggregation across multiple providers ──
@patch("services.llm_service._get_providers")
@patch.dict(os.environ, {"MAX_PROVIDER_RETRIES": "0"})
def test_partial_question_aggregation_across_providers(mock_get_providers):
    """Scenario D: Groq returns 6 valid questions, Cloudflare returns 4 valid questions; combined = 10."""
    full_json = json.loads(_make_valid_quiz_json("Relativity"))
    groq_questions = full_json["questions"][:6]
    cf_questions = full_json["questions"][6:10]

    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.return_value = json.dumps({"questions": groq_questions})

    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.return_value = json.dumps({"questions": cf_questions})

    mock_get_providers.return_value = [mock_groq, mock_cf]

    with patch("services.llm_service.get_or_create_student", return_value="student_001"):
        quiz = generate_quiz(
            course_code="22PH201",
            course_name="Modern Physics",
            topic_name="Relativity",
            syllabus_context="Special relativity, general relativity",
            student_id="student_001",
            concepts=["Relativity Core Principles"]
        )

    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count >= 1
    assert mock_cf.generate_quiz_raw.call_count >= 1
    # provider_used reflects the first provider that successfully returned questions (groq),
    # not the provider that provided the final questions (cloudflare). This is existing behavior.
    assert quiz["provider_used"] == "groq"


# ── Scenario E: Provider in cooldown being skipped during fallback ──
@patch("services.llm_service._get_providers")
@patch.dict(os.environ, {"GROQ_API_KEY": "test_key"})
def test_provider_in_cooldown_skipped(mock_get_providers):
    """Scenario E: Groq is in cooldown, system skips it and uses NVIDIA instead."""
    # Set up cooldown for Groq via the real quota_manager
    from services.quota_manager import quota_manager, RateLimitInfo, RateLimitType
    quota_manager.reset_all()
    quota_manager.record_rate_limit(
        "groq",
        "llama-3.1-8b-instant",
        "test_key",
        RateLimitInfo(RateLimitType.TPD, retry_after_seconds=3600.0, is_daily_exhaustion=True)
    )

    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.return_value = _make_valid_quiz_json("Thermodynamics")

    mock_get_providers.return_value = [mock_groq, mock_nvidia]

    with patch("services.llm_service.get_or_create_student", return_value="student_001"):
        quiz = generate_quiz(
            course_code="22PH301",
            course_name="Thermodynamics",
            topic_name="Thermodynamics",
            syllabus_context="Laws of thermodynamics, entropy, heat transfer",
            student_id="student_001",
            concepts=["Thermodynamics Core Principles"]
        )

    assert quiz["provider_used"] == "nvidia"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 0  # Never called due to cooldown
    assert mock_nvidia.generate_quiz_raw.call_count == 1


# ── Scenario F: Provider being skipped when insufficient deadline remains ──
@patch("services.llm_service._get_providers")
def test_provider_skipped_insufficient_deadline(mock_get_providers):
    """Scenario F: With very little deadline remaining, Groq is skipped by deadline check, NVIDIA is also skipped."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True

    mock_get_providers.return_value = [mock_groq, mock_nvidia]

    # Very short deadline (0.5s) - Groq needs min 2.5s, NVIDIA needs min 4.0s
    # Both are skipped by deadline check, neither is called
    with patch.dict(os.environ, {"OVERALL_DEADLINE_SECONDS": "0.5"}):
        with patch("services.llm_service.get_or_create_student", return_value="student_001"):
            with patch("services.llm_service._load_local_demo_questions", return_value=None):
                with pytest.raises(AIServiceUnavailableError):
                    generate_quiz(
                        course_code="22AM301",
                        course_name="Deep Learning",
                        topic_name="Neural Networks",
                        syllabus_context="Context",
                        student_id="student_001",
                    )

    # Both providers are skipped due to insufficient deadline, neither is called
    assert mock_groq.generate_quiz_raw.call_count == 0
    assert mock_nvidia.generate_quiz_raw.call_count == 0


# ── Scenario G: Mixed failures across all four providers ──
@patch("services.llm_service._get_providers")
def test_mixed_failures_all_four_providers(mock_get_providers):
    """Scenario G: Groq 429, NVIDIA 503, Gemini 400 (permanent), Cloudflare succeeds."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq 429", category="RATE_LIMIT_429")

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.side_effect = ProviderUnavailableError("NVIDIA 503", category="SERVICE_UNAVAILABLE_503")

    mock_gemini = MagicMock(spec=BaseAIProvider)
    mock_gemini.name = "gemini"
    mock_gemini.is_configured.return_value = True
    mock_gemini.generate_quiz_raw.side_effect = ProviderConfigError("Gemini 400 Bad Request", category="MALFORMED_REQUEST_400")

    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.return_value = _make_valid_quiz_json("Cosmology")

    mock_get_providers.return_value = [mock_groq, mock_nvidia, mock_gemini, mock_cf]

    with patch("services.llm_service.get_or_create_student", return_value="student_001"):
        quiz = generate_quiz(
            course_code="22PH401",
            course_name="Cosmology",
            topic_name="Cosmology",
            syllabus_context="Big Bang, cosmic microwave background, dark matter",
            student_id="student_001",
            concepts=["Cosmology Core Principles"]
        )

    assert quiz["provider_used"] == "cloudflare"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 1
    assert mock_nvidia.generate_quiz_raw.call_count == 1
    assert mock_gemini.generate_quiz_raw.call_count == 1
    assert mock_cf.generate_quiz_raw.call_count == 1


# ── Scenario H: Invalid credentials followed by successful fallback ──
@patch("services.llm_service._get_providers")
def test_invalid_credentials_then_successful_fallback(mock_get_providers):
    """Scenario H: Groq has invalid credentials (AUTH_CONFIG_ERROR), NVIDIA succeeds."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderConfigError(
        "Groq API key is invalid or unauthorized.", category="AUTH_CONFIG_ERROR"
    )

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.return_value = _make_valid_quiz_json("Thermodynamics")

    mock_get_providers.return_value = [mock_groq, mock_nvidia]

    with patch("services.llm_service.get_or_create_student", return_value="student_001"):
        quiz = generate_quiz(
            course_code="22PH301",
            course_name="Thermodynamics",
            topic_name="Thermodynamics",
            syllabus_context="Laws of thermodynamics",
            student_id="student_001",
            concepts=["Thermodynamics Core Principles"]
        )

    assert quiz["provider_used"] == "nvidia"
    assert len(quiz["questions"]) == 10
    assert mock_groq.generate_quiz_raw.call_count == 1
    assert mock_nvidia.generate_quiz_raw.call_count == 1


# ── Scenario I: All providers failing and returning correct controlled error ──
@patch("services.llm_service._get_providers")
@patch("services.llm_service._load_local_demo_questions", return_value=None)
@patch.dict(os.environ, {"MAX_PROVIDER_RETRIES": "0"})
def test_all_four_providers_fail_controlled_error(mock_load_demo, mock_get_providers):
    """Scenario I: All four providers fail; correct controlled error raised."""
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq 503", category="SERVICE_UNAVAILABLE_503")

    mock_nvidia = MagicMock(spec=BaseAIProvider)
    mock_nvidia.name = "nvidia"
    mock_nvidia.is_configured.return_value = True
    mock_nvidia.generate_quiz_raw.side_effect = ProviderUnavailableError("NVIDIA 503", category="SERVICE_UNAVAILABLE_503")

    mock_gemini = MagicMock(spec=BaseAIProvider)
    mock_gemini.name = "gemini"
    mock_gemini.is_configured.return_value = True
    mock_gemini.generate_quiz_raw.side_effect = ProviderUnavailableError("Gemini 503", category="SERVICE_UNAVAILABLE_503")

    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.side_effect = ProviderUnavailableError("Cloudflare 503", category="SERVICE_UNAVAILABLE_503")

    mock_get_providers.return_value = [mock_groq, mock_nvidia, mock_gemini, mock_cf]

    with patch("services.llm_service.get_or_create_student", return_value="student_001"):
        with pytest.raises(AIServiceUnavailableError) as exc_info:
            generate_quiz(
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name="Unknown Topic",
                syllabus_context="Context",
                student_id="student_001",
            )

    assert "Quiz generation is temporarily unavailable. Please try again shortly." in str(exc_info.value)
    assert exc_info.value.status_code in (503, 429)
    assert mock_groq.generate_quiz_raw.call_count == 1
    assert mock_nvidia.generate_quiz_raw.call_count == 1
    assert mock_gemini.generate_quiz_raw.call_count == 1
    assert mock_cf.generate_quiz_raw.call_count == 1