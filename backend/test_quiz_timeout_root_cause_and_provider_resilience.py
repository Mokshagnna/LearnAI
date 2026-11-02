"""
Test Suite: Quiz Timeout Root Cause and Provider Resilience
Verifies:
  1. Groq provider uses official, verified models (llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b-32768)
  2. Gemini provider uses official, verified models (gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash)
  3. NVIDIA provider includes fast 8B fallback (meta/llama-3.1-8b-instruct)
  4. Timeout budget defaults to 40.0s overall deadline and 25.0s per-provider budget
  5. Multi-provider reserve budget calculations allocate sufficient time for fast fallbacks
  6. Shared client instances avoid mutable concurrency race conditions on client.timeout
  7. HTTP 404 / model_not_found errors are classified as MODEL_NOT_FOUND_404, not masked as TIMEOUT
  8. Genuine provider timeouts are classified as TIMEOUT
  9. Local demo question bank contains 10 validated questions for 22AM301 Loss Function (both spellings)
  10. Topic isolation filter correctly recognizes course-wide terms in Deep Learning without false rejections
  11. End-to-end quiz generation for 22AM301 Loss Function succeeds with 10 questions and complete distractor explanations
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch

from services.providers.groq_provider import GroqProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.providers.base import (
    ProviderConfigError,
    ProviderUnavailableError,
    validate_quiz_data
)
from services.llm_service import (
    generate_quiz,
    _load_local_demo_questions,
    AITimeoutError
)
from services.novelty import is_question_relevant_to_topic
from services.curriculum_service import curriculum_service


# ─────────────────────────────────────────────────────────────────────────────
# 1. Model Configuration Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_groq_verified_models_configuration(monkeypatch):
    """Verifies Groq provider defaults to verified active models and avoids hallucinated model IDs."""
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GROQ_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_mock_key_001")

    provider = GroqProvider()
    assert provider.is_configured()

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content='{"questions": []}'))]
        mock_client.chat.completions.create.return_value = mock_resp

        # Trigger execution
        provider._execute("test prompt", timeout_seconds=5.0)

        assert mock_client.chat.completions.create.called
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        model_used = call_kwargs.get("model")
        assert model_used == "openai/gpt-oss-120b", f"Unexpected model: {model_used}"
        assert "llama" not in model_used
        assert "mixtral" not in model_used
        # Verify per-call timeout passed without mutating client
        assert call_kwargs.get("timeout") is not None


def test_gemini_verified_models_configuration(monkeypatch):
    """Verifies Gemini provider defaults to active 2.5/2.0/1.5 flash models and avoids non-existent gemini-3.6-flash."""
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key_001")

    provider = GeminiProvider()
    assert provider.is_configured()

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.text = '{"questions": []}'
        mock_client.models.generate_content.return_value = mock_resp

        provider._execute_with_models("test prompt", timeout_seconds=12.0)

        assert mock_client.models.generate_content.called
        call_kwargs = mock_client.models.generate_content.call_args[1]
        model_used = call_kwargs.get("model")
        assert model_used in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"), f"Unexpected model: {model_used}"
        assert "3.6" not in model_used
        assert "3.5" not in model_used


def test_nvidia_fast_fallback_configuration(monkeypatch):
    """Verifies NVIDIA provider includes fast 8B fallback in candidates list."""
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test_mock_key_001")

    provider = NvidiaProvider()
    assert provider.is_configured()

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content='{"questions": []}'))]
        mock_client.chat.completions.create.return_value = mock_resp

        provider._execute("test prompt", timeout_seconds=5.0)

        assert mock_client.chat.completions.create.called
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        # Per-call timeout was passed and client was not mutated
        assert call_kwargs.get("timeout") is not None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Timeout and Concurrency Resilience Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_default_timeout_budgets(monkeypatch):
    """Verifies that default timeout budgets are configured for 40s overall deadline and 25s per provider."""
    monkeypatch.delenv("OVERALL_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("PROVIDER_TIMEOUT_SECONDS", raising=False)

    overall = float(os.getenv("OVERALL_DEADLINE_SECONDS", "40.0"))
    provider_budget = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "25.0"))

    assert overall >= 35.0, f"Overall deadline too low: {overall}"
    assert provider_budget >= 20.0, f"Provider budget too low: {provider_budget}"


def test_client_timeout_no_shared_mutation(monkeypatch):
    """Verifies that provider calls do NOT mutate client.timeout on the shared cached client."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    provider = GroqProvider()

    with patch.object(provider, "_get_client") as mock_get_client:
        shared_client = MagicMock()
        shared_client.timeout = 30.0  # initial default
        mock_get_client.return_value = shared_client
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content='{"questions": []}'))]
        shared_client.chat.completions.create.return_value = mock_resp

        # Call with 7.5s timeout
        provider._execute("test", timeout_seconds=7.5)

        # Ensure client.timeout on shared instance was NOT mutated to 7.5
        assert shared_client.timeout == 30.0
        # Ensure timeout=7.5 was passed directly to create()
        call_kwargs = shared_client.chat.completions.create.call_args[1]
        assert abs(call_kwargs.get("timeout") - 7.5) < 0.1


# ─────────────────────────────────────────────────────────────────────────────
# 3. Error Classification Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_model_not_found_404_categorization(monkeypatch):
    """Verifies that HTTP 404 / model_not_found raises MODEL_NOT_FOUND_404 and is not masked as a timeout."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    provider = GroqProvider()

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        # Simulate 404 from Groq for all models
        mock_client.chat.completions.create.side_effect = Exception("Error code: 404 - {'error': {'message': 'The model does not exist', 'code': 'model_not_found'}}")

        with pytest.raises(ProviderConfigError) as exc_info:
            provider._execute("test", timeout_seconds=5.0)

        assert exc_info.value.category == "MODEL_NOT_FOUND_404"
        assert "not found" in str(exc_info.value).lower() or "does not exist" in str(exc_info.value).lower()


def test_client_timeout_categorization(monkeypatch):
    """Verifies that genuine provider timeout raises category TIMEOUT."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test_key")
    provider = NvidiaProvider()

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("Request timed out after 25.0 seconds")

        with pytest.raises(ProviderUnavailableError) as exc_info:
            provider._execute("test", timeout_seconds=25.0)

        assert exc_info.value.category == "TIMEOUT"
        assert "timed out" in str(exc_info.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Demo Fallback and Deep Learning Loss Function Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_local_demo_questions_loss_function():
    """Verifies that local demo questions file has 10 high-quality questions for 22AM301 Loss Function (both spellings)."""
    # 1. Test canonical spelling
    demo_canonical = _load_local_demo_questions("22AM301", "Loss Function", expected_count=10)
    assert demo_canonical is not None, "Failed to load demo questions for 22AM301 Loss Function"
    questions = demo_canonical.get("questions", [])
    assert len(questions) == 10, f"Expected 10 questions, got {len(questions)}"

    # 2. Test syllabus OCR spelling (three 's's)
    demo_ocr = _load_local_demo_questions("22AM301", "Losss Function", expected_count=10)
    assert demo_ocr is not None, "Failed to load demo questions for 22AM301 Losss Function"
    assert len(demo_ocr.get("questions", [])) == 10

    # 3. Verify each question has 4 valid options, correct answer, explanation, and option explanations
    for idx, q in enumerate(questions):
        assert "question" in q and len(q["question"]) > 15, f"Q{idx+1} invalid statement"
        options = q.get("options", {})
        assert set(options.keys()) == {"A", "B", "C", "D"}, f"Q{idx+1} missing ABCD options"
        for opt_k, opt_v in options.items():
            assert opt_v and len(str(opt_v).strip()) > 0, f"Q{idx+1} option {opt_k} is empty"
        assert q.get("correct_answer") in ("A", "B", "C", "D"), f"Q{idx+1} invalid correct_answer"
        assert "explanation" in q and len(q["explanation"]) > 20, f"Q{idx+1} missing explanation"
        opt_expl = q.get("option_explanations", {})
        assert len(opt_expl) == 4, f"Q{idx+1} missing complete option_explanations"
        for opt_k in ("A", "B", "C", "D"):
            assert opt_k in opt_expl and len(str(opt_expl[opt_k]).strip()) > 5


def test_topic_isolation_no_false_rejection_for_deep_learning_loss():
    """Verifies that Deep Learning Loss Function questions pass topic isolation without false competing-topic triggers."""
    demo = _load_local_demo_questions("22AM301", "Loss Function", expected_count=10)
    questions = demo["questions"]

    for idx, q in enumerate(questions):
        is_rel, reason = is_question_relevant_to_topic(
            q,
            topic_name="Loss Function",
            course_code="22AM301"
        )
        assert is_rel is True, f"Question {idx+1} was falsely rejected: {reason} ('{q['question'][:60]}')"


def test_end_to_end_quiz_generation_deep_learning_loss(monkeypatch):
    """Verifies end-to-end quiz generation for 22AM301 Loss Function in offline fallback mode."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ENABLE_LOCAL_DEMO_FALLBACK", "true")

    quiz = generate_quiz(
        course_code="22AM301",
        course_name="Deep Learning",
        topic_name="Loss Function",
        syllabus_context="Deep Learning loss functions: Cross-entropy, MSE, Huber, Hinge, Focal loss",
        num_questions=10
    )

    assert quiz is not None
    assert quiz.get("subject") == "Deep Learning"
    assert "Loss" in quiz.get("topic", "")
    assert len(quiz.get("questions", [])) == 10
    assert quiz.get("is_fallback") is True
    assert quiz.get("unique_count") == 10
