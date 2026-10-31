# Archived Legacy Supabase / PostgreSQL Artifacts

This directory contains retired artifacts from LearnAI's legacy Phase 2 Supabase PostgreSQL architecture.

## Architecture Context

LearnAI has migrated to:
- **Production:** TiDB Cloud Serverless (MySQL 8.0 wire protocol via PyMySQL + DBUtils connection pooling)
- **Local Development & Testing:** SQLite with WAL mode (`backend/data/learnai.db`)

PostgreSQL / Supabase is no longer used by the application in development, testing, or production. Production startup guards explicitly reject PostgreSQL URLs (`postgresql://` or `postgres://`) to prevent misconfiguration.

## Archived Files

| File | Original Location | Purpose & Reason for Archival |
| :--- | :--- | :--- |
| `001_initial_supabase_schema.sql` | `backend/migrations/` | Initial Supabase PostgreSQL schema with TIMESTAMPTZ, JSONB, and UUID primary keys. Superseded by `001_initial_mysql_schema.sql`. Moved here so migration discovery in `backend/migrations/` only sees MySQL migrations. |
| `001_initial_supabase_schema.rollback.sql` | `backend/migrations/` | Rollback script for the initial Supabase schema. |
| `verify_supabase_target.py` | `backend/scripts/` | Script for testing connection, SSL, and latency to Supabase PostgreSQL endpoints. Superseded by MySQL verification scripts. |
| `migrate_sqlite_to_postgres.py` | `backend/scripts/` | One-way data migration utility from SQLite to Supabase PostgreSQL. Superseded by MySQL migration tooling. |
| `test_phase2_stage_m_postgres_migration.py` | `backend/` | Stage M automated unit test suite for SQLite-to-PostgreSQL translation and migration. |
| `test_phase2_stage_o_postgres_integration.py` | `backend/` | Stage O live integration tests against Supabase PostgreSQL endpoints. Superseded by `test_phase2_stage_o_mysql_integration.py`. |
| `test_phase2_stage_p_postgres_application.py` | `backend/` | Stage P application workflow tests targeting Supabase PostgreSQL. |
| `postgres_adapter.py` | `backend/services/db/` | Psycopg 3 connection wrapper and adapter for PostgreSQL. Superseded by `mysql_adapter.py`. |

## Safety & Isolation

- None of these files are discovered, executed, or imported by the runtime application, migration runner, or active test suites.
- Production environment guards in `backend/test_phase2_stage_q_production_guards.py` continue to ensure that any accidental `postgresql://` configuration is rejected immediately at startup.
