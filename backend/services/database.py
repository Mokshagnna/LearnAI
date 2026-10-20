import sqlite3
import json
import uuid
import logging
import re
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple, Union

from services.db.base import (
    CompatibleRow,
    DatabaseConfig,
    DatabaseConnectionError,
    translate_placeholders_to_pg,
    parse_json_safely,
    parse_json_column,
    serialize_json_for_db,
    is_production_mode,
)
from services.db.sqlite_adapter import SQLiteAdapter
from services.db.postgres_adapter import (
    PostgresAdapter,
    PostgresConnectionWrapper,
    PostgresCursorWrapper,
)
from services.db.mysql_adapter import (
    MySQLAdapter,
    MySQLConnectionWrapper,
    MySQLCursorWrapper,
)
from services.db.migrations import apply_migrations

logger = logging.getLogger("learnai.database")

_db_config = DatabaseConfig()
_sqlite_adapter = SQLiteAdapter(_db_config)
_postgres_adapter: Optional[PostgresAdapter] = None
_mysql_adapter: Optional[MySQLAdapter] = None


def get_db_config() -> DatabaseConfig:
    global _db_config
    return _db_config


def is_postgres_active() -> bool:
    return _db_config.is_postgres


def is_mysql_active() -> bool:
    return _db_config.is_mysql


def is_pg_conn(conn: Any) -> bool:
    if isinstance(conn, PostgresConnectionWrapper):
        return True
    if hasattr(conn, "_is_mysql") or getattr(conn, "dialect", None) == "mysql" or isinstance(conn, MySQLConnectionWrapper):
        return False
    return hasattr(conn, "_pool")


def is_mysql_conn(conn: Any) -> bool:
    return (
        isinstance(conn, MySQLConnectionWrapper)
        or getattr(conn, "_is_mysql", False)
        or getattr(conn, "dialect", None) == "mysql"
        or type(conn).__name__ == "MySQLConnectionWrapper"
    )


def get_utc_now() -> datetime:
    """Return naive UTC datetime matching existing database format."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_utc_now_iso() -> str:
    """Return ISO-8601 string in UTC matching existing database format."""
    return get_utc_now().isoformat()

# ── Database Path & Configuration ─────────────────────────────────────────────

def get_db_path() -> Path:
    """
    Resolve SQLite database path from environment or default local data directory.
    Precedence:
    1. LEARNAI_DB_PATH: Primary production persistent mount path (e.g. /var/data/learnai.db)
    2. SQLITE_DB_PATH: Alternative/legacy configuration path
    3. Default: backend/data/learnai.db

    Explicitly handles directory creation and errors; does not silently fallback
    to unintended locations.
    """
    custom_path = os.getenv("LEARNAI_DB_PATH") or os.getenv("SQLITE_DB_PATH")
    if custom_path:
        p = Path(custom_path).resolve()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Failed to create database directory for %s: %s", p, e)
            raise RuntimeError(f"Cannot initialize database directory at {p.parent}: {e}") from e
        return p

    default_p = Path(__file__).resolve().parent.parent / "data" / "learnai.db"
    try:
        default_p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error("Failed to create default database directory for %s: %s", default_p, e)
        raise RuntimeError(f"Cannot initialize default database directory at {default_p.parent}: {e}") from e
    return default_p


DB_PATH = get_db_path()

# Configurable Session Expiration (default 24 hours / 86400s)
DEFAULT_SESSION_TTL_SECONDS = int(os.getenv("QUIZ_SESSION_EXPIRATION_SECONDS", "86400"))

# Configurable Adaptive Engine Thresholds
ADAPTIVE_PROMOTION_THRESHOLD = float(os.getenv("ADAPTIVE_PROMOTION_THRESHOLD", "0.80"))
ADAPTIVE_DEMOTION_THRESHOLD = float(os.getenv("ADAPTIVE_DEMOTION_THRESHOLD", "0.40"))
ADAPTIVE_MIN_QUESTIONS_THRESHOLD = int(os.getenv("ADAPTIVE_MIN_QUESTIONS_THRESHOLD", "5"))

# Valid Adaptive Levels in Step-Wise Progression
ADAPTIVE_LEVEL_FOUNDATIONAL = "foundational"
ADAPTIVE_LEVEL_MIXED = "mixed"
ADAPTIVE_LEVEL_ADVANCED = "advanced"
VALID_ADAPTIVE_LEVELS = {ADAPTIVE_LEVEL_FOUNDATIONAL, ADAPTIVE_LEVEL_MIXED, ADAPTIVE_LEVEL_ADVANCED}


def normalise_text(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def reset_db_adapter() -> None:
    """Reset cached PostgreSQL and MySQL adapters and pools for test isolation."""
    global _postgres_adapter, _mysql_adapter
    if _postgres_adapter is not None:
        try:
            PostgresAdapter.close_pool()
        except Exception:
            pass
        _postgres_adapter = None
    if _mysql_adapter is not None:
        try:
            MySQLAdapter.close_pool()
        except Exception:
            pass
        _mysql_adapter = None


def get_connection(db_path: Optional[Path] = None) -> Any:
    """
    Acquire database connection.
    If running in production:
        Requires PostgreSQL or MySQL via DATABASE_URL; explicit SQLite db_path or missing
        production database configuration raises DatabaseConnectionError (no silent fallback).
    If db_path is explicitly provided in non-production:
        Always uses SQLite for that path (essential for test isolation).
    If db_path is None in non-production:
        If DATABASE_URL or MYSQL_DATABASE_URL is MySQL: uses MySQLAdapter with connection pool.
        If DATABASE_URL or SUPABASE_DB_URL is PostgreSQL: uses PostgresAdapter with connection pool.
        Else: uses SQLiteAdapter with WAL mode and busy timeout.
    """
    global _postgres_adapter, _mysql_adapter

    if is_production_mode():
        if db_path is not None:
            logger.critical(
                "CRITICAL: Explicit SQLite db_path provided in production mode. "
                "SQLite is strictly prohibited in production."
            )
            raise DatabaseConnectionError(
                "Production configuration error: Explicit SQLite path is prohibited in production mode."
            )
        if _db_config.is_postgres:
            logger.critical(
                "CRITICAL: PostgreSQL connection configured in production mode. "
                "This deployment requires MySQL exclusively."
            )
            raise DatabaseConnectionError(
                "Production configuration error: PostgreSQL is prohibited in production. "
                "MySQL connection is required."
            )
        if not _db_config.is_mysql:
            logger.critical(
                "CRITICAL: get_connection() invoked in production mode without valid MySQL DATABASE_URL configured. "
                "Silent fallback to SQLite is prohibited in production."
            )
            raise DatabaseConnectionError(
                "Production configuration error: MySQL connection is required in production. "
                "Silent fallback to SQLite is prohibited."
            )
        if _mysql_adapter is None:
            _mysql_adapter = MySQLAdapter(_db_config)
        return _mysql_adapter.get_connection()

    if db_path is not None:
        return _sqlite_adapter.get_connection(db_path=db_path)

    if _db_config.is_mysql:
        if _mysql_adapter is None:
            _mysql_adapter = MySQLAdapter(_db_config)
        return _mysql_adapter.get_connection()

    if _db_config.is_postgres:
        if _postgres_adapter is None:
            _postgres_adapter = PostgresAdapter(_db_config)
        return _postgres_adapter.get_connection()

    return _sqlite_adapter.get_connection()


def _ensure_column(cursor: Any, table: str, column: str, column_type: str):
    """Safely add a column to an existing table if it does not already exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        logger.info("Migrated schema: added column %s to table %s", column, table)


def init_db(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Initialize database schema with idempotent DDL, WAL journal mode, and safe migrations.
    - In production mode:
      Requires DATABASE_URL and executes MySQL or Supabase PostgreSQL migrations.
      Explicit SQLite path or missing DATABASE_URL raises DatabaseConnectionError (no silent fallback).
    - In development/test mode:
      If MySQL is configured: runs versioned MySQL migrations.
      If PostgreSQL is configured: runs versioned Supabase migrations.
      If db_path is provided or no production DB is configured: initializes SQLite.
    Preserves all existing data in students, quiz_attempts, and question_metadata.
    Adds quiz_sessions, student_answers, and topic_mastery.
    """
    global _postgres_adapter, _mysql_adapter

    if is_production_mode():
        if db_path is not None:
            logger.critical(
                "CRITICAL: Explicit SQLite db_path provided to init_db() in production mode. "
                "SQLite is strictly prohibited in production."
            )
            raise DatabaseConnectionError(
                "Production configuration error: Explicit SQLite path is prohibited in production mode."
            )
        if _db_config.is_postgres:
            logger.critical(
                "CRITICAL: PostgreSQL connection configured in production mode for init_db(). "
                "This deployment requires MySQL exclusively."
            )
            raise DatabaseConnectionError(
                "Production configuration error: PostgreSQL is prohibited in production. "
                "MySQL connection is required."
            )
        if not _db_config.is_mysql:
            logger.critical(
                "CRITICAL: init_db() invoked in production mode without DATABASE_URL configured. "
                "Silent fallback to SQLite is prohibited in production."
            )
            raise DatabaseConnectionError(
                "Production configuration error: DATABASE_URL is required in production. "
                "Silent fallback to SQLite is prohibited."
            )
        if _mysql_adapter is None:
            _mysql_adapter = MySQLAdapter(_db_config)
        conn = _mysql_adapter.get_connection()
        try:
            applied = apply_migrations(conn, dialect="mysql")
            logger.info("MySQL initialized successfully (applied migrations: %s)", applied)
            return {"status": "ok", "backend": "mysql", "applied_migrations": applied}
        finally:
            conn.close()

    if db_path is None and _db_config.is_mysql:
        if _mysql_adapter is None:
            _mysql_adapter = MySQLAdapter(_db_config)
        conn = _mysql_adapter.get_connection()
        try:
            applied = apply_migrations(conn, dialect="mysql")
            logger.info("MySQL initialized successfully (applied migrations: %s)", applied)
            return {"status": "ok", "backend": "mysql", "applied_migrations": applied}
        finally:
            conn.close()

    if db_path is None and _db_config.is_postgres:
        if _postgres_adapter is None:
            _postgres_adapter = PostgresAdapter(_db_config)
        conn = _postgres_adapter.get_connection()
        try:
            applied = apply_migrations(conn, is_postgres=True, dialect="postgres")
            logger.info("Supabase PostgreSQL initialized successfully (applied migrations: %s)", applied)
            return {"status": "ok", "backend": "postgres", "applied_migrations": applied}
        finally:
            conn.close()

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Enable WAL mode for high concurrency (non-blocking readers)
    cursor.execute("PRAGMA journal_mode = WAL;")
    jm_row = cursor.fetchone()
    active_journal_mode = jm_row[0] if jm_row else "unknown"
    cursor.execute("PRAGMA synchronous = NORMAL;")

    # 1. Existing Core Tables (preserved unchanged)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            last_active TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            course_code TEXT NOT NULL,
            course_name TEXT NOT NULL,
            topic_name TEXT NOT NULL,
            quiz_data TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            percentage REAL NOT NULL,
            status TEXT NOT NULL,
            difficulty_accuracy TEXT NOT NULL,
            concept_accuracy TEXT NOT NULL,
            weak_concepts TEXT NOT NULL,
            strong_concepts TEXT NOT NULL,
            recommended_difficulty TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS question_metadata (
            id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            course_code TEXT NOT NULL,
            topic_name TEXT NOT NULL,
            question_index INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            question_fingerprint TEXT NOT NULL,
            concept TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            question_type TEXT DEFAULT 'mcq',
            correct_answer TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (attempt_id) REFERENCES quiz_attempts (id)
        )
    """)

    # 2. Add optional session_id column to quiz_attempts if migrating from Phase 1
    _ensure_column(cursor, "quiz_attempts", "session_id", "TEXT REFERENCES quiz_sessions(id)")

    # 3. New Phase 2 Table: quiz_sessions (Session Persistence & Ownership)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_sessions (
            id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            course_code TEXT NOT NULL,
            course_name TEXT NOT NULL,
            topic_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'in_progress',
            questions_snapshot TEXT NOT NULL,
            recommended_difficulty TEXT DEFAULT 'mixed',
            adaptive_metadata TEXT,
            created_at TEXT NOT NULL,
            submitted_at TEXT,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    """)

    # 4. New Phase 2 Table: student_answers (Granular Question Responses)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_answers (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            question_index INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            selected_answer TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            difficulty TEXT NOT NULL,
            concept TEXT NOT NULL,
            explanation TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, question_index),
            FOREIGN KEY (session_id) REFERENCES quiz_sessions (id),
            FOREIGN KEY (attempt_id) REFERENCES quiz_attempts (id),
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    """)

    # 5. New Phase 2 Table: topic_mastery (Normalized Topic Performance)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topic_mastery (
            id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            course_code TEXT NOT NULL,
            topic_name TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            total_questions INTEGER NOT NULL DEFAULT 0,
            total_correct INTEGER NOT NULL DEFAULT 0,
            mastery_score REAL NOT NULL DEFAULT 0.0,
            current_level TEXT NOT NULL DEFAULT 'mixed',
            weak_concepts TEXT NOT NULL DEFAULT '[]',
            strong_concepts TEXT NOT NULL DEFAULT '[]',
            last_practiced_at TEXT NOT NULL,
            UNIQUE(student_id, course_code, topic_name),
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    """)

    # Indexes
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_quiz_attempts_student
        ON quiz_attempts (student_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_quiz_attempts_course_topic
        ON quiz_attempts (course_code, topic_name)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_question_metadata_student_topic
        ON question_metadata (student_id, course_code, topic_name)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_question_metadata_fingerprint
        ON question_metadata (question_fingerprint)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_quiz_sessions_student_status
        ON quiz_sessions (student_id, status)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_quiz_sessions_expires
        ON quiz_sessions (expires_at)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_student_answers_session
        ON student_answers (session_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_student_answers_student_concept
        ON student_answers (student_id, concept)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_student_answers_attempt
        ON student_answers (attempt_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_topic_mastery_lookup
        ON topic_mastery (student_id, course_code, topic_name)
    """)

    conn.commit()

    # 6. Auto-backfill topic_mastery from historical quiz_attempts if topic_mastery is empty
    cursor.execute("SELECT COUNT(*) FROM topic_mastery")
    mastery_count = cursor.fetchone()[0]
    if mastery_count == 0:
        cursor.execute("SELECT COUNT(*) FROM quiz_attempts")
        attempts_count = cursor.fetchone()[0]
        if attempts_count > 0:
            logger.info("Auto-migrating %d historical quiz attempts into topic_mastery...", attempts_count)
            backfill_topic_mastery_from_attempts(conn=conn)

    conn.close()
    actual_path = db_path or get_db_path()
    logger.info("Database initialized successfully at %s (journal_mode=%s)", actual_path, active_journal_mode)
    return {
        "status": "initialized",
        "backend": "sqlite",
        "path": str(actual_path),
        "journal_mode": str(active_journal_mode)
    }


# ── Student Management ────────────────────────────────────────────────────────

def get_or_create_student(student_id: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> str:
    """Retrieve existing student or create a new student record."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    now = get_utc_now_iso()

    if student_id and str(student_id).strip():
        sid = str(student_id).strip()
        cursor.execute("SELECT id FROM students WHERE id = ?", (sid,))
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE students SET last_active = ? WHERE id = ?",
                (now, sid)
            )
            conn.commit()
            if close_conn:
                conn.close()
            return sid
        else:
            cursor.execute(
                "INSERT INTO students (id, created_at, last_active) VALUES (?, ?, ?)",
                (sid, now, now)
            )
            conn.commit()
            if close_conn:
                conn.close()
            logger.info("Registered student: %s", sid)
            return sid

    new_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO students (id, created_at, last_active) VALUES (?, ?, ?)",
        (new_id, now, now)
    )
    conn.commit()
    if close_conn:
        conn.close()
    logger.info("Created new student: %s", new_id)
    return new_id


# ── Quiz Session Lifecycle Management ─────────────────────────────────────────

def create_quiz_session(
    session_id: str,
    student_id: str,
    course_code: str,
    course_name: str,
    topic_name: str,
    questions: List[Dict[str, Any]],
    recommended_difficulty: str = "mixed",
    adaptive_metadata: Optional[Dict[str, Any]] = None,
    duration_seconds: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Persist an active quiz session with questions snapshot and configurable TTL.
    Validates student ownership and sets initial status to 'in_progress'.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    now_dt = get_utc_now()
    created_at = now_dt.isoformat()
    ttl = duration_seconds if duration_seconds is not None else DEFAULT_SESSION_TTL_SECONDS
    expires_at = (now_dt + timedelta(seconds=ttl)).isoformat()

    # Ensure student exists
    get_or_create_student(student_id, conn=conn)

    cursor.execute("""
        INSERT INTO quiz_sessions (
            id, student_id, course_code, course_name, topic_name,
            status, questions_snapshot, recommended_difficulty,
            adaptive_metadata, created_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        student_id,
        course_code,
        course_name,
        topic_name,
        "in_progress",
        serialize_json_for_db(questions, is_postgres=is_pg_conn(conn)),
        recommended_difficulty,
        serialize_json_for_db(adaptive_metadata or {}, is_postgres=is_pg_conn(conn)),
        created_at,
        expires_at
    ))

    conn.commit()
    if close_conn:
        conn.close()

    logger.info(
        "Created quiz session %s for student %s (course=%s topic='%s' expires_at=%s)",
        session_id, student_id, course_code, topic_name, expires_at
    )
    return {
        "id": session_id,
        "student_id": student_id,
        "course_code": course_code,
        "course_name": course_name,
        "topic_name": topic_name,
        "status": "in_progress",
        "questions": questions,
        "recommended_difficulty": recommended_difficulty,
        "adaptive_metadata": adaptive_metadata or {},
        "created_at": created_at,
        "expires_at": expires_at
    }


def get_quiz_session(
    session_id: str,
    student_id: Optional[str] = None,
    auto_expire: bool = True,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """
    Retrieve quiz session by ID.
    - If student_id is provided, strictly enforces student ownership (raises PermissionError on mismatch).
    - If session is 'in_progress' and has expired, updates status to 'expired' (without deleting any historical data).
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM quiz_sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()

    if not row:
        if close_conn:
            conn.close()
        return None

    session = dict(row)

    # Normalize datetime fields to ISO strings for cross-backend consistency (PostgreSQL vs SQLite)
    for dt_col in ("created_at", "expires_at", "submitted_at"):
        val = session.get(dt_col)
        if isinstance(val, datetime):
            session[dt_col] = val.isoformat()

    # 1. Ownership Validation
    if student_id and session["student_id"] != student_id:
        if close_conn:
            conn.close()
        logger.warning(
            "Session ownership mismatch: session %s belongs to student %s, not %s",
            session_id, session["student_id"], student_id
        )
        raise PermissionError(f"Quiz session {session_id} does not belong to student {student_id}")

    # 2. Expiration Validation
    now_iso = get_utc_now_iso()
    exp_iso = session.get("expires_at") or ""
    if session["status"] == "in_progress" and exp_iso and now_iso > exp_iso:
        if auto_expire:
            cursor.execute("UPDATE quiz_sessions SET status = 'expired' WHERE id = ?", (session_id,))
            conn.commit()
            session["status"] = "expired"
            logger.info("Session %s transitioned to 'expired' (expired at %s)", session_id, exp_iso)

    # 3. Deserialize JSON snapshots
    session["questions"] = parse_json_safely(session.get("questions_snapshot"), default=[])
    session["adaptive_metadata"] = parse_json_safely(session.get("adaptive_metadata"), default={})

    if close_conn:
        conn.close()

    return session


def complete_quiz_session(
    session_id: str,
    student_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Transition an active quiz session to 'completed' upon successful submission.
    Enforces ownership, active status, expiration, and idempotency (prevents double submissions).
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    session = get_quiz_session(session_id, student_id=student_id, auto_expire=True, conn=conn)

    if not session:
        if close_conn:
            conn.close()
        raise ValueError(f"Quiz session {session_id} not found.")

    if session["status"] == "completed":
        if close_conn:
            conn.close()
        raise ValueError(f"Quiz session {session_id} has already been submitted.")

    if session["status"] == "expired":
        if close_conn:
            conn.close()
        raise ValueError(f"Quiz session {session_id} has expired and can no longer be submitted.")

    now_iso = get_utc_now_iso()
    cursor.execute(
        "UPDATE quiz_sessions SET status = 'completed', submitted_at = ? WHERE id = ?",
        (now_iso, session_id)
    )
    if close_conn:
        conn.commit()
    session["status"] = "completed"
    session["submitted_at"] = now_iso

    if close_conn:
        conn.close()

    logger.info("Completed quiz session %s for student %s at %s", session_id, student_id, now_iso)
    return session


# ── Granular Student Answer Tracking ──────────────────────────────────────────

def save_student_answers(
    session_id: str,
    attempt_id: str,
    student_id: str,
    answers_details: List[Dict[str, Any]],
    conn: Optional[sqlite3.Connection] = None
) -> int:
    """
    Persist per-question submitted responses into student_answers table.
    Enforces foreign keys and UNIQUE(session_id, question_index) duplicate-submission protection.
    Returns the count of saved answers.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    now = get_utc_now_iso()
    saved_count = 0

    for idx, item in enumerate(answers_details):
        ans_id = str(uuid.uuid4())
        q_text = item.get("question", "")
        sel_ans = item.get("selected_answer", "")
        corr_ans = item.get("correct_answer", "")
        is_corr = 1 if item.get("is_correct") else 0
        diff = item.get("difficulty", "Medium")
        concept = item.get("concept", "General")
        expl = item.get("explanation", "")

        cursor.execute("""
            INSERT INTO student_answers (
                id, session_id, attempt_id, student_id, question_index,
                question_text, selected_answer, correct_answer, is_correct,
                difficulty, concept, explanation, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ans_id, session_id, attempt_id, student_id, idx,
            q_text, sel_ans, corr_ans, is_corr,
            diff, concept, expl, now
        ))
        saved_count += 1

    if close_conn:
        conn.commit()
        conn.close()

    logger.info("Saved %d student answers for session %s attempt %s", saved_count, session_id, attempt_id)
    return saved_count


def get_student_answers_for_session(
    session_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """Retrieve all granular student answers for a given quiz session."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM student_answers
        WHERE session_id = ?
        ORDER BY question_index ASC
    """, (session_id,))
    rows = cursor.fetchall()
    if close_conn:
        conn.close()
    return [dict(r) for r in rows]


def get_student_answers_for_attempt(
    attempt_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """Retrieve all granular student answers for a given quiz attempt."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM student_answers
        WHERE attempt_id = ?
        ORDER BY question_index ASC
    """, (attempt_id,))
    rows = cursor.fetchall()
    if close_conn:
        conn.close()
    return [dict(r) for r in rows]


def get_attempt_by_session_id(
    session_id: str,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """
    Retrieve completed quiz attempt record associated with a quiz session.
    Used for idempotency: if a student submits a completed session again,
    returns the original attempt result without re-evaluating or duplicate writes.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM quiz_attempts
        WHERE session_id = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (session_id,))
    row = cursor.fetchone()
    if close_conn:
        conn.close()

    if not row:
        return None

    attempt = dict(row)
    attempt["quiz_data"] = parse_json_safely(attempt.get("quiz_data"), default={})
    attempt["difficulty_accuracy"] = parse_json_safely(attempt.get("difficulty_accuracy"), default={})
    attempt["concept_accuracy"] = parse_json_safely(attempt.get("concept_accuracy"), default={})
    attempt["weak_concepts"] = parse_json_safely(attempt.get("weak_concepts"), default=[])
    attempt["strong_concepts"] = parse_json_safely(attempt.get("strong_concepts"), default=[])

    return attempt


def format_attempt_result(
    attempt: Dict[str, Any],
    session: Dict[str, Any],
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """Format an existing completed attempt into the standard evaluation API response."""
    quiz_data = attempt.get("quiz_data", {})
    analysis = quiz_data.get("analysis", {
        "summary": "Completed quiz attempt.",
        "revision": "Review the question explanations below.",
        "recommendation": "Review the detailed question explanations below and practice more questions."
    })

    details = []
    student_answers = get_student_answers_for_session(session["id"], conn=conn)
    if student_answers:
        for sa in student_answers:
            details.append({
                "question": sa["question_text"],
                "selected_answer": sa["selected_answer"],
                "correct_answer": sa["correct_answer"],
                "is_correct": bool(sa["is_correct"]),
                "explanation": sa.get("explanation") or "",
                "concept": sa["concept"],
                "difficulty": sa["difficulty"]
            })

    return {
        "score": attempt["score"],
        "total": attempt["total"],
        "percentage": attempt["percentage"],
        "status": attempt["status"],
        "weak_concepts": attempt["weak_concepts"],
        "strong_concepts": attempt["strong_concepts"],
        "difficulty_accuracy": attempt["difficulty_accuracy"],
        "concept_accuracy": attempt["concept_accuracy"],
        "details": details,
        "analysis": analysis,
        "student_id": attempt["student_id"],
        "is_duplicate_submission": True
    }


def submit_quiz_atomic(
    session_id: str,
    student_id: Optional[str],
    answers: List[str],
    evaluator_fn: Any,
    analysis_generator_fn: Optional[Any] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Atomically validate, grade, and persist quiz submission.
    Guarantees:
    - Session existence, student ownership, status, and expiration validation.
    - Idempotency: duplicate submissions for already-completed sessions return the existing result.
    - Trusted server-side grading: evaluates answers exclusively against persisted questions_snapshot.
    - Atomic writes: session completion, quiz_attempt, question_metadata, student_answers, and topic_mastery
      are committed in a single database transaction. If any write fails, transaction rolls back cleanly.
    - Topic mastery updated exactly once per session.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        # 1. Retrieve session and validate existence, ownership, and expiration
        session = get_quiz_session(session_id, student_id=student_id, auto_expire=True, conn=conn)
        if not session:
            raise LookupError(f"Quiz session {session_id} not found.")

        # 2. Check session status
        if session["status"] == "completed":
            existing_attempt = get_attempt_by_session_id(session_id, conn=conn)
            if existing_attempt:
                logger.info("Duplicate submission for completed session %s; returning existing result.", session_id)
                res = format_attempt_result(existing_attempt, session, conn=conn)
                if close_conn:
                    conn.close()
                return res
            raise ValueError(f"Quiz session {session_id} has already been submitted.")

        if session["status"] == "expired":
            raise ValueError(f"Quiz session {session_id} has expired (exceeded 24 hours). Please generate a new quiz.")

        if session["status"] != "in_progress":
            raise ValueError(f"Cannot submit quiz: session status is '{session['status']}'.")

        # 3. Validate answers against trusted server questions
        questions = session.get("questions", [])
        if not isinstance(answers, list) or len(answers) != len(questions):
            raise ValueError(
                f"Submitted answers count ({len(answers) if isinstance(answers, list) else 0}) "
                f"does not match quiz question count ({len(questions)})."
            )

        for idx, ans in enumerate(answers):
            if not isinstance(ans, str):
                raise ValueError(f"Answer at index {idx} must be a string, got {type(ans).__name__}.")

        # 4. Grade answers using trusted server questions
        result = evaluator_fn(questions, answers)

        # 5. Generate AI analysis (or fallback)
        if analysis_generator_fn:
            try:
                analysis = analysis_generator_fn(
                    session["course_name"],
                    session["topic_name"],
                    result.get("weak_concepts", []),
                    result.get("score", 0),
                    result.get("total", len(questions))
                )
            except Exception as e:
                logger.warning("AI analysis generation error in submit_quiz_atomic: %s", e)
                weak_str = ", ".join(result.get("weak_concepts", [])) if result.get("weak_concepts") else "None identified"
                analysis = {
                    "summary": "AI feedback is temporarily unavailable. Please try again later.",
                    "revision": f"Review concepts: {weak_str}." if result.get("weak_concepts") else "Review the question explanations below.",
                    "recommendation": "Review the detailed question explanations below and practice more questions."
                }
        else:
            analysis = {
                "summary": "Quiz submitted successfully.",
                "revision": "Review the question explanations below.",
                "recommendation": "Review the detailed question explanations below and practice more questions."
            }
        result["analysis"] = analysis

        # 6. Atomic Database Transaction
        cursor = conn.cursor()
        now_iso = get_utc_now_iso()
        attempt_id = str(uuid.uuid4())
        eff_student_id = session["student_id"]

        # Step a: Mark session completed
        cursor.execute(
            "UPDATE quiz_sessions SET status = 'completed', submitted_at = ? WHERE id = ?",
            (now_iso, session_id)
        )

        # Step b: Insert quiz attempt
        quiz_data = {
            "subject": session["course_name"],
            "topic": session["topic_name"],
            "course_code": session["course_code"],
            "questions": questions,
            "recommended_difficulty": session.get("recommended_difficulty", "mixed"),
            "adaptive_explanation": session.get("adaptive_metadata", {}).get("adaptive_explanation"),
            "attempt_id": attempt_id,
            "analysis": analysis
        }

        cursor.execute("""
            INSERT INTO quiz_attempts (
                id, student_id, course_code, course_name, topic_name,
                quiz_data, score, total, percentage, status,
                difficulty_accuracy, concept_accuracy,
                weak_concepts, strong_concepts,
                recommended_difficulty, created_at, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            attempt_id,
            eff_student_id,
            session["course_code"],
            session["course_name"],
            session["topic_name"],
            serialize_json_for_db(quiz_data, is_postgres=is_pg_conn(conn)),
            result["score"],
            result["total"],
            result["percentage"],
            result["status"],
            serialize_json_for_db(result["difficulty_accuracy"], is_postgres=is_pg_conn(conn)),
            serialize_json_for_db(result["concept_accuracy"], is_postgres=is_pg_conn(conn)),
            serialize_json_for_db(result["weak_concepts"], is_postgres=is_pg_conn(conn)),
            serialize_json_for_db(result["strong_concepts"], is_postgres=is_pg_conn(conn)),
            session.get("recommended_difficulty"),
            now_iso,
            session_id
        ))

        # Step c: Save question metadata for novelty tracking
        for idx, q in enumerate(questions):
            q_id = str(uuid.uuid4())
            q_text = q.get("question", "")
            q_fp = normalise_text(q_text)
            cursor.execute("""
                INSERT INTO question_metadata (
                    id, attempt_id, student_id, course_code, topic_name,
                    question_index, question_text, question_fingerprint,
                    concept, difficulty, question_type, correct_answer, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                q_id, attempt_id, eff_student_id, session["course_code"],
                session["topic_name"], idx, q_text, q_fp,
                q.get("concept", ""), q.get("difficulty", ""),
                "mcq", q.get("correct_answer", ""), now_iso
            ))

        # Step d: Save granular student answers
        save_student_answers(session_id, attempt_id, eff_student_id, result["details"], conn=conn)

        # Step e: Update topic mastery exactly once
        upsert_topic_mastery(
            student_id=eff_student_id,
            course_code=session["course_code"],
            topic_name=session["topic_name"],
            score=result["score"],
            total=result["total"],
            weak_concepts=result["weak_concepts"],
            strong_concepts=result["strong_concepts"],
            current_level=session.get("recommended_difficulty"),
            conn=conn
        )

        if close_conn:
            conn.commit()

        result["student_id"] = eff_student_id
        logger.info(
            "Atomically submitted quiz session %s (attempt=%s score=%d/%d student=%s)",
            session_id, attempt_id, result["score"], result["total"], eff_student_id
        )
        return result

    except Exception:
        if close_conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if close_conn:
            try:
                conn.close()
            except Exception:
                pass


# ── Topic Mastery & Adaptive State Tracking ───────────────────────────────────

def get_topic_mastery(
    student_id: str,
    course_code: str,
    topic_name: str,
    conn: Optional[sqlite3.Connection] = None
) -> Optional[Dict[str, Any]]:
    """
    Retrieve normalized topic performance summary for student and topic.
    Returns None if no practice history exists for this topic.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM topic_mastery
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
    """, (student_id, course_code, topic_name))
    row = cursor.fetchone()
    if close_conn:
        conn.close()

    if not row:
        return None

    mastery = dict(row)
    mastery["weak_concepts"] = parse_json_safely(mastery.get("weak_concepts"), default=[])
    mastery["strong_concepts"] = parse_json_safely(mastery.get("strong_concepts"), default=[])

    return mastery


def compute_next_adaptive_level(
    current_level: str,
    total_questions: int,
    mastery_score: float,
    promotion_threshold: float = ADAPTIVE_PROMOTION_THRESHOLD,
    demotion_threshold: float = ADAPTIVE_DEMOTION_THRESHOLD,
    min_questions: int = ADAPTIVE_MIN_QUESTIONS_THRESHOLD
) -> str:
    """
    Step-wise adaptive difficulty transition policy:
        Foundational <-> Mixed <-> Advanced

    Heuristic Rules:
    1. Requires at least min_questions (default 5) before promotion or demotion.
    2. Moves at most one level per adaptive decision.
    3. Never jumps directly between Foundational and Advanced.
    4. Note: This heuristic provides pedagogical adaptation and is not a scientifically
       validated psychometric scale.
    """
    lvl = current_level.lower() if current_level else ADAPTIVE_LEVEL_MIXED
    if lvl not in VALID_ADAPTIVE_LEVELS:
        lvl = ADAPTIVE_LEVEL_MIXED

    if total_questions < min_questions:
        return lvl

    if mastery_score >= promotion_threshold:
        if lvl == ADAPTIVE_LEVEL_FOUNDATIONAL:
            return ADAPTIVE_LEVEL_MIXED
        elif lvl == ADAPTIVE_LEVEL_MIXED:
            return ADAPTIVE_LEVEL_ADVANCED
        else:
            return ADAPTIVE_LEVEL_ADVANCED
    elif mastery_score <= demotion_threshold:
        if lvl == ADAPTIVE_LEVEL_ADVANCED:
            return ADAPTIVE_LEVEL_MIXED
        elif lvl == ADAPTIVE_LEVEL_MIXED:
            return ADAPTIVE_LEVEL_FOUNDATIONAL
        else:
            return ADAPTIVE_LEVEL_FOUNDATIONAL
    else:
        return lvl


def upsert_topic_mastery(
    student_id: str,
    course_code: str,
    topic_name: str,
    score: int,
    total: int,
    weak_concepts: List[str],
    strong_concepts: List[str],
    current_level: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Update topic mastery upon quiz submission in a single database transaction.
    Maintains running count of attempts, questions, correct answers, and step-wise level.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM topic_mastery
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
    """, (student_id, course_code, topic_name))
    existing = cursor.fetchone()

    now = get_utc_now_iso()

    if existing:
        ex_dict = dict(existing)
        new_attempts = ex_dict["attempt_count"] + 1
        new_total_q = ex_dict["total_questions"] + total
        new_total_c = ex_dict["total_correct"] + score
        base_level = current_level or ex_dict["current_level"]

        # Merge weak concepts and strong concepts
        ex_weak = parse_json_safely(ex_dict.get("weak_concepts"), default=[])
        ex_strong = parse_json_safely(ex_dict.get("strong_concepts"), default=[])

        # Fresh strong concepts supersede older weak concepts
        merged_weak = [c for c in list(dict.fromkeys(ex_weak + weak_concepts)) if c not in strong_concepts]
        merged_strong = list(dict.fromkeys(ex_strong + strong_concepts))
    else:
        new_attempts = 1
        new_total_q = total
        new_total_c = score
        base_level = current_level or ADAPTIVE_LEVEL_MIXED
        merged_weak = list(dict.fromkeys(weak_concepts))
        merged_strong = list(dict.fromkeys(strong_concepts))

    mastery_score = round(new_total_c / new_total_q, 4) if new_total_q > 0 else 0.0

    # Derive next_level according to step-wise adaptive rules (50% benchmark)
    cursor.execute("""
        SELECT score, total, percentage, recommended_difficulty, created_at
        FROM quiz_attempts
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
        ORDER BY created_at ASC
    """, (student_id, course_code, topic_name))
    attempts_rows = [dict(r) for r in cursor.fetchall()]

    from services.adaptive import calculate_adaptive_progression
    if attempts_rows:
        prog = calculate_adaptive_progression(attempts_rows, initial_level=base_level)
        next_level = prog["recommended_difficulty"]
    else:
        # Standalone invocation without historical quiz_attempts rows (e.g. unit tests / migrations)
        next_level = compute_next_adaptive_level(base_level, new_total_q, mastery_score)

    mastery_id = str(uuid.uuid4())
    use_mysql = is_mysql_conn(conn) or (is_mysql_active() if conn is None else False)

    if use_mysql:
        cursor.execute("""
            INSERT INTO topic_mastery (
                id, student_id, course_code, topic_name, attempt_count,
                total_questions, total_correct, mastery_score, current_level,
                weak_concepts, strong_concepts, last_practiced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
                attempt_count = VALUES(attempt_count),
                total_questions = VALUES(total_questions),
                total_correct = VALUES(total_correct),
                mastery_score = VALUES(mastery_score),
                current_level = VALUES(current_level),
                weak_concepts = VALUES(weak_concepts),
                strong_concepts = VALUES(strong_concepts),
                last_practiced_at = VALUES(last_practiced_at)
        """, (
            mastery_id, student_id, course_code, topic_name, new_attempts,
            new_total_q, new_total_c, mastery_score, next_level,
            serialize_json_for_db(merged_weak, is_postgres=False),
            serialize_json_for_db(merged_strong, is_postgres=False),
            now
        ))
    else:
        cursor.execute("""
            INSERT INTO topic_mastery (
                id, student_id, course_code, topic_name, attempt_count,
                total_questions, total_correct, mastery_score, current_level,
                weak_concepts, strong_concepts, last_practiced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(student_id, course_code, topic_name) DO UPDATE SET
                attempt_count = excluded.attempt_count,
                total_questions = excluded.total_questions,
                total_correct = excluded.total_correct,
                mastery_score = excluded.mastery_score,
                current_level = excluded.current_level,
                weak_concepts = excluded.weak_concepts,
                strong_concepts = excluded.strong_concepts,
                last_practiced_at = excluded.last_practiced_at
        """, (
            mastery_id, student_id, course_code, topic_name, new_attempts,
            new_total_q, new_total_c, mastery_score, next_level,
            serialize_json_for_db(merged_weak, is_postgres=is_pg_conn(conn)),
            serialize_json_for_db(merged_strong, is_postgres=is_pg_conn(conn)),
            now
        ))

    if close_conn:
        conn.commit()

    updated = {
        "student_id": student_id,
        "course_code": course_code,
        "topic_name": topic_name,
        "attempt_count": new_attempts,
        "total_questions": new_total_q,
        "total_correct": new_total_c,
        "mastery_score": mastery_score,
        "current_level": next_level,
        "weak_concepts": merged_weak,
        "strong_concepts": merged_strong,
        "last_practiced_at": now
    }

    if close_conn:
        conn.close()

    logger.info(
        "Updated topic mastery: student=%s topic='%s' attempts=%d mastery=%.2f level=%s",
        student_id, topic_name, new_attempts, mastery_score, next_level
    )
    return updated


def backfill_topic_mastery_from_attempts(conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Backfill topic_mastery table from historical quiz_attempts in chronological order.
    Preserves all existing historical records and computes accurate step-wise progression.
    Returns the count of quiz attempts processed.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT student_id, course_code, topic_name, score, total, weak_concepts, strong_concepts, created_at
        FROM quiz_attempts
        ORDER BY created_at ASC
    """)
    rows = cursor.fetchall()

    processed_count = 0
    for r in rows:
        wc = parse_json_safely(r.get("weak_concepts"), default=[])
        sc = parse_json_safely(r.get("strong_concepts"), default=[])

        upsert_topic_mastery(
            student_id=r["student_id"],
            course_code=r["course_code"],
            topic_name=r["topic_name"],
            score=r["score"],
            total=r["total"],
            weak_concepts=wc,
            strong_concepts=sc,
            conn=conn
        )
        processed_count += 1

    conn.commit()
    if close_conn:
        conn.close()

    logger.info("Backfilled topic_mastery from %d historical quiz attempts", processed_count)
    return processed_count


# ── Question Bank Stored Question Retrieval ───────────────────────────────────

def get_stored_questions_for_topic(
    course_code: str,
    topic_name: str,
    difficulty: Optional[str] = None,
    exclude_fingerprints: Optional[List[str]] = None,
    limit: int = 10,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve validated stored questions from historical quiz attempts for a topic.
    Safeguards:
    - Never serves questions from unrelated courses or topics.
    - Excludes questions matching exclude_fingerprints.
    - Validates presence of text, options (A-D non-empty), and correct_answer.
    - Matches requested difficulty when specified.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT quiz_data FROM quiz_attempts
        WHERE course_code = ? AND topic_name = ?
        ORDER BY created_at DESC
    """, (course_code, topic_name))

    rows = cursor.fetchall()
    if close_conn:
        conn.close()

    seen_fingerprints = set(exclude_fingerprints or [])
    matching_questions: List[Dict[str, Any]] = []

    for row in rows:
        quiz_obj = parse_json_column(row, "quiz_data", default={})
        if not quiz_obj:
            continue

        questions = quiz_obj.get("questions", [])
        for q in questions:
            q_text = q.get("question", "").strip()
            if not q_text:
                continue

            fp = normalise_text(q_text)
            if fp in seen_fingerprints:
                continue

            options = q.get("options", {})
            if not isinstance(options, dict) or not all(k in options and str(options[k]).strip() for k in ["A", "B", "C", "D"]):
                continue

            correct = q.get("correct_answer", "").strip().upper()
            if correct not in {"A", "B", "C", "D"}:
                continue

            q_diff = str(q.get("difficulty", "Medium")).capitalize()
            if difficulty and q_diff.lower() != difficulty.lower():
                continue

            seen_fingerprints.add(fp)
            matching_questions.append({
                "question": q_text,
                "options": {
                    "A": str(options["A"]).strip(),
                    "B": str(options["B"]).strip(),
                    "C": str(options["C"]).strip(),
                    "D": str(options["D"]).strip()
                },
                "correct_answer": correct,
                "difficulty": q_diff,
                "concept": str(q.get("concept") or topic_name).strip(),
                "explanation": str(q.get("explanation") or "").strip()
            })

            if len(matching_questions) >= limit:
                return matching_questions

    return matching_questions


# ── Existing Attempt Persistence & Historical Query Functions ─────────────────

def save_quiz_attempt(
    student_id: str,
    course_code: str,
    course_name: str,
    topic_name: str,
    quiz_data: Dict[str, Any],
    score: int,
    total: int,
    percentage: float,
    status: str,
    difficulty_accuracy: Dict[str, Dict[str, int]],
    concept_accuracy: Dict[str, Dict[str, int]],
    weak_concepts: List[str],
    strong_concepts: List[str],
    recommended_difficulty: Optional[str] = None,
    session_id: Optional[str] = None,
    details: Optional[List[Dict[str, Any]]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> str:
    """
    Save complete quiz attempt, updating topic mastery and persisting granular answers.
    Maintains 100% backward compatibility for callers that do not supply session_id or details.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    # Ensure student exists so foreign key is always satisfied
    get_or_create_student(student_id, conn=conn)

    cursor = conn.cursor()
    attempt_id = str(uuid.uuid4())
    now = get_utc_now_iso()

    # 1. Insert into quiz_attempts
    cursor.execute("""
        INSERT INTO quiz_attempts (
            id, student_id, course_code, course_name, topic_name,
            quiz_data, score, total, percentage, status,
            difficulty_accuracy, concept_accuracy,
            weak_concepts, strong_concepts,
            recommended_difficulty, created_at, session_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        attempt_id,
        student_id,
        course_code,
        course_name,
        topic_name,
        serialize_json_for_db(quiz_data, is_postgres=is_pg_conn(conn)),
        score,
        total,
        percentage,
        status,
        serialize_json_for_db(difficulty_accuracy, is_postgres=is_pg_conn(conn)),
        serialize_json_for_db(concept_accuracy, is_postgres=is_pg_conn(conn)),
        serialize_json_for_db(weak_concepts, is_postgres=is_pg_conn(conn)),
        serialize_json_for_db(strong_concepts, is_postgres=is_pg_conn(conn)),
        recommended_difficulty,
        now,
        session_id
    ))

    # 2. Save question metadata for novelty tracking (existing behavior preserved)
    questions = quiz_data.get("questions", [])
    for idx, q in enumerate(questions):
        q_id = str(uuid.uuid4())
        q_text = q.get("question", "")
        q_fp = normalise_text(q_text)
        cursor.execute("""
            INSERT INTO question_metadata (
                id, attempt_id, student_id, course_code, topic_name,
                question_index, question_text, question_fingerprint,
                concept, difficulty, question_type, correct_answer, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            q_id,
            attempt_id,
            student_id,
            course_code,
            topic_name,
            idx,
            q_text,
            q_fp,
            q.get("concept", ""),
            q.get("difficulty", ""),
            "mcq",
            q.get("correct_answer", ""),
            now
        ))

    # 3. If session_id and details are provided, complete the session and save granular answers
    if session_id:
        try:
            complete_quiz_session(session_id, student_id, conn=conn)
        except Exception as e:
            logger.warning("Session completion check during save_quiz_attempt: %s", e)

        if details:
            try:
                save_student_answers(session_id, attempt_id, student_id, details, conn=conn)
            except Exception as e:
                logger.warning("Saving granular student answers during save_quiz_attempt: %s", e)

    # 4. Upsert topic mastery within the transaction
    try:
        upsert_topic_mastery(
            student_id=student_id,
            course_code=course_code,
            topic_name=topic_name,
            score=score,
            total=total,
            weak_concepts=weak_concepts,
            strong_concepts=strong_concepts,
            current_level=recommended_difficulty,
            conn=conn
        )
    except Exception as e:
        logger.warning("Updating topic mastery during save_quiz_attempt: %s", e)

    conn.commit()
    if close_conn:
        conn.close()
    logger.info("Saved quiz attempt %s for student %s (session_id=%s)", attempt_id, student_id, session_id)
    return attempt_id


def get_student_history(student_id: str, limit: int = 50, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Retrieve historical quiz attempts for a student."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM quiz_attempts
        WHERE student_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (student_id, limit))

    rows = cursor.fetchall()
    if close_conn:
        conn.close()

    attempts = []
    for row in rows:
        attempt = dict(row)
        attempt["quiz_data"] = parse_json_safely(attempt.get("quiz_data"), default={})
        attempt["difficulty_accuracy"] = parse_json_safely(attempt.get("difficulty_accuracy"), default={})
        attempt["concept_accuracy"] = parse_json_safely(attempt.get("concept_accuracy"), default={})
        attempt["weak_concepts"] = parse_json_safely(attempt.get("weak_concepts"), default=[])
        attempt["strong_concepts"] = parse_json_safely(attempt.get("strong_concepts"), default=[])
        attempts.append(attempt)

    return attempts


def get_topic_history(student_id: str, course_code: str, topic_name: str, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Retrieve historical quiz attempts for a specific student, course, and topic."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM quiz_attempts
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
        ORDER BY created_at DESC
    """, (student_id, course_code, topic_name))

    rows = cursor.fetchall()
    if close_conn:
        conn.close()

    attempts = []
    for row in rows:
        attempt = dict(row)
        attempt["quiz_data"] = parse_json_safely(attempt.get("quiz_data"), default={})
        attempt["difficulty_accuracy"] = parse_json_safely(attempt.get("difficulty_accuracy"), default={})
        attempt["concept_accuracy"] = parse_json_safely(attempt.get("concept_accuracy"), default={})
        attempt["weak_concepts"] = parse_json_safely(attempt.get("weak_concepts"), default=[])
        attempt["strong_concepts"] = parse_json_safely(attempt.get("strong_concepts"), default=[])
        attempts.append(attempt)

    return attempts


def get_recent_student_answers_for_topic(
    student_id: str,
    course_code: str,
    topic_name: str,
    limit: int = 10,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve most recent valid graded student answers for a student, course, and topic.
    Returns list of answer dicts ordered from newest to oldest.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT sa.* FROM student_answers sa
        JOIN quiz_attempts qa ON sa.attempt_id = qa.id
        WHERE qa.student_id = ? AND qa.course_code = ? AND qa.topic_name = ?
        ORDER BY sa.created_at DESC, sa.question_index DESC
        LIMIT ?
    """, (student_id, course_code, topic_name, limit))

    rows = cursor.fetchall()
    if close_conn:
        conn.close()

    return [dict(r) for r in rows]


def get_concept_performance(student_id: str, course_code: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Dict[str, int]]:
    """Aggregate per-concept correct and total counts from quiz attempts."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    if course_code:
        cursor.execute("""
            SELECT concept_accuracy FROM quiz_attempts
            WHERE student_id = ? AND course_code = ?
        """, (student_id, course_code))
    else:
        cursor.execute("""
            SELECT concept_accuracy FROM quiz_attempts
            WHERE student_id = ?
        """, (student_id,))

    rows = cursor.fetchall()
    if close_conn:
        conn.close()

    aggregated: Dict[str, Dict[str, int]] = {}
    for row in rows:
        concept_acc = parse_json_column(row, "concept_accuracy", default={})
        if not concept_acc:
            continue
        for concept, stats in concept_acc.items():
            if concept not in aggregated:
                aggregated[concept] = {"correct": 0, "total": 0}
            aggregated[concept]["correct"] += stats.get("correct", 0)
            aggregated[concept]["total"] += stats.get("total", 0)

    return aggregated


def get_difficulty_performance(student_id: str, course_code: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Dict[str, int]]:
    """Aggregate per-difficulty correct and total counts from quiz attempts."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    if course_code:
        cursor.execute("""
            SELECT difficulty_accuracy FROM quiz_attempts
            WHERE student_id = ? AND course_code = ?
        """, (student_id, course_code))
    else:
        cursor.execute("""
            SELECT difficulty_accuracy FROM quiz_attempts
            WHERE student_id = ?
        """, (student_id,))

    rows = cursor.fetchall()
    if close_conn:
        conn.close()

    aggregated: Dict[str, Dict[str, int]] = {}
    for row in rows:
        diff_acc = parse_json_column(row, "difficulty_accuracy", default={})
        if not diff_acc:
            continue
        for diff, stats in diff_acc.items():
            if diff not in aggregated:
                aggregated[diff] = {"correct": 0, "total": 0}
            aggregated[diff]["correct"] += stats.get("correct", 0)
            aggregated[diff]["total"] += stats.get("total", 0)

    return aggregated


def get_question_fingerprints(student_id: str, course_code: str, topic_name: str, limit: int = 50, conn: Optional[sqlite3.Connection] = None) -> List[str]:
    """Get question fingerprints from question_metadata table for novelty checking."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT question_fingerprint FROM question_metadata
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (student_id, course_code, topic_name, limit))

    rows = cursor.fetchall()
    if close_conn:
        conn.close()

    fingerprints = [row["question_fingerprint"] for row in rows if row["question_fingerprint"]]
    seen = set()
    unique = []
    for fp in fingerprints:
        if fp not in seen:
            seen.add(fp)
            unique.append(fp)
    return unique


def get_question_concepts(student_id: str, course_code: str, topic_name: str, limit: int = 30, conn: Optional[sqlite3.Connection] = None) -> List[str]:
    """Get question concepts from question_metadata table for novelty checking."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    cursor.execute("""
        SELECT concept FROM question_metadata
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (student_id, course_code, topic_name, limit))

    rows = cursor.fetchall()
    if close_conn:
        conn.close()

    concepts = [row["concept"] for row in rows if row["concept"]]
    seen = set()
    unique = []
    for c in concepts:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# ── Student Progress Dashboard & Learning Analytics ───────────────────────────

def get_student_dashboard_data(
    student_id: str,
    course_code: Optional[str] = None,
    limit: int = 20,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Retrieve comprehensive student progress dashboard metrics and learning analytics.
    Enforces:
    - Strict student isolation (never leaks data across students).
    - Optional course_code scoping.
    - Bounded queries (limit capped between 1 and 100).
    - Separation of cumulative topic accuracy vs. recent (10-question) accuracy.
    - Only completed quizzes from `quiz_attempts` count toward stats.
    - Deterministic, pedagogical practice recommendations with explicit disclaimer.
    - Graceful empty state for students without attempts.
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    cursor = conn.cursor()
    safe_limit = max(1, min(int(limit), 100))

    try:
        # 1. Overview Metrics (Aggregated strictly from completed quiz_attempts)
        if course_code:
            cursor.execute("""
                SELECT 
                    COUNT(*) as completed_quizzes,
                    COALESCE(SUM(total), 0) as total_questions,
                    COALESCE(SUM(score), 0) as total_correct,
                    MAX(created_at) as last_practiced_at,
                    COUNT(DISTINCT topic_name) as topics_practiced
                FROM quiz_attempts
                WHERE student_id = ? AND course_code = ?
            """, (student_id, course_code))
        else:
            cursor.execute("""
                SELECT 
                    COUNT(*) as completed_quizzes,
                    COALESCE(SUM(total), 0) as total_questions,
                    COALESCE(SUM(score), 0) as total_correct,
                    MAX(created_at) as last_practiced_at,
                    COUNT(DISTINCT topic_name) as topics_practiced
                FROM quiz_attempts
                WHERE student_id = ?
            """, (student_id,))

        overview_row = cursor.fetchone()
        completed_quizzes = int(overview_row["completed_quizzes"]) if overview_row else 0
        total_questions = int(overview_row["total_questions"]) if overview_row else 0
        total_correct = int(overview_row["total_correct"]) if overview_row else 0
        last_practiced_at = overview_row["last_practiced_at"] if overview_row else None
        topics_practiced = int(overview_row["topics_practiced"]) if overview_row else 0

        overall_accuracy = round((total_correct / total_questions) * 100, 1) if total_questions > 0 else 0.0

        overview = {
            "completed_quizzes": completed_quizzes,
            "total_questions": total_questions,
            "total_correct": total_correct,
            "overall_accuracy": overall_accuracy,
            "topics_practiced": topics_practiced,
            "last_practiced_at": last_practiced_at
        }

        # 2. Recent Performance Trends (Ordered chronologically, newest first)
        if course_code:
            cursor.execute("""
                SELECT id, course_code, course_name, topic_name, score, total, percentage, status, recommended_difficulty, created_at
                FROM quiz_attempts
                WHERE student_id = ? AND course_code = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (student_id, course_code, safe_limit))
        else:
            cursor.execute("""
                SELECT id, course_code, course_name, topic_name, score, total, percentage, status, recommended_difficulty, created_at
                FROM quiz_attempts
                WHERE student_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (student_id, safe_limit))

        recent_trends = []
        for row in cursor.fetchall():
            recent_trends.append({
                "attempt_id": row["id"],
                "course_code": row["course_code"],
                "course_name": row["course_name"],
                "topic_name": row["topic_name"],
                "score": int(row["score"]),
                "total": int(row["total"]),
                "percentage": float(row["percentage"]),
                "status": row["status"],
                "recommended_difficulty": row["recommended_difficulty"] or "mixed",
                "created_at": row["created_at"]
            })

        # 3. Topic Progress & Mastery
        if course_code:
            cursor.execute("""
                SELECT * FROM topic_mastery
                WHERE student_id = ? AND course_code = ?
                ORDER BY last_practiced_at DESC
            """, (student_id, course_code))
        else:
            cursor.execute("""
                SELECT * FROM topic_mastery
                WHERE student_id = ?
                ORDER BY last_practiced_at DESC
            """, (student_id,))

        mastery_rows = cursor.fetchall()

        # If topic_mastery is empty for this student but completed_quizzes > 0, backfill automatically
        if not mastery_rows and completed_quizzes > 0:
            backfill_topic_mastery_from_attempts(conn=conn)
            if course_code:
                cursor.execute("""
                    SELECT * FROM topic_mastery
                    WHERE student_id = ? AND course_code = ?
                    ORDER BY last_practiced_at DESC
                """, (student_id, course_code))
            else:
                cursor.execute("""
                    SELECT * FROM topic_mastery
                    WHERE student_id = ?
                    ORDER BY last_practiced_at DESC
                """, (student_id,))
            mastery_rows = cursor.fetchall()

        topic_progress = []
        all_weak_concepts = []
        all_strong_concepts = []
        practiced_topic_keys = set()

        for m in mastery_rows:
            t_course = m["course_code"]
            t_name = m["topic_name"]
            t_attempts = int(m["attempt_count"])
            t_total_q = int(m["total_questions"])
            t_total_c = int(m["total_correct"])
            t_level = m["current_level"]
            t_last = m["last_practiced_at"]
            practiced_topic_keys.add((t_course, t_name))

            weak_c = parse_json_column(m, "weak_concepts", default=[])
            strong_c = parse_json_column(m, "strong_concepts", default=[])

            all_weak_concepts.extend(weak_c)
            all_strong_concepts.extend(strong_c)

            cumulative_acc = round((t_total_c / t_total_q) * 100, 1) if t_total_q > 0 else 0.0

            # Compute recent accuracy from up to 10 latest granular answers
            recent_answers = get_recent_student_answers_for_topic(
                student_id=student_id,
                course_code=t_course,
                topic_name=t_name,
                limit=10,
                conn=conn
            )
            if recent_answers:
                rec_total = len(recent_answers)
                rec_correct = sum(1 for a in recent_answers if a.get("is_correct"))
                recent_acc = round((rec_correct / rec_total) * 100, 1)
                rec_count = rec_total
            else:
                recent_acc = cumulative_acc
                rec_count = t_total_q

            topic_progress.append({
                "course_code": t_course,
                "topic_name": t_name,
                "attempt_count": t_attempts,
                "total_questions": t_total_q,
                "total_correct": t_total_c,
                "cumulative_accuracy": cumulative_acc,
                "recent_accuracy": recent_acc,
                "recent_questions_count": rec_count,
                "current_level": t_level,
                "weak_concepts": weak_c,
                "strong_concepts": strong_c,
                "last_practiced_at": t_last
            })

        # Concept insights: active weak concepts (filtering any mastered into strong concepts)
        dedup_strong = list(dict.fromkeys(all_strong_concepts))
        dedup_weak = [c for c in list(dict.fromkeys(all_weak_concepts)) if c not in dedup_strong]

        concept_insights = {
            "weak_concepts": dedup_weak,
            "strong_concepts": dedup_strong
        }

        # 4. Recommended Topics (Pedagogical Practice Heuristics)
        # Personalized recommendations are strictly derived from verified student performance:
        # - Topics with low recent accuracy (< 60%) or weak concepts (High Priority - Review)
        # - Topics with high mastery (>= 80%) ready for difficulty advancement (Medium Priority - Review)
        review_topics = []

        # Heuristic A: Topics with low recent accuracy (< 60%) or weak concepts (High Priority - Review)
        for tp in topic_progress:
            if tp["recent_accuracy"] < 60.0 or len(tp["weak_concepts"]) > 0:
                rec_diff = "foundational" if tp["current_level"] == "foundational" else "mixed"
                weak_hint = f" ({', '.join(tp['weak_concepts'][:2])})" if tp["weak_concepts"] else ""
                review_topics.append({
                    "course_code": tp["course_code"],
                    "topic_name": tp["topic_name"],
                    "priority": "high",
                    "reason": f"Needs review: recent accuracy is {tp['recent_accuracy']}%{weak_hint}. Reinforce key concepts.",
                    "recommended_difficulty": rec_diff,
                    "recent_accuracy": tp["recent_accuracy"],
                    "weak_concepts": tp["weak_concepts"][:3]
                })

        # Heuristic B: Topics with high mastery (>= 80%) ready for advancement (Medium Priority - Review)
        for tp in topic_progress:
            if tp["recent_accuracy"] >= 80.0 and tp["current_level"] != "advanced":
                rec_diff = "advanced" if tp["current_level"] == "mixed" else "mixed"
                review_topics.append({
                    "course_code": tp["course_code"],
                    "topic_name": tp["topic_name"],
                    "priority": "medium",
                    "reason": f"Strong mastery ({tp['recent_accuracy']}%)! Ready to advance to {rec_diff} difficulty.",
                    "recommended_difficulty": rec_diff,
                    "recent_accuracy": tp["recent_accuracy"],
                    "weak_concepts": []
                })

        review_topics = review_topics[:4]

        # Personalized recommendations are strictly review_topics (never unpracticed topics)
        recommended_topics = list(review_topics)

        # Explore topics (syllabus unpracticed topics) - ONLY relevant to the requested course_code
        # or courses the student has actually practiced. Never leak unrelated curriculum courses!
        explore_topics = []
        try:
            from services.curriculum_service import curriculum_service
            curriculum_data = curriculum_service.get_curriculum()

            if course_code:
                target_courses = [c for c in curriculum_data.get("courses", []) if c.get("code") == course_code]
            else:
                practiced_courses = {tp["course_code"] for tp in topic_progress}
                target_courses = [c for c in curriculum_data.get("courses", []) if c.get("code") in practiced_courses]

            unpracticed_count = 0
            max_unpracticed = 4
            for c in target_courses:
                c_code = c.get("code", "")
                for t in c.get("topics", []):
                    t_name = t.get("name", "")
                    if (c_code, t_name) not in practiced_topic_keys:
                        explore_topics.append({
                            "course_code": c_code,
                            "topic_name": t_name,
                            "priority": "low",
                            "reason": "Unpracticed syllabus topic. Begin practice with mixed-difficulty questions.",
                            "recommended_difficulty": "mixed"
                        })
                        unpracticed_count += 1
                        if unpracticed_count >= max_unpracticed:
                            break
                if unpracticed_count >= max_unpracticed:
                    break
        except Exception as e:
            logger.warning("Could not fetch curriculum for dashboard recommendations: %s", e)

        explore_topics = explore_topics[:4]

        disclaimer = (
            "Difficulty recommendations and mastery insights are algorithmic practice heuristics "
            "designed for syllabus revision, not psychometric or accredited academic evaluations."
        )

        return {
            "student_id": student_id,
            "course_code": course_code,
            "overview": overview,
            "recent_trends": recent_trends,
            "topic_progress": topic_progress,
            "concept_insights": concept_insights,
            "review_topics": review_topics,
            "explore_topics": explore_topics,
            "recommended_topics": recommended_topics,
            "has_completed_quizzes": completed_quizzes > 0,
            "disclaimer": disclaimer
        }

    finally:
        if close_conn:
            conn.close()