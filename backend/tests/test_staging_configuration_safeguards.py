"""
test_staging_configuration_safeguards.py
========================================
Focused test suite verifying explicit staging configuration safeguards:
1. Default .env loading behavior when LEARNAI_ENV_FILE is unset or empty.
2. Explicit staging-file selection via LEARNAI_ENV_FILE.
3. Fail-closed behavior (FileNotFoundError, no silent fallback) when explicit file is missing.
4. Consistent resolution between backend/main.py and backend/services/llm_service.py.
5. Staging SQLite database path honoring via LEARNAI_DB_PATH.
6. Guard verification: Staging mode cannot bypass production MySQL requirements or Render Cloud Guard.
"""

import os
from pathlib import Path
import pytest

from services.env_loader import resolve_and_load_env
from services.db.base import DatabaseConfig, is_production_mode
from services.database import init_db, get_connection, DatabaseConnectionError


class TestStagingEnvironmentLoading:
    """Tests for resolve_and_load_env and LEARNAI_ENV_FILE handling."""

    def test_default_env_loading_behavior(self, tmp_path, monkeypatch):
        """When LEARNAI_ENV_FILE is unset, loads base_dir / .env if present."""
        monkeypatch.delenv("LEARNAI_ENV_FILE", raising=False)
        monkeypatch.delenv("MOCK_DEFAULT_VAR", raising=False)

        mock_env = tmp_path / ".env"
        mock_env.write_text("MOCK_DEFAULT_VAR=active_default_123\n", encoding="utf-8")

        resolved = resolve_and_load_env(tmp_path)
        assert resolved == mock_env
        assert os.getenv("MOCK_DEFAULT_VAR") == "active_default_123"

    def test_default_env_when_file_absent(self, tmp_path, monkeypatch):
        """When LEARNAI_ENV_FILE is unset and .env does not exist, returns base_dir / .env without raising."""
        monkeypatch.delenv("LEARNAI_ENV_FILE", raising=False)

        nonexistent_base = tmp_path / "empty_dir"
        nonexistent_base.mkdir()

        resolved = resolve_and_load_env(nonexistent_base)
        assert resolved == nonexistent_base / ".env"

    def test_explicit_staging_file_selection(self, tmp_path, monkeypatch):
        """When LEARNAI_ENV_FILE is set to an existing file, loads it with override=True."""
        monkeypatch.delenv("MOCK_STAGING_KEY", raising=False)
        staging_file = tmp_path / ".env.staging.local"
        staging_file.write_text("MOCK_STAGING_KEY=staging_secret_token_xyz\n", encoding="utf-8")

        monkeypatch.setenv("LEARNAI_ENV_FILE", str(staging_file))

        # Pass a different base_dir to prove explicit path overrides default base_dir / .env
        other_dir = tmp_path / "ignored_dir"
        other_dir.mkdir()
        ignored_env = other_dir / ".env"
        ignored_env.write_text("MOCK_STAGING_KEY=should_be_overridden\n", encoding="utf-8")

        resolved = resolve_and_load_env(other_dir)
        assert resolved == staging_file.resolve()
        assert os.getenv("MOCK_STAGING_KEY") == "staging_secret_token_xyz"

    def test_missing_explicit_environment_file_fails_closed(self, tmp_path, monkeypatch):
        """When LEARNAI_ENV_FILE points to a missing file, raises FileNotFoundError and never silently falls back."""
        monkeypatch.delenv("CANARY_FALLBACK_VAR", raising=False)
        missing_file = tmp_path / "does_not_exist.env.staging"
        monkeypatch.setenv("LEARNAI_ENV_FILE", str(missing_file))

        # Place a default .env in base_dir to verify silent fallback does NOT occur
        default_env = tmp_path / ".env"
        default_env.write_text("CANARY_FALLBACK_VAR=leaked_from_default\n", encoding="utf-8")

        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_and_load_env(tmp_path)

        assert "Configured LEARNAI_ENV_FILE not found" in str(exc_info.value)
        assert "silent fallback prohibited" in str(exc_info.value)
        # Ensure default .env was NOT loaded
        assert os.getenv("CANARY_FALLBACK_VAR") is None

    def test_empty_or_whitespace_learnai_env_file_uses_default(self, tmp_path, monkeypatch):
        """Empty or whitespace-only LEARNAI_ENV_FILE behaves as unset."""
        monkeypatch.setenv("LEARNAI_ENV_FILE", "   ")
        resolved = resolve_and_load_env(tmp_path)
        assert resolved == tmp_path / ".env"

    def test_both_modules_select_same_environment_file(self, tmp_path, monkeypatch):
        """Verify main.py BASE_DIR and llm_service.py _BACKEND_DIR resolve the exact same environment file."""
        from main import BASE_DIR as main_base_dir
        from services.llm_service import _BACKEND_DIR as llm_backend_dir

        # Both base directories resolve to the backend directory
        assert main_base_dir.resolve() == llm_backend_dir.resolve()

        # Under default:
        monkeypatch.delenv("LEARNAI_ENV_FILE", raising=False)
        p_main = resolve_and_load_env(main_base_dir)
        p_llm = resolve_and_load_env(llm_backend_dir)
        assert p_main == p_llm

        # Under explicit override:
        staging_file = tmp_path / ".env.staging.shared"
        staging_file.write_text("SHARED_VAR=1\n", encoding="utf-8")
        monkeypatch.setenv("LEARNAI_ENV_FILE", str(staging_file))

        p_main_override = resolve_and_load_env(main_base_dir)
        p_llm_override = resolve_and_load_env(llm_backend_dir)
        assert p_main_override == p_llm_override == staging_file.resolve()


class TestStagingDatabaseSafety:
    """Tests for database path resolution, isolation, and production guards."""

    def test_staging_db_path_honored(self, tmp_path, monkeypatch):
        """LEARNAI_DB_PATH directs SQLite connection to the explicit staging database path."""
        staging_db = tmp_path / "data" / "staging_gate1_test.db"
        monkeypatch.setenv("LEARNAI_DB_PATH", str(staging_db))
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("MYSQL_DATABASE_URL", raising=False)
        monkeypatch.delenv("RENDER", raising=False)

        config = DatabaseConfig()
        assert not config.is_production
        assert config.dialect == "sqlite"
        assert config.get_sqlite_path() == staging_db.resolve()

        # Initialize schema and verify table creation on staging database
        summary = init_db()
        assert summary.get("status") in ("ok", "initialized")
        assert staging_db.is_file()

        # Verify active connection is strictly attached to staging_db
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA database_list;")
            rows = cursor.fetchall()
            main_entry = next(r for r in rows if r["name"] == "main" or r[1] == "main")
            db_file_path = main_entry["file"] if "file" in main_entry.keys() else main_entry[2]
            assert Path(db_file_path).resolve() == staging_db.resolve()
        finally:
            conn.close()

    def test_production_guard_prevents_silent_sqlite_selection(self, tmp_path, monkeypatch):
        """In production mode, LEARNAI_DB_PATH cannot silently select SQLite; init_db raises DatabaseConnectionError."""
        staging_db = tmp_path / "staging_leak_attempt.db"
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("LEARNAI_DB_PATH", str(staging_db))

        with pytest.raises(DatabaseConnectionError) as exc_info:
            init_db()

        assert "Silent fallback to SQLite is prohibited" in str(exc_info.value)
        assert not staging_db.exists()

    def test_render_guard_cannot_be_bypassed_by_staging_environment(self, monkeypatch):
        """When RENDER=true, is_production_mode() is ALWAYS True even if ENVIRONMENT=staging."""
        monkeypatch.setenv("RENDER", "true")
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("APP_ENV", "staging")

        assert is_production_mode() is True
