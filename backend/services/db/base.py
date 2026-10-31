"""
base.py
======
Unified Database Abstraction Layer interfaces, wrappers, and configuration
for LearnAI (supporting SQLite and Supabase PostgreSQL).
"""

import os
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("learnai.db")


class DatabaseConnectionError(RuntimeError):
    """Raised when database connection fails, particularly for fail-fast safeguards."""
    pass


class CompatibleRow(dict):
    """
    Unified row wrapper providing dual dict-like ('row["col"]') and
    tuple-like ('row[0]') access across SQLite and PostgreSQL backends.
    Fully compatible with dict(row), row.get(), and row.keys().
    """

    def __init__(self, description: Any, values: Tuple[Any, ...]):
        cols = [d.name if hasattr(d, "name") else d[0] for d in description]
        super().__init__(zip(cols, values))
        self._values = tuple(values)
        self._keys = list(cols)

    def __getitem__(self, key: Union[int, str]) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)

    def keys(self) -> List[str]:
        return self._keys

    def values(self) -> Tuple[Any, ...]:
        return self._values

    def items(self):
        return super().items()


def translate_placeholders(query: str) -> str:
    """
    Translate SQLite '?' parameter placeholders to '%s' placeholders (used by PostgreSQL and MySQL),
    preserving any '?' inside single-quoted string literals and SQL comments (-- and /* */).
    """
    out = []
    i = 0
    n = len(query)
    in_quote = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        if in_quote:
            out.append(query[i])
            if query[i] == "'":
                if i + 1 < n and query[i + 1] == "'":
                    out.append(query[i + 1])
                    i += 1
                else:
                    in_quote = False
            i += 1
        elif in_line_comment:
            out.append(query[i])
            if query[i] == "\n":
                in_line_comment = False
            i += 1
        elif in_block_comment:
            out.append(query[i])
            if query[i : i + 2] == "*/":
                out.append(query[i + 1])
                i += 1
                in_block_comment = False
            i += 1
        else:
            if query[i] == "'":
                in_quote = True
                out.append(query[i])
                i += 1
            elif query[i : i + 2] == "--":
                in_line_comment = True
                out.append(query[i : i + 2])
                i += 2
            elif query[i : i + 2] == "/*":
                in_block_comment = True
                out.append(query[i : i + 2])
                i += 2
            elif query[i] == "?":
                out.append("%s")
                i += 1
            else:
                out.append(query[i])
                i += 1

    return "".join(out)


# Backward-compatibility alias
translate_placeholders_to_pg = translate_placeholders


def parse_json_safely(val: Any, default: Any = None) -> Any:
    """
    Safely deserialize JSON columns from database rows.
    Handles:
    - Already deserialized dict or list (from PostgreSQL JSONB via psycopg)
    - Serialized JSON string (from SQLite TEXT, Postgres TEXT, or MySQL JSON)
    - Raw bytes (from MySQL drivers)
    - None or empty values with fallback default
    """
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, (bytes, bytearray)):
        try:
            val = val.decode("utf-8")
        except Exception:
            return default
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            return default
        try:
            return json.loads(stripped)
        except Exception:
            return default
    return default


def parse_json_column(row: Any, col: str, default: Any = None) -> Any:
    """
    Safely extract and deserialize a JSON column from any row representation
    (sqlite3.Row, CompatibleRow, or dict). Prevents AttributeError on sqlite3.Row.
    """
    if row is None:
        return default
    try:
        val = row[col]
    except Exception:
        return default
    return parse_json_safely(val, default)


def serialize_json_for_db(val: Any, is_postgres: bool = False, dialect: Optional[str] = None) -> Any:
    """
    Safely serialize JSON columns for database insertion or update.
    - For SQLite / MySQL: returns JSON string (TEXT / JSON).
    - For PostgreSQL: returns psycopg Jsonb object if psycopg is available,
      or serialized string if columns are TEXT.
    """
    if val is None:
        return None

    use_postgres = is_postgres or (dialect == "postgres")
    if use_postgres:
        try:
            from psycopg.types.json import Jsonb
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    return Jsonb(parsed)
                except Exception:
                    return Jsonb(val)
            return Jsonb(val)
        except ImportError:
            pass

    # SQLite, MySQL, or fallback string serialization
    if isinstance(val, str):
        return val
    return json.dumps(val)


def is_production_mode() -> bool:
    """
    Determine whether the application is running in production mode.
    Precedence policy:
    1. Render Cloud Guard (Highest Priority):
       If deployed on Render (RENDER=true or 1), ALWAYS evaluates to True.
       Production safeguards can NEVER be accidentally disabled on Render.
    2. Canonical Environment Variable (ENVIRONMENT):
       - 'production' or 'prod' -> True
       - 'development', 'dev', 'test', 'testing', 'local' -> False
    3. Secondary Environment Alias (APP_ENV):
       - 'production' or 'prod' -> True
       - 'development', 'dev', 'test', 'testing', 'local' -> False
    4. Default (unset or unrecognized): False (local development mode).
    """
    if os.getenv("RENDER", "").strip().lower() in ("true", "1"):
        return True

    env = os.getenv("ENVIRONMENT", "").strip().lower()
    if env in ("production", "prod"):
        return True
    if env in ("development", "dev", "test", "testing", "local"):
        return False

    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env in ("production", "prod"):
        return True
    if app_env in ("development", "dev", "test", "testing", "local"):
        return False

    return False


def validate_production_db_config() -> None:
    """
    Validate that database configuration is present and properly formatted
    when running in production mode.
    Strictly requires canonical DATABASE_URL in production (no aliases).
    Fails fast with clear error message; never prints credentials.
    Enforces MySQL exclusively in production; PostgreSQL and SQLite are prohibited.
    """
    if not is_production_mode():
        return

    db_url = (os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        logger.critical(
            "CRITICAL: Production startup failed: DATABASE_URL is not configured. "
            "Production mode requires a valid MySQL database connection and will not fall back to SQLite."
        )
        raise RuntimeError(
            "Production startup error: DATABASE_URL environment variable is required and cannot be empty. "
            "Production LearnAI must connect to MySQL; silent SQLite fallback is prohibited."
        )

    is_pg = db_url.startswith("postgresql://") or db_url.startswith("postgres://")
    if is_pg:
        logger.critical(
            "CRITICAL: Production startup failed: PostgreSQL is not supported for this deployment. "
            "Production LearnAI requires a MySQL connection URI."
        )
        raise RuntimeError(
            "Production configuration error: PostgreSQL is not supported for this deployment. "
            "Production LearnAI requires a MySQL connection URI (starting with mysql:// or mysql+pymysql://)."
        )

    is_mysql = db_url.startswith("mysql://") or db_url.startswith("mysql+pymysql://")
    if not is_mysql:
        logger.critical(
            "CRITICAL: Production startup failed: DATABASE_URL must be a MySQL connection URI."
        )
        raise RuntimeError(
            "Production configuration error: DATABASE_URL must be a MySQL connection URI "
            "(starting with mysql:// or mysql+pymysql://)."
        )


class DatabaseConfig:
    """
    Database configuration manager.
    Detects whether PostgreSQL, MySQL, or SQLite is active based on environment variables.

    Precedence:
    1. Explicit custom database_url passed to constructor (if provided)
    2. DATABASE_URL (production canonical) or MYSQL_DATABASE_URL (development/test alias)
    3. LEARNAI_DB_PATH -> SQLite persistent path
    4. SQLITE_DB_PATH -> SQLite legacy path
    5. Default -> backend/data/learnai.db
    """

    def __init__(self, database_url: Optional[str] = None):
        self._custom_database_url = database_url

    @property
    def is_production(self) -> bool:
        """True if running in production mode."""
        return is_production_mode()

    @property
    def dialect(self) -> str:
        url = self.database_url
        if not url:
            return "sqlite"
        if url.startswith("mysql://") or url.startswith("mysql+pymysql://"):
            return "mysql"
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            return "postgres"
        return "sqlite"

    @property
    def is_postgres(self) -> bool:
        """True if PostgreSQL connection is configured."""
        return self.dialect == "postgres"

    @property
    def is_mysql(self) -> bool:
        """True if MySQL connection is configured."""
        return self.dialect == "mysql"

    @property
    def is_sqlite(self) -> bool:
        """True if SQLite is active."""
        return self.dialect == "sqlite"

    @property
    def database_url(self) -> Optional[str]:
        if getattr(self, "_custom_database_url", None) is not None:
            return self._custom_database_url.strip() if self._custom_database_url else None
        if self.is_production:
            url = os.getenv("DATABASE_URL")
        else:
            url = os.getenv("DATABASE_URL") or os.getenv("MYSQL_DATABASE_URL")
        return url.strip() if url else None

    @property
    def pool_min_size(self) -> int:
        return int(os.getenv("DB_POOL_MIN_SIZE", "1"))

    @property
    def pool_max_size(self) -> int:
        return int(os.getenv("DB_POOL_MAX_SIZE", "5"))

    @property
    def connect_timeout(self) -> float:
        return float(os.getenv("DB_CONNECT_TIMEOUT", "10.0"))

    def get_sqlite_path(self) -> Path:
        """Resolve SQLite path safely."""
        custom_path = os.getenv("LEARNAI_DB_PATH") or os.getenv("SQLITE_DB_PATH")
        if custom_path:
            p = Path(custom_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        default_p = Path(__file__).resolve().parent.parent.parent / "data" / "learnai.db"
        default_p.parent.mkdir(parents=True, exist_ok=True)
        return default_p
