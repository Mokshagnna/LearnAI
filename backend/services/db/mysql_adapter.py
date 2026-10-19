"""
mysql_adapter.py
================
MySQL database adapter for LearnAI production deployment.
Uses PyMySQL with DBUtils.PooledDB connection pooling, fail-fast error handling,
parameter placeholder translation, and CompatibleRow mapping.
"""

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse, parse_qs, unquote

from services.db.base import (
    CompatibleRow,
    DatabaseConfig,
    DatabaseConnectionError,
    translate_placeholders,
)

logger = logging.getLogger("learnai.db.mysql")


class MySQLCursorWrapper:
    """
    Cursor wrapper for PyMySQL cursor:
    - Translates SQLite '?' parameter placeholders to MySQL '%s' placeholders.
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

    @property
    def lastrowid(self) -> Any:
        return getattr(self._cursor, "lastrowid", None)

    def execute(self, query: str, params: Optional[Union[Tuple[Any, ...], List[Any]]] = None) -> "MySQLCursorWrapper":
        mysql_query = translate_placeholders(query)
        if params is not None:
            exec_params = tuple(params) if isinstance(params, (list, tuple)) else params
            self._cursor.execute(mysql_query, exec_params)
        else:
            self._cursor.execute(mysql_query)
        return self

    def executemany(self, query: str, params_seq: List[Union[Tuple[Any, ...], List[Any]]]) -> "MySQLCursorWrapper":
        mysql_query = translate_placeholders(query)
        self._cursor.executemany(mysql_query, params_seq)
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


class MySQLConnectionWrapper:
    """
    Connection wrapper that returns wrapped cursors and manages transaction / pool lifecycle.
    """

    def __init__(self, raw_conn: Any, pool: Optional[Any] = None):
        self._conn = raw_conn
        self._pool = pool
        self._closed = False
        self._is_mysql = True
        self.dialect = "mysql"

    @property
    def autocommit(self) -> bool:
        return getattr(self._conn, "autocommit_mode", False)

    @autocommit.setter
    def autocommit(self, value: bool):
        if hasattr(self._conn, "autocommit"):
            self._conn.autocommit(value)

    def cursor(self) -> MySQLCursorWrapper:
        raw_cur = self._conn.cursor()
        return MySQLCursorWrapper(raw_cur)

    def execute(self, query: str, params: Optional[Union[Tuple[Any, ...], List[Any]]] = None) -> MySQLCursorWrapper:
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
        try:
            self._conn.close()
        except Exception as e:
            logger.warning("Error closing / returning MySQL connection to pool: %s", e)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


class MySQLAdapter:
    """
    Adapter for MySQL database connections with pooling via DBUtils.PooledDB.
    Enforces fail-fast behavior when DATABASE_URL is configured for MySQL.
    """

    _pool_lock = threading.Lock()
    _pool = None

    def __init__(self, config: Optional[DatabaseConfig] = None):
        self.config = config or DatabaseConfig()
        if not self.config.is_mysql:
            raise DatabaseConnectionError(
                "Cannot initialize MySQLAdapter: configured database URL is not a MySQL connection string."
            )

    @staticmethod
    def parse_connection_params(url: str) -> Dict[str, Any]:
        """
        Parse MySQL URI into PyMySQL connection keyword arguments.
        Supports:
        - mysql://user:password@host:port/database?ssl_mode=REQUIRED
        - mysql+pymysql://user:password@host:port/database
        Handles URL-encoded credentials safely.
        """
        parsed = urlparse(url)
        username = unquote(parsed.username) if parsed.username else ""
        password = unquote(parsed.password) if parsed.password else ""
        hostname = parsed.hostname or "localhost"
        port = parsed.port or 3306
        database = parsed.path.lstrip("/") if parsed.path else ""

        qs = parse_qs(parsed.query)
        params: Dict[str, Any] = {
            "user": username,
            "password": password,
            "host": hostname,
            "port": port,
            "database": database,
            "charset": "utf8mb4",
            "autocommit": False,
        }

        # SSL Configuration
        ssl_mode = qs.get("ssl_mode", qs.get("ssl-mode", [""]))[0].upper()
        ssl_enabled = qs.get("ssl", [""])[0].lower() in ("true", "1", "required")

        if ssl_mode in ("REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY") or ssl_enabled:
            ssl_kwargs: Dict[str, Any] = {}
            if "ssl_ca" in qs:
                ssl_kwargs["ca"] = qs["ssl_ca"][0]
            if "ssl_cert" in qs:
                ssl_kwargs["cert"] = qs["ssl_cert"][0]
            if "ssl_key" in qs:
                ssl_kwargs["key"] = qs["ssl_key"][0]
            params["ssl"] = ssl_kwargs if ssl_kwargs else {"check_hostname": False}

        return params

    def _get_pool(self):
        if MySQLAdapter._pool is not None:
            return MySQLAdapter._pool

        with MySQLAdapter._pool_lock:
            if MySQLAdapter._pool is not None:
                return MySQLAdapter._pool

            conn_params = self.parse_connection_params(self.config.database_url)
            try:
                import pymysql
                import pymysql.constants.CLIENT
                from dbutils.pooled_db import PooledDB

                logger.info(
                    "Initializing MySQL connection pool (min=%d, max=%d, timeout=%.1fs)...",
                    self.config.pool_min_size,
                    self.config.pool_max_size,
                    self.config.connect_timeout,
                )

                # Enable MULTI_STATEMENTS so SQL migration scripts can run safely
                client_flag = pymysql.constants.CLIENT.MULTI_STATEMENTS

                pool = PooledDB(
                    creator=pymysql,
                    mincached=self.config.pool_min_size,
                    maxconnections=self.config.pool_max_size,
                    blocking=True,
                    maxusage=None,
                    setsession=["SET time_zone = '+00:00'"],
                    ping=1,  # Ping on checkout to ensure connection is live
                    connect_timeout=int(self.config.connect_timeout),
                    client_flag=client_flag,
                    **conn_params
                )

                # Test connectivity immediately (Fail-Fast)
                test_conn = pool.connection()
                try:
                    with test_conn.cursor() as cur:
                        cur.execute("SELECT 1;")
                        cur.fetchone()
                finally:
                    test_conn.close()

                logger.info("MySQL connection pool initialized and verified successfully.")
                MySQLAdapter._pool = pool
                return MySQLAdapter._pool
            except Exception as e:
                host_label = conn_params.get("host", "database")
                logger.critical(
                    "CRITICAL: Failed to connect to MySQL database at %s: %s. "
                    "Fail-fast safeguard triggered. Will NEVER fall back silently to SQLite.",
                    host_label,
                    e,
                )
                raise DatabaseConnectionError(
                    f"MySQL connection failed: {e}. "
                    "Application will not start or continue with missing production database."
                ) from e

    def get_connection(self) -> MySQLConnectionWrapper:
        """
        Acquire a pooled connection wrapped in MySQLConnectionWrapper.
        """
        pool = self._get_pool()
        try:
            raw_conn = pool.connection()
            return MySQLConnectionWrapper(raw_conn, pool=pool)
        except Exception as e:
            logger.error("Failed to acquire connection from MySQL pool: %s", e)
            raise DatabaseConnectionError(f"Database connection pool exhausted or failed: {e}") from e

    @classmethod
    def close_pool(cls):
        """Cleanly close connection pool on application shutdown."""
        with cls._pool_lock:
            if cls._pool is not None:
                try:
                    cls._pool.close()
                    logger.info("MySQL connection pool closed.")
                except Exception as e:
                    logger.warning("Error closing MySQL connection pool: %s", e)
                finally:
                    cls._pool = None
