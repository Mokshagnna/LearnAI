# LearnAI — Pre-Provisioning Staging Isolation Proof

**Document Version:** 1.0  
**Audit Date:** November 3, 2026  
**Auditor:** Senior Backend Architect & Production Reliability Engineer  
**Repository:** `C:\Users\moksh\OneDrive\Desktop\IDP` (`Mokshagnna/LearnAI`)  
**Commit Inspected:** `80aa8e8` (`Improve provider quota handling and quiz generation reliability`)  
**Scope:** Strictly Read-Only Architectural Isolation Audit  

---

## 1. Executive Summary & Final Classification

### Final Classification:
> ### ⚠️ **READY AFTER CONFIGURATION CHANGES**
> *(No implementation code changes are strictly required to achieve isolation, but specific environment, process-level configuration, and `.gitignore` updates are mandatory before provisioning credentials).*

This read-only audit evaluated LearnAI's application startup, environment variable ingestion, database selection, logging filters, and AI provider invocation lifecycles. 

### Key Audit Findings:
1. **Startup & Configuration Loading:** The application (`backend/main.py` and `backend/services/llm_service.py`) does **not** natively load `.env.staging`. Both files hardcode `_ENV_FILE = BASE_DIR / ".env"` with `load_dotenv(_ENV_FILE, override=True)`. Neither `backend/.env` nor root `.env` currently exists. Staging configuration must be injected into the process environment externally or loaded via an isolated staging runner.
2. **Git Ignore Vulnerability:** **`backend/.env.staging` is NOT ignored by `.gitignore`!** Running `git check-ignore` confirms that `.env.staging` would be tracked by Git as an untracked file, presenting an accidental commit hazard. However, `.gitignore` *does* ignore `.env.*.local`. Therefore, naming the staging file `backend/.env.staging.local` is safely ignored out-of-the-box.
3. **Database Selection & Sandboxing:** `DatabaseConfig` dynamically respects `LEARNAI_DB_PATH`. When `is_production_mode()` is `False` and `DATABASE_URL` is unset, SQLite creates the target database file and directory automatically with WAL mode and `PRAGMA busy_timeout = 5000`. `backend/data/staging_gate1.db` can be selected without modifying production configuration.
4. **Secret Exposure Safeguards:** `SecretRedactionFilter` is attached to all primary loggers (`learnai`, `learnai.api`, `learnai.orchestrator`, `learnai.providers`, `uvicorn`). Provider names (e.g. `groq`, `nvidia`) are logged, but keys are sanitized.
5. **Call-Count Control (Enforcing Exactly 4 Calls):** Under default settings, `llm_service.py` and provider adapters can trigger up to 12+ requests due to retries (`MAX_PROVIDER_RETRIES=1`), selective question replacement (`MAX_REPLACEMENT_ROUNDS=2`), model fallbacks (`candidate_models`), and cross-provider failover. To guarantee **exactly 1 request per provider (4 total)**, explicit configuration overrides are required.

---

## 2. Application Startup & Configuration Ingestion

### 2.1 Code Implementation Analysis
In both `backend/main.py:155–163` and `backend/services/llm_service.py:50–55`:
```python
_ENV_FILE = BASE_DIR / ".env"
if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=True)
else:
    load_dotenv(override=True)
```

### 2.2 Operational Behavior & Risk Analysis
- **Is `.env.staging` supported explicitly?**  
  **No.** There is no code path checking for `os.getenv("ENV_FILE")` or `.env.staging`.
- **Can the process accidentally load `.env` or fallback credentials?**  
  **Yes, if `backend/.env` or root `.env` exists.** Because `override=True` is hardcoded, any `.env` file present on disk will forcibly overwrite variables pre-set in the process environment.
  - *Current Machine State:* Verified via `Test-Path backend/.env` (`False`) and `Test-Path .env` (`False`). Neither file exists.
- **Production Environment Lockdown (`is_production_mode()`):**  
  Defined in `backend/services/db/base.py:187–217`:
  - If `RENDER=true` or `ENVIRONMENT=production`, production guards activate: SQLite is strictly prohibited and `init_db()` will raise `DatabaseConnectionError`.
  - For staging SQLite isolation, `ENVIRONMENT=staging` (or `test`) must be set, and `RENDER` must be unset/empty.

### 2.3 Exact Staging Startup Command

To launch the isolated staging backend without code changes, environment variables from `backend/.env.staging.local` must be populated into the process environment before Uvicorn starts.

#### Supported PowerShell Command (from `C:\Users\moksh\OneDrive\Desktop\IDP\backend`):
```powershell
# 1. Verify working directory is backend
cd C:\Users\moksh\OneDrive\Desktop\IDP\backend

# 2. Populate environment from backend/.env.staging.local
Get-Content .env.staging.local | Where-Object { $_ -match '^[^#=]+=' } | ForEach-Object {
    $parts = $_.Split('=', 2)
    [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim())
}

# 3. Launch isolated staging Uvicorn server on dedicated staging port (e.g. 8001)
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --log-level info
```

---

## 3. Database Selection & Isolation Proof

### 3.1 Trace of `LEARNAI_DB_PATH`
1. **Environment Ingestion:** Process reads `os.getenv("LEARNAI_DB_PATH")`.
2. **Path Resolution:** `DatabaseConfig.get_sqlite_path()` ([`backend/services/db/base.py:335`](file:///C:/Users/moksh/OneDrive/Desktop/IDP/backend/services/db/base.py#L335)) resolves the path to an absolute `Path` object and executes `p.parent.mkdir(parents=True, exist_ok=True)`.
3. **Adapter Hook:** `SQLiteAdapter.get_connection()` ([`backend/services/db/sqlite_adapter.py:33`](file:///C:/Users/moksh/OneDrive/Desktop/IDP/backend/services/db/sqlite_adapter.py#L33)) acquires connection via `sqlite3.connect(str(target), timeout=10.0)`.
4. **Schema Initialization:** At startup, `backend/main.py:209` executes `init_db()`.
   - `init_db()` enables WAL journal mode (`PRAGMA journal_mode = WAL;`).
   - Executes idempotent `CREATE TABLE IF NOT EXISTS` for `students`, `quiz_attempts`, `quiz_sessions`, `student_answers`, `question_metadata`, and `topic_mastery`.
5. **Fallback Precedence:**
   ```
   1. DATABASE_URL / MYSQL_DATABASE_URL (If set, attempts MySQL — MUST BE UNSET for SQLite staging)
   2. LEARNAI_DB_PATH (Primary SQLite override -> Target: backend/data/staging_gate1.db)
   3. SQLITE_DB_PATH (Secondary legacy alias)
   4. Default fallback -> backend/data/learnai.db (Standard local dev database)
   ```

### 3.2 Database Isolation Proof Procedure
Before and during Gate 1 execution, run the following verification checks:

1. **Active Database PRAGMA Query:**
   Execute a direct query against the running application's database adapter:
   ```python
   from services.database import get_connection
   conn = get_connection()
   cursor = conn.cursor()
   cursor.execute("PRAGMA database_list;")
   for row in cursor.fetchall():
       print(f"Attached DB: seq={row[0]} name={row[1]} file={row[2]}")
   ```
   *Proof Criterion:* `file` MUST match `C:\Users\moksh\OneDrive\Desktop\IDP\backend\data\staging_gate1.db`.
2. **Filesystem Timestamp & Size Verification:**
   - Verify `backend/data/staging_gate1.db` file size grows and `LastWriteTime` updates when a quiz is generated.
   - Verify `backend/data/learnai.db` timestamp does **not** change.
3. **Student Record Isolation:**
   - Execute query `SELECT COUNT(*) FROM students;` on `staging_gate1.db`.
   - Before Gate 1: Exactly `0` records.
   - After Gate 1: Exactly `4` synthetic test records (`staging_gate1_<provider>_01`).

---

## 4. Secret Handling & Git Ignore Verification

### 4.1 Git Ignore Audit Findings
Direct testing with `git check-ignore` revealed a critical discrepancy:
```powershell
git check-ignore -v backend/.env.staging .env.staging
# Exit code 1 (NOT IGNORED)

git check-ignore -v backend/.env.staging.local
# Output: .gitignore:17:.env.*.local    backend/.env.staging.local (IGNORED)
```
- **Finding:** `.gitignore` explicitly lists `backend/.env`, `.env`, `.env.local`, and `.env.*.local`. It does **not** use a wildcard `.env*`.
- **Mandatory Action:** To prevent accidental tracking of staging secrets, either:
  1. Use the file name **`backend/.env.staging.local`** (automatically ignored by line 17), OR
  2. Add `backend/.env.staging` to `.gitignore`.

### 4.2 Logging Security Audit
- `backend/services/logger_utils.py` installs `SecretRedactionFilter` on `learnai`, `learnai.api`, `learnai.orchestrator`, `learnai.providers`, and `uvicorn`.
- Redacts regex patterns matching Groq (`gsk_...`), NVIDIA (`nvapi-...`), Gemini (`AIza...`), Bearer tokens, and `api-key=...`.
- `backend/services/llm_service.py` logs provider names via `[p.name for p in configured_providers]`, never credentials.
- All exception strings pass through `sanitize_error_text(...)`.
- `CloudflareProvider` headers are created dynamically in `_headers()` and are not logged.

### 4.3 Minimum Required Environment Variables by Provider

| Provider | Minimum Required Variable Names | Optional / Fallback Variables |
| :--- | :--- | :--- |
| **Groq** | `GROQ_API_KEY` | `GROQ_MODEL`, `GROQ_FALLBACK_MODEL`, `GROQ_TIMEOUT_SECONDS` |
| **NVIDIA NIM** | `NVIDIA_API_KEY` | `NVIDIA_MODEL`, `NVIDIA_FALLBACK_MODEL`, `NVIDIA_TIMEOUT_SECONDS` |
| **Google Gemini** | `GEMINI_API_KEY` | `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL` |
| **Cloudflare** | `CLOUDFLARE_API_TOKEN`<br>`CLOUDFLARE_ACCOUNT_ID` | `CLOUDFLARE_MODEL`, `CLOUDFLARE_TIMEOUT_SECONDS` |

---

## 5. Provider Selection & Model Precedence

### 5.1 Model Selection Precedence

```
1. Environment Variable Override (e.g. GROQ_MODEL, NVIDIA_MODEL, GEMINI_MODEL, CLOUDFLARE_MODEL)
   └── Validated against in-code KNOWN_INVALID_MODELS (e.g. meta/llama-3.3-70b-instruct is rejected on NVIDIA)
2. Configured In-Code Default (when env var is unset)
3. Fallback Environment Variable (e.g. GROQ_FALLBACK_MODEL, NVIDIA_FALLBACK_MODEL)
4. Hardcoded Candidate Model List (evaluated sequentially if previous models fail with recoverable error)
```

### 5.2 Variable Names and Non-Secret Model Values

| Provider | In-Code Default Model | Code-Enforced Fallback | Model Override Variable | Timeout Variable (Default) |
| :--- | :--- | :--- | :--- | :--- |
| **Groq** | `llama-3.1-8b-instant` | `llama-3.3-70b-versatile` | `GROQ_MODEL`<br>`GROQ_FALLBACK_MODEL` | `GROQ_TIMEOUT_SECONDS` (11.0s) |
| **NVIDIA NIM** | `meta/llama-3.1-8b-instruct` | `meta/llama-3.1-70b-instruct` | `NVIDIA_MODEL`<br>`NVIDIA_FALLBACK_MODEL` | `NVIDIA_TIMEOUT_SECONDS` (16.0s) |
| **Google Gemini** | `gemini-1.5-flash` | `gemini-1.5-pro` | `GEMINI_MODEL`<br>`GEMINI_FALLBACK_MODEL` | `PROVIDER_TIMEOUT_SECONDS` (25.0s) |
| **Cloudflare** | `@cf/meta/llama-3.1-8b-instruct` | N/A | `CLOUDFLARE_MODEL` | `CLOUDFLARE_TIMEOUT_SECONDS` (20.0s) |

---

## 6. Provider Retry & Fallback Call-Count Analysis

### 6.1 Sources of Additional / Hidden Provider Calls

If Gate 1 were executed against `/api/quiz/generate` without constraints, the following mechanisms in `backend/services/llm_service.py` could multiply requests:

1. **Provider Retries (`max_retries`):**
   - Controlled by `MAX_PROVIDER_RETRIES` (defaults to `1` in code: `range(max_retries + 1)` = 2 attempts).
2. **Selective Question Replacement:**
   - If candidate questions generated in attempt 1 contain formatting errors or duplicates, `llm_service.py:676` enters a replacement loop up to `MAX_REPLACEMENT_ROUNDS` (defaults to `2`). Each round issues a separate call to `provider.generate_quiz_raw(...)`.
3. **Internal Provider Model Fallback:**
   - In `groq_provider.py` and `nvidia_provider.py`, if the primary model encounters a recoverable 429 or 503, the adapter iterates to `fallback_model` within the same call.
4. **Cross-Provider Failover Loop:**
   - In `_get_providers()`, unlisted providers are appended to `ordered`. If multiple provider keys are present, a failure on Groq immediately invokes NVIDIA NIM.

### 6.2 Hard Enforcement of Exactly Four Calls

To guarantee that Gate 1 dispatches **strictly 1 request per provider with zero retries**:

1. **Step-by-Step Isolation:** Execute each provider test sequentially in an isolated process context where **only that provider's API key is set**.
2. **Disable Retries in Configuration:**
   ```bash
   MAX_PROVIDER_RETRIES=0
   AI_MAX_RETRIES=0
   ```
3. **Disable Selective Replacement in Configuration:**
   ```bash
   MAX_REPLACEMENT_ROUNDS=0
   ```
4. **Prevent Model Fallback Cascades:**
   Set the fallback model equal to the primary model for Groq and NVIDIA:
   ```bash
   GROQ_FALLBACK_MODEL=llama-3.1-8b-instant
   NVIDIA_FALLBACK_MODEL=meta/llama-3.1-8b-instruct
   ```
5. **Leave Secondary Keys Empty:**
   Do **not** configure `GROQ_BACKUP_API_KEY`, `NVIDIA_BACKUP_API_KEY`, or `GEMINI_BACKUP_API_KEY`.
6. **Synthetic Student Identity:**
   Use unique synthetic student IDs per step (`staging_gate1_groq_01`, `staging_gate1_nvidia_01`, etc.).

---

## 7. Recommended Code Changes (Listed but NOT Implemented)

To make staging isolation first-class and native in the future without relying on external PowerShell environment loaders, the following optional enhancements can be considered for a future release:

1. **Native `LEARNAI_ENV_FILE` Support:**
   In `backend/main.py:155` and `backend/services/llm_service.py:51`:
   ```python
   # Proposed: allow custom env file path via environment variable
   _env_override = os.getenv("LEARNAI_ENV_FILE")
   _ENV_FILE = Path(_env_override).resolve() if _env_override else BASE_DIR / ".env"
   ```
2. **Explicit `.gitignore` Update:**
   Add explicit wildcards to `.gitignore`:
   ```gitignore
   .env*
   !.env.example
   ```

*(Per authorization constraints, these changes are documented for review only and have NOT been implemented).*

---

## 8. Precise Checklist for Safely Provisioning Staging Credentials

Follow this exact sequence when ready to provision staging:

- [ ] **Step 1: Create Staging Env File with Safe Extension**  
  Create `backend/.env.staging.local` (verified ignored by `.gitignore`).
- [ ] **Step 2: Configure Dedicated Staging Database Path**  
  ```bash
  LEARNAI_DB_PATH=backend/data/staging_gate1.db
  ```
- [ ] **Step 3: Ensure Production DB URL is Empty**  
  Verify `DATABASE_URL` and `MYSQL_DATABASE_URL` are **not** present in `.env.staging.local`.
- [ ] **Step 4: Set Environment Mode to Staging**  
  ```bash
  ENVIRONMENT=staging
  APP_ENV=staging
  DEBUG=false
  ```
- [ ] **Step 5: Enforce Zero-Retry and Zero-Replenishment Bounds**  
  ```bash
  MAX_PROVIDER_RETRIES=0
  MAX_REPLACEMENT_ROUNDS=0
  OVERALL_DEADLINE_SECONDS=30.0
  ```
- [ ] **Step 6: Provision Dedicated Staging Provider Keys**  
  Add test keys for Groq, NVIDIA NIM, Gemini, and Cloudflare. Never paste production keys.
- [ ] **Step 7: Verify Git Status**  
  Run `git status --short`. Confirm `backend/.env.staging.local` does **not** appear in untracked files.
- [ ] **Step 8: Execute Pre-Flight Database PRAGMA Check**  
  Confirm connection points to `staging_gate1.db` before triggering external network calls.

---

## 9. Safety Confirmation

- **No live provider calls** were dispatched ($0.00 spend strictly preserved).
- **No secret values or API keys** were printed, copied, or exposed.
- **No production databases** were accessed or written to.
- **No source code or configuration files** were edited (only this report was created).
- **No Git commits, pushes, or deployments** were performed.
- **All unstaged and untracked files** remain untouched.
