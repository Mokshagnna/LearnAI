#!/usr/bin/env python3
"""
verify_supabase_target.py
=========================
Safely checks Supabase PostgreSQL target connectivity and identity
WITHOUT exposing passwords or secrets.

Checks:
- Host, port, database name, current user, current schema
- PostgreSQL version string
- Supavisor connection mode (transaction pooler port 6543 vs session pooler port 5432)
- SSL/TLS encryption status via psycopg3 pgconn.ssl_in_use
- Existing table inventory and row counts (confirming clean/empty target)
"""

import os
import sys
import logging
from urllib.parse import urlparse
from pathlib import Path

# Add backend directory to sys.path and load backend/.env relative to script
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

env_file = backend_dir / ".env"
if env_file.is_file():
    try:
        from dotenv import load_dotenv
        # override=False preserves any explicitly exported shell environment variables
        load_dotenv(env_file, override=False)
    except ImportError:
        pass

from services.db.base import DatabaseConfig
from services.db.postgres_adapter import PostgresAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("learnai.verify_target")


def mask_connection_url(url: str) -> str:
    """Mask password from connection URL for safe logging and display."""
    if not url:
        return "<not configured>"
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if "@" in netloc:
            creds, host_part = netloc.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                netloc = f"{user}:***@{host_part}"
            else:
                netloc = f"{creds}:***@{host_part}"
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    except Exception:
        return "postgresql://***:***@***"


def verify_target(target_url: str = None) -> dict:
    """
    Verify PostgreSQL target connectivity.
    Precedence:
    1. Explicitly passed target_url argument
    2. TEST_DATABASE_URL environment variable
    3. DATABASE_URL environment variable
    4. SUPABASE_DB_URL environment variable
    """
    url = target_url or os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if not url:
        raise ValueError(
            "No target PostgreSQL URL provided. Set TEST_DATABASE_URL in backend/.env or pass --target-url."
        )

    masked_url = mask_connection_url(url)
    logger.info("Verifying PostgreSQL target: %s", masked_url)

    normalized_url = PostgresAdapter.normalize_database_url(url)
    parsed = urlparse(normalized_url)

    info = {
        "target_endpoint": parsed.hostname,
        "port": parsed.port or 5432,
        "database_name": parsed.path.lstrip("/"),
        "user": parsed.username,
        "masked_url": masked_url,
        "is_pooler": "pooler.supabase.com" in (parsed.hostname or ""),
        "ssl_mode": "require",
    }

    import psycopg

    try:
        with psycopg.connect(normalized_url, connect_timeout=10) as conn:
            # 1. SSL/TLS Verification via psycopg 3
            try:
                info["ssl_in_use"] = bool(conn.pgconn.ssl_in_use)
            except Exception as e:
                logger.warning("Could not check ssl_in_use on pgconn: %s", e)
                info["ssl_in_use"] = "unknown"

            with conn.cursor() as cur:
                # 2. Server Version
                cur.execute("SELECT version();")
                info["server_version"] = cur.fetchone()[0]

                # 3. Database & User & Schema
                cur.execute("SELECT current_database(), current_user, current_schema();")
                db_name, curr_user, curr_schema = cur.fetchone()
                info["current_database"] = db_name
                info["current_user"] = curr_user
                info["current_schema"] = curr_schema

                # 4. Existing User Tables in 'public' schema
                cur.execute("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name;
                """)
                tables = [r[0] for r in cur.fetchall()]
                info["existing_tables"] = tables
                info["is_clean_slate"] = len(tables) == 0

                # 5. Table row counts if any tables exist
                table_counts = {}
                for t in tables:
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {t};")
                        table_counts[t] = cur.fetchone()[0]
                    except Exception:
                        table_counts[t] = "error"
                info["table_counts"] = table_counts

        info["connection_status"] = "successful"
        return info

    except Exception as e:
        info["connection_status"] = "failed"
        info["error"] = str(e)
        return info


def main():
    target_url = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        result = verify_target(target_url)
        print("\n=== SUPABASE TARGET VERIFICATION REPORT ===")
        print(f"Connection Status : {result.get('connection_status')}")
        print(f"Target Host       : {result.get('target_endpoint')}")
        pooler_mode = "Supavisor Pooler (Port 6543 / Transaction)" if result.get('port') == 6543 else "Supavisor Pooler (Port 5432 / Session)" if result.get('is_pooler') else "Direct Postgres"
        print(f"Target Port       : {result.get('port')} ({pooler_mode})")
        print(f"Database Name     : {result.get('database_name')}")
        print(f"User / Ref        : {result.get('user')}")
        print(f"SSL In Use        : {result.get('ssl_in_use')}")
        print(f"Server Version    : {result.get('server_version', 'N/A')}")
        print(f"Clean Slate       : {result.get('is_clean_slate')} ({len(result.get('existing_tables', []))} tables found)")
        if result.get("existing_tables"):
            print(f"Existing Tables   : {result.get('existing_tables')}")
            print(f"Row Counts        : {result.get('table_counts')}")
        if result.get("error"):
            print(f"Error Details     : {result.get('error')}")
            sys.exit(1)
        sys.exit(0)
    except Exception as e:
        print(f"\nTarget verification failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
