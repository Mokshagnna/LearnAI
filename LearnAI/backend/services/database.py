import sqlite3
import json
import uuid
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger("learnai.database")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "learnai.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def normalise_text(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

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

    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


def get_or_create_student(student_id: Optional[str] = None) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()

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
            conn.close()
            return sid
        else:
            cursor.execute(
                "INSERT INTO students (id, created_at, last_active) VALUES (?, ?, ?)",
                (sid, now, now)
            )
            conn.commit()
            conn.close()
            logger.info("Registered student: %s", sid)
            return sid

    new_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO students (id, created_at, last_active) VALUES (?, ?, ?)",
        (new_id, now, now)
    )
    conn.commit()
    conn.close()
    logger.info("Created new student: %s", new_id)
    return new_id


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
    recommended_difficulty: Optional[str] = None
) -> str:
    conn = get_connection()
    cursor = conn.cursor()

    attempt_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    cursor.execute("""
        INSERT INTO quiz_attempts (
            id, student_id, course_code, course_name, topic_name,
            quiz_data, score, total, percentage, status,
            difficulty_accuracy, concept_accuracy,
            weak_concepts, strong_concepts,
            recommended_difficulty, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        attempt_id,
        student_id,
        course_code,
        course_name,
        topic_name,
        json.dumps(quiz_data),
        score,
        total,
        percentage,
        status,
        json.dumps(difficulty_accuracy),
        json.dumps(concept_accuracy),
        json.dumps(weak_concepts),
        json.dumps(strong_concepts),
        recommended_difficulty,
        now
    ))

    # Save question metadata for novelty tracking
    questions = quiz_data.get("questions", [])
    for idx, q in enumerate(questions):
        q_id = str(uuid.uuid4())
        q_text = q.get("question", "")
        q_fp = normalise_text(q_text)  # Reuse the normalise function logic
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

    conn.commit()
    conn.close()
    logger.info("Saved quiz attempt %s for student %s", attempt_id, student_id)
    return attempt_id


def get_student_history(student_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM quiz_attempts
        WHERE student_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (student_id, limit))

    rows = cursor.fetchall()
    conn.close()

    attempts = []
    for row in rows:
        attempt = dict(row)
        attempt["quiz_data"] = json.loads(attempt["quiz_data"])
        attempt["difficulty_accuracy"] = json.loads(attempt["difficulty_accuracy"])
        attempt["concept_accuracy"] = json.loads(attempt["concept_accuracy"])
        attempt["weak_concepts"] = json.loads(attempt["weak_concepts"])
        attempt["strong_concepts"] = json.loads(attempt["strong_concepts"])
        attempts.append(attempt)

    return attempts


def get_topic_history(student_id: str, course_code: str, topic_name: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM quiz_attempts
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
        ORDER BY created_at DESC
    """, (student_id, course_code, topic_name))

    rows = cursor.fetchall()
    conn.close()

    attempts = []
    for row in rows:
        attempt = dict(row)
        attempt["quiz_data"] = json.loads(attempt["quiz_data"])
        attempt["difficulty_accuracy"] = json.loads(attempt["difficulty_accuracy"])
        attempt["concept_accuracy"] = json.loads(attempt["concept_accuracy"])
        attempt["weak_concepts"] = json.loads(attempt["weak_concepts"])
        attempt["strong_concepts"] = json.loads(attempt["strong_concepts"])
        attempts.append(attempt)

    return attempts


def get_concept_performance(student_id: str, course_code: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    conn = get_connection()
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
    conn.close()

    aggregated = {}
    for row in rows:
        concept_acc = json.loads(row["concept_accuracy"])
        for concept, stats in concept_acc.items():
            if concept not in aggregated:
                aggregated[concept] = {"correct": 0, "total": 0}
            aggregated[concept]["correct"] += stats["correct"]
            aggregated[concept]["total"] += stats["total"]

    return aggregated


def get_difficulty_performance(student_id: str, course_code: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    conn = get_connection()
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
    conn.close()

    aggregated = {}
    for row in rows:
        diff_acc = json.loads(row["difficulty_accuracy"])
        for diff, stats in diff_acc.items():
            if diff not in aggregated:
                aggregated[diff] = {"correct": 0, "total": 0}
            aggregated[diff]["correct"] += stats["correct"]
            aggregated[diff]["total"] += stats["total"]

    return aggregated


def get_question_fingerprints(student_id: str, course_code: str, topic_name: str, limit: int = 50) -> List[str]:
    """Get question fingerprints from question_metadata table for novelty checking."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT question_fingerprint FROM question_metadata
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (student_id, course_code, topic_name, limit))

    rows = cursor.fetchall()
    conn.close()

    fingerprints = [row["question_fingerprint"] for row in rows if row["question_fingerprint"]]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for fp in fingerprints:
        if fp not in seen:
            seen.add(fp)
            unique.append(fp)
    return unique


def get_question_concepts(student_id: str, course_code: str, topic_name: str, limit: int = 30) -> List[str]:
    """Get question concepts from question_metadata table for novelty checking."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT concept FROM question_metadata
        WHERE student_id = ? AND course_code = ? AND topic_name = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (student_id, course_code, topic_name, limit))

    rows = cursor.fetchall()
    conn.close()

    concepts = [row["concept"] for row in rows if row["concept"]]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for c in concepts:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique