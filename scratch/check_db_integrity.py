import sqlite3

con = sqlite3.connect('backend/data/staging_gate1.db')
cur = con.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() if not r[0].startswith('sqlite_')]
print("=== TABLE COUNTS IN staging_gate1.db ===")
for t in tables:
    count = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t:25}: {count} rows")

print("\n=== INTEGRITY CHECKS ===")
print("  PRAGMA integrity_check  :", cur.execute("PRAGMA integrity_check").fetchall())
print("  PRAGMA foreign_key_check:", cur.execute("PRAGMA foreign_key_check").fetchall())

# Print sample data of the test student created during journey
print("\n=== TEST STUDENT RECORDS ===")
student = cur.execute("SELECT id, student_id, email, full_name, created_at FROM student_accounts").fetchall()
print("  student_accounts:", student)
sessions = cur.execute("SELECT id, student_id, course_code, topic_name, status FROM quiz_sessions").fetchall()
print("  quiz_sessions   :", sessions)
attempts = cur.execute("SELECT id, student_id, score, total, percentage FROM quiz_attempts").fetchall()
print("  quiz_attempts   :", attempts)
mastery = cur.execute("SELECT student_id, topic_name, attempt_count, mastery_score, current_level FROM topic_mastery").fetchall()
print("  topic_mastery   :", mastery)
