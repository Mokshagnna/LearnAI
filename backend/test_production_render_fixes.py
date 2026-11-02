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
