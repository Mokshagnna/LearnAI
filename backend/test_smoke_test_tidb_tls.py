"""
test_smoke_test_tidb_tls.py
===========================
Offline unit tests for standalone read-only TiDB TLS smoke test script.
Verifies all validation guards, secret masking, connection closing, and mock execution.
Does NOT open any real database connections or network sockets.
"""

import os
import socket
import pytest
from pathlib import Path
from scripts.smoke_test_tidb_tls import (
    validate_smoke_test_url,
    run_smoke_test,
    sanitize_error_message,
    main as smoke_main,
)

# Standard public root CA certificate (ISRG Root X1 / Let's Encrypt / TiDB Cloud standard public root CA)
TEST_CA_CERT_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "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\n"
    "-----END CERTIFICATE-----\n"
)


@pytest.fixture
def ca_cert_path(tmp_path: Path) -> str:
    """Create a temporary, portable CA certificate file for TLS smoke tests."""
    ca_file = tmp_path / "test-tidb-ca.pem"
    ca_file.write_text(TEST_CA_CERT_PEM, encoding="utf-8")
    return str(ca_file)


@pytest.fixture
def valid_tidb_url(ca_cert_path: str) -> str:
    """Construct a portable valid synthetic TiDB URL referencing the temporary CA certificate."""
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

    def test_non_mysql_scheme_rejected(self, ca_cert_path):
        url = f"postgresql://app:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_smoke_test_url(url)
        assert valid is False
        assert "Invalid scheme" in msg

    def test_missing_ssl_mode_rejected(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        url = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_smoke_test_url(url)
        assert valid is False
        assert "strictly requires ssl_mode=VERIFY_IDENTITY" in msg

    def test_weaker_ssl_modes_rejected(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        for mode in ("REQUIRED", "VERIFY_CA", "DISABLED", "PREFERRED"):
            url = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode={mode}&ssl_ca={ca_cert_path}"
            valid, msg, _ = validate_smoke_test_url(url)
            assert valid is False
            assert "strictly requires ssl_mode=VERIFY_IDENTITY" in msg

    def test_missing_or_invalid_ca_file_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        # Missing ssl_ca parameter
        url1 = "mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode=VERIFY_IDENTITY"
        valid, msg, _ = validate_smoke_test_url(url1)
        assert valid is False
        assert "ssl_ca parameter is missing" in msg

        # Non-existent CA file
        nonexistent_path = (tmp_path / "nonexistent-ca.pem").as_posix()
        url2 = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode=VERIFY_IDENTITY&ssl_ca={nonexistent_path}"
        valid, msg, _ = validate_smoke_test_url(url2)
        assert valid is False
        assert "CA certificate file not found" in msg

    def test_production_targets_rejected(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        # Prod username
        url1 = f"mysql://prod_admin:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/test?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_smoke_test_url(url1)
        assert valid is False
        assert "production target indicators" in msg

        # Prod database name
        url2 = f"mysql://test_user:pwd@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_prod?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_smoke_test_url(url2)
        assert valid is False
        assert "production target indicators" in msg

        # Unrelated prod host
        url3 = f"mysql://test_user:pwd@mysql-production.example.com:3306/test?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
        valid, msg, _ = validate_smoke_test_url(url3)
        assert valid is False
        assert "production target indicators" in msg

    def test_remote_target_without_confirmation_rejected(self, monkeypatch, valid_tidb_url):
        monkeypatch.delenv("CONFIRM_DISPOSABLE_TEST_TARGET", raising=False)
        valid, msg, _ = validate_smoke_test_url(valid_tidb_url)
        assert valid is False
        assert "CONFIRM_DISPOSABLE_TEST_TARGET=true" in msg

    def test_valid_tidb_url_accepted(self, monkeypatch, valid_tidb_url):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        valid, msg, params = validate_smoke_test_url(valid_tidb_url)
        assert valid is True
        assert msg == "Validation successful."
        assert params["host"] == "gateway01.us-west-2.prod.aws.tidbcloud.com"
        assert params["port"] == 4000
        assert params["database"] == "learnai_test"
        assert params["ssl"].check_hostname is True


class TestSmokeTestExecutionAndMocking:
    """Offline unit tests for smoke test execution flow using mock connections."""

    def test_run_smoke_test_mock_success(self, monkeypatch, valid_tidb_url):
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

        result = run_smoke_test(url=valid_tidb_url, connect_fn=mock_connect)

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

    def test_run_smoke_test_mock_failure_sanitizes_password(self, monkeypatch, ca_cert_path):
        monkeypatch.setenv("CONFIRM_DISPOSABLE_TEST_TARGET", "true")
        secret_password = "SuperSecret_P@ssword123!"
        target_url = (
            f"mysql://test_user:{secret_password}@gateway01.us-west-2.prod.aws.tidbcloud.com:4000/learnai_test"
            f"?ssl_mode=VERIFY_IDENTITY&ssl_ca={ca_cert_path}"
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
