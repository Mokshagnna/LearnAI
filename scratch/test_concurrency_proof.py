import sqlite3
import threading
import time
from pathlib import Path

db_path = Path("scratch/test_concurrency.db")
if db_path.exists():
    db_path.unlink()

# Initialize schema with WAL
conn = sqlite3.connect(str(db_path))
conn.execute("PRAGMA journal_mode = WAL;")
conn.execute("PRAGMA busy_timeout = 5000;")
conn.execute("""
    CREATE TABLE IF NOT EXISTS test_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        score INTEGER,
        created_at REAL
    );
""")
conn.commit()
conn.close()

errors = []

def worker(worker_id):
    try:
        for i in range(15):
            c = sqlite3.connect(str(db_path), timeout=5.0)
            c.execute("PRAGMA busy_timeout = 5000;")
            c.execute(
                "INSERT INTO test_submissions (user_id, score, created_at) VALUES (?, ?, ?)",
                (f"user_{worker_id}", i * 10, time.time())
            )
            c.commit()
            # Read back
            cur = c.execute("SELECT COUNT(*) FROM test_submissions WHERE user_id = ?", (f"user_{worker_id}",))
            cnt = cur.fetchone()[0]
            assert cnt >= i + 1
            c.close()
    except Exception as e:
        errors.append((worker_id, e))

threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
t0 = time.perf_counter()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.perf_counter() - t0

# Final check
conn = sqlite3.connect(str(db_path))
cur = conn.execute("SELECT COUNT(*) FROM test_submissions")
total = cur.fetchone()[0]
conn.close()

print(f"Total rows: {total}/120 inserted in {elapsed:.3f}s. Errors: {len(errors)}")
if errors:
    print("Errors:", errors)
