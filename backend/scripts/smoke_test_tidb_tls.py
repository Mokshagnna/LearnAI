#!/usr/bin/env python3
"""
smoke_test_tidb_tls.py
======================
Standalone read-only TLS smoke test for TiDB Cloud Serverless / MySQL.

Requirements:
- Reads connection URL strictly from TEST_MYSQL_DATABASE_URL.
- Enforces ssl_mode=VERIFY_IDENTITY (full certificate and hostname verification).
- Rejects non-disposable or production targets.
- Executes solely: SELECT VERSION()
- Never runs migrations, table creation, inserts, updates, or deletes.
- Strictly protects secrets: never prints password, full URL, or username.
- Closes cursor and connection reliably in finally blocks.
"""

import os
import sys
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.db.mysql_adapter import MySQLAdapter
from scripts.migrate_sqlite_to_mysql import is_verified_mysql_test_target, is_production_target


def sanitize_error_message(error: Exception, raw_url: Optional[str] = None) -> str:
    """Sanitize error messages to ensure no credentials or sensitive URLs are exposed."""
    msg = str(error)
    if raw_url:
        try:
            parsed = urlparse(raw_url)
            if parsed.password and parsed.password in msg:
                msg = msg.replace(parsed.password, "******")
            if parsed.username and parsed.username in msg:
                msg = msg.replace(parsed.username, "[USER]")
        except Exception:
            pass
    msg = re.sub(r":([^:@\s]+)@", ":******@", msg)
    return msg


def validate_smoke_test_url(url: Optional[str]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate that the URL satisfies strict read-only TiDB TLS smoke test rules.
    Rules:
    1. URL must be non-empty string.
    2. URL must use mysql:// or mysql+pymysql:// scheme.
    3. URL must pass is_verified_mysql_test_target (checks production guards and test db name).
    4. Query parameter ssl_mode MUST be VERIFY_IDENTITY (no weaker mode permitted).
    5. Query parameter ssl_ca MUST point to an existing readable file.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return False, "Environment variable TEST_MYSQL_DATABASE_URL is not set or empty.", {}

    trimmed = url.strip()
    try:
        parsed = urlparse(trimmed)
    except Exception as e:
        return False, f"Failed to parse TEST_MYSQL_DATABASE_URL: {e}", {}

    if parsed.scheme not in ("mysql", "mysql+pymysql"):
        return False, f"Invalid scheme '{parsed.scheme}'. Must be 'mysql' or 'mysql+pymysql'.", {}

    # Check target safeguards
    if is_production_target(trimmed):
        return False, "Rejected: Target URL was flagged by production target indicators.", {}

    if not is_verified_mysql_test_target(trimmed):
        return False, (
            "Rejected: Target URL is not a verified disposable test target. "
            "Remote targets require database name containing 'test' or 'dev' "
            "and CONFIRM_DISPOSABLE_TEST_TARGET=true."
        ), {}

    # Enforce strictly ssl_mode=VERIFY_IDENTITY
    qs = parse_qs(parsed.query)
    ssl_mode_val = qs.get("ssl_mode", qs.get("ssl-mode", [""]))[0].strip().upper().replace("-", "_")
    if ssl_mode_val != "VERIFY_IDENTITY":
        return False, (
            f"Rejected: ssl_mode is '{ssl_mode_val or 'MISSING'}'. "
            "TiDB TLS smoke test strictly requires ssl_mode=VERIFY_IDENTITY "
            "for full certificate chain and hostname verification."
        ), {}

    # Enforce valid CA file
    ca_file = qs.get("ssl_ca", [None])[0]
    if not ca_file:
        return False, "Rejected: ssl_ca parameter is missing. A valid CA certificate path is required.", {}

    if not Path(ca_file).is_file():
        return False, f"Rejected: CA certificate file not found at '{ca_file}'.", {}

    # Parse connection parameters using existing MySQLAdapter
    try:
        params = MySQLAdapter.parse_connection_params(trimmed)
    except Exception as e:
        return False, f"Failed to parse MySQL connection parameters: {sanitize_error_message(e, trimmed)}", {}

    return True, "Validation successful.", params


def run_smoke_test(
    url: Optional[str] = None,
    connect_timeout: int = 10,
    connect_fn: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Execute read-only TLS smoke test against TiDB/MySQL target.
    Executes solely: SELECT VERSION()
    """
    target_url = url if url is not None else os.getenv("TEST_MYSQL_DATABASE_URL")
    valid, message, params = validate_smoke_test_url(target_url)

    if not valid:
        return {
            "status": "FAIL",
            "error": message,
            "server_version": None,
            "host": None,
            "port": None,
            "database": None,
            "tls_mode": None,
        }

    host = params.get("host")
    port = params.get("port")
    database = params.get("database")
    tls_mode = "VERIFY_IDENTITY"

    conn = None
    cur = None
    try:
        import pymysql

        fn = connect_fn or pymysql.connect
        conn_params = dict(params)
        conn_params["connect_timeout"] = connect_timeout

        conn = fn(**conn_params)
        cur = conn.cursor()

        # Strictly execute only SELECT VERSION()
        cur.execute("SELECT VERSION();")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("Database returned empty result for SELECT VERSION().")

        version_str = row[0] if isinstance(row, (tuple, list)) else row.get("VERSION()", str(row))
        return {
            "status": "PASS",
            "server_version": str(version_str).strip(),
            "host": host,
            "port": port,
            "database": database,
            "tls_mode": tls_mode,
            "error": None,
        }
    except Exception as e:
        sanitized = sanitize_error_message(e, target_url)
        return {
            "status": "FAIL",
            "server_version": None,
            "host": host,
            "port": port,
            "database": database,
            "tls_mode": tls_mode,
            "error": sanitized,
        }
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def main() -> int:
    """CLI entrypoint for standalone smoke test."""
    print("=" * 60)
    print("LearnAI -- Read-Only TiDB TLS Connection Smoke Test")
    print("=" * 60)

    result = run_smoke_test()

    if result["status"] == "PASS":
        print("[PASS] TLS Handshake and Query Succeeded!")
        print(f"  Target Host:    {result['host']}")
        print(f"  Target Port:    {result['port']}")
        print(f"  Database:       {result['database']}")
        print(f"  TLS Mode:       {result['tls_mode']}")
        print(f"  Server Version: {result['server_version']}")
        print("=" * 60)
        return 0
    else:
        print("[FAIL] TiDB TLS Smoke Test Failed.")
        if result.get("host"):
            print(f"  Target Host:    {result['host']}")
            print(f"  Target Port:    {result['port']}")
            print(f"  Database:       {result['database']}")
            print(f"  TLS Mode:       {result['tls_mode']}")
        print(f"  Error Detail:   {result['error']}")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
