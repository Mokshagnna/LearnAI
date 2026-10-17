"""
test_phase2_stage_f_production.py
=================================
Automated verification suite for LearnAI Stage F:
- Production persistence path precedence and directory safety
- SQLite WAL mode and connection hardening (foreign keys, busy timeout, synchronous)
- Student identity and session ownership enforcement
- Cryptographic HMAC token signing, expiration, and tampering detection
- Transactionally consistent SQLite online backup, retention, and safe restoration
"""

import os
import sqlite3
import time
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter
from services.database import (
    init_db,
    get_connection,
    get_db_path,
    get_or_create_student,
    create_quiz_session,
    save_quiz_attempt
)
from services.auth_utils import (
    sign_student_identity,
    verify_student_identity,
    validate_student_id_format,
    auth_header_for
)
from services.backup import (
    backup_database,
    restore_database,
    verify_database_integrity,
    prune_old_backups
)


SAMPLE_QUESTIONS = [
    {
        "question": f"Question {i+1}: What is the operational principle of McCulloch-Pitts neuron?",
        "options": {
            "A": "Linear threshold activation",
            "B": "Radial basis kernel",
            "C": "Dynamic routing",
            "D": "Recurrent memory cell"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Neuron Model",
        "explanation": "McCulloch-Pitts neuron uses a threshold activation rule over binary inputs."
    }
    for i in range(10)
]


@pytest.fixture
def isolated_db_client(tmp_path, monkeypatch):
    """
    Creates an isolated temporary SQLite database, initializes schema,
    resets rate limiters, and sets LEARNAI_DB_PATH so all application routes use the isolated database.
    """
    db_file = tmp_path / "stage_f_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()

    client = TestClient(app)
    return client, db_file


# ── 1. Production Persistence Path Precedence & WAL Mode ──────────────────────

class TestPersistentPathAndWALMode:

    def test_path_precedence_learnai_over_sqlite_and_default(self, tmp_path, monkeypatch):
        """LEARNAI_DB_PATH takes top precedence, followed by SQLITE_DB_PATH, then default."""
        p_learnai = tmp_path / "custom_learnai.db"
        p_sqlite = tmp_path / "custom_sqlite.db"

        # Case 1: Both set -> LEARNAI_DB_PATH wins
        monkeypatch.setenv("LEARNAI_DB_PATH", str(p_learnai))
        monkeypatch.setenv("SQLITE_DB_PATH", str(p_sqlite))
        assert get_db_path() == p_learnai.resolve()

        # Case 2: Only SQLITE_DB_PATH set
        monkeypatch.delenv("LEARNAI_DB_PATH", raising=False)
        assert get_db_path() == p_sqlite.resolve()

        # Case 3: Neither set -> default local path
        monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
        default_p = get_db_path()
        assert default_p.name == "learnai.db"
        assert "data" in str(default_p)

    def test_init_db_enables_wal_mode_and_returns_summary(self, tmp_path):
        """init_db sets PRAGMA journal_mode = WAL and returns summary dict."""
        test_db = tmp_path / "wal_test.db"
        summary = init_db(test_db)

        assert summary["status"] == "initialized"
        assert summary["path"] == str(test_db.resolve())
        # SQLite returns 'wal' for file-based databases
        assert summary["journal_mode"].lower() == "wal"

        # Verify directly via a fresh connection
        conn = get_connection(test_db)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        row = cursor.fetchone()
        assert row[0].lower() == "wal"
        conn.close()

    def test_connections_enforce_foreign_keys_and_busy_timeout(self, isolated_db_client):
        """All acquired connections enforce foreign_keys=ON and busy_timeout=5000."""
        _, db_file = isolated_db_client
        conn = get_connection(db_file)
        cursor = conn.cursor()

        cursor.execute("PRAGMA foreign_keys;")
        assert cursor.fetchone()[0] == 1

        cursor.execute("PRAGMA busy_timeout;")
        assert cursor.fetchone()[0] >= 5000

        conn.close()


# ── 2. Student Identity and Session Ownership ─────────────────────────────────

class TestStudentIdentityAndSessionOwnership:

    def test_session_retrieval_requires_student_id_for_existing_session(self, isolated_db_client):
        """Calling GET /api/quiz/session/{id} without student_id returns HTTP 400 when session exists."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_owner_1", conn=conn)
        create_quiz_session(
            session_id="sess_req_id",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Perceptrons",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        # Request with missing student_id
        res = client.get("/api/quiz/session/sess_req_id")
        assert res.status_code == 400
        assert "student_id is required" in res.json()["detail"]

        # Request with empty student_id
        res_empty = client.get("/api/quiz/session/sess_req_id?student_id=")
        assert res_empty.status_code == 400

    def test_session_retrieval_enforces_ownership_mismatch(self, isolated_db_client):
        """Calling GET /api/quiz/session/{id} with another student's ID returns HTTP 403."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_owner_2", conn=conn)
        create_quiz_session(
            session_id="sess_owner_check",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Perceptrons",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        # Unauthorized student attempts access
        res = client.get("/api/quiz/session/sess_owner_check?student_id=attacker_student")
        assert res.status_code == 403
        assert "Access denied" in res.json()["detail"]

        # Legitimate owner accesses session
        res_owner = client.get(
            f"/api/quiz/session/sess_owner_check?student_id={student_id}",
            headers=auth_header_for(student_id)
        )
        assert res_owner.status_code == 200
        assert res_owner.json()["session_id"] == "sess_owner_check"

    def test_submit_requires_student_id(self, isolated_db_client):
        """Calling POST /api/quiz/submit without student_id returns HTTP 400 when session exists."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_submit_req", conn=conn)
        create_quiz_session(
            session_id="sess_submit_check",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Perceptrons",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        # Submit without student_id
        res = client.post("/api/quiz/submit", json={
            "quiz_id": "sess_submit_check",
            "answers": ["A"] * 10
        })
        assert res.status_code == 400
        assert "student_id is required" in res.json()["detail"]

    def test_submit_enforces_ownership(self, isolated_db_client):
        """Submitting with a mismatched student_id returns HTTP 403."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_real_owner", conn=conn)
        create_quiz_session(
            session_id="sess_mismatch",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Perceptrons",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        # Attacker submits answers for another student's session
        res = client.post("/api/quiz/submit", json={
            "quiz_id": "sess_mismatch",
            "answers": ["A"] * 10,
            "student_id": "attacker_student_99"
        })
        assert res.status_code == 403
        assert "Access denied" in res.json()["detail"]

    def test_malformed_student_id_rejected_on_endpoints(self, isolated_db_client):
        """Whitespace or excessively long student IDs are rejected with HTTP 400."""
        client, _ = isolated_db_client

        # Whitespace ID
        res1 = client.get("/api/student/%20%20/dashboard")
        assert res1.status_code == 400

        res2 = client.get("/api/student/%20%20/history")
        assert res2.status_code == 400

        res3 = client.get("/api/student/%20%20/performance")
        assert res3.status_code == 400

        # Excessively long ID (>100 chars)
        long_id = "a" * 105
        res4 = client.get(f"/api/student/{long_id}/dashboard")
        assert res4.status_code == 400


# ── 3. Cryptographic HMAC Token Signing ───────────────────────────────────────

class TestCryptographicHMACAuthTokens:

    def test_sign_and_verify_valid_token(self):
        """Tokens signed with HMAC-SHA256 verify successfully."""
        student_id = "test-student-uuid-1234"
        token = sign_student_identity(student_id)

        is_valid, decoded_id, err = verify_student_identity(token)
        assert is_valid is True
        assert decoded_id == student_id
        assert err is None

    def test_tampered_token_rejected(self):
        """Tampering with token payload or signature returns False with signature mismatch."""
        student_id = "test-student-tamper"
        token = sign_student_identity(student_id)

        parts = token.split(".")
        # Tamper with signature
        tampered_sig = parts[2][:-4] + "ffff"
        tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"

        is_valid, decoded_id, err = verify_student_identity(tampered_token)
        assert is_valid is False
        assert "Signature mismatch" in err

    def test_expired_token_rejected(self):
        """Tokens with past expiration timestamp are rejected as expired."""
        student_id = "test-student-expired"
        # Negative TTL creates already-expired token
        token = sign_student_identity(student_id, ttl_seconds=-100)

        is_valid, decoded_id, err = verify_student_identity(token)
        assert is_valid is False
        assert decoded_id == student_id
        assert "Token expired" in err

    def test_malformed_token_rejected(self):
        """Malformed or empty tokens are rejected safely."""
        assert verify_student_identity("")[0] is False
        assert verify_student_identity("not.a.valid.token.parts")[0] is False
        assert verify_student_identity("singlepart")[0] is False


# ── 4. SQLite Online Backup and Restore ───────────────────────────────────────

class TestSQLiteOnlineBackupAndRestore:

    def test_backup_database_creates_consistent_snapshot(self, tmp_path):
        """backup_database creates an intact SQLite file verified by PRAGMA integrity_check."""
        db_file = tmp_path / "active_db.db"
        backup_dir = tmp_path / "backups"

        init_db(db_file)
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_backup_1", conn=conn)
        create_quiz_session(
            session_id="sess_backup_test",
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            questions=SAMPLE_QUESTIONS,
            conn=conn
        )
        conn.close()

        # Run online backup
        result = backup_database(db_path=db_file, backup_dir=backup_dir)

        assert result["status"] == "success"
        backup_path = Path(result["backup_file"])
        assert backup_path.is_file()
        assert result["size_bytes"] > 0

        # Direct integrity check
        assert verify_database_integrity(backup_path) is True

        # Verify data inside the backup snapshot
        backup_conn = sqlite3.connect(str(backup_path))
        c = backup_conn.cursor()
        c.execute("SELECT id FROM students WHERE id = ?", (student_id,))
        assert c.fetchone() is not None
        c.execute("SELECT id FROM quiz_sessions WHERE id = 'sess_backup_test'")
        assert c.fetchone() is not None
        backup_conn.close()

    def test_backup_never_overwrites_active_db(self, tmp_path):
        """Specifying active database as target backup raises ValueError."""
        db_file = tmp_path / "active_db_safe.db"
        init_db(db_file)

        # 1. Attempting to pass destination path identical to active db is rejected
        with pytest.raises(ValueError, match="cannot be the same as the active database"):
            backup_database(db_path=db_file, dest_path=db_file)

        # 2. Attempting to set backup directory to active db file itself is rejected
        with pytest.raises(ValueError, match="cannot be the active database"):
            backup_database(db_path=db_file, backup_dir=db_file)

    def test_backup_retention_policy_pruning(self, tmp_path):
        """Prunes expired backups while unconditionally preserving the latest backup."""
        backup_dir = tmp_path / "retention_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        active_db = tmp_path / "active_retention.db"
        init_db(active_db)

        # Create 3 dummy backup files with past modified times
        old_file1 = backup_dir / "learnai_backup_20260101_100000_111111.db"
        old_file2 = backup_dir / "learnai_backup_20260102_100000_222222.db"
        new_file = backup_dir / "learnai_backup_20260110_100000_333333.db"

        for f in [old_file1, old_file2, new_file]:
            f.write_text("dummy")

        # Set old timestamps (15 days ago for old1/old2, 1 hour ago for new)
        now_ts = time.time()
        os.utime(old_file1, (now_ts - 86400 * 15, now_ts - 86400 * 15))
        os.utime(old_file2, (now_ts - 86400 * 14, now_ts - 86400 * 14))
        os.utime(new_file, (now_ts - 3600, now_ts - 3600))

        # Prune with 7 days retention
        deleted = prune_old_backups(backup_dir, retention_days=7, active_db_path=active_db)

        # Both old files pruned
        assert len(deleted) == 2
        assert not old_file1.exists()
        assert not old_file2.exists()
        # Newest file preserved
        assert new_file.exists()

    def test_restore_database_with_safety_copy(self, tmp_path):
        """Restoring from a backup validates integrity and preserves .pre_restore.bak copy."""
        original_db = tmp_path / "prod_db.db"
        backup_dir = tmp_path / "backups_restore"

        init_db(original_db)
        conn = get_connection(original_db)
        student_id = get_or_create_student("stud_restore_test", conn=conn)
        conn.close()

        # Create backup
        backup_res = backup_database(db_path=original_db, backup_dir=backup_dir)
        backup_file = Path(backup_res["backup_file"])

        # Mutate the active database by adding a new attempt
        conn = get_connection(original_db)
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="New Topic After Backup",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=[],
            conn=conn
        )
        conn.close()

        # Restore from backup snapshot
        restore_res = restore_database(backup_file=backup_file, target_db_path=original_db)

        assert restore_res["status"] == "restored"
        assert verify_database_integrity(original_db) is True

        # Verify .pre_restore.bak exists
        safety_copy = original_db.with_suffix(".pre_restore.bak")
        assert safety_copy.is_file()

        # Verify restored DB state has 0 attempts (restored to initial snapshot)
        conn_restored = get_connection(original_db)
        cur = conn_restored.cursor()
        cur.execute("SELECT COUNT(*) FROM quiz_attempts")
        assert cur.fetchone()[0] == 0
        conn_restored.close()

    def test_restore_rejects_corrupted_backup(self, tmp_path):
        """Attempting to restore from a corrupt file fails and preserves active DB intact."""
        active_db = tmp_path / "active_corrupt_test.db"
        init_db(active_db)

        # Create invalid/corrupted backup file
        corrupt_backup = tmp_path / "corrupt.db"
        corrupt_backup.write_text("THIS IS NOT A VALID SQLITE DATABASE FILE")

        with pytest.raises(ValueError) as exc:
            restore_database(backup_file=corrupt_backup, target_db_path=active_db)

        assert "integrity check" in str(exc.value).lower()
        # Active database remains intact and healthy
        assert verify_database_integrity(active_db) is True
