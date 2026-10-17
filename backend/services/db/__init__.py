"""
services.db package
===================
Unified database layer for LearnAI.
Exports database adapters, configuration, and helpers.
"""

from services.db.base import (
    CompatibleRow,
    DatabaseConfig,
    DatabaseConnectionError,
    translate_placeholders_to_pg,
    parse_json_safely,
    parse_json_column,
    serialize_json_for_db,
)
from services.db.sqlite_adapter import SQLiteAdapter
from services.db.postgres_adapter import (
    PostgresAdapter,
    PostgresConnectionWrapper,
    PostgresCursorWrapper,
)
from services.db.migrations import (
    apply_migrations,
    rollback_migration,
    ensure_migration_table,
    get_applied_migrations,
)

__all__ = [
    "CompatibleRow",
    "DatabaseConfig",
    "DatabaseConnectionError",
    "translate_placeholders_to_pg",
    "parse_json_safely",
    "parse_json_column",
    "serialize_json_for_db",
    "SQLiteAdapter",
    "PostgresAdapter",
    "PostgresConnectionWrapper",
    "PostgresCursorWrapper",
    "apply_migrations",
    "rollback_migration",
    "ensure_migration_table",
    "get_applied_migrations",
]
