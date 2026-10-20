"""
test_phase2_stage_q_production_guards.py
========================================
Comprehensive automated tests for LearnAI Stage Q5 & Q5.1 production fail-fast safeguards:
1. Production startup with missing DATABASE_URL (and rejection of SUPABASE_DB_URL alias).
2. Production startup with missing SESSION_SECRET_KEY (and rejection of LEARNAI_SECRET_KEY alias).
3. Production startup with known development fallback secrets (rejected safely without credential leakage).
4. Valid production configuration.
5. Local development and existing test fixtures (SQLite and backward-compatible aliases preserved).
6. PostgreSQL connection failure without silent SQLite fallback.
7. Unbypassable Render runtime environment detection and precedence policy across all 5 combinations:
   - RENDER=true alone -> Production
   - RENDER=true + ENVIRONMENT=development -> Production (Render guard cannot be bypassed)
   - RENDER=true + APP_ENV=test -> Production (Render guard cannot be bypassed)
   - ENVIRONMENT=production + APP_ENV=development -> Production
   - ENVIRONMENT=test + APP_ENV=production -> Non-production (canonical ENVIRONMENT takes precedence)
8. Invalid database URI scheme detection in production.
9. Explicit SQLite path rejection in production mode.
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.db.base import (
    DatabaseConfig,
    DatabaseConnectionError,
    is_production_mode,
    validate_production_db_config,
)
from services.auth_utils import (
    get_session_secret,
    validate_production_auth_config,
    is_known_insecure_secret,
    KNOWN_INSECURE_SECRETS,
)
from services.database import (
    get_connection,
    init_db,
    reset_db_adapter,
)
from main import validate_production_environment


@pytest.fixture(autouse=True)
def clean_adapter_state():
    """Ensure adapter state is reset before and after every test."""
    reset_db_adapter()
    yield
    reset_db_adapter()


# ── 1. Production Startup with Missing DATABASE_URL ────────────────────────────

class TestMissingDatabaseUrlInProduction:

    def test_production_startup_fails_fast_when_database_url_missing(self, monkeypatch):
        """Production startup must raise RuntimeError when DATABASE_URL is missing."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secure-random-hmac-key-998877665544332211")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        err_msg = str(exc_info.value)
        assert "DATABASE_URL environment variable is required" in err_msg
        assert "silent SQLite fallback is prohibited" in err_msg

    def test_production_db_validator_fails_when_database_url_blank(self, monkeypatch):
        """validate_production_db_config must reject empty/whitespace DATABASE_URL."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATABASE_URL", "   ")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_db_config()

        err_msg = str(exc_info.value)
        assert "DATABASE_URL environment variable is required" in err_msg

    def test_production_rejects_supabase_db_url_alias(self, monkeypatch):
        """In production, SUPABASE_DB_URL alias cannot bypass missing DATABASE_URL."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://user:pass@localhost:5432/learnai")
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        assert "DATABASE_URL environment variable is required" in str(exc_info.value)

        # Confirm DatabaseConfig does not treat PostgreSQL as active when canonical DATABASE_URL is missing in prod
        cfg = DatabaseConfig()
        assert cfg.database_url is None
        assert cfg.is_postgres is False

    def test_init_db_in_production_without_database_url_raises_and_never_calls_sqlite(self, monkeypatch):
        """init_db() in production must raise DatabaseConnectionError and not create/touch SQLite."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_DB_URL", raising=False)

        with pytest.raises(DatabaseConnectionError) as exc_info:
            init_db()

        err_msg = str(exc_info.value)
        assert "DATABASE_URL is required in production" in err_msg
        assert "Silent fallback to SQLite is prohibited" in err_msg

    def test_get_connection_in_production_without_database_url_raises_and_never_calls_sqlite(self, monkeypatch):
        """get_connection() in production must raise DatabaseConnectionError and not return SQLite."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_DB_URL", raising=False)

        with pytest.raises(DatabaseConnectionError) as exc_info:
            get_connection()

        err_msg = str(exc_info.value)
        assert "MySQL connection is required in production" in err_msg
        assert "Silent fallback to SQLite is prohibited" in err_msg


# ── 2. Production Startup with Missing SESSION_SECRET_KEY ──────────────────────

class TestMissingSessionSecretKeyInProduction:

    def test_production_startup_fails_fast_when_session_secret_key_missing(self, monkeypatch):
        """Production startup must raise RuntimeError when SESSION_SECRET_KEY is missing."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@localhost:3306/learnai")
        monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
        monkeypatch.delenv("LEARNAI_SECRET_KEY", raising=False)

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        err_msg = str(exc_info.value)
        assert "SESSION_SECRET_KEY environment variable is required" in err_msg

    def test_production_rejects_learnai_secret_key_alias(self, monkeypatch):
        """In production, LEARNAI_SECRET_KEY is not accepted as an alias for SESSION_SECRET_KEY."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@localhost:3306/learnai")
        monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
        monkeypatch.setenv("LEARNAI_SECRET_KEY", "legacy-key-that-must-be-rejected-in-prod")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        assert "SESSION_SECRET_KEY environment variable is required" in str(exc_info.value)

    def test_get_session_secret_in_production_fails_when_missing(self, monkeypatch):
        """get_session_secret() must raise RuntimeError in production when secret is absent."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
        monkeypatch.delenv("LEARNAI_SECRET_KEY", raising=False)

        with pytest.raises(RuntimeError) as exc_info:
            get_session_secret()

        assert "SESSION_SECRET_KEY environment variable is required" in str(exc_info.value)

    def test_get_session_secret_in_production_fails_when_blank(self, monkeypatch):
        """get_session_secret() must raise RuntimeError in production when secret is whitespace."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "   \t  ")

        with pytest.raises(RuntimeError) as exc_info:
            get_session_secret()

        assert "SESSION_SECRET_KEY environment variable is required" in str(exc_info.value)


# ── 3. Production Startup with Known Fallback Secret ───────────────────────────

class TestKnownDevelopmentFallbackSecretInProduction:

    @pytest.mark.parametrize("fallback_secret", [
        "learnai-default-dev-secret-key-change-in-production-32b!",
        "your_secure_random_hmac_secret_here",
        "change_me",
        "changeme",
        "secret",
        "dev-secret",
        "development",
        "default",
        "custom-key-change-in-production-test",
        "custom-key-change_in_production_test",
    ])
    def test_production_rejects_known_insecure_secrets(self, monkeypatch, fallback_secret):
        """Production mode must reject template or development fallback secrets."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@localhost:3306/learnai")
        monkeypatch.setenv("SESSION_SECRET_KEY", fallback_secret)

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        err_msg = str(exc_info.value)
        assert "cannot use a known development fallback or template secret" in err_msg
        # Ensure secret value is never interpolated or displayed in the error message
        assert f": {fallback_secret}" not in err_msg
        assert f"'{fallback_secret}'" not in err_msg
        assert f'"{fallback_secret}"' not in err_msg
        if len(fallback_secret) > 15:
            assert fallback_secret not in err_msg

    def test_is_known_insecure_secret_detection(self):
        """Verify is_known_insecure_secret correctly identifies known patterns."""
        assert is_known_insecure_secret("learnai-default-dev-secret-key-change-in-production-32b!")
        assert is_known_insecure_secret("your_secure_random_hmac_secret_here")
        assert is_known_insecure_secret("change_me")
        assert is_known_insecure_secret("SOME_CHANGE-IN-PRODUCTION_VALUE")
        assert is_known_insecure_secret("")
        assert not is_known_insecure_secret("x7k9P_mQ2$vL9#aR4!wT8*yU1@bE5%zC")


# ── 4. Valid Production Configuration ──────────────────────────────────────────

class TestValidProductionConfiguration:

    def test_valid_production_passes_all_checks(self, monkeypatch):
        """With valid MySQL URL and strong secret, all production checks pass."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATABASE_URL", "mysql://appuser:apppass@db.example.com:3306/proddb")
        strong_secret = "k8V#mP2$wL9@aR4!yU1*bE5%zC7&jF3(qH6)"
        monkeypatch.setenv("SESSION_SECRET_KEY", strong_secret)

        assert is_production_mode() is True
        # Must not raise
        validate_production_environment()
        validate_production_auth_config()
        validate_production_db_config()

        secret_bytes = get_session_secret()
        assert secret_bytes == strong_secret.encode("utf-8")


# ── 5. Local Development and Test Fixtures ────────────────────────────────────

class TestLocalDevelopmentAndTestFixtures:

    def test_development_mode_allows_default_secrets_and_sqlite(self, monkeypatch, tmp_path):
        """In development mode, absent secrets and SQLite are completely permitted."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
        monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
        monkeypatch.delenv("LEARNAI_SECRET_KEY", raising=False)

        assert is_production_mode() is False

        # validate_production_environment should be a safe no-op
        validate_production_environment()

        # get_session_secret returns default dev secret
        dev_secret = get_session_secret()
        assert dev_secret == b"learnai-default-dev-secret-key-change-in-production-32b!"

        # SQLite initialization on custom test path works cleanly
        test_db = tmp_path / "test_dev.db"
        summary = init_db(test_db)
        assert summary["status"] in ("ok", "initialized")
        assert summary["backend"] == "sqlite"

        conn = get_connection(test_db)
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM students;")
        assert cur.fetchone()[0] == 0
        conn.close()

    def test_development_mode_accepts_legacy_aliases(self, monkeypatch):
        """In development mode, legacy aliases LEARNAI_SECRET_KEY and SUPABASE_DB_URL are accepted."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://dev:pass@localhost:5432/devdb")
        monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
        monkeypatch.setenv("LEARNAI_SECRET_KEY", "legacy-dev-secret-key-for-test")

        cfg = DatabaseConfig()
        assert cfg.database_url == "postgresql://dev:pass@localhost:5432/devdb"
        assert cfg.is_postgres is True
        assert get_session_secret() == b"legacy-dev-secret-key-for-test"


# ── 6. Database Connection Failure Without SQLite Fallback ────────────────────

class TestPostgresProhibitedInProduction:

    def test_postgres_in_production_fails_fast_in_get_connection(self, monkeypatch):
        """
        When PostgreSQL is configured in production mode:
        get_connection() must raise DatabaseConnectionError and NEVER connect or fall back.
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "strong-prod-secret-key-12345678901234567890")
        monkeypatch.setenv("DATABASE_URL", "postgresql://learnai:secret@127.0.0.1:5432/learnai")

        reset_db_adapter()

        with pytest.raises(DatabaseConnectionError) as exc_info:
            get_connection()

        err_msg = str(exc_info.value)
        assert "PostgreSQL is prohibited in production" in err_msg
        assert "MySQL connection is required" in err_msg

    def test_postgres_in_production_fails_fast_in_init_db(self, monkeypatch):
        """
        When PostgreSQL is configured in production mode:
        init_db() must raise DatabaseConnectionError and NEVER connect or fall back.
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "strong-prod-secret-key-12345678901234567890")
        monkeypatch.setenv("DATABASE_URL", "postgresql://learnai:secret@127.0.0.1:5432/learnai")

        reset_db_adapter()

        with pytest.raises(DatabaseConnectionError) as exc_info:
            init_db()

        err_msg = str(exc_info.value)
        assert "PostgreSQL is prohibited in production" in err_msg
        assert "MySQL connection is required" in err_msg


class TestMySQLFailureWithoutFallback:

    def test_unreachable_mysql_fails_fast_in_production(self, monkeypatch):
        """
        When MySQL connection fails in production mode:
        Must raise DatabaseConnectionError and NEVER fall back silently to SQLite.
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "strong-prod-secret-key-12345678901234567890")
        monkeypatch.setenv("DATABASE_URL", "mysql://learnai:secret@127.0.0.1:59999/learnai?connect_timeout=1")
        monkeypatch.setenv("DB_CONNECT_TIMEOUT", "1.0")

        reset_db_adapter()

        with pytest.raises(DatabaseConnectionError) as exc_info:
            get_connection()

        err_msg = str(exc_info.value)
        assert "MySQL connection failed" in err_msg or "failed" in err_msg.lower()

        with pytest.raises(DatabaseConnectionError):
            init_db()


# ── 7. Render Runtime Environment Detection & Precedence Matrix ───────────────

class TestRenderEnvironmentDetectionAndPrecedence:

    def test_combination_1_render_true_alone_triggers_production(self, monkeypatch):
        """Combination 1: RENDER=true alone triggers production mode."""
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv("RENDER", "true")

        assert is_production_mode() is True

        # Enforces missing DATABASE_URL
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        assert "DATABASE_URL environment variable is required" in str(exc_info.value)

    def test_combination_2_render_true_plus_environment_development(self, monkeypatch):
        """Combination 2: RENDER=true plus ENVIRONMENT=development CANNOT disable production safeguards on Render."""
        monkeypatch.setenv("RENDER", "true")
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("APP_ENV", raising=False)

        assert is_production_mode() is True

        # Confirms safeguards still trigger
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")
        with pytest.raises(RuntimeError):
            validate_production_environment()

    def test_combination_3_render_true_plus_app_env_test(self, monkeypatch):
        """Combination 3: RENDER=true plus APP_ENV=test CANNOT disable production safeguards on Render."""
        monkeypatch.setenv("RENDER", "true")
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("APP_ENV", "test")

        assert is_production_mode() is True

    def test_combination_4_environment_production_plus_app_env_development(self, monkeypatch):
        """Combination 4: Canonical ENVIRONMENT=production takes precedence over alias APP_ENV=development."""
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("APP_ENV", "development")

        assert is_production_mode() is True

    def test_combination_5_environment_test_plus_app_env_production(self, monkeypatch):
        """Combination 5: Canonical ENVIRONMENT=test takes precedence over alias APP_ENV=production."""
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.setenv("APP_ENV", "production")

        assert is_production_mode() is False


# ── 8. Invalid Database URI Scheme in Production ──────────────────────────────

class TestInvalidDatabaseUrlScheme:

    def test_non_mysql_scheme_rejected_in_production(self, monkeypatch):
        """DATABASE_URL pointing to SQLite or non-MySQL scheme must fail fast."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///backend/data/learnai.db")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        assert "must be a MySQL connection URI" in str(exc_info.value)

    def test_unsupported_schemes_rejected_in_production(self, monkeypatch):
        """Unsupported database URI schemes (mongodb, redis, etc.) must be rejected."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")

        for bad_url in ("mongodb://localhost:27017/learnai", "redis://localhost:6379/0", "oracle://localhost/db"):
            monkeypatch.setenv("DATABASE_URL", bad_url)
            with pytest.raises(RuntimeError) as exc_info:
                validate_production_environment()
            assert "must be a MySQL connection URI" in str(exc_info.value)


# ── 9. Explicit SQLite Path Rejected in Production Mode ───────────────────────

class TestExplicitSqlitePathRejectedInProduction:

    def test_explicit_sqlite_db_path_rejected_in_production(self, monkeypatch, tmp_path):
        """Passing an explicit SQLite path in production mode must be rejected immediately."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")
        monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@localhost:3306/learnai")

        fake_sqlite_path = tmp_path / "sqlite_prohibited.db"

        with pytest.raises(DatabaseConnectionError) as exc_info:
            get_connection(fake_sqlite_path)

        assert "Explicit SQLite path is prohibited in production mode" in str(exc_info.value)

        with pytest.raises(DatabaseConnectionError) as exc_info:
            init_db(fake_sqlite_path)

        assert "Explicit SQLite path is prohibited in production mode" in str(exc_info.value)


# ── 10. Production PostgreSQL Rejection (MySQL-Only Requirement) ───────────────

class TestProductionPostgresRejection:

    def test_postgres_standard_scheme_rejected_in_production(self, monkeypatch):
        """postgresql:// URI scheme must be explicitly rejected in production mode."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")
        monkeypatch.setenv("DATABASE_URL", "postgresql://appuser:apppass@db.example.com:5432/proddb")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        err_msg = str(exc_info.value)
        assert "PostgreSQL is not supported for this deployment" in err_msg
        assert "Production LearnAI requires a MySQL connection URI" in err_msg

    def test_postgres_alias_scheme_rejected_in_production(self, monkeypatch):
        """postgres:// URI scheme must be explicitly rejected in production mode."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")
        monkeypatch.setenv("DATABASE_URL", "postgres://appuser:apppass@db.example.com:5432/proddb")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_environment()

        err_msg = str(exc_info.value)
        assert "PostgreSQL is not supported for this deployment" in err_msg
        assert "Production LearnAI requires a MySQL connection URI" in err_msg

    def test_postgres_rejected_by_validate_production_db_config(self, monkeypatch):
        """validate_production_db_config must fail fast when postgresql is configured."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATABASE_URL", "postgresql://appuser:apppass@db.example.com:5432/proddb")

        with pytest.raises(RuntimeError) as exc_info:
            validate_production_db_config()

        err_msg = str(exc_info.value)
        assert "PostgreSQL is not supported for this deployment" in err_msg


# ── 11. Production MySQL Configuration, URL Parsing, & Driver Safeguards ───────

class TestProductionMySqlConfiguration:

    def test_mysql_scheme_accepted_in_production(self, monkeypatch):
        """mysql:// URI scheme must be accepted in production mode."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")
        monkeypatch.setenv("DATABASE_URL", "mysql://learnai_user:secret_pass@mysql.host.internal:3306/learnai_prod")

        # Must not raise
        validate_production_environment()

    def test_mysql_pymysql_scheme_accepted_in_production(self, monkeypatch):
        """mysql+pymysql:// URI scheme must be accepted in production mode."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SESSION_SECRET_KEY", "prod-secret-key-998877665544332211")
        monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://learnai_user:secret_pass@mysql.host.internal:3306/learnai_prod")

        # Must not raise
        validate_production_environment()

    def test_database_config_detects_mysql(self):
        """DatabaseConfig must properly identify MySQL dialect and flags."""
        from services.db.base import DatabaseConfig
        cfg = DatabaseConfig(database_url="mysql://app_user:pwd@db.host.io:3306/app_db")
        assert cfg.dialect == "mysql"
        assert cfg.is_mysql is True
        assert cfg.is_postgres is False
        assert cfg.is_sqlite is False

    def test_database_config_detects_mysql_pymysql_driver_prefix(self):
        """DatabaseConfig must properly identify mysql+pymysql dialect and flags."""
        from services.db.base import DatabaseConfig
        cfg = DatabaseConfig(database_url="mysql+pymysql://app_user:pwd@db.host.io:3306/app_db")
        assert cfg.dialect == "mysql"
        assert cfg.is_mysql is True
        assert cfg.is_postgres is False
        assert cfg.is_sqlite is False

    def test_mysql_adapter_parses_url_encoded_credentials(self):
        """Passwords and usernames containing URL-encoded special characters must be properly decoded."""
        from services.db.mysql_adapter import MySQLAdapter
        url = "mysql://user%40domain:p%40ss%23word%25123%21@db.example.com:3307/learnai_db"
        params = MySQLAdapter.parse_connection_params(url)
        assert params["user"] == "user@domain"
        assert params["password"] == "p@ss#word%123!"
        assert params["host"] == "db.example.com"
        assert params["port"] == 3307
        assert params["database"] == "learnai_db"
        assert params["charset"] == "utf8mb4"

    def test_mysql_adapter_rejects_non_mysql_configuration(self):
        """MySQLAdapter must fail fast if instantiated with a non-MySQL DatabaseConfig."""
        from services.db.mysql_adapter import MySQLAdapter
        sqlite_cfg = DatabaseConfig(database_url=None)
        with pytest.raises(DatabaseConnectionError) as exc_info:
            MySQLAdapter(sqlite_cfg)
        assert "is not a MySQL connection string" in str(exc_info.value)

        pg_cfg = DatabaseConfig(database_url="postgresql://user:pass@localhost:5432/db")
        with pytest.raises(DatabaseConnectionError) as exc_info:
            MySQLAdapter(pg_cfg)
        assert "is not a MySQL connection string" in str(exc_info.value)

    def test_mysql_adapter_tls_mode_parsing(self, tmp_path):
        """MySQLAdapter must correctly configure TLS Context for VERIFY_IDENTITY, VERIFY_CA, and REQUIRED."""
        import ssl
        from services.db.mysql_adapter import MySQLAdapter

        # Case 1: VERIFY_IDENTITY with CA file
        fake_ca = tmp_path / "ca.pem"
        fake_ca.write_text("-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n")
        ca_url = f"mysql://u:p@host:3306/db?ssl_mode=VERIFY_IDENTITY&ssl_ca={fake_ca}"
        with patch("ssl.create_default_context") as mock_ctx_factory:
            mock_ctx = mock_ctx_factory.return_value
            params = MySQLAdapter.parse_connection_params(ca_url)
            mock_ctx_factory.assert_called_with(cafile=str(fake_ca))
            assert mock_ctx.check_hostname is True
            assert mock_ctx.verify_mode == ssl.CERT_REQUIRED
            assert params["ssl"] is mock_ctx

        # Case 2: VERIFY_CA disables check_hostname
        with patch("ssl.create_default_context") as mock_ctx_factory:
            mock_ctx = mock_ctx_factory.return_value
            ca_url2 = f"mysql://u:p@host:3306/db?ssl_mode=VERIFY_CA&ssl_ca={fake_ca}"
            MySQLAdapter.parse_connection_params(ca_url2)
            assert mock_ctx.check_hostname is False
            assert mock_ctx.verify_mode == ssl.CERT_REQUIRED

        # Case 3: ssl_mode=REQUIRED (wire encryption without custom CA)
        req_url = "mysql://u:p@host:3306/db?ssl_mode=REQUIRED"
        params_req = MySQLAdapter.parse_connection_params(req_url)
        assert params_req["ssl"] == {"check_hostname": False}

        # Case 4: ssl_mode=DISABLED
        dis_url = "mysql://u:p@host:3306/db?ssl_mode=DISABLED"
        params_dis = MySQLAdapter.parse_connection_params(dis_url)
        assert "ssl" not in params_dis
