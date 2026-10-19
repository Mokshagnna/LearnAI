"""
test_smoke_test_tidb_tls.py
===========================
Offline unit tests for standalone read-only TiDB TLS smoke test script.
Verifies all validation guards, secret masking, connection closing, and mock execution.
Does NOT open any real database connections or network sockets.
"""

import os
import pytest
from pathlib import Path
from scripts.smoke_test_tidb_tls import (
    validate_smoke_test_url,
    run_smoke_test,
    sanitize_error_message,
    main as smoke_main,
)

CA_PATH = r"C:\Users\moksh\LearnAI-secrets\tidb-ca.pem"
VALID_TIDB_URL = (
    f"mysql://test_user:strong_pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_test"
    f"?ssl_mode=VERIFY_IDENTITY&ssl_ca={CA_PATH}"
)


class TestSmokeTestValidationSafeguards:
    """Offline unit tests for smoke test URL validation."""

    def test_missing_or_empty_url_rejected(self):
        valid, msg, _ = validate_smoke_test_url(None)
        assert valid is False
        assert "not set or empty" in msg

        valid, msg, _ = validate_smoke_test_url("")
        assert valid is False

        valid, msg, _ = validate_smoke_test_url("   ")
        assert valid is False

    def test_non_mysql_scheme_rejected(self):
        url = f"postgresql://app:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode=VERIFY_IDENTITY&ssl_ca={CA_PATH}"
        valid, msg, _ = validate_smoke_test_url(url)
        assert valid is False
        assert "Invalid scheme" in msg

    def test_missing_ssl_mode_rejected(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_ca={CA_PATH}"
        valid, msg, _ = validate_smoke_test_url(url)
        assert valid is False
        assert "strictly requires ssl_mode=VERIFY_IDENTITY" in msg

    def test_weaker_ssl_modes_rejected(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        for mode in ("REQUIRED", "VERIFY_CA", "DISABLED", "PREFERRED"):
            url = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode={mode}&ssl_ca={CA_PATH}"
            valid, msg, _ = validate_smoke_test_url(url)
            assert valid is False
            assert "strictly requires ssl_mode=VERIFY_IDENTITY" in msg

    def test_missing_or_invalid_ca_file_rejected(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        # Missing ssl_ca parameter
        url1 = "mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode=VERIFY_IDENTITY"
        valid, msg, _ = validate_smoke_test_url(url1)
        assert valid is False
        assert "ssl_ca parameter is missing" in msg

        # Non-existent CA file
        url2 = "mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode=VERIFY_IDENTITY&ssl_ca=C:/nonexistent/ca.pem"
        valid, msg, _ = validate_smoke_test_url(url2)
        assert valid is False
        assert "CA certificate file not found" in msg

    def test_production_targets_rejected(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        # Prod username
        url1 = f"mysql://prod_admin:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode=VERIFY_IDENTITY&ssl_ca={CA_PATH}"
        valid, msg, _ = validate_smoke_test_url(url1)
        assert valid is False
        assert "production target indicators" in msg

        # Prod database name
        url2 = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_prod?ssl_mode=VERIFY_IDENTITY&ssl_ca={CA_PATH}"
        valid, msg, _ = validate_smoke_test_url(url2)
        assert valid is False
        assert "production target indicators" in msg

        # Unrelated prod host
        url3 = f"mysql://test_user:pwd@mysql-production.example.com:3306/test?ssl_mode=VERIFY_IDENTITY&ssl_ca={CA_PATH}"
        valid, msg, _ = validate_smoke_test_url(url3)
        assert valid is False
        assert "production target indicators" in msg

    def test_remote_target_without_confirmation_rejected(self, monkeypatch):
        monkeypatch.delenv("CONFIRM_DISPOSABLE_TEST_TARGET", raising=False)
        valid, msg, _ = validate_smoke_test_url(VALID_TIDB_URL)
        assert valid is False
        assert "CONFIRM_DISPOSABLE_TEST_TARGET=true" in msg

    def test_valid_tidb_url_accepted(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        valid, msg, params = validate_smoke_test_url(VALID_TIDB_URL)
        assert valid is True
        assert msg == "Validation successful."
        assert params["host"] == "gateway01.us-west-2.prod.aws.tidbcloud.com"
        assert params["port"] == 4000
        assert params["database"] == "learnai_test"
        assert params["ssl"].check_hostname is True


class TestSmokeTestExecutionAndMocking:
    """Offline unit tests for smoke test execution flow using mock connections."""

    def test_run_smoke_test_mock_success(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")

        class MockCursor:
            def __init__(self):
                self.closed = False
                self.executed_queries = []

            def execute(self, query):
                self.executed_queries.append(query)

            def fetchone(self):
                return ("8.0.11-TiDB-v8.1.0",)

            def close(self):
                self.closed = True

        class MockConnection:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.closed = False
                self.cur = MockCursor()

            def cursor(self):
                return self.cur

            def close(self):
                self.closed = True

        mock_conn = None

        def mock_connect(**kwargs):
            nonlocal mock_conn
            mock_conn = MockConnection(**kwargs)
            return mock_conn

        result = run_smoke_test(url=VALID_TIDB_URL, connect_fn=mock_connect)

        assert result["status"] == "PASS"
        assert result["server_version"] == "8.0.11-TiDB-v8.1.0"
        assert result["host"] == "gateway01.us-west-2.prod.aws.tidbcloud.com"
        assert result["port"] == 4000
        assert result["database"] == "learnai_test"
        assert result["tls_mode"] == "VERIFY_IDENTITY"
        assert result["error"] is None

        # Verify only SELECT VERSION() was executed
        assert mock_conn.cur.executed_queries == ["SELECT VERSION();"]
        # Verify cursor and connection were properly closed in finally blocks
        assert mock_conn.cur.closed is True
        assert mock_conn.closed is True

    def test_run_smoke_test_mock_failure_sanitizes_password(self, monkeypatch):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        secret_password = "SuperSecret_P@ssword123!"
        target_url = (
            f"mysql://test_user:{secret_password}@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_test"
            f"?ssl_mode=VERIFY_IDENTITY&ssl_ca={CA_PATH}"
        )

        class FailingConnection:
            def __init__(self, **kwargs):
                self.closed = False

            def cursor(self):
                raise RuntimeError(f"Connection failed for user test_user with password {secret_password} at port 4000")

            def close(self):
                self.closed = True

        failing_conn = None

        def mock_connect(**kwargs):
            nonlocal failing_conn
            failing_conn = FailingConnection(**kwargs)
            return failing_conn

        result = run_smoke_test(url=target_url, connect_fn=mock_connect)

        assert result["status"] == "FAIL"
        assert result["server_version"] is None
        assert secret_password not in result["error"]
        assert "******" in result["error"]
        assert failing_conn.closed is True

    def test_smoke_main_cli_returns_nonzero_when_url_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_MYSQL_DATABASE_URL", raising=False)
        ret = smoke_main()
        assert ret == 1
