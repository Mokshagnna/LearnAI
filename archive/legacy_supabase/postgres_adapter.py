"""
postgres_adapter.py
===================
Supabase PostgreSQL database adapter for LearnAI production deployment.
Uses psycopg (v3) with psycopg_pool.ConnectionPool, Supavisor compatibility,
fail-fast error handling, parameter placeholder translation, and CompatibleRow mapping.
"""

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from services.db.base import (
    CompatibleRow,
    DatabaseConfig,
    DatabaseConnectionError,
    translate_placeholders_to_pg,
)

logger = logging.getLogger("learnai.db.postgres")


class PostgresCursorWrapper:
    """
    Cursor wrapper for psycopg cursor:
    - Translates SQLite '?' parameter placeholders to PostgreSQL '%s' placeholders.
    - Converts query results into CompatibleRow objects supporting both row['col'] and row[0].
    """

    def __init__(self, raw_cursor: Any):
        self._cursor = raw_cursor

    @property
    def description(self) -> Any:
        return self._cursor.description

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def execute(self, query: str, params: Optional[Union[Tuple[Any, ...], List[Any]]] = None) -> "PostgresCursorWrapper":
        pg_query = translate_placeholders_to_pg(query)
        if params is not None:
            # Convert list/tuple into a tuple for psycopg execution
            # Ensure JSON parameters or primitive values are passed properly
            exec_params = tuple(params) if isinstance(params, (list, tuple)) else params
            self._cursor.execute(pg_query, exec_params)
        else:
            self._cursor.execute(pg_query)
        return self

    def executemany(self, query: str, params_seq: List[Union[Tuple[Any, ...], List[Any]]]) -> "PostgresCursorWrapper":
        pg_query = translate_placeholders_to_pg(query)
        self._cursor.executemany(pg_query, params_seq)
        return self

    def fetchone(self) -> Optional[CompatibleRow]:
        row = self._cursor.fetchone()
        if row is None:
            return None
        if not self._cursor.description:
            return row
        return CompatibleRow(self._cursor.description, row)

    def fetchall(self) -> List[CompatibleRow]:
        rows = self._cursor.fetchall()
        if not rows:
            return []
        desc = self._cursor.description
        if not desc:
            return rows
        return [CompatibleRow(desc, r) for r in rows]

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class PostgresConnectionWrapper:
    """
    Connection wrapper that returns wrapped cursors and manages transaction / pool lifecycle.
    """

    def __init__(self, raw_conn: Any, pool: Optional[Any] = None):
        self._conn = raw_conn
        self._pool = pool
        self._closed = False

    @property
    def autocommit(self) -> bool:
        return self._conn.autocommit

    @autocommit.setter
    def autocommit(self, value: bool):
        self._conn.autocommit = value

    def cursor(self) -> PostgresCursorWrapper:
        raw_cur = self._conn.cursor()
        return PostgresCursorWrapper(raw_cur)

    def execute(self, query: str, params: Optional[Union[Tuple[Any, ...], List[Any]]] = None) -> PostgresCursorWrapper:
        cur = self.cursor()
        return cur.execute(query, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            try:
                self._pool.putconn(self._conn)
            except Exception as e:
                logger.warning("Error returning connection to pool: %s", e)
        else:
            try:
                self._conn.close()
            except Exception as e:
                logger.warning("Error closing raw postgres connection: %s", e)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


class PostgresAdapter:
    """
    Adapter for Supabase PostgreSQL connections with pooling via psycopg_pool.
    Enforces fail-fast behavior when DATABASE_URL is configured.
    """

    _pool_lock = threading.Lock()
    _pool = None

    def __init__(self, config: Optional[DatabaseConfig] = None):
        self.config = config or DatabaseConfig()
        if not self.config.is_postgres:
            raise DatabaseConnectionError(
                "Cannot initialize PostgresAdapter: no DATABASE_URL or SUPABASE_DB_URL configured."
            )

    @staticmethod
    def normalize_database_url(url: str) -> str:
        """
        Ensure sslmode=require is present in the connection string for Supabase,
        and handle postgres:// vs postgresql:// scheme.
        """
        parsed = urlparse(url)
        scheme = "postgresql" if parsed.scheme in ("postgres", "postgresql") else parsed.scheme
        qs = parse_qs(parsed.query)
        if "sslmode" not in qs:
            qs["sslmode"] = ["require"]
        new_query = urlencode(qs, doseq=True)
        return urlunparse((
            scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))

    def _get_pool(self):
        if PostgresAdapter._pool is not None and not PostgresAdapter._pool.closed:
            return PostgresAdapter._pool

        with PostgresAdapter._pool_lock:
            if PostgresAdapter._pool is not None and not PostgresAdapter._pool.closed:
                return PostgresAdapter._pool

            conn_url = self.normalize_database_url(self.config.database_url)
            try:
                import psycopg
                from psycopg_pool import ConnectionPool

                logger.info(
                    "Initializing Supabase PostgreSQL connection pool (min=%d, max=%d, timeout=%.1fs)...",
                    self.config.pool_min_size,
                    self.config.pool_max_size,
                    self.config.connect_timeout,
                )

                # Supavisor pooler recommendations:
                # Use connection_timeout and keep connections bounded
                pool = ConnectionPool(
                    conninfo=conn_url,
                    min_size=self.config.pool_min_size,
                    max_size=self.config.pool_max_size,
                    timeout=self.config.connect_timeout,
                    open=False,
                )
                pool.open()
                # Test connectivity immediately (Fail-Fast)
                with pool.connection(timeout=self.config.connect_timeout) as test_conn:
                    with test_conn.cursor() as cur:
                        cur.execute("SELECT 1;")
                        cur.fetchone()

                logger.info("Supabase PostgreSQL connection pool initialized and verified successfully.")
                PostgresAdapter._pool = pool
                return PostgresAdapter._pool
            except Exception as e:
                logger.critical(
                    "CRITICAL: Failed to connect to Supabase PostgreSQL at %s: %s. "
                    "Fail-fast safeguard triggered. Will NEVER fall back silently to SQLite.",
                    self.config.database_url.split("@")[-1] if "@" in self.config.database_url else "database",
                    e,
                )
                raise DatabaseConnectionError(
                    f"PostgreSQL connection to Supabase failed: {e}. "
                    "Application will not start or continue with missing production database."
                ) from e

    def get_connection(self) -> PostgresConnectionWrapper:
        """
        Acquire a pooled connection wrapped in PostgresConnectionWrapper.
        """
        pool = self._get_pool()
        try:
            raw_conn = pool.getconn(timeout=self.config.connect_timeout)
            return PostgresConnectionWrapper(raw_conn, pool=pool)
        except Exception as e:
            logger.error("Failed to acquire connection from PostgreSQL pool: %s", e)
            raise DatabaseConnectionError(f"Database connection pool exhausted or failed: {e}") from e

    @classmethod
    def close_pool(cls):
        """Cleanly close connection pool on application shutdown."""
        with cls._pool_lock:
            if cls._pool is not None:
                try:
                    cls._pool.close()
                    logger.info("Supabase PostgreSQL connection pool closed.")
                except Exception as e:
                    logger.warning("Error closing connection pool: %s", e)
                finally:
                    cls._pool = None
