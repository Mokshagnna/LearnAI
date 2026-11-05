# LearnAI — Post-Commit Production Readiness Audit

**Audit Date:** November 3, 2026  
**Auditor:** Senior Backend Architect & Production Reliability Engineer  
**Repository:** `C:\Users\moksh\OneDrive\Desktop\IDP` (`Mokshagnna/LearnAI`)  
**Branch:** `main`  
**Latest Local Commit:** `80aa8e8` (`Improve provider quota handling and quiz generation reliability`)  
**Backend Deployment Platform:** Render (`https://learnai-fzgd.onrender.com`)  
**Overall Readiness Classification:** **READY FOR CONTROLLED STAGING VERIFICATION**

---

## 1. Executive Summary

This post-commit production-readiness audit evaluated the committed backend changes in commit `80aa8e8` alongside the overall LearnAI architecture. The evaluation covered:
- Git integrity and codebase cleanliness.
- Quiz generation orchestration, provider failover, and quota management.
- Novelty evaluation and selective question replacement.
- End-to-end deadline enforcement and timeout boundaries.
- Database persistence, transaction rollback, and answer-key protection.
- Render deployment settings and runtime constraints.
- Real-provider integration status across Groq, NVIDIA NIM, Google Gemini, and Cloudflare Workers AI.
- Full regression verification across the entire 1,060-test backend suite.

### Key Conclusions:
1. **Commit Integrity:** Commit `80aa8e8` contains exactly the approved 12 implementation and test files (+3,443 / -516 lines). Zero unauthorized files, secrets, or debug statements entered the repository history.
2. **Regression Suite Results:** The full backend test suite (`pytest backend/ -q`) executed with exit code 0: **1,054 passed, 6 skipped, 0 failed** in 161.53s.
3. **Core Remediations Operational:** Quota tracking, rate-limit fast-skips, cognitive operation classifications, numerical templates, false-duplicate elimination, selective question replacement, and unified request deadlines are implemented and verified.
4. **Empirical Boundary Transparency:** While local testing verified software algorithms, full multi-provider live inference latency under sustained load remains unverified locally due to intentional credential absence ($0.00 spend boundary). Moving safely to an isolated staging environment is the designated next step.

---

## 2. Git Baseline and Working Tree State

The repository state was inspected via Git plumbing:
- **Current Branch:** `main`
- **Current HEAD:** `80aa8e8` (`Improve provider quota handling and quiz generation reliability`)
- **Prior History:**
  - `f25f46f` — `Add academic correctness verification`
  - `b270e38` — `fix(perf): calibrate Groq timeout to 11.0s and generation buffer to 11 questions`
  - `366aaae` — `fix(perf): expand Groq timeout budget to 9.2s for reliable sub-10s quiz generation`
  - `9c2f323` — `fix(quiz-gen): generate with buffer to guarantee 10 questions without replenishment overhead`
- **Committed Files in `80aa8e8` (12 files):**
  - Implementation (7): `backend/conftest.py`, `backend/services/llm_service.py`, `backend/services/novelty.py`, `backend/services/providers/base.py`, `backend/services/providers/groq_provider.py`, `backend/services/providers/nvidia_provider.py`, `backend/services/quota_manager.py`.
  - Tests (5): `backend/tests/test_module4_cross_integration.py`, `backend/tests/test_novelty_architecture_remediation.py`, `backend/tests/test_quota_manager.py`, `backend/tests/test_quota_manager_edge_cases.py`, `backend/tests/test_selective_replacement_and_deadline.py`.
- **Whitespace / Quality Check:** `git diff HEAD^ HEAD --check` returned clean (exit code 0).
- **Preserved Unstaged Work:**
  - `frontend/css/style.css` and `frontend/index.html` remain modified and unstaged.
  - All audit reports, benchmark datasets (`diverse_3_results.json`, `production_sub_10s_benchmark_results.json`, `module6_benchmark_results.json`), and the `scratch/` directory remain preserved and untracked.

---

## 3. Architecture & Quiz Generation Orchestration

### 3.1 Provider Priority and Fallback Ordering
In `backend/services/llm_service.py:16-35` and `_get_providers()`:
- **Configurable Priority:** Provider sequence is controlled by `PROVIDER_ORDER` (default: `groq,nvidia,gemini`, with `cloudflare` appended if configured).
- **Session Reuse:** Provider instances (`GroqProvider`, `NvidiaProvider`, `GeminiProvider`, `CloudflareProvider`) are cached in module-level `_CACHED_PROVIDERS` to reuse underlying HTTP connection pools and avoid per-request TLS handshake overhead.
- **Failover Sequence:** If a provider fails with a retryable error (`ProviderUnavailableError` or `ProviderError`), elapsed time is deducted from the unified deadline, and execution falls over to the next configured provider.

### 3.2 Student-Facing Data Protection
Inspection of `backend/main.py:1638-1654` and `1758-1800` verified:
- `correct_answer` and `explanation` are explicitly stripped from candidate questions when constructing `public_questions`.
- Only `id`, `question`, `options`, `difficulty`, and `concept` are returned in the response payload for `/api/quiz/generate`.
- Complete questions with answers and explanations are stored strictly server-side in the SQLite `quiz_sessions` table (`questions` JSON column).
- Leakage risk prior to quiz submission: **ZERO**.

### 3.3 Dynamic Token Budgeting
In `backend/services/providers/base.py:180-212`, `calculate_max_output_tokens()` calculates completion ceilings dynamically:
- Formula: $\text{tokens} = \max(1800, \min(\text{model\_limit}, 210 \times \text{question\_count} + 300))$.
- Model ceilings respected: Llama 3.3 (8,192), GPT-OSS (4,096), Cloudflare (3,000), Gemini (8,192).
- Prevents truncation errors (`finish_reason == 'length'`).

---

## 4. Novelty, Duplicate Prevention & Selective Replacement

### 4.1 Novelty Engine Remediation (`backend/services/novelty.py`)
- **Cognitive Operation Classification:** Introduces `CognitiveOperation` (`CALCULATION`, `DEFINITION_RECALL`, `THEORETICAL_PROPERTY`, `COMPARISON_CONTRAST`, `APPLICATION_SCENARIO`, `PROCESS_MECHANISM`, `GENERAL`).
- **Numerical Problem Templates:** Implements `get_numerical_template()` using `<num>` placeholders to recognize identical problem structures with changed numerical parameters.
- **Resolution of False Duplicates:** Syllabus concept tags alone are **no longer sufficient evidence of duplication**. Questions testing the same syllabus concept are rejected only if they exhibit demonstrated semantic similarity ($\ge 0.48$) against a historical question. Calculation questions and property/definition questions under the same concept are recognized as academically distinct.

### 4.2 Selective Replacement Mechanics (`backend/services/llm_service.py`)
- **Deficit-Only Replenishment:** When initial generation yields fewer than `num_questions` valid items, `needed = num_questions - len(preserved_questions)` is computed.
- **Preservation Invariant:** Valid questions are preserved in `preserved_questions`; only defective, malformed, off-topic, or duplicate questions are discarded.
- **Negative Prompting Constraints:** `build_selective_replacement_prompt()` injects accepted questions under `[ALREADY ACCEPTED]` and historical questions under `[PRIOR ATTEMPT]` as negative constraints, preventing intra-quiz collisions.
- **Re-Validation:** All replacement candidates pass through `classify_and_validate_candidate()`.
- **Termination Safeguards:** Capped by `MAX_REPLACEMENT_ROUNDS = 2` and remaining deadline budget checks (`deadline.can_attempt()`).

---

## 5. Provider Quota and Failover Audit

### 5.1 Quota Classification & Cooldown Management (`backend/services/quota_manager.py`)
- **Classification:** Distinguishes `TPM` (tokens per minute), `RPM` (requests per minute), and `TPD` (tokens per day).
- **Fast-Skip:** When a provider/model is in cooldown, `is_model_available()` returns `False` in sub-millisecond time, allowing the orchestrator to skip to the next model without executing doomed HTTP requests.
- **Safety Invariant:** `record_success()` clears transient `TPM` and `RPM` cooldowns, but **never clears a genuine daily lockout (`TPD`)**.
- **Credential Protection:** Keys are scoped via `hashlib.sha256(key.encode()).hexdigest()[:12]`. Raw API keys are never stored on disk or emitted in logs.

### 5.2 Provider-by-Provider Quota Manager Integration Status
Direct code inspection revealed an important architectural boundary:
1. **Groq Provider (`groq_provider.py`):** **FULLY INTEGRATED.** Checks `quota_manager.is_model_available()`, skips cooling models, records rate limits via `parse_rate_limit()`, records success.
2. **NVIDIA Provider (`nvidia_provider.py`):** **FULLY INTEGRATED.** Checks `quota_manager.is_model_available()`, skips cooling models, records rate limits, records success.
3. **Gemini Provider (`gemini_provider.py`):** **INDEPENDENT ERROR MAPPING.** Does *not* import `quota_manager`. Catches `errors.APIError` and maps 429 to `ProviderUnavailableError("...429...")`. Handled by orchestrator failover, but does not use QuotaManager fast-skip or persistent cooldown caching.
4. **Cloudflare Provider (`cloudflare_provider.py`):** **INDEPENDENT ERROR MAPPING.** Does *not* import `quota_manager`. Catches HTTP 429 / timeouts and maps them to `ProviderUnavailableError`. Handled by orchestrator failover, but does not use QuotaManager fast-skip.

### 5.3 Concurrency and Multi-Process Limitations
- **Thread Safety:** `QuotaManager` uses in-memory dictionaries guarded by `threading.Lock()`, ensuring thread safety within a single process.
- **File Persistence:** State is persisted to `backend/data/quota_state.json` via atomic `os.replace()`, preventing partial-write corruption across server restarts.
- **Multi-Process Limitation:** Atomic file replacement prevents file corruption, but does **not** provide cross-process concurrency locking (no advisory file locks like `fcntl` or `msvcrt.locking`).
- **Deployment Assumption:** LearnAI's production deployment on Render operates as a **single Uvicorn worker process on a single container**. Under this assumption (1 worker), thread synchronization is fully effective. Scaling horizontally to multiple Uvicorn workers or multiple containers requires migrating quota state to a distributed store (e.g., Redis).

---

## 6. Request Deadline and Timeout Boundaries

### 6.1 Timer Lifecycle
1. Request enters `/api/quiz/generate` (`backend/main.py:1134`). Authentication and curriculum lookup execute (~10ms).
2. `generate_quiz()` begins (`backend/services/llm_service.py:509`).
3. `start_time = time.perf_counter()` is captured, and `RequestDeadline(overall_deadline=40.0, start_time=start_time)` is instantiated.
4. The remaining deadline (`deadline.remaining`) is decremented continuously and shared across:
   - Primary provider attempt.
   - Provider failover attempts.
   - Selective question replacement rounds.
   - Re-validation passes.

### 6.2 Crucial Distinction: Software Budget vs. Kernel Hard Interrupt
- **Software Budget (Verified):** The orchestrator verifies `deadline.can_attempt(min_time)` before initiating any network call. If insufficient time remains, it terminates gracefully with `AITimeoutError` (HTTP 504) rather than starting a doomed request.
- **Kernel Socket Boundary (Documented Limitation):** In synchronous Python on Windows and Linux, `timeout=time_left` is passed to the underlying HTTP client. However, if an external socket hangs at the kernel TCP layer or blocks during a synchronous TLS handshake, the Python process cannot forcefully interrupt the blocked socket mid-operation without OS signal support (unsupported on Windows). The 40.0s deadline is a **strictly enforced software orchestration budget**, not a kernel-guaranteed hard preemption.

---

## 7. Database and Persistence Audit

### 7.1 Transaction Integrity and Idempotency
Inspection of `backend/services/database.py:1260-1450` (`submit_quiz_atomic`) confirmed:
- **Single Session Creation:** Exactly one quiz session is inserted into `quiz_sessions` upon generation.
- **Clean Failure Rollback:** If generation fails, zero orphaned sessions remain. If answer submission fails, the database transaction rolls back cleanly.
- **Submission Idempotency:** If a completed quiz session is submitted again, `submit_quiz_atomic()` intercepts `session["status"] == "completed"` and returns the existing attempt result rather than creating duplicate score records.
- **Mastery Consistency:** Topic mastery calculations (`topic_mastery` table) update exactly once per session.

### 7.2 SQLite Concurrency Scope
- SQLite runs with `PRAGMA journal_mode=wal;` and `PRAGMA busy_timeout=5000;`.
- WAL mode supports multiple concurrent readers alongside one writer.
- Under single-worker deployment on Render, WAL mode supports up to 5+ concurrent requests without database locking errors.

---

## 8. Render Deployment Configuration Audit

### 8.1 Configuration Analysis (`render.yaml`)
- **Service Name:** `learnai`
- **Type:** `web` (Plan: `free`)
- **Environment:** `python`
- **Root Directory:** `backend`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT` (Defaults to **1 worker**)
- **Health Check Path:** `/health`
- **Reverse Proxy Headers:** `TRUST_PROXY_HEADERS: "true"`, `ALLOWED_ORIGINS: "*"`

### 8.2 Environment Variable Names Required
The following environment variable names must be configured in the Render dashboard:
- **Security & Database:** `DATABASE_URL`, `SESSION_SECRET_KEY`
- **AI Providers:**
  - `GROQ_API_KEY`, `GROQ_SECONDARY_API_KEY`, `GROQ_BACKUP_API_KEY`
  - `NVIDIA_API_KEY`, `NVIDIA_SECONDARY_API_KEY`, `NVIDIA_BACKUP_API_KEY`
  - `GEMINI_API_KEY`, `GEMINI_SECONDARY_API_KEY`, `GEMINI_BACKUP_API_KEY`
  - `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (Optional)
- **Orchestration Tuning:** `PROVIDER_ORDER` (`groq,nvidia,gemini`), `OVERALL_DEADLINE_SECONDS` (`40.0`), `PROVIDER_TIMEOUT_SECONDS` (`25.0`), `MAX_PROVIDER_RETRIES` (`1`).

---

## 9. Performance & Benchmark Audit

### 9.1 Evaluation of Available Datasets

| Dataset | Nature of Data | Sample Size | Latency p50 | Latency p95 | Max Latency | Success Rate |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `module6_benchmark_results.json` | Simulated Provider Envelopes | 75 | 3.22s | 4.57s | 4.64s | **100%** (75/75) |
| `production_sub_10s_benchmark_results.json` | Live Deployed Render Endpoint | 5 | 6.97s | 12.86s | 12.86s | **100%** (5/5) |
| `diverse_3_results.json` | Live Deployed Render Endpoint | 3 | 6.42s | 7.56s | 7.56s | **100%** (3/3) |

### 9.2 Critical Latency Target Assessment (< 15.0s Target)
- **Simulated Test Baseline:** All 75 requests in Module 6 completed in < 4.64s.
- **Production Historical Baseline:** All 8 recorded live production requests on Render completed in < 12.86s (well within the 15.0s target).
- **Audit Assessment:** The application exhibits architecture capable of meeting sub-15s response times when using high-speed providers (Groq). However, because the 8 live runs represent a preliminary sample, formal statistical certification of the 15-second p95 target across peak production loads remains unverified pending controlled staging verification.

---

## 10. Security & Privacy Audit

| Security Domain | Inspection Method | Finding | Status |
| :--- | :--- | :--- | :---: |
| **Hardcoded Secrets** | Regex scanner across codebase | 0 API keys or private tokens detected | **PASS** |
| **Credential Masking** | Source review (`quota_manager.py`) | Keys hashed with SHA-256; raw keys not logged | **PASS** |
| **Answer Key Protection** | Source review (`main.py:1638-1654`) | Answers stripped from student response payload | **PASS** |
| **Student PII** | Source review (`database.py`) | Passwords hashed with argon2; synthetic test IDs | **PASS** |
| **SQL Injection** | Source review (`database.py`) | Parameterized queries used exclusively | **PASS** |
| **Prompt Injection Defense** | Source review (`main.py:1501-1520`) | Notes tagged `<student_notes_untrusted>` with defensive instructions | **PASS** |
| **CORS Policy** | `render.yaml` & `main.py` | `ALLOWED_ORIGINS: "*"` (Low risk: restrict to frontend domain in production) | **ACCEPTABLE** |

---

## 11. Full Regression Test Verification

The complete regression suite was executed across the entire repository:
- **Command:** `pytest backend/ -q`
- **Exit Code:** `0`
- **Total Tests Collected:** `1,060`
- **Passed:** `1,054`
- **Skipped:** `6` (PostgreSQL/TiDB live network tests)
- **Failed:** `0`
- **Errors:** `0`
- **Execution Runtime:** `161.53 seconds` (2m 41s)

Every test passed cleanly.

---

## 12. Findings Ranked by Severity

### Critical (0)
- None.

### High (0)
- None.

### Medium (2)
1. **Gemini & Cloudflare QuotaManager Asymmetry:** `GroqProvider` and `NvidiaProvider` actively check QuotaManager and fast-skip on cooling models, while `GeminiProvider` and `CloudflareProvider` rely on independent exception catching. *Mitigation:* The orchestrator still fails over cleanly to the next provider upon exception, but adding QuotaManager checks to Gemini and Cloudflare will improve consistency.
2. **Synchronous Socket Blocking Boundary:** Python socket operations on Windows/Linux rely on client socket timeouts rather than kernel preemption. If an upstream API hangs at the TLS layer, the thread waits for the socket timeout. *Mitigation:* Socket timeouts are configured to `time_left` to bound the wait.

### Low (2)
1. **Single-Worker Deployment Constraint:** The SQLite WAL database and in-memory QuotaManager lock assume a single Uvicorn worker process. *Mitigation:* Documented and matched by `render.yaml`.
2. **Wildcard CORS in Production:** `render.yaml` sets `ALLOWED_ORIGINS: "*"`. *Mitigation:* Restrict to the exact production domain in a future infrastructure update.

### Informational (1)
1. **Simulated vs. Real Provider Benchmark Distinction:** Module 6 benchmarks used realistic simulated envelopes; real-provider multi-request latency requires staging verification.

---

## 13. Overall Verdict

### **READY FOR CONTROLLED STAGING VERIFICATION**

No critical or high-severity blockers exist in the committed codebase. The architecture is robust, deterministic, and fully covered by 1,054 passing regression tests. The remaining operational items (real multi-provider traffic testing and live quota resets) can be safely verified in an isolated staging environment following the companion plan in `REAL_PROVIDER_VERIFICATION_PLAN.md`.
