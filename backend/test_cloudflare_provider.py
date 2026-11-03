"""
Test Suite: Cloudflare Workers AI Provider

All tests are mocked / fault-injection tests. No real Cloudflare API call is made.
They verify the provider's request construction, HTTP-status categorization, error
handling, and integration with the LearnAI orchestration / validation pipeline.

Run:  python -m pytest test_cloudflare_provider.py -v
"""
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.providers.base import (
    BaseAIProvider,
    ProviderConfigError,
    ProviderError,
    ProviderUnavailableError,
    validate_quiz_data,
    validate_single_question,
)
from services.providers.cloudflare_provider import CloudflareProvider
from services.novelty import is_question_relevant_to_topic
from test_phase1_reliability import _make_valid_quiz_json

from services.llm_service import (
    generate_quiz,
    AIServiceUnavailableError,
    AITimeoutError,
    AIConfigError,
    _get_providers,
)


# ── helpers ─────────────────────────────────────────────────────────────

def _mock_http_response(status_code=200, text="", json_data=None, raise_json_error=False):
    """Build a MagicMock resembling httpx.Response with controllable status/body."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    if raise_json_error:
        response.json = MagicMock(side_effect=ValueError("No JSON could be decoded"))
    else:
        if json_data is None:
            json_data = {
                "result": {"response": '{"questions": []}'},
                "success": True,
                "errors": [],
                "messages": [],
            }
        response.json = MagicMock(return_value=json_data)
    return response


def _patch_httpx_client(response):
    """Patch httpx.Client so its context manager yields a client whose .post returns `response`."""
    mock_client = MagicMock()
    mock_client.post.return_value = response
    cm = MagicMock()
    cm.__enter__.return_value = mock_client
    cm.__exit__.return_value = False
    return patch("services.providers.cloudflare_provider.httpx.Client", return_value=cm)


def _env_creds():
    return {
        "CLOUDFLARE_ENABLED": "true",
        "CLOUDFLARE_ACCOUNT_ID": "test_account_123",
        "CLOUDFLARE_API_TOKEN": "test_token_123",
        "CLOUDFLARE_MODEL": "@cf/meta/llama-3.1-8b-instruct",
    }


# ── 1. Successful Cloudflare API response ───────────────────────────────

def test_successful_cloudflare_quiz_response(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    assert provider.is_configured()

    quiz_json = _make_valid_quiz_json("Python Functions", "Python Programming")
    response = _mock_http_response(
        200,
        json_data={"result": {"response": quiz_json}, "success": True, "errors": [], "messages": []},
    )
    with _patch_httpx_client(response):
        result = provider._execute("Generate a quiz...", timeout_seconds=10.0)

    assert result == quiz_json


# ── 2. Missing credentials ──────────────────────────────────────────────

def test_missing_credentials_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDFLARE_ENABLED", "true")
    provider = CloudflareProvider()
    assert provider.is_configured() is False

    with pytest.raises(ProviderConfigError) as exc:
        provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "MISSING_CREDENTIALS"


def test_cloudflare_disabled_cleanly(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ENABLED", "false")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    provider = CloudflareProvider()
    assert provider.is_configured() is False


# ── 3. Invalid token (401) ──────────────────────────────────────────────

def test_invalid_token_401(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    response = _mock_http_response(401, text="Unauthorized")
    with _patch_httpx_client(response):
        with pytest.raises(ProviderConfigError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "AUTH_CONFIG_ERROR"


# ── 4. Insufficient token permissions (403) ─────────────────────────────

def test_insufficient_permissions_403(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    response = _mock_http_response(403, text="Forbidden: insufficient permissions")
    with _patch_httpx_client(response):
        with pytest.raises(ProviderConfigError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "AUTH_CONFIG_ERROR"


# ── 5. Invalid Account ID (404) ─────────────────────────────────────────

def test_invalid_account_id_404(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    response = _mock_http_response(
        404, json_data={"success": False, "errors": [{"message": "Account not found"}], "messages": []}
    )
    with _patch_httpx_client(response):
        with pytest.raises(ProviderConfigError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "MODEL_NOT_FOUND_404"


# ── 6. Unsupported model (404) ──────────────────────────────────────────

def test_unsupported_model_404(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    response = _mock_http_response(
        404, json_data={"success": False, "errors": [{"message": "Model not found"}], "messages": []}
    )
    with _patch_httpx_client(response):
        with pytest.raises(ProviderConfigError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "MODEL_NOT_FOUND_404"


# ── 7. HTTP 429 rate limit ──────────────────────────────────────────────

def test_rate_limit_429(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    response = _mock_http_response(
        429, json_data={"success": False, "errors": [{"message": "Too many requests"}], "messages": []}
    )
    with _patch_httpx_client(response):
        with pytest.raises(ProviderUnavailableError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "RATE_LIMIT_429"


# ── 8. Daily quota exhaustion (200 envelope with quota error) ───────────

def test_daily_quota_exhaustion_envelope(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    # Workers AI can return HTTP 200 with success=false and a quota error in errors[]
    response = _mock_http_response(
        200,
        json_data={
            "success": False,
            "errors": [{"message": "Daily quota exceeded for Workers AI"}],
            "messages": [],
        },
    )
    with _patch_httpx_client(response):
        with pytest.raises(ProviderUnavailableError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "RATE_LIMIT_429"


# ── 9. HTTP 5xx ─────────────────────────────────────────────────────────

def test_server_error_5xx(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    response = _mock_http_response(500, text="Internal Server Error")
    with _patch_httpx_client(response):
        with pytest.raises(ProviderUnavailableError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "SERVER_ERROR_500"


# ── 10. Network failure ─────────────────────────────────────────────────

def test_network_failure(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    cm = MagicMock()
    cm.__enter__.side_effect = httpx.ConnectError("DNS resolution failed")
    cm.__exit__.return_value = False
    with patch("services.providers.cloudflare_provider.httpx.Client", return_value=cm):
        with pytest.raises(ProviderUnavailableError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "NETWORK_ERROR"


# ── 11. Timeout ─────────────────────────────────────────────────────────

def test_timeout(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    cm = MagicMock()
    cm.__enter__.side_effect = httpx.TimeoutException("Read timed out")
    cm.__exit__.return_value = False
    with patch("services.providers.cloudflare_provider.httpx.Client", return_value=cm):
        with pytest.raises(ProviderUnavailableError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "TIMEOUT"


# ── 12. Malformed JSON response ─────────────────────────────────────────

def test_malformed_json_response(monkeypatch):
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)
    provider = CloudflareProvider()
    response = _mock_http_response(200, text="not json at all", raise_json_error=True)
    with _patch_httpx_client(response):
        with pytest.raises(ProviderError) as exc:
            provider._execute("prompt", timeout_seconds=10.0)
    assert exc.value.category == "MALFORMED_JSON_PARSE_ERROR"


# ── 13. Invalid quiz structure rejected ─────────────────────────────────

def test_invalid_quiz_structure_rejected():
    with pytest.raises(ValueError):
        validate_quiz_data(
            {"questions": []},
            course_name="Python Programming",
            topic_name="Python Functions",
            min_questions=1,
        )


# ── 14. Duplicate questions removed ─────────────────────────────────────

def test_duplicate_questions_removed():
    q = {
        "question": "What is a Python function?",
        "options": {"A": "A callable block", "B": "A variable", "C": "A loop", "D": "A class"},
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "def",
        "explanation": "A function is a callable block of code.",
    }
    result = validate_quiz_data(
        {"questions": [q, dict(q)]},
        course_name="Python Programming",
        topic_name="Python Functions",
        min_questions=1,
    )
    assert len(result["questions"]) == 1  # duplicate collapsed


# ── 15. Irrelevant questions rejected ───────────────────────────────────

def test_irrelevant_question_rejected():
    on_topic = {
        "question": "How do you define a function in Python?",
        "options": {"A": "def name():", "B": "function name()", "C": "create name()", "D": "new name()"},
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "def",
        "explanation": "Use the def keyword.",
    }
    off_topic = {
        "question": "What is the capital of France?",
        "options": {"A": "Paris", "B": "London", "C": "Berlin", "D": "Madrid"},
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "geography",
        "explanation": "Paris is the capital of France.",
    }
    assert is_question_relevant_to_topic(on_topic, "Python Functions")[0] is True
    assert is_question_relevant_to_topic(off_topic, "Python Functions")[0] is False


# ── 16. Incorrect answer format rejected ────────────────────────────────

def test_incorrect_answer_format_rejected():
    q = {
        "question": "How do you define a function in Python?",
        "options": {"A": "def name():", "B": "function name()", "C": "create name()", "D": "new name()"},
        "correct_answer": "Z",  # not A-D
        "difficulty": "Easy",
        "concept": "def",
        "explanation": "Use the def keyword.",
    }
    assert validate_single_question(q, topic_name="Python Functions") is None


# ── 17. Provider failover: Cloudflare fails, Groq succeeds ──────────────

def test_cloudflare_failover_to_groq(monkeypatch):
    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Cloudflare 503", category="SERVICE_UNAVAILABLE_503"
    )

    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.return_value = _make_valid_quiz_json("Python Functions")

    with patch("services.llm_service._get_providers", return_value=[mock_cf, mock_groq]):
        with patch("services.llm_service.get_or_create_student", return_value="student_cf"):
            quiz = generate_quiz(
                course_code="22CS101",
                course_name="Python Programming",
                topic_name="Python Functions",
                syllabus_context="def keyword, parameters, return values",
                student_id="student_cf",
                num_questions=5,
            )

    assert quiz["provider_used"] == "groq"
    assert len(quiz["questions"]) == 5
    assert mock_cf.generate_quiz_raw.call_count == 1
    assert mock_groq.generate_quiz_raw.call_count == 1


# ── 18. All providers unavailable ───────────────────────────────────────

def test_all_providers_unavailable_raises(monkeypatch):
    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Cloudflare 503", category="SERVICE_UNAVAILABLE_503"
    )
    mock_groq = MagicMock(spec=BaseAIProvider)
    mock_groq.name = "groq"
    mock_groq.is_configured.return_value = True
    mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Groq 503", category="SERVICE_UNAVAILABLE_503"
    )

    with patch("services.llm_service._get_providers", return_value=[mock_cf, mock_groq]):
        with patch("services.llm_service.get_or_create_student", return_value="student_all"):
            with pytest.raises((AIServiceUnavailableError, AITimeoutError)):
                generate_quiz(
                    course_code="22CS101",
                    course_name="Python Programming",
                    topic_name="Python Functions",
                    syllabus_context="def keyword, parameters, return values",
                    student_id="student_all",
                    num_questions=5,
                )


# ── 19. No static or cached fallback ────────────────────────────────────

def test_no_static_or_cached_fallback(monkeypatch):
    """When every provider fails, no demo/curated questions are substituted."""
    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.side_effect = ProviderUnavailableError(
        "Cloudflare failed", category="SERVICE_UNAVAILABLE_503"
    )

    with patch("services.llm_service._get_providers", return_value=[mock_cf]):
        with patch("services.llm_service.get_or_create_student", return_value="student_nfb"):
            with patch("services.llm_service._load_local_demo_questions", return_value=None):
                with pytest.raises((AIServiceUnavailableError, AITimeoutError, AIConfigError)):
                    generate_quiz(
                        course_code="22CS101",
                        course_name="Python Programming",
                        topic_name="Python Functions",
                        syllabus_context="def keyword",
                        student_id="student_nfb",
                        num_questions=5,
                    )


# ── 20. Provider attribution (logs + provider_used) ─────────────────────

def test_cloudflare_provider_attribution(monkeypatch, caplog):
    monkeypatch.setenv("PROVIDER_ORDER", "cloudflare")
    for k, v in _env_creds().items():
        monkeypatch.setenv(k, v)

    provider = CloudflareProvider()
    quiz_json = _make_valid_quiz_json("Python Functions", "Python Programming")
    response = _mock_http_response(
        200,
        json_data={"result": {"response": quiz_json}, "success": True, "errors": [], "messages": []},
    )
    with _patch_httpx_client(response):
        with caplog.at_level("INFO", logger="learnai.providers.cloudflare"):
            result = provider._execute("Generate a quiz...", timeout_seconds=10.0)

    assert result == quiz_json
    assert any("CLOUDFLARE SUCCESS" in rec.getMessage() for rec in caplog.records)


def test_quiz_provider_used_is_cloudflare(monkeypatch):
    """End-to-end: a Cloudflare-generated quiz is attributed to Cloudflare."""
    mock_cf = MagicMock(spec=BaseAIProvider)
    mock_cf.name = "cloudflare"
    mock_cf.is_configured.return_value = True
    mock_cf.generate_quiz_raw.return_value = _make_valid_quiz_json("Python Functions")

    with patch("services.llm_service._get_providers", return_value=[mock_cf]):
        with patch("services.llm_service.get_or_create_student", return_value="student_attr"):
            quiz = generate_quiz(
                course_code="22CS101",
                course_name="Python Programming",
                topic_name="Python Functions",
                syllabus_context="def keyword, parameters, return values",
                student_id="student_attr",
                num_questions=5,
            )

    assert quiz["provider_used"] == "cloudflare"
    assert len(quiz["questions"]) == 5
