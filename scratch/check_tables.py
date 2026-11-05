import sqlite3
conn = sqlite3.connect('backend/data/learnai.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("Tables:", cursor.fetchall())
for tbl in ['users', 'auth_users', 'student_auth', 'accounts']:
    try:
        cursor.execute(f"PRAGMA table_info({tbl})")
        print(f"Table {tbl}:", cursor.fetchall())
        cursor.execute(f"SELECT * FROM {tbl} LIMIT 2")
        print(f"Data {tbl}:", cursor.fetchall())
    except Exception as e:
        pass
conn.close()
