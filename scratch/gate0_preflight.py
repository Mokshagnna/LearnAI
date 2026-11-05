import os
import sys
from pathlib import Path

# Explicitly set LEARNAI_ENV_FILE to staging
os.environ["LEARNAI_ENV_FILE"] = "backend/.env.staging.local"

sys.path.insert(0, str(Path("backend").resolve()))
from services.env_loader import resolve_and_load_env
from services.database import is_production_mode, get_db_path, get_connection

backend_dir = Path("backend").resolve()
loaded_env = resolve_and_load_env(backend_dir)

env_val = os.getenv("ENVIRONMENT")
app_env_val = os.getenv("APP_ENV")
prod_mode = is_production_mode()
db_path = get_db_path()

# Shell env leaks check
db_url_leak = bool(os.getenv("DATABASE_URL"))
mysql_url_leak = bool(os.getenv("MYSQL_DATABASE_URL"))
render_leak = bool(os.getenv("RENDER"))

print("=== GATE 0 PREFLIGHT REPORT ===")
print(f"LEARNAI_ENV_FILE set to: {os.environ.get('LEARNAI_ENV_FILE')}")
print(f"Resolved env file: {loaded_env}")
print(f"Env file exists: {loaded_env.is_file() if loaded_env else False}")
print(f"ENVIRONMENT: {env_val}")
print(f"APP_ENV: {app_env_val}")
print(f"is_production_mode: {prod_mode}")
print(f"DATABASE_URL present: {db_url_leak}")
print(f"MYSQL_DATABASE_URL present: {mysql_url_leak}")
print(f"RENDER present: {render_leak}")
print(f"Active DB path: {db_path.resolve()}")
print(f"DB is staging_gate1.db: {db_path.name == 'staging_gate1.db'}")
print(f"DB is NOT learnai.db: {db_path.name != 'learnai.db'}")

# Inspect DB tables
conn = get_connection(db_path=db_path)
tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
records_count = {}
for t in tables:
    if not t.startswith("sqlite_"):
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        records_count[t] = cnt
conn.close()
print(f"DB tables count: {len(tables)}")
print(f"DB record counts: {records_count}")

# Credential presence (YES / NO only, zero leakage)
creds = {
    "GROQ_API_KEY": "YES" if bool(os.getenv("GROQ_API_KEY", "").strip()) else "NO",
    "NVIDIA_API_KEY": "YES" if bool(os.getenv("NVIDIA_API_KEY", "").strip()) else "NO",
    "GEMINI_API_KEY": "YES" if bool(os.getenv("GEMINI_API_KEY", "").strip()) else "NO",
    "CLOUDFLARE_API_TOKEN": "YES" if bool(os.getenv("CLOUDFLARE_API_TOKEN", "").strip()) else "NO",
    "CLOUDFLARE_ACCOUNT_ID": "YES" if bool(os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()) else "NO",
}
print("=== CREDENTIAL PRESENCE ===")
for k, v in creds.items():
    print(f"  {k}: {v}")
