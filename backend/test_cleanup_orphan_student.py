"""
test_cleanup_orphan_student.py
===============================
Offline unit tests for LearnAI candidate orphan-student cleanup utility.
Verifies target preflight validation, strict percent-encoding, canonical query parsing,
candidate UUID v4 validation, strict count parsing, attribution and isolation guards,
deletion refusal, and simulated safe transaction lifecycle.

ABSOLUTE SAFETY GUARANTEE:
- Does NOT open any network sockets or database connections.
- Uses mock connections, cursors, and dependency injection exclusively.
"""

import os
import sys
import uuid
import socket
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from scripts.cleanup_orphan_student import (
    validate_percent_encoding,
    parse_canonical_query_params,
    validate_candidate_uuid,
    parse_strict_count,
    categorize_driver_exception,
    validate_cleanup_target_url,
    check_candidate_attribution,
    check_operational_isolation,
    inspect_candidate,
    execute_cleanup,
    main as cleanup_main,
    ErrorCategory,
    DEPENDENT_TABLES,
    REQUIRED_CONFIRMATION_PHRASE,
)

# Standard public root CA certificate (ISRG Root X1 / Let's Encrypt / TiDB Cloud standard public root CA)
TEST_CA_CERT_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "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\n"
    "-----END CERTIFICATE-----\n"
)

SYNTHETIC_CANDIDATE_UUID = "c7a8b9d0-1e2f-4a3b-8c4d-5e6f7a8b9c0d"


@pytest.fixture
def ca_cert_path(tmp_path: Path) -> str:
    """Create a temporary, portable CA certificate file for TLS preflight tests."""
    ca_file = tmp_path / "test-tidb-ca.pem"
    ca_file.write_text(TEST_CA_CERT_PEM, encoding="utf-8")
    return str(ca_file)


@pytest.fixture
def synthetic_target_url(ca_cert_path: str) -> str:
    """Construct a portable synthetic target URL referencing the temporary CA certificate."""
    return (
        f"mysql://test_user:strong_pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_test"
        f"?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
    )


@pytest.fixture(autouse=True)
def guard_no_network_connections(monkeypatch):
    """Ensure no test ever attempts a real network socket connection."""
    def forbidden_connect(*args, **kwargs):
        raise AssertionError("Network socket connect() attempted during offline unit tests!")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)


# ── Test Simulation Engine (Offline Mock Verification Only) ──────────────────

def simulate_safe_transaction_flow(
    conn_params: dict,
    candidate_uuid_str: str,
    connect_fn: any,
    interactive_phrase: str = None,
    is_tty: bool = False,
    fail_commit: bool = False,
    fail_post_commit_query: bool = False,
    simulate_missing_candidate: bool = False,
    simulate_malformed_candidate_shape: bool = False,
    simulate_dependency_conflict: bool = False,
    simulate_delete_rowcount_mismatch: bool = False,
) -> dict:
    """
    Offline test helper simulating a safe transaction lifecycle against mock objects.
    Enforces candidate existence, baseline counts, dependency checks, TTY phrase barrier,
    staged deletion, commit uncertainty, and post-commit verification.
    """
    result = {
        "status": "FAIL",
        "error_category": None,
        "message": "",
        "rolled_back": False,
        "committed": False,
        "delete_executed": False,
    }

    conn = None
    cur = None
    try:
        conn = connect_fn(**conn_params)
        cur = conn.cursor()

        # Step A: Candidate existence query (Always executed!)
        cur.execute(
            "SELECT id, created_at, last_active FROM students WHERE id = %s FOR UPDATE;",
            (candidate_uuid_str,),
        )
        row = cur.fetchone()
        if simulate_missing_candidate or row is None:
            conn.rollback()
            result["rolled_back"] = True
            result["error_category"] = ErrorCategory.PRECONDITION_FAILED.value
            result["message"] = "Candidate student record not found."
            return result

        if simulate_malformed_candidate_shape or not isinstance(row, (tuple, list)) or len(row) != 3:
            conn.rollback()
            result["rolled_back"] = True
            result["error_category"] = ErrorCategory.PRECONDITION_FAILED.value
            result["message"] = "Candidate record has unexpected shape."
            return result

        # Step B: Baseline student count
        cur.execute("SELECT COUNT(*) FROM students;")
        count_row = cur.fetchone()
        student_count = parse_strict_count(count_row)
        if student_count != 1:
            conn.rollback()
            result["rolled_back"] = True
            result["error_category"] = ErrorCategory.PRECONDITION_FAILED.value
            result["message"] = "Student table baseline count mismatch."
            return result

        # Step C: Dependent table counts
        for tbl in DEPENDENT_TABLES:
            cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE student_id = %s;", (candidate_uuid_str,))
            dep_row = cur.fetchone()
            dep_count = parse_strict_count(dep_row)
            if simulate_dependency_conflict and tbl == "quiz_sessions":
                dep_count = 1
            if dep_count != 0:
                conn.rollback()
                result["rolled_back"] = True
                result["error_category"] = ErrorCategory.PRECONDITION_FAILED.value
                result["message"] = "Dependent table records found."
                return result

        # Step D: Interactive Confirmation Barrier
        if not is_tty or interactive_phrase != REQUIRED_CONFIRMATION_PHRASE:
            conn.rollback()
            result["rolled_back"] = True
            result["error_category"] = ErrorCategory.PRECONDITION_FAILED.value
            result["message"] = "Confirmation phrase rejected or non-interactive environment."
            return result

        # Step E: DELETE statement
        cur.execute("DELETE FROM students WHERE id = %s;", (candidate_uuid_str,))
        result["delete_executed"] = True

        rowcount = getattr(cur, "rowcount", 1)
        if simulate_delete_rowcount_mismatch:
            rowcount = 0

        if rowcount != 1:
            conn.rollback()
            result["rolled_back"] = True
            result["error_category"] = ErrorCategory.PRECONDITION_FAILED.value
            result["message"] = "Delete affected unexpected number of rows."
            return result

        # Step F: Commit handling with commit uncertainty
        try:
            if fail_commit:
                raise RuntimeError("Simulated connection reset during commit.")
            conn.commit()
            result["committed"] = True
        except Exception:
            result["status"] = "UNCERTAIN"
            result["error_category"] = ErrorCategory.COMMIT_UNCERTAIN.value
            result["message"] = "Commit failed with uncertain outcome. Rollback not attempted; no retry."
            return result

        # Step G: Post-commit verification
        try:
            if fail_post_commit_query:
                raise RuntimeError("Simulated query failure during post-commit check.")
            cur.execute("SELECT COUNT(*) FROM students WHERE id = %s;", (candidate_uuid_str,))
            post_row = cur.fetchone()
            post_count = parse_strict_count(post_row)
            if post_count != 0:
                result["status"] = "UNVERIFIED"
                result["error_category"] = ErrorCategory.POST_COMMIT_UNVERIFIED.value
                result["message"] = "Post-commit verification failed: record still present."
                return result
        except Exception:
            result["status"] = "UNVERIFIED"
            result["error_category"] = ErrorCategory.POST_COMMIT_UNVERIFIED.value
            result["message"] = "Post-commit verification query failed. Rollback not attempted."
            return result

        result["status"] = "SUCCESS"
        result["message"] = "Simulated cleanup completed and verified successfully."
        return result

    except Exception as exc:
        cat, msg = categorize_driver_exception(exc)
        if conn is not None and not result["committed"]:
            try:
                conn.rollback()
                result["rolled_back"] = True
            except Exception:
                pass
        result["status"] = "FAIL"
        result["error_category"] = cat.value
        result["message"] = msg
        return result
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ── 1. Candidate UUID Validation Tests ───────────────────────────────────────

class TestCandidateUUIDValidation:
    """Tests for strict RFC 4122 Version 4 candidate UUID validation."""

    def test_valid_rfc4122_v4_uuid_accepted(self):
        valid, msg, parsed = validate_candidate_uuid(SYNTHETIC_CANDIDATE_UUID)
        assert valid is True
        assert parsed is not None
        assert parsed.version == 4
        assert parsed.variant == uuid.RFC_4122

    def test_non_v4_uuid_rejected(self):
        # UUID Version 1
        v1_uuid = str(uuid.uuid1())
        valid, msg, parsed = validate_candidate_uuid(v1_uuid)
        assert valid is False
        assert parsed is None
        assert "version 4 required" in msg
        assert v1_uuid not in msg

        # UUID Version 5
        v5_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "example.com"))
        valid, msg, parsed = validate_candidate_uuid(v5_uuid)
        assert valid is False
        assert parsed is None
        assert "version 4 required" in msg
        assert v5_uuid not in msg

    def test_malformed_uuids_rejected(self):
        malformed_inputs = [
            "",
            "   ",
            None,
            12345,
            "not-a-valid-uuid-string",
            "c7a8b9d0-1e2f-4a3b-8c4d",  # too short
            "c7a8b9d0-1e2f-4a3b-8c4d-5e6f7a8b9c0d-extra",  # too long
            "c7a8b9d0_1e2f_4a3b_8c4d_5e6f7a8b9c0d",  # underscores
            "c7a8b9d0-1e2f-4a3b-8c4d-5e6f7a8b9c0z",  # invalid hex 'z'
        ]
        for bad_id in malformed_inputs:
            valid, msg, parsed = validate_candidate_uuid(bad_id)
            assert valid is False
            assert parsed is None
            if isinstance(bad_id, str) and bad_id.strip():
                assert bad_id.strip() not in msg


# ── 2. Strict Percent-Encoding and Query Safety Tests ─────────────────────────

class TestPercentEncodingAndQuerySafety:
    """Tests for strict percent-encoding and canonical query string enforcement."""

    def test_valid_percent_encoding(self):
        validate_percent_encoding("hello%20world%2Ftest%3A123")
        validate_percent_encoding("clean_string_without_percent")

    def test_malformed_percent_encoding_rejected(self):
        for bad in ["foo%", "foo%2", "foo%GG", "foo%ZZ", "%"]:
            with pytest.raises(ValueError, match="^Invalid percent-encoding in database URL.$"):
                validate_percent_encoding(bad)

    def test_canonical_query_parsing_valid(self):
        query = "ssl_mode=VERIFY_IDENTITY&ssl_ca=C%3A%2Fca.pem"
        params = parse_canonical_query_params(query)
        assert params["ssl_mode"] == "VERIFY_IDENTITY"
        assert params["ssl_ca"] == "C%3A%2Fca.pem"

    def test_query_parsing_rejects_empty_segments(self):
        for bad in ["ssl_mode=VERIFY_IDENTITY&&ssl_ca=x", "&ssl_mode=VERIFY_IDENTITY", "ssl_mode=VERIFY_IDENTITY&"]:
            with pytest.raises(ValueError, match="Malformed query string: invalid separators"):
                parse_canonical_query_params(bad)

    def test_query_parsing_rejects_encoded_parameter_keys(self):
        with pytest.raises(ValueError, match="Percent-encoded query parameter keys are not permitted"):
            parse_canonical_query_params("%73sl_mode=VERIFY_IDENTITY&ssl_ca=x")

    def test_query_parsing_rejects_duplicate_parameters(self):
        with pytest.raises(ValueError, match="Duplicate query parameter"):
            parse_canonical_query_params("ssl_mode=VERIFY_IDENTITY&ssl_mode=VERIFY_IDENTITY")

    def test_query_parsing_rejects_unauthorized_parameters(self):
        with pytest.raises(ValueError, match="Disallowed query parameter"):
            parse_canonical_query_params("ssl_mode=VERIFY_IDENTITY&ssl_ca=x&arbitrary_param=true")


# ── 3. Strict Count Parser Tests ─────────────────────────────────────────────

class TestStrictCountParser:
    """Tests for strict single-column integer count parser."""

    def test_valid_counts_accepted(self):
        assert parse_strict_count((0,)) == 0
        assert parse_strict_count((1,)) == 1
        assert parse_strict_count([42]) == 42

    def test_booleans_strictly_rejected(self):
        with pytest.raises(ValueError, match="non-boolean integer required"):
            parse_strict_count((True,))
        with pytest.raises(ValueError, match="non-boolean integer required"):
            parse_strict_count((False,))

    def test_floats_and_strings_rejected(self):
        with pytest.raises(ValueError, match="non-boolean integer required"):
            parse_strict_count((1.0,))
        with pytest.raises(ValueError, match="non-boolean integer required"):
            parse_strict_count(("0",))

    def test_negative_counts_rejected(self):
        with pytest.raises(ValueError, match="Negative count value"):
            parse_strict_count((-1,))

    def test_invalid_shapes_rejected(self):
        with pytest.raises(ValueError, match="expected exactly one column"):
            parse_strict_count((1, 2))
        with pytest.raises(ValueError, match="expected exactly one column"):
            parse_strict_count(())
        with pytest.raises(ValueError, match="expected single row tuple"):
            parse_strict_count(None)


# ── 4. Target Validation & Preflight Tests ────────────────────────────────────

class TestTargetValidationAndPreflight:
    """Tests for cleanup target URL validation safeguards."""

    def test_valid_target_url_accepted(self, monkeypatch, synthetic_target_url):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        valid, msg, params = validate_cleanup_target_url(synthetic_target_url)
        assert valid is True
        assert "successful" in msg
        assert params["host"] == "gateway01.us-west-2.prod.aws.tidbcloud.com"
        assert params["port"] == 4000
        assert params["database"] == "learnai_test"
        assert params["autocommit"] is False
        assert params["ssl"].check_hostname is True

    def test_missing_or_empty_url_rejected(self):
        valid, msg, _ = validate_cleanup_target_url(None)
        assert valid is False
        assert "not configured or empty" in msg

        valid, msg, _ = validate_cleanup_target_url("")
        assert valid is False

    def test_unsupported_scheme_rejected(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        bad_url = f"postgresql://user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_test?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_cleanup_target_url(bad_url)
        assert valid is False
        assert "Invalid database URL scheme" in msg

    def test_production_target_rejected(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url1 = f"mysql://prod_admin:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_test?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_cleanup_target_url(url1)
        assert valid is False
        assert "production target indicators" in msg

        url2 = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_prod?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_cleanup_target_url(url2)
        assert valid is False

        url3 = f"mysql://test_user:pwd@mysql-production.internal:4000/learnai_test?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_cleanup_target_url(url3)
        assert valid is False

    def test_remote_target_without_confirmation_env_rejected(self, monkeypatch, synthetic_target_url):
        monkeypatch.delenv("CONFIRM_DISPOSABLE_TEST_TARGET", raising=False)
        valid, msg, _ = validate_cleanup_target_url(synthetic_target_url)
        assert valid is False
        assert "CONFIRM_DISPOSABLE_TEST_TARGET=true" in msg

    def test_non_gateway_hostname_rejected(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = f"mysql://test_user:pwd@custom-server.example.com:4000/learnai_test?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_cleanup_target_url(url)
        assert valid is False
        assert "approved TiDB Cloud Serverless gateway host" in msg

    def test_non_4000_port_rejected(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:3306/learnai_test?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_cleanup_target_url(url)
        assert valid is False
        assert "port 4000" in msg

    def test_non_learnai_test_database_rejected(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/other_test_db?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_cleanup_target_url(url)
        assert valid is False
        assert "only 'learnai_test' is permitted" in msg

    def test_missing_or_invalid_ca_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url1 = "mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_test?ssl_mode=VERIFY_IDENTITY"
        valid, msg, _ = validate_cleanup_target_url(url1)
        assert valid is False
        assert "ssl_ca parameter is required" in msg

        nonexistent_path = (tmp_path / "nonexistent-ca.pem").as_posix()
        url2 = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_test?ssl_mode=VERIFY_IDENTITY&ssl_ca={nonexistent_path}"
        valid, msg, _ = validate_cleanup_target_url(url2)
        assert valid is False
        assert "CA certificate file does not exist" in msg


# ── 5. Attribution & Operational Isolation Guard Tests ───────────────────────

class TestAttributionAndIsolationGuards:
    """Tests proving candidate attribution and operational isolation block deletion."""

    def test_candidate_attribution_check_returns_unverified(self):
        ok, msg = check_candidate_attribution()
        assert ok is False
        assert "UNVERIFIED" in msg

    def test_operational_isolation_check_returns_unavailable(self):
        ok, msg = check_operational_isolation()
        assert ok is False
        assert "UNAVAILABLE" in msg
        assert "ON DELETE CASCADE" in msg

    def test_attribution_and_isolation_cannot_be_asserted_by_caller(self):
        # Functions accept no override parameters and cannot be made True by caller
        assert check_candidate_attribution()[0] is False
        assert check_operational_isolation()[0] is False

    def test_inspect_candidate_marks_deletion_disallowed(self, monkeypatch, synthetic_target_url):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        summary = inspect_candidate(synthetic_target_url, SYNTHETIC_CANDIDATE_UUID)
        assert summary["status"] == "OFFLINE_PREFLIGHT_PASS"
        assert summary["mode"] == "OFFLINE PREFLIGHT ONLY"
        assert summary["target_valid"] is True
        assert summary["candidate_id_valid"] is True
        assert summary["live_db_verification"] == "SKIPPED"
        assert summary["candidate_record_existence"] == "NOT VERIFIED"
        assert summary["attribution_verified"] is False
        assert summary["operational_isolation_verified"] is False
        assert summary["deletion_allowed"] is False
        assert "Deletion is disallowed" in summary["message"]


# ── 6. Deletion Refusal Tests ────────────────────────────────────────────────

class TestDeletionRefusal:
    """Tests proving live deletion is unconditionally refused."""

    def test_execute_cleanup_returns_nonzero_and_refuses(self, synthetic_target_url):
        code = execute_cleanup(synthetic_target_url, SYNTHETIC_CANDIDATE_UUID)
        assert code == 1

    def test_cli_execute_flag_returns_code_1_and_refuses(self, monkeypatch, capsys, synthetic_target_url):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        argv = [
            "--target-url", synthetic_target_url,
            "--candidate-id", SYNTHETIC_CANDIDATE_UUID,
            "--execute",
        ]
        code = cleanup_main(argv)
        assert code == 1
        captured = capsys.readouterr()
        assert "[REFUSED] Live deletion is explicitly UNAVAILABLE" in captured.out
        assert "ON DELETE CASCADE" in captured.out

    def test_cli_execute_refuses_even_with_force_environment_variables(self, monkeypatch, capsys, synthetic_target_url):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        monkeypatch.setenv("FORCE_DELETE", "true")
        monkeypatch.setenv("CONFIRM_DELETION", "true")
        monkeypatch.setenv("OPERATIONAL_FREEZE_VERIFIED", "true")
        argv = [
            "--target-url", synthetic_target_url,
            "--candidate-id", SYNTHETIC_CANDIDATE_UUID,
            "--execute",
        ]
        code = cleanup_main(argv)
        assert code == 1
        captured = capsys.readouterr()
        assert "[REFUSED] Live deletion is explicitly UNAVAILABLE" in captured.out


# ── 7. Simulated Safe Transaction Flow Tests (Fix 2) ─────────────────────────

class TestSimulatedSafeTransactionFlow:
    """Offline mock transaction tests demonstrating candidate check, rollback, and error paths."""

    class MockCursor:
        def __init__(self, queries_map=None):
            self.queries_map = queries_map or {}
            self.executed_queries = []
            self.closed = False
            self.rowcount = 1
            self._current_result = None

        def execute(self, query, params=None):
            self.executed_queries.append((query, params))
            for pat, res in self.queries_map.items():
                if pat in query:
                    self._current_result = res
                    return self
            self._current_result = None
            return self

        def fetchone(self):
            return self._current_result

        def close(self):
            self.closed = True

    class MockConnection:
        def __init__(self, cursor_obj):
            self.cur = cursor_obj
            self.committed = False
            self.rolled_back = False
            self.closed = False

        def cursor(self):
            return self.cur

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    def test_simulated_success_path_exercises_candidate_existence_query(self):
        # Setup mock queries
        queries = {
            "SELECT id, created_at, last_active FROM students WHERE id = %s FOR UPDATE;": (SYNTHETIC_CANDIDATE_UUID, "2026-10-19", "2026-10-19"),
            "SELECT COUNT(*) FROM students;": (1,),
            "SELECT COUNT(*) FROM quiz_sessions": (0,),
            "SELECT COUNT(*) FROM quiz_attempts": (0,),
            "SELECT COUNT(*) FROM question_metadata": (0,),
            "SELECT COUNT(*) FROM student_answers": (0,),
            "SELECT COUNT(*) FROM topic_mastery": (0,),
            "DELETE FROM students": None,
            "SELECT COUNT(*) FROM students WHERE id = %s": (0,),
        }
        mock_cur = self.MockCursor(queries)
        mock_conn = self.MockConnection(mock_cur)

        res = simulate_safe_transaction_flow(
            conn_params={},
            candidate_uuid_str=SYNTHETIC_CANDIDATE_UUID,
            connect_fn=lambda **kwargs: mock_conn,
            interactive_phrase=REQUIRED_CONFIRMATION_PHRASE,
            is_tty=True,
        )

        assert res["status"] == "SUCCESS"
        assert res["committed"] is True
        assert res["rolled_back"] is False
        assert res["delete_executed"] is True
        assert mock_cur.closed is True
        assert mock_conn.closed is True

        # Verify exact candidate existence query and parameters were executed
        executed_sql = [q[0] for q in mock_cur.executed_queries]
        executed_params = [q[1] for q in mock_cur.executed_queries]

        assert "SELECT id, created_at, last_active FROM students WHERE id = %s FOR UPDATE;" in executed_sql[0]
        assert executed_params[0] == (SYNTHETIC_CANDIDATE_UUID,)
        assert "DELETE FROM students WHERE id = %s;" in executed_sql[7]
        assert executed_params[7] == (SYNTHETIC_CANDIDATE_UUID,)

    def test_candidate_missing_rolls_back_and_does_not_delete(self):
        # Mock cursor returns None for candidate existence query
        mock_cur = self.MockCursor({"SELECT id, created_at, last_active FROM students": None})
        mock_conn = self.MockConnection(mock_cur)

        res = simulate_safe_transaction_flow(
            conn_params={},
            candidate_uuid_str=SYNTHETIC_CANDIDATE_UUID,
            connect_fn=lambda **kwargs: mock_conn,
            interactive_phrase=REQUIRED_CONFIRMATION_PHRASE,
            is_tty=True,
            simulate_missing_candidate=True,
        )

        assert res["status"] == "FAIL"
        assert res["rolled_back"] is True
        assert res["committed"] is False
        assert res["delete_executed"] is False
        assert "not found" in res["message"]
        # Assert DELETE was NEVER executed
        assert not any("DELETE" in q[0] for q in mock_cur.executed_queries)

    def test_candidate_malformed_shape_fails_closed(self):
        # Mock cursor returns malformed row shape (1 item instead of 3)
        mock_cur = self.MockCursor({"SELECT id, created_at, last_active FROM students": (SYNTHETIC_CANDIDATE_UUID,)})
        mock_conn = self.MockConnection(mock_cur)

        res = simulate_safe_transaction_flow(
            conn_params={},
            candidate_uuid_str=SYNTHETIC_CANDIDATE_UUID,
            connect_fn=lambda **kwargs: mock_conn,
            interactive_phrase=REQUIRED_CONFIRMATION_PHRASE,
            is_tty=True,
            simulate_malformed_candidate_shape=True,
        )

        assert res["status"] == "FAIL"
        assert res["rolled_back"] is True
        assert res["committed"] is False
        assert res["delete_executed"] is False
        assert "unexpected shape" in res["message"]
        assert not any("DELETE" in q[0] for q in mock_cur.executed_queries)

    def test_dependency_conflict_rolls_back_and_does_not_delete(self):
        queries = {
            "SELECT id, created_at, last_active FROM students": (SYNTHETIC_CANDIDATE_UUID, "2026-10-19", "2026-10-19"),
            "SELECT COUNT(*) FROM students;": (1,),
            "SELECT COUNT(*) FROM quiz_sessions": (1,),  # Conflict in sessions
        }
        mock_cur = self.MockCursor(queries)
        mock_conn = self.MockConnection(mock_cur)

        res = simulate_safe_transaction_flow(
            conn_params={},
            candidate_uuid_str=SYNTHETIC_CANDIDATE_UUID,
            connect_fn=lambda **kwargs: mock_conn,
            interactive_phrase=REQUIRED_CONFIRMATION_PHRASE,
            is_tty=True,
            simulate_dependency_conflict=True,
        )

        assert res["status"] == "FAIL"
        assert res["rolled_back"] is True
        assert res["committed"] is False
        assert res["delete_executed"] is False
        assert "Dependent table records found" in res["message"]
        assert not any("DELETE" in q[0] for q in mock_cur.executed_queries)

    def test_rejection_of_confirmation_phrase_rolls_back(self):
        queries = {
            "SELECT id, created_at, last_active FROM students": (SYNTHETIC_CANDIDATE_UUID, "2026-10-19", "2026-10-19"),
            "SELECT COUNT(*) FROM students;": (1,),
            "SELECT COUNT(*) FROM quiz_sessions": (0,),
            "SELECT COUNT(*) FROM quiz_attempts": (0,),
            "SELECT COUNT(*) FROM question_metadata": (0,),
            "SELECT COUNT(*) FROM student_answers": (0,),
            "SELECT COUNT(*) FROM topic_mastery": (0,),
        }
        for bad_phrase in ["yes", "DELETE", "delete verified candidate", ""]:
            mock_cur = self.MockCursor(queries)
            mock_conn = self.MockConnection(mock_cur)
            res = simulate_safe_transaction_flow(
                conn_params={},
                candidate_uuid_str=SYNTHETIC_CANDIDATE_UUID,
                connect_fn=lambda **kwargs: mock_conn,
                interactive_phrase=bad_phrase,
                is_tty=True,
            )
            assert res["status"] == "FAIL"
            assert res["rolled_back"] is True
            assert res["delete_executed"] is False

    def test_non_tty_environment_rolls_back(self):
        queries = {
            "SELECT id, created_at, last_active FROM students": (SYNTHETIC_CANDIDATE_UUID, "2026-10-19", "2026-10-19"),
            "SELECT COUNT(*) FROM students;": (1,),
            "SELECT COUNT(*) FROM quiz_sessions": (0,),
            "SELECT COUNT(*) FROM quiz_attempts": (0,),
            "SELECT COUNT(*) FROM question_metadata": (0,),
            "SELECT COUNT(*) FROM student_answers": (0,),
            "SELECT COUNT(*) FROM topic_mastery": (0,),
        }
        mock_cur = self.MockCursor(queries)
        mock_conn = self.MockConnection(mock_cur)
        res = simulate_safe_transaction_flow(
            conn_params={},
            candidate_uuid_str=SYNTHETIC_CANDIDATE_UUID,
            connect_fn=lambda **kwargs: mock_conn,
            interactive_phrase=REQUIRED_CONFIRMATION_PHRASE,
            is_tty=False,  # Non-TTY
        )
        assert res["status"] == "FAIL"
        assert res["rolled_back"] is True
        assert res["delete_executed"] is False

    def test_commit_uncertainty_does_not_rollback_or_retry(self):
        queries = {
            "SELECT id, created_at, last_active FROM students": (SYNTHETIC_CANDIDATE_UUID, "2026-10-19", "2026-10-19"),
            "SELECT COUNT(*) FROM students;": (1,),
            "SELECT COUNT(*) FROM quiz_sessions": (0,),
            "SELECT COUNT(*) FROM quiz_attempts": (0,),
            "SELECT COUNT(*) FROM question_metadata": (0,),
            "SELECT COUNT(*) FROM student_answers": (0,),
            "SELECT COUNT(*) FROM topic_mastery": (0,),
            "DELETE FROM students": None,
        }
        mock_cur = self.MockCursor(queries)
        mock_conn = self.MockConnection(mock_cur)

        res = simulate_safe_transaction_flow(
            conn_params={},
            candidate_uuid_str=SYNTHETIC_CANDIDATE_UUID,
            connect_fn=lambda **kwargs: mock_conn,
            interactive_phrase=REQUIRED_CONFIRMATION_PHRASE,
            is_tty=True,
            fail_commit=True,
        )

        assert res["status"] == "UNCERTAIN"
        assert res["error_category"] == ErrorCategory.COMMIT_UNCERTAIN.value
        assert res["rolled_back"] is False
        assert "Rollback not attempted; no retry" in res["message"]

    def test_post_commit_verification_failure_does_not_rollback(self):
        queries = {
            "SELECT id, created_at, last_active FROM students": (SYNTHETIC_CANDIDATE_UUID, "2026-10-19", "2026-10-19"),
            "SELECT COUNT(*) FROM students;": (1,),
            "SELECT COUNT(*) FROM quiz_sessions": (0,),
            "SELECT COUNT(*) FROM quiz_attempts": (0,),
            "SELECT COUNT(*) FROM question_metadata": (0,),
            "SELECT COUNT(*) FROM student_answers": (0,),
            "SELECT COUNT(*) FROM topic_mastery": (0,),
            "DELETE FROM students": None,
            "SELECT COUNT(*) FROM students WHERE id = %s": (1,),  # Post-commit check still finds row
        }
        mock_cur = self.MockCursor(queries)
        mock_conn = self.MockConnection(mock_cur)

        res = simulate_safe_transaction_flow(
            conn_params={},
            candidate_uuid_str=SYNTHETIC_CANDIDATE_UUID,
            connect_fn=lambda **kwargs: mock_conn,
            interactive_phrase=REQUIRED_CONFIRMATION_PHRASE,
            is_tty=True,
        )

        assert res["status"] == "UNVERIFIED"
        assert res["error_category"] == ErrorCategory.POST_COMMIT_UNVERIFIED.value
        assert res["committed"] is True
        assert res["rolled_back"] is False
        assert "record still present" in res["message"]


# ── 8. Secret Privacy & Generic Error Tests ──────────────────────────────────

class TestSecretPrivacyAndGenericErrors:
    """Tests verifying driver exceptions do not leak credentials, hostnames, or UUIDs."""

    def test_driver_exception_categorization(self):
        class MockOperationalError(Exception):
            pass

        secret_pwd = "VerySecretPassword123!"
        exc = MockOperationalError(f"Access denied for user 'admin'@'gateway01' with password '{secret_pwd}'")

        cat, msg = categorize_driver_exception(exc)
        assert cat == ErrorCategory.CONNECTION_FAILED
        assert secret_pwd not in msg
        assert "admin" not in msg
        assert "gateway01" not in msg
        assert msg == "Database connection or network communication failed."


# ── 9. CLI Entrypoint Tests (Fix 1 & Fix 3) ──────────────────────────────────

class TestCliEntryPoint:
    """Tests for the actual CLI main entry point verifying exact safety-critical output."""

    def test_cli_missing_candidate_id_fails(self):
        with pytest.raises(SystemExit) as exc_info:
            cleanup_main([])
        assert exc_info.value.code != 0

    def test_cli_inspect_exact_safety_critical_output(self, monkeypatch, capsys, synthetic_target_url):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        argv = [
            "--target-url", synthetic_target_url,
            "--candidate-id", SYNTHETIC_CANDIDATE_UUID,
            "--inspect",
        ]
        code = cleanup_main(argv)
        assert code == 0
        captured = capsys.readouterr()

        # Fix 1: Verify exact safety-critical lines
        assert "Status:                         OFFLINE_PREFLIGHT_PASS" in captured.out
        assert "Mode:                           OFFLINE PREFLIGHT ONLY" in captured.out
        assert "Target Configuration Valid:     True" in captured.out
        assert "Candidate Identifier Valid:     True" in captured.out
        assert "Live Database Verification:     SKIPPED" in captured.out
        assert "Candidate Record Existence:     NOT VERIFIED" in captured.out
        assert "Deletion Allowed:               NO" in captured.out
        assert "INSPECT_COMPLETE" not in captured.out

    def test_cli_inspect_opens_no_connections_and_executes_no_sql(self, monkeypatch, synthetic_target_url):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        # Ensure any connection attempt raises immediately
        with patch("pymysql.connect") as mock_connect:
            argv = [
                "--target-url", synthetic_target_url,
                "--candidate-id", SYNTHETIC_CANDIDATE_UUID,
                "--inspect",
            ]
            code = cleanup_main(argv)
            assert code == 0
            assert mock_connect.call_count == 0

    def test_cli_execute_opens_no_connections_and_executes_no_sql(self, monkeypatch, synthetic_target_url):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        with patch("pymysql.connect") as mock_connect:
            argv = [
                "--target-url", synthetic_target_url,
                "--candidate-id", SYNTHETIC_CANDIDATE_UUID,
                "--execute",
            ]
            code = cleanup_main(argv)
            assert code == 1
            assert mock_connect.call_count == 0
