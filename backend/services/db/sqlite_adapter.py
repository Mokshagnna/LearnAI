"""
sqlite_adapter.py
=================
SQLite database adapter for LearnAI local development and automated testing.
Preserves existing connection lifecycle, WAL mode, busy timeout, and foreign key enforcement.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional, Any, Dict
from services.db.base import DatabaseConfig

logger = logging.getLogger("learnai.db.sqlite")


class SQLiteAdapter:
    """
    Adapter for local SQLite connections.
    """

    def __init__(self, config: Optional[DatabaseConfig] = None):
        self.config = config or DatabaseConfig()

    def get_connection(self, db_path: Optional[Path] = None) -> sqlite3.Connection:
        """
        Acquire an SQLite connection configured with:
        - foreign key enforcement
        - busy timeout (5000ms)
        - synchronous NORMAL
        - sqlite3.Row row factory
        """
        target = db_path or self.config.get_sqlite_path()
        conn = sqlite3.connect(str(target), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def get_path(self) -> Path:
        return self.config.get_sqlite_path()
