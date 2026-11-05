import sqlite3
import json

con = sqlite3.connect('backend/data/staging_gate1.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=" * 80)
print("DEEP DIVE AUDIT OF backend/data/staging_gate1.db")
print("=" * 80)

# 1. student_accounts
print("\n--- TABLE: student_accounts ---")
rows = cur.execute("SELECT * FROM student_accounts").fetchall()
for r in rows:
    print(dict(r))

# 2. students
print("\n--- TABLE: students ---")
rows = cur.execute("SELECT * FROM students").fetchall()
for r in rows:
    print(dict(r))

# 3. student_sessions
print("\n--- TABLE: student_sessions ---")
rows = cur.execute("SELECT * FROM student_sessions").fetchall()
for r in rows:
    print(dict(r))

# 4. quiz_sessions
print("\n--- TABLE: quiz_sessions ---")
rows = cur.execute("SELECT id, student_id, course_code, topic_name, status, created_at, submitted_at, expires_at FROM quiz_sessions").fetchall()
for r in rows:
    print(dict(r))

# 5. quiz_attempts
print("\n--- TABLE: quiz_attempts ---")
rows = cur.execute("SELECT id, student_id, course_code, topic_name, score, total, percentage, status, created_at, session_id FROM quiz_attempts").fetchall()
for r in rows:
    print(dict(r))

# 6. topic_mastery
print("\n--- TABLE: topic_mastery ---")
rows = cur.execute("SELECT * FROM topic_mastery").fetchall()
for r in rows:
    print(dict(r))

# 7. question_metadata counts per attempt
print("\n--- TABLE: question_metadata (attempt breakdown) ---")
rows = cur.execute("SELECT attempt_id, count(*) as count FROM question_metadata GROUP BY attempt_id").fetchall()
for r in rows:
    print(dict(r))

# 8. student_answers counts per attempt
print("\n--- TABLE: student_answers (attempt breakdown) ---")
rows = cur.execute("SELECT attempt_id, session_id, count(*) as count FROM student_answers GROUP BY attempt_id, session_id").fetchall()
for r in rows:
    print(dict(r))

# 9. Integrity checks
print("\n--- INTEGRITY & FOREIGN KEY CHECKS ---")
print("PRAGMA integrity_check:", cur.execute("PRAGMA integrity_check").fetchall())
print("PRAGMA foreign_key_check:", cur.execute("PRAGMA foreign_key_check").fetchall())
