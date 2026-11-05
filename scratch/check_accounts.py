import sqlite3
conn = sqlite3.connect('backend/data/learnai.db')
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(student_accounts)")
print("Cols:", cursor.fetchall())
cursor.execute("SELECT student_id, full_name, email FROM student_accounts LIMIT 5")
print("Accounts:", cursor.fetchall())
conn.close()
