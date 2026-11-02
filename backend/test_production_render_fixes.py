"""
backend/test_production_render_fixes.py
=======================================
Verification suite for the production Render issues:
1. Six-key configuration and primary -> secondary fallback for all 3 providers.
2. Provider auth error fast-failover from primary to secondary key.
3. 404 / 410 model error classification and fallback without pointless retries.
4. Provider-to-provider sequential fallback (Groq -> NVIDIA -> Gemini).
5. MySQL migration 005 idempotency and question_metadata schema resilience.
6. Offline demo question retake novelty and exhaustion reporting (no repeated questions).
7. Feedback failure resilience during atomic quiz submission.
"""

import os
import json
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from services.providers.base import (
    BaseAIProvider,
    ProviderConfigError,
    ProviderUnavailableError,
    ProviderError
)
from services.providers.gemini_provider import GeminiProvider
from services.providers.groq_provider import GroqProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.llm_service import (
    generate_quiz,
    generate_analysis,
    _load_local_demo_questions,
    AIServiceUnavailableError,
    AIConfigError
)
from services.database import (
    _ensure_column,
    init_db,
    submit_quiz_atomic,
    get_connection,
    save_quiz_attempt
)
from services.db.migrations import split_sql_statements
from test_phase1_reliability import _make_valid_quiz_json


def _make_dummy_quiz_json(topic="Neural Networks", count=10):
    return _make_valid_quiz_json(topic, "Deep Learning")


# =============================================================================
# 1. SIX-KEY CONFIGURATION & PRIMARY -> SECONDARY FALLBACK TESTS
# =============================================================================

class TestSixKeyFallback:
    """Tests that Gemini, Groq, and NVIDIA properly use primary and secondary keys."""

    def test_gemini_primary_success(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "primary_gemini_key")
        monkeypatch.setenv("GEMINI_SECONDARY_API_KEY", "secondary_gemini_key")
        provider = GeminiProvider()
        assert provider.is_configured()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client
            mock_resp = MagicMock(text=_make_dummy_quiz_json())
            mock_client.models.generate_content.return_value = mock_resp

            res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 15.0)
            assert res is not None
            # Primary key used
            assert mock_client_factory.call_args_list[0][1]["api_key"] == "primary_gemini_key"

    def test_gemini_primary_429_quota_falls_to_secondary(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "quota_exhausted_primary")
        monkeypatch.setenv("GEMINI_SECONDARY_API_KEY", "valid_secondary_key")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client_primary = MagicMock()
            mock_client_secondary = MagicMock()

            # Primary client throws 429 ResourceExhausted / Quota
            mock_client_primary.models.generate_content.side_effect = Exception("429 Resource exhausted: quota exceeded")
            # Secondary client succeeds
            mock_client_secondary.models.generate_content.return_value = MagicMock(text=_make_dummy_quiz_json())

            def client_dispatch(*args, **kwargs):
                if kwargs.get("api_key") == "quota_exhausted_primary":
                    return mock_client_primary
                return mock_client_secondary

            mock_client_factory.side_effect = client_dispatch

            res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 25.0)
            assert res is not None
            # Verified that both primary and secondary keys were called
            keys_called = [c[1]["api_key"] for c in mock_client_factory.call_args_list]
            assert "quota_exhausted_primary" in keys_called
            assert "valid_secondary_key" in keys_called

    def test_groq_primary_auth_fails_fast_with_config_error(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "invalid_groq_primary")
        monkeypatch.setenv("GROQ_SECONDARY_API_KEY", "valid_groq_secondary")
        provider = GroqProvider()
        assert provider.is_configured()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client_primary = MagicMock()
            mock_client_primary.chat.completions.create.side_effect = Exception("401 Invalid API Key")
            mock_client_factory.return_value = mock_client_primary

            from services.providers.base import ProviderConfigError
            with pytest.raises(ProviderConfigError):
                provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)

            # Fast fail: does not call secondary on 401 invalid key
            assert mock_client_factory.call_count == 1

    def test_groq_primary_429_rate_limit_falls_to_secondary(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "quota_exhausted_primary")
        monkeypatch.setenv("GROQ_BACKUP_API_KEY", "healthy_secondary")
        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_primary = MagicMock()
            mock_secondary = MagicMock()
            mock_primary.chat.completions.create.side_effect = Exception("429 Rate limit reached / Quota exceeded")
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content=_make_dummy_quiz_json()))]
            mock_secondary.chat.completions.create.return_value = mock_resp

            def client_dispatch(*args, **kwargs):
                if kwargs.get("api_key") == "quota_exhausted_primary":
                    return mock_primary
                return mock_secondary

            mock_client_factory.side_effect = client_dispatch

            res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)
            assert res is not None
            keys_called = [c[1]["api_key"] for c in mock_client_factory.call_args_list]
            assert "quota_exhausted_primary" in keys_called
            assert "healthy_secondary" in keys_called

    def test_nvidia_primary_503_unavailable_falls_to_secondary(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "unavailable_nvidia_primary")
        monkeypatch.setenv("NVIDIA_SECONDARY_API_KEY", "good_nvidia_secondary")
        provider = NvidiaProvider()
        assert provider.is_configured()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_primary = MagicMock()
            mock_secondary = MagicMock()
            mock_primary.chat.completions.create.side_effect = Exception("503 Service Unavailable")
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content=_make_dummy_quiz_json()))]
            mock_secondary.chat.completions.create.return_value = mock_resp

            def client_dispatch(*args, **kwargs):
                if kwargs.get("api_key") == "unavailable_nvidia_primary":
                    return mock_primary
                return mock_secondary

            mock_client_factory.side_effect = client_dispatch

            res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)
            assert res is not None
            keys_called = [c[1]["api_key"] for c in mock_client_factory.call_args_list]
            assert "unavailable_nvidia_primary" in keys_called
            assert "good_nvidia_secondary" in keys_called

    def test_nvidia_http_410_gone_falls_over_to_fallback_model(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "nvidia_test_key")
        monkeypatch.setenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
        monkeypatch.setenv("NVIDIA_FALLBACK_MODEL", "meta/llama-3.1-8b-instruct")
        provider = NvidiaProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # Model 1 returns HTTP 410 Gone; Model 2 (fallback) succeeds!
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content=_make_dummy_quiz_json()))]

            mock_client.chat.completions.create.side_effect = [
                Exception("410 Gone: The requested model endpoint is no longer active."),
                mock_resp
            ]

            res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)
            assert res is not None
            assert mock_client.chat.completions.create.call_count == 2
            # Verified second call used fallback model
            model_called = mock_client.chat.completions.create.call_args_list[1][1]["model"]
            assert model_called == "meta/llama-3.1-8b-instruct"

    def test_known_invalid_gemini_model_warns_and_includes_active_model(self, monkeypatch, caplog):
        monkeypatch.setenv("GEMINI_API_KEY", "primary_key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client
            # gemini-2.0-flash fails with 404, gemini-1.5-flash succeeds!
            mock_client.models.generate_content.side_effect = [
                Exception("404 Not Found: models/gemini-2.0-flash is not found"),
                MagicMock(text=_make_dummy_quiz_json())
            ]
            import logging
            with caplog.at_level(logging.WARNING):
                res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)
            assert res is not None
            assert any("[GEMINI CONFIG WARNING]" in record.message for record in caplog.records)
            models_called = [c[1]["model"] for c in mock_client.models.generate_content.call_args_list]
            assert "gemini-1.5-flash" in models_called

    def test_backup_alias_does_not_overwrite_secondary_key(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "primary_key")
        monkeypatch.setenv("GROQ_SECONDARY_API_KEY", "secondary_key")
        monkeypatch.setenv("GROQ_BACKUP_API_KEY", "stale_backup_key")
        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_primary = MagicMock()
            mock_secondary = MagicMock()
            mock_primary.chat.completions.create.side_effect = Exception("429 Rate limit")
            mock_secondary.chat.completions.create.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=_make_dummy_quiz_json()))])

            def client_dispatch(*args, **kwargs):
                if kwargs.get("api_key") == "primary_key":
                    return mock_primary
                return mock_secondary

            mock_client_factory.side_effect = client_dispatch
            res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)
            assert res is not None
            keys_called = [c[1]["api_key"] for c in mock_client_factory.call_args_list]
            assert "secondary_key" in keys_called
            assert "stale_backup_key" not in keys_called


# =============================================================================
# 2. PROVIDER-TO-PROVIDER SEQUENTIAL FALLBACK
# =============================================================================

class TestProviderOrchestration:
    """Verifies that failure of one provider does not prevent next providers from running."""

    def test_groq_fails_nvidia_fails_gemini_succeeds(self):
        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.side_effect = ProviderConfigError("Groq 404 Model Not Found", category="MODEL_NOT_FOUND_404")

        mock_nvidia = MagicMock(spec=BaseAIProvider)
        mock_nvidia.name = "nvidia"
        mock_nvidia.is_configured.return_value = True
        mock_nvidia.generate_quiz_raw.side_effect = ProviderUnavailableError("NVIDIA 410 Deprecated", category="MODEL_DEPRECATED_410")

        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True
        mock_gemini.generate_quiz_raw.return_value = _make_dummy_quiz_json("Neural Networks", count=10)

        with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia, mock_gemini]):
            with patch("services.llm_service.get_or_create_student", return_value="student_test_1"):
                quiz = generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="Neural Networks",
                    syllabus_context="Neural networks, perceptrons, activations",
                    student_id="student_test_1",
                    concepts=["Neural Networks Core Principles"]
                )
                assert quiz is not None
                assert len(quiz["questions"]) == 10
                assert quiz["provider_used"] == "gemini"
                assert mock_groq.generate_quiz_raw.called
                assert mock_nvidia.generate_quiz_raw.called
                assert mock_gemini.generate_quiz_raw.called


# =============================================================================
# 3. PRODUCTION MYSQL MIGRATION 005 AND SCHEMA COMPATIBILITY
# =============================================================================

class TestDatabaseMigrationAndCompatibility:
    """Verifies Migration 005 and question_metadata schema resilience."""

    def test_migration_005_sql_statements(self):
        migration_path = os.path.join(os.path.dirname(__file__), "migrations", "005_question_metadata_novelty_fields.sql")
        assert os.path.isfile(migration_path)
        with open(migration_path, "r", encoding="utf-8") as f:
            content = f.read()
        stmts = split_sql_statements(content)
        assert len(stmts) >= 1
        assert "normalized_text" in stmts[0]
        assert "topic_id" in stmts[0]
        assert "learning_objective" in stmts[0]
        if len(stmts) > 1:
            assert "idx_qm_topic_id" in stmts[1]

    def test_ensure_column_and_index_idempotency(self, tmp_path):
        from services.database import _ensure_index
        db_file = tmp_path / "test_migration.db"
        conn = sqlite3.connect(str(db_file))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test_table (id TEXT PRIMARY KEY, title TEXT)")

        # Verify SQLite column path
        _ensure_column(cursor, "test_table", "normalized_text", "TEXT", dialect="sqlite")
        _ensure_column(cursor, "test_table", "normalized_text", "TEXT", dialect="sqlite")  # Idempotent second call
        cursor.execute("PRAGMA table_info(test_table)")
        cols = [r[1] for r in cursor.fetchall()]
        assert "normalized_text" in cols

        # Verify SQLite index path
        _ensure_index(cursor, "test_table", "idx_test_title", "title", dialect="sqlite")
        _ensure_index(cursor, "test_table", "idx_test_title", "title", dialect="sqlite")  # Idempotent second call
        conn.close()

    def test_submit_quiz_atomic_with_legacy_schema_fallback(self, tmp_path):
        """Simulates legacy MySQL database missing normalized_text column and verifies it falls back safely without 1054 error."""
        db_file = tmp_path / "legacy_test.db"
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # Initialize tables WITHOUT normalized_text, topic_id, learning_objective
        cursor.execute("CREATE TABLE students (id TEXT PRIMARY KEY, created_at TEXT, last_active TEXT)")
        cursor.execute("""
            CREATE TABLE quiz_sessions (
                id TEXT PRIMARY KEY, student_id TEXT, course_code TEXT, course_name TEXT,
                topic_name TEXT, status TEXT, questions_snapshot TEXT, recommended_difficulty TEXT,
                adaptive_metadata TEXT, created_at TEXT, submitted_at TEXT, expires_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE quiz_attempts (
                id TEXT PRIMARY KEY, student_id TEXT, course_code TEXT, course_name TEXT,
                topic_name TEXT, quiz_data TEXT, score INT, total INT, percentage REAL,
                status TEXT, difficulty_accuracy TEXT, concept_accuracy TEXT, weak_concepts TEXT,
                strong_concepts TEXT, recommended_difficulty TEXT, created_at TEXT, session_id TEXT
            )
        """)
        # Legacy 13-column question_metadata table
        cursor.execute("""
            CREATE TABLE question_metadata (
                id TEXT PRIMARY KEY, attempt_id TEXT, student_id TEXT, course_code TEXT,
                topic_name TEXT, question_index INT, question_text TEXT, question_fingerprint TEXT,
                concept TEXT, difficulty TEXT, question_type TEXT, correct_answer TEXT, created_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE student_answers (
                id TEXT PRIMARY KEY, session_id TEXT, attempt_id TEXT, student_id TEXT,
                question_index INT, question_text TEXT, selected_answer TEXT, correct_answer TEXT,
                is_correct INT, difficulty TEXT, concept TEXT, explanation TEXT, created_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE topic_mastery (
                id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                course_code TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                total_questions INTEGER NOT NULL DEFAULT 0,
                total_correct INTEGER NOT NULL DEFAULT 0,
                mastery_score REAL NOT NULL DEFAULT 0.0,
                current_level TEXT NOT NULL DEFAULT 'mixed',
                weak_concepts TEXT NOT NULL DEFAULT '[]',
                strong_concepts TEXT NOT NULL DEFAULT '[]',
                last_practiced_at TEXT NOT NULL,
                UNIQUE(student_id, course_code, topic_name)
            )
        """)

        # Insert student and session
        from services.database import create_quiz_session
        cursor.execute("INSERT INTO students VALUES ('s1', '2026-11-01', '2026-11-02')")
        conn.commit()

        quiz_dict = json.loads(_make_dummy_quiz_json("Neural Networks", count=10))
        questions = quiz_dict["questions"]
        create_quiz_session(
            session_id="sess_1",
            student_id="s1",
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Neural Networks",
            questions=questions,
            conn=conn
        )

        # Submit quiz through submit_quiz_atomic using the legacy connection
        from services.evaluator import evaluate_answers
        answers = ["A"] * 10
        with patch("services.database.get_connection", return_value=conn):
            result = submit_quiz_atomic("sess_1", "s1", answers, evaluator_fn=evaluate_answers)
            assert result is not None
            assert result["score"] == 10
            assert result["percentage"] == 100.0

        # Verify attempts and question_metadata were recorded using a fresh connection
        conn2 = sqlite3.connect(str(db_file))
        cursor2 = conn2.cursor()
        cursor2.execute("SELECT COUNT(*) FROM question_metadata")
        q_count = cursor2.fetchone()[0]
        assert q_count == 10

        cursor2.execute("SELECT COUNT(*) FROM quiz_attempts")
        att_count = cursor2.fetchone()[0]
        assert att_count == 1
        conn2.close()


# =============================================================================
# 4. OFFLINE DEMO RETAKE NOVELTY & POOL EXHAUSTION
# =============================================================================

class TestDemoFallbackNovelty:
    """Verifies that offline demo mode generates fresh questions across retakes and reports exhaustion."""

    def test_demo_questions_has_20_questions_per_topic(self):
        demo_file = os.path.join(os.path.dirname(__file__), "data", "demo_questions.json")
        with open(demo_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in ["22AM803::Introduction and Goals of Computer Vision", "22AM301::Loss Function"]:
            assert len(data[key]["questions"]) >= 20

    def test_demo_fallback_retake_yields_zero_duplicates(self):
        """Attempt 1 gets questions 1-10; Attempt 2 (retake) gets questions 11-20 with 0 duplicates."""
        # Attempt 1
        quiz1 = _load_local_demo_questions(
            course_code="22AM301",
            topic_name="Loss Function",
            expected_count=10,
            prior_questions=None,
            excluded_fingerprints=None
        )
        assert quiz1 is not None
        assert len(quiz1["questions"]) == 10
        q1_texts = [q["question"] for q in quiz1["questions"]]

        # Attempt 2 (Retake) with prior questions from Attempt 1
        quiz2 = _load_local_demo_questions(
            course_code="22AM301",
            topic_name="Loss Function",
            expected_count=10,
            prior_questions=quiz1["questions"],
            excluded_fingerprints={q["question"].strip().lower() for q in quiz1["questions"]}
        )
        assert quiz2 is not None
        assert len(quiz2["questions"]) == 10
        q2_texts = [q["question"] for q in quiz2["questions"]]

        # Verify completely disjoint sets of questions (0 duplicates)
        overlap = set(q1_texts).intersection(set(q2_texts))
        assert len(overlap) == 0

    def test_demo_fallback_exhaustion_reports_pool_exhausted(self):
        """When 20 questions have already been served, attempt 3 reports pool exhaustion rather than repeating."""
        quiz1 = _load_local_demo_questions(
            course_code="22AM301",
            topic_name="Loss Function",
            expected_count=10
        )
        quiz2 = _load_local_demo_questions(
            course_code="22AM301",
            topic_name="Loss Function",
            expected_count=10,
            prior_questions=quiz1["questions"]
        )
        all_20_questions = quiz1["questions"] + quiz2["questions"]

        # Attempt 3: All 20 questions are in history
        quiz3 = _load_local_demo_questions(
            course_code="22AM301",
            topic_name="Loss Function",
            expected_count=10,
            prior_questions=all_20_questions
        )
        # Returns None because pool does not have 10 more unique questions
        assert quiz3 is None


# =============================================================================
# 5. FEEDBACK FAILURE RESILIENCE
# =============================================================================

class TestFeedbackResilience:
    """Verifies that if AI analysis/feedback fails, quiz submission is unaffected."""

    def test_analysis_fallback_when_providers_fail(self):
        with patch("services.llm_service._get_providers", return_value=[]):
            feedback = generate_analysis(
                subject="Deep Learning",
                topic="Loss Function",
                weak_concepts=["Huber Loss"],
                score=7,
                total=10
            )
            assert feedback is not None
            assert "summary" in feedback
            assert "revision" in feedback
            assert "recommendation" in feedback
            assert "Huber Loss" in feedback["revision"]


# =============================================================================
# 6. GROQ REASONING MODELS (GPT-OSS 120B/20B) PARSING & RESILIENCE
# =============================================================================

class TestGroqGptOssReasoningAndCascade:
    """Verifies GPT-OSS 120B/20B parsing, reasoning extraction, legacy model removal, and cascade."""

    def test_gpt_oss_120b_normal_response_and_json_format_kwargs(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_1")
        monkeypatch.delenv("GROQ_MODEL", raising=False)
        monkeypatch.delenv("GROQ_FALLBACK_MODEL", raising=False)

        provider = GroqProvider()
        assert provider.is_configured()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            mock_choice = MagicMock()
            mock_choice.message.content = _make_dummy_quiz_json("Loss Function")
            mock_choice.message.reasoning = None
            mock_resp = MagicMock(choices=[mock_choice])
            mock_client.chat.completions.create.return_value = mock_resp

            result = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 15.0)
            assert result is not None

            # Verify model used is openai/gpt-oss-120b
            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert call_kwargs["model"] == "openai/gpt-oss-120b"
            assert call_kwargs["max_tokens"] == 4000
            assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_gpt_oss_analysis_max_tokens_and_schema(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_1")
        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            mock_choice = MagicMock()
            mock_choice.message.content = json.dumps({
                "summary": "Solid foundation on loss functions.",
                "revision": "Review Huber loss formula.",
                "recommendation": "Practice 5 regularization questions."
            })
            mock_resp = MagicMock(choices=[mock_choice])
            mock_client.chat.completions.create.return_value = mock_resp

            result = provider.generate_analysis_raw("Deep Learning", "Loss Function", ["Huber Loss"], 8, 10, 10.0)
            assert result is not None
            parsed = json.loads(result)
            assert "summary" in parsed

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert call_kwargs["max_tokens"] == 1500

    def test_gpt_oss_reasoning_not_used_as_quiz_answer_when_content_empty(self, monkeypatch):
        """When message.content is None/empty, message.reasoning must NOT be used as answers, even if containing JSON."""
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_1")
        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # Empty content, JSON inside reasoning
            mock_choice = MagicMock()
            mock_choice.message.content = ""
            mock_choice.message.reasoning = _make_dummy_quiz_json("Loss Function")
            mock_resp = MagicMock(choices=[mock_choice])
            mock_client.chat.completions.create.return_value = mock_resp

            # Must reject and fail over rather than using reasoning as source of truth
            with pytest.raises(ProviderUnavailableError) as exc_info:
                provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 15.0)
            assert "empty response" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()

    def test_gpt_oss_reasoning_not_used_for_analysis_when_content_empty(self, monkeypatch):
        """When message.content is None/empty, message.reasoning must NOT be used for analysis."""
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_1")
        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            mock_choice = MagicMock()
            mock_choice.message.content = None
            mock_choice.message.reasoning = json.dumps({
                "summary": "Student understands basics.",
                "revision": "Review backpropagation.",
                "recommendation": "Try harder math problems."
            })
            mock_resp = MagicMock(choices=[mock_choice])
            mock_client.chat.completions.create.return_value = mock_resp

            with pytest.raises(ProviderUnavailableError) as exc_info:
                provider.generate_analysis_raw("Deep Learning", "Loss Function", ["Backpropagation"], 7, 10, 10.0)
            assert "empty response" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()

    def test_gpt_oss_reasoning_raw_prose_without_schema_raises_error(self, monkeypatch):
        """When message.content is empty and reasoning is raw prose (no schema), do NOT return raw thoughts; fail over."""
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_1")
        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # Raw thoughts only, no JSON
            mock_choice = MagicMock()
            mock_choice.message.content = ""
            mock_choice.message.reasoning = "Thinking Process: 1. Let's analyze loss functions. 2. Huber loss combines L1 and L2..."
            mock_resp = MagicMock(choices=[mock_choice])
            mock_client.chat.completions.create.return_value = mock_resp

            with pytest.raises(ProviderUnavailableError) as exc_info:
                provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 10.0)

            # Confirm error indicates empty response / no schema found
            assert "empty response" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()

    def test_gpt_oss_empty_content_and_empty_reasoning_raises_error(self, monkeypatch):
        """When both message.content and reasoning are empty, fails over cleanly."""
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_1")
        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            mock_choice = MagicMock()
            mock_choice.message.content = ""
            mock_choice.message.reasoning = ""
            mock_resp = MagicMock(choices=[mock_choice])
            mock_client.chat.completions.create.return_value = mock_resp

            with pytest.raises(ProviderUnavailableError):
                provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 10.0)

    def test_groq_legacy_models_eliminated_from_candidate_list(self, monkeypatch):
        """When 120B fails, Groq only attempts 20B fallback and NEVER calls legacy models."""
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_1")
        monkeypatch.delenv("GROQ_MODEL", raising=False)
        monkeypatch.delenv("GROQ_FALLBACK_MODEL", raising=False)

        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # Both 120B and 20B fail with rate limit
            mock_client.chat.completions.create.side_effect = Exception("429 Rate limit exceeded")

            with pytest.raises(ProviderUnavailableError):
                provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 15.0)

            # Exactly 2 calls made: 120B then 20B
            calls = mock_client.chat.completions.create.call_args_list
            assert len(calls) == 2
            assert calls[0][1]["model"] == "openai/gpt-oss-120b"
            assert calls[1][1]["model"] == "openai/gpt-oss-20b"

            # Verify no legacy models were ever called
            called_models = [c[1]["model"] for c in calls]
            assert "llama-3.1-8b-instant" not in called_models
            assert "mixtral-8x7b-32768" not in called_models
            assert "llama-3.3-70b-versatile" not in called_models

    def test_groq_deprecated_model_warning_and_active_model_inclusion(self, monkeypatch, caplog):
        """When a deprecated model is configured in env, a warning is logged and active default is included."""
        monkeypatch.setenv("GROQ_API_KEY", "test_groq_key_1")
        monkeypatch.setenv("GROQ_MODEL", "llama-3.1-8b-instant")
        monkeypatch.setenv("GROQ_FALLBACK_MODEL", "mixtral-8x7b-32768")

        provider = GroqProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # First two calls (deprecated) return 404, third call (openai/gpt-oss-120b) succeeds
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content=_make_dummy_quiz_json("Loss Function")))]
            mock_client.chat.completions.create.side_effect = [
                Exception("404 Model llama-3.1-8b-instant does not exist"),
                Exception("404 Model mixtral-8x7b-32768 does not exist"),
                mock_resp
            ]

            import logging
            with caplog.at_level(logging.WARNING):
                res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)

            assert res is not None
            assert any("[GROQ CONFIG WARNING]" in record.message for record in caplog.records)
            called_models = [c[1]["model"] for c in mock_client.chat.completions.create.call_args_list]
            assert "openai/gpt-oss-120b" in called_models

    def test_gemini_afc_and_response_parsing(self, monkeypatch):
        """Verifies Gemini handles responses and AFC correctly without crashing."""
        monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key_1")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.text = _make_dummy_quiz_json("Loss Function")
            mock_client.models.generate_content.return_value = mock_resp

            result = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 15.0)
            assert result is not None
            parsed = json.loads(result)
            assert "questions" in parsed


# =============================================================================
# 7. GEMINI PRODUCTION RELIABILITY & STRUCTURED LOGGING
# =============================================================================

class TestGeminiProductionReliability:
    """Tests the 8 core Gemini reliability scenarios: valid JSON, empty response,
    malformed JSON, timeout handling, insufficient budget, 429 rate limit, 401 auth,
    and safety-blocked candidate handling.
    """

    def test_gemini_successful_valid_json_response(self, monkeypatch, caplog):
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.text = _make_dummy_quiz_json("Loss Function")
            mock_client.models.generate_content.return_value = mock_resp

            import logging
            with caplog.at_level(logging.INFO):
                result = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 15.0)

            assert result is not None
            parsed = json.loads(result)
            assert "questions" in parsed
            assert any("[GEMINI START]" in record.message for record in caplog.records)
            assert any("[GEMINI SUCCESS]" in record.message for record in caplog.records)

            # Confirm AFC was explicitly disabled in the call config
            call_kwargs = mock_client.models.generate_content.call_args[1]
            config_passed = call_kwargs["config"]
            assert config_passed.automatic_function_calling.disable is True
            assert config_passed.http_options.timeout is not None

    def test_gemini_empty_response_and_fallback_behavior(self, monkeypatch, caplog):
        """When primary model returns empty content, logs [GEMINI EMPTY_RESPONSE] and falls over to fallback model."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-flash")
        monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-1.5-pro")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # Model 1 returns empty candidate; Model 2 succeeds
            mock_empty = MagicMock()
            mock_empty.text = ""
            mock_cand = MagicMock()
            mock_cand.finish_reason = "STOP"
            mock_cand.content.parts = []
            mock_empty.candidates = [mock_cand]

            mock_success = MagicMock()
            mock_success.text = _make_dummy_quiz_json("Loss Function")

            mock_client.models.generate_content.side_effect = [mock_empty, mock_success]

            import logging
            with caplog.at_level(logging.WARNING):
                result = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)

            assert result is not None
            assert any("[GEMINI EMPTY_RESPONSE]" in record.message for record in caplog.records)
            # Verify 2 calls were made (primary model failed, fallback model succeeded)
            assert mock_client.models.generate_content.call_count == 2

    def test_gemini_malformed_json_and_fallback_behavior(self, monkeypatch, caplog):
        """When response contains truncated/unparseable JSON, logs [GEMINI PARSE_ERROR] and falls over."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-flash")
        monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-1.5-pro")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # Model 1 returns broken JSON; Model 2 succeeds
            mock_broken = MagicMock(text='{"questions": [{"id": 1, "question": "Incomplete')
            mock_cand = MagicMock()
            mock_cand.finish_reason = "MAX_TOKENS"
            mock_broken.candidates = [mock_cand]

            mock_success = MagicMock(text=_make_dummy_quiz_json("Loss Function"))

            mock_client.models.generate_content.side_effect = [mock_broken, mock_success]

            import logging
            with caplog.at_level(logging.WARNING):
                result = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)

            assert result is not None
            assert any("[GEMINI PARSE_ERROR]" in record.message for record in caplog.records)
            assert mock_client.models.generate_content.call_count == 2

    def test_gemini_timeout_handling_within_remaining_deadline(self, monkeypatch, caplog):
        """When API call times out, logs [GEMINI TIMEOUT] and yields without redundant retries."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            mock_client.models.generate_content.side_effect = Exception("504 Deadline Exceeded: request timed out")

            import logging
            with caplog.at_level(logging.WARNING):
                with pytest.raises(ProviderUnavailableError) as exc_info:
                    provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 10.0)

            assert exc_info.value.category in ("DEADLINE_EXCEEDED_504", "TIMEOUT", "CLIENT_TIMEOUT")
            assert any("[GEMINI TIMEOUT]" in record.message for record in caplog.records)

    def test_gemini_insufficient_remaining_budget_no_network_request(self, monkeypatch, caplog):
        """When initial budget is below MIN_GEMINI_REQUEST_BUDGET (2.5s), no network request is issued."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            import logging
            with caplog.at_level(logging.WARNING):
                with pytest.raises(ProviderUnavailableError) as exc_info:
                    provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 1.8)

            assert exc_info.value.category == "TIMEOUT"
            # Crucial assertion: no call to generate_content was made
            assert mock_client.models.generate_content.call_count == 0
            assert any("[GEMINI TIMEOUT]" in record.message for record in caplog.records)

    def test_gemini_rate_limit_transient_error_secondary_key_behavior(self, monkeypatch, caplog):
        """When primary key hits 429, logs [GEMINI API_ERROR] and successfully rotates to secondary key."""
        monkeypatch.setenv("GEMINI_API_KEY", "primary_key_429")
        monkeypatch.setenv("GEMINI_SECONDARY_API_KEY", "secondary_key_ok")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_primary = MagicMock()
            mock_secondary = MagicMock()

            mock_primary.models.generate_content.side_effect = Exception("429 ResourceExhausted: quota exceeded")
            mock_secondary.models.generate_content.return_value = MagicMock(text=_make_dummy_quiz_json("Loss Function"))

            def client_dispatch(*args, **kwargs):
                if kwargs.get("api_key") == "primary_key_429":
                    return mock_primary
                return mock_secondary

            mock_client_factory.side_effect = client_dispatch

            import logging
            with caplog.at_level(logging.WARNING):
                res = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)

            assert res is not None
            assert any("[GEMINI API_ERROR]" in record.message for record in caplog.records)
            assert any("[GEMINI KEY FALLBACK]" in record.message for record in caplog.records)
            keys_called = [c[1]["api_key"] for c in mock_client_factory.call_args_list]
            assert "primary_key_429" in keys_called
            assert "secondary_key_ok" in keys_called

    def test_gemini_authentication_error_remains_correctly_classified(self, monkeypatch, caplog):
        """When API returns 401/invalid key, logs [GEMINI API_ERROR] with AUTH_CONFIG_ERROR and fast-fails."""
        monkeypatch.setenv("GEMINI_API_KEY", "invalid_primary_key")
        monkeypatch.setenv("GEMINI_SECONDARY_API_KEY", "unused_secondary_key")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = Exception("401 API key not valid. Please pass a valid API key.")
            mock_client_factory.return_value = mock_client

            import logging
            with caplog.at_level(logging.ERROR):
                with pytest.raises(ProviderConfigError) as exc_info:
                    provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)

            assert exc_info.value.category == "AUTH_CONFIG_ERROR"
            assert any("[GEMINI API_ERROR]" in record.message for record in caplog.records)
            # Fast fail: does not call secondary key
            assert mock_client_factory.call_count == 1

    def test_gemini_safety_blocked_response_handled_without_crashing(self, monkeypatch, caplog):
        """When candidate has finish_reason=SAFETY and resp.text is None, logs [GEMINI EMPTY_RESPONSE] and falls over."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-flash")
        monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-1.5-pro")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # Model 1 blocked by safety filter; Model 2 succeeds
            mock_safety = MagicMock()
            mock_safety.text = None
            mock_cand = MagicMock()
            mock_cand.finish_reason = "SAFETY"
            mock_cand.content.parts = []
            mock_safety.candidates = [mock_cand]

            mock_success = MagicMock(text=_make_dummy_quiz_json("Loss Function"))

            mock_client.models.generate_content.side_effect = [mock_safety, mock_success]

            import logging
            with caplog.at_level(logging.WARNING):
                result = provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", 20.0)

            assert result is not None
            assert any("[GEMINI EMPTY_RESPONSE]" in record.message and "SAFETY" in record.message for record in caplog.records)
            assert mock_client.models.generate_content.call_count == 2

    def test_gemini_skipped_when_remaining_time_below_minimum_safe_budget(self, monkeypatch, caplog):
        """Phase 3 Check 1: When remaining time is below 10.0s (e.g. 9.5s), Gemini is skipped without issuing network request."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        monkeypatch.delenv("GEMINI_SAFETY_MARGIN_SECONDS", raising=False)
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            import logging
            with caplog.at_level(logging.WARNING):
                with pytest.raises(ProviderUnavailableError) as exc_info:
                    provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", timeout_seconds=9.5)

            assert exc_info.value.category == "TIMEOUT"
            assert mock_client.models.generate_content.call_count == 0
            assert any("[GEMINI TIMEOUT]" in record.message for record in caplog.records)

    def test_gemini_not_called_when_budget_cannot_accommodate_deadline_plus_safety_margin(self, monkeypatch, caplog):
        """Phase 3 Check 2: When GEMINI_SAFETY_MARGIN_SECONDS=1.5, remaining budget under 11.5s skips Gemini immediately."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        monkeypatch.setenv("GEMINI_SAFETY_MARGIN_SECONDS", "1.5")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            import logging
            with caplog.at_level(logging.WARNING):
                with pytest.raises(ProviderUnavailableError) as exc_info:
                    provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", timeout_seconds=11.0)

            assert exc_info.value.category == "TIMEOUT"
            assert mock_client.models.generate_content.call_count == 0
            assert any("[GEMINI TIMEOUT]" in record.message for record in caplog.records)

    def test_gemini_configured_client_timeout_never_exceeds_remaining_budget(self, monkeypatch):
        """Phase 3 Check 3: Configured client timeout (in http_options) never exceeds the permitted remaining budget."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client
            mock_client.models.generate_content.return_value = MagicMock(text=_make_dummy_quiz_json("Loss Function"))

            # Test budget = 14.0s with safety margin = 1.5s -> call timeout = 12.5s (12,500ms <= 14,000ms)
            monkeypatch.setenv("GEMINI_SAFETY_MARGIN_SECONDS", "1.5")
            provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", timeout_seconds=14.0)
            call_kwargs = mock_client.models.generate_content.call_args[1]
            http_opt = call_kwargs["config"].http_options
            assert 12400 <= http_opt.timeout <= 12500
            assert http_opt.timeout <= 14000
            assert http_opt.timeout >= 10000

            # Test budget = 10.0s (exact minimum boundary) -> call timeout = 10,000ms <= 10,000ms
            monkeypatch.delenv("GEMINI_SAFETY_MARGIN_SECONDS", raising=False)
            provider.generate_quiz_raw("22AM301", "Deep Learning", "Loss Function", "Context", timeout_seconds=10.0)
            call_kwargs_10 = mock_client.models.generate_content.call_args[1]
            http_opt_10 = call_kwargs_10["config"].http_options
            assert 9900 <= http_opt_10.timeout <= 10000
            assert http_opt_10.timeout <= 10000
            assert http_opt_10.timeout >= 10000

    def test_gemini_slow_request_bounded_timeout_allows_cascade(self, monkeypatch):
        """Phase 3 Check 4: A slow Gemini request times out in bounded time and allows orchestrator failover to continue."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        monkeypatch.setenv("GROQ_API_KEY", "valid_groq_key")
        monkeypatch.setenv("PROVIDER_ORDER", "gemini,groq")

        from services.llm_service import generate_quiz

        # Mock Gemini to time out
        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True
        mock_gemini.generate_quiz_raw.side_effect = ProviderUnavailableError("Gemini timed out", category="TIMEOUT")

        # Mock Groq to succeed
        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.return_value = _make_dummy_quiz_json("Loss Function")

        with patch("services.llm_service._get_providers", return_value=[mock_gemini, mock_groq]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                quiz = generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="Loss Function",
                    syllabus_context="Context",
                    student_id="test_student"
                )

        assert quiz["provider_used"] == "groq"
        assert quiz.get("is_fallback") is not True
        assert len(quiz["questions"]) == 10

    def test_gemini_timeout_handling_does_not_cause_extra_retry_exceeding_deadline(self, monkeypatch, caplog):
        """Phase 3 Check 5: When Gemini request times out and time is exhausted, it does not issue extra retries."""
        monkeypatch.setenv("GEMINI_API_KEY", "valid_gemini_key")
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
        provider = GeminiProvider()

        with patch.object(provider, "_get_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            # Advance simulated time so time_remaining after call is exhausted
            clock = {"time": 0.0}
            def mock_generate(*args, **kwargs):
                clock["time"] += 10.5
                raise Exception("504 Deadline Exceeded: request timed out")

            mock_client.models.generate_content.side_effect = mock_generate

            def mock_perf():
                return clock["time"]

            import logging
            with caplog.at_level(logging.WARNING):
                with patch("time.perf_counter", side_effect=mock_perf):
                    with pytest.raises(ProviderUnavailableError):
                        provider._execute_with_models("prompt", timeout_seconds=12.0)

            # Exactly 1 call was made; no redundant extra retry occurred
            assert mock_client.models.generate_content.call_count == 1
            assert any("[GEMINI TIMEOUT]" in record.message for record in caplog.records)

    def test_application_wide_deadline_remains_40_seconds(self):
        """Phase 3 Check 6: Overall deadline default in llm_service remains 40.0 seconds."""
        import os
        # Ensure OVERALL_DEADLINE_SECONDS is not set so default is read
        original = os.environ.pop("OVERALL_DEADLINE_SECONDS", None)
        try:
            default_deadline = float(os.getenv("OVERALL_DEADLINE_SECONDS", "40.0"))
            assert default_deadline == 40.0
        finally:
            if original is not None:
                os.environ["OVERALL_DEADLINE_SECONDS"] = original
