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


def translate_placeholders_to_pg(query: str) -> str:
    """
    Translate SQLite '?' parameter placeholders to PostgreSQL '%s' placeholders,
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


def parse_json_safely(val: Any, default: Any = None) -> Any:
    """
    Safely deserialize JSON columns from database rows.
    Handles:
    - Already deserialized dict or list (from PostgreSQL JSONB via psycopg)
    - Serialized JSON string (from SQLite TEXT or Postgres TEXT)
    - None or empty values with fallback default
    """
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
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


def serialize_json_for_db(val: Any, is_postgres: bool = False) -> Any:
    """
    Safely serialize JSON columns for database insertion or update.
    - For SQLite: returns JSON string (TEXT).
    - For PostgreSQL: returns psycopg Jsonb object if psycopg is available,
      or serialized string if columns are TEXT.
    """
    if val is None:
        return None

    if is_postgres:
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

    # SQLite or fallback string serialization
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
    Validate that PostgreSQL database configuration is present and properly formatted
    when running in production mode.
    Strictly requires canonical DATABASE_URL in production (no aliases).
    Fails fast with clear error message; never prints credentials.
    """
    if not is_production_mode():
        return

    db_url = (os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        logger.critical(
            "CRITICAL: Production startup failed: DATABASE_URL is not configured. "
            "Production mode requires a valid PostgreSQL database connection and will not fall back to SQLite."
        )
        raise RuntimeError(
            "Production startup error: DATABASE_URL environment variable is required and cannot be empty. "
            "Production LearnAI must connect to PostgreSQL; silent SQLite fallback is prohibited."
        )

    if not (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
        logger.critical(
            "CRITICAL: Production startup failed: DATABASE_URL must be a PostgreSQL connection URI."
        )
        raise RuntimeError(
            "Production configuration error: DATABASE_URL must be a PostgreSQL connection URI "
            "(starting with postgresql:// or postgres://)."
        )


class DatabaseConfig:
    """
    Database configuration manager.
    Detects whether PostgreSQL or SQLite is active based on environment variables.

    Precedence:
    1. DATABASE_URL (production canonical) or SUPABASE_DB_URL (development legacy alias)
    2. LEARNAI_DB_PATH -> SQLite persistent path
    3. SQLITE_DB_PATH -> SQLite legacy path
    4. Default -> backend/data/learnai.db
    """

    @property
    def is_production(self) -> bool:
        """True if running in production mode."""
        return is_production_mode()

    @property
    def is_postgres(self) -> bool:
        """True if PostgreSQL / Supabase connection is configured."""
        url = self.database_url
        return bool(url and url.strip())

    @property
    def database_url(self) -> Optional[str]:
        if self.is_production:
            url = os.getenv("DATABASE_URL")
        else:
            url = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
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
