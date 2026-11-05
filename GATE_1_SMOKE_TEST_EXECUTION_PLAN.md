# LearnAI — Gate 1 Smoke Test Execution Plan

**Document Version:** 1.0  
**Date:** November 3, 2026  
**Auditor:** Senior Backend Architect & Production Reliability Engineer  
**Repository:** `C:\Users\moksh\OneDrive\Desktop\IDP` (`Mokshagnna/LearnAI`)  
**Target Environment:** Isolated Local Staging Sandbox (via `backend/.env.staging`) or Dedicated Render Staging Service  
**Execution Phase:** Gate 1 (Single Controlled Smoke Test)  
**Execution Status:** **NOT EXECUTED — AWAITING STAGING PROVISIONING & USER AUTHORIZATION**

---

## 1. Objective and Safety Scope

Gate 1 is a minimal, low-risk, single-call verification protocol designed to validate real external connectivity, authentication, response parsing, and schema compliance across each supported AI provider individually before any multi-call testing or batch benchmarking is permitted.

### Strict Safety Boundaries:
- **Maximum Provider Inferences:** Exactly **one single controlled request per provider** (4 requests total: 1 Groq, 1 NVIDIA, 1 Gemini, 1 Cloudflare).
- **Zero Automatic Retries:** If a provider fails, do **not** retry or fall back automatically during Gate 1. Record the failure, isolate the cause, and stop.
- **Isolated Staging Database:** Must run against an isolated SQLite database (`backend/data/staging_gate1.db`), completely disconnected from production.
- **Synthetic Data Exclusively:** Test student ID: `staging_gate1_student_<timestamp>`. No production student data.
- **Zero Spend Exposure:** Upper-bound cost across all 4 single calls is conservatively estimated at **< $0.03 USD** (or $0.00 on provider free tiers).
- **Immediate Halt Condition:** Testing stops immediately upon any unexpected HTTP status, latency exceeding per-provider timeout, schema malformation, or billing discrepancy.

---

## 2. In-Code Provider Configuration & Model Verification

Direct code inspection of the committed provider classes revealed the exact in-code defaults and verified model identifiers:

| Provider | In-Code Default Model | Code-Enforced Fallback | Known Invalid Models (In Code) | Timeout Setting | Output Token Ceiling | QuotaManager Integration |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: |
| **Groq** (`groq_provider.py`) | `llama-3.1-8b-instant` | `llama-3.3-70b-versatile` | `llama3-70b-8192` (deprecated) | 11.0s (`GROQ_TIMEOUT_SECONDS`) | Dynamic (~2,610 tokens) | **YES** (Fast-skips cooling models) |
| **NVIDIA NIM** (`nvidia_provider.py`) | `meta/llama-3.1-8b-instruct` | `meta/llama-3.1-70b-instruct` | `meta/llama-3.3-70b-instruct` (Explicitly blocked) | 16.0s (`NVIDIA_TIMEOUT_SECONDS`) | Dynamic (~2,610 tokens) | **YES** (Fast-skips cooling models) |
| **Google Gemini** (`gemini_provider.py`) | `gemini-1.5-flash` | `gemini-1.5-pro` | `gemini-2.0-flash`, `gemini-1.0-pro` | 20.0s (`GEMINI_TIMEOUT_SECONDS`) | Dynamic (~2,610 tokens) | **NO** (Exception failover only) |
| **Cloudflare** (`cloudflare_provider.py`) | `@cf/meta/llama-3.1-8b-instruct` | N/A | Older `@cf/meta/llama-2-*` | 25.0s (`CLOUDFLARE_TIMEOUT_SECONDS`) | 3,000 completion limit | **NO** (Exception failover only) |

*Important Finding:* `NvidiaProvider` explicitly defines `KNOWN_INVALID_NVIDIA_MODELS = {"meta/llama-3.3-70b-instruct"}` in code. Previous plans referencing `llama-3.3-70b` on NVIDIA were incorrect; the verified model on NVIDIA NIM is `meta/llama-3.1-8b-instruct` (or `meta/llama-3.1-70b-instruct`).

---

## 3. Required Staging Environment Variables (Names Only)

To execute Gate 1, the following environment variables must be provisioned in an isolated `backend/.env.staging` file. *Never commit this file or expose values:*

```bash
# Isolated Staging Database (DO NOT point to production)
LEARNAI_DB_PATH=backend/data/staging_gate1.db

# Dedicated Staging Session Secret
SESSION_SECRET_KEY=staging_ephemeral_secret_key_gate1_test_only

# Staging Provider API Keys (Must be dedicated test keys, never production keys)
GROQ_API_KEY=<staging_groq_key>
NVIDIA_API_KEY=<staging_nvidia_key>
GEMINI_API_KEY=<staging_gemini_key>
CLOUDFLARE_API_TOKEN=<staging_cloudflare_token>
CLOUDFLARE_ACCOUNT_ID=<staging_cloudflare_account_id>

# Orchestration Bounds
MAX_PROVIDER_RETRIES=0
OVERALL_DEADLINE_SECONDS=40.0
DEADLINE_TARGET_SECONDS=15.0
```

---

## 4. Step-by-Step Gate 1 Execution Sequence

Gate 1 consists of four isolated calls executed sequentially with manual telemetry verification between calls:

### Step 1.1: Groq Single-Call Smoke Test (Call 1)
1. **Configuration:** Set `PROVIDER_ORDER=groq`.
2. **Payload:**
   ```json
   {
     "course_code": "22MT103",
     "topic": "Inverse of a Matrix",
     "student_id": "staging_gate1_groq_01",
     "retake": false,
     "num_questions": 10
   }
   ```
3. **Execution:** Dispatch `POST /api/quiz/generate`.
4. **Verification:**
   - Status code: `200 OK`.
   - Latency recorded: Target < 8.0s (Timeout: 11.0s).
   - Questions count: Exactly 10.
   - Answer key check: `correct_answer` and `explanation` MUST NOT appear in the client JSON response.
   - Check Groq developer console: verify exactly 1 API call logged and token usage ~3,200 tokens.

### Step 1.2: NVIDIA NIM Single-Call Smoke Test (Call 2)
1. **Configuration:** Set `PROVIDER_ORDER=nvidia`.
2. **Payload:** Same standardized syllabus topic (`22MT103`, `Inverse of a Matrix`, student `staging_gate1_nvidia_01`).
3. **Execution:** Dispatch `POST /api/quiz/generate`.
4. **Verification:**
   - Status code: `200 OK`.
   - Latency recorded: Target < 12.0s (Timeout: 16.0s).
   - Questions count: Exactly 10.
   - Verify OpenAI-compatible chat completion parsing.
   - Check NVIDIA dashboard: verify token consumption from trial credits (~3,200 tokens).

### Step 1.3: Google Gemini Single-Call Smoke Test (Call 3)
1. **Configuration:** Set `PROVIDER_ORDER=gemini`.
2. **Payload:** Standard syllabus topic (`22MT103`, `Inverse of a Matrix`, student `staging_gate1_gemini_01`).
3. **Execution:** Dispatch `POST /api/quiz/generate`.
4. **Verification:**
   - Status code: `200 OK`.
   - Latency recorded: Target < 15.0s (Timeout: 20.0s).
   - Questions count: Exactly 10.
   - Verify Google GenAI SDK parsing and finish reason.
   - Check Google Cloud console: verify request falls under free tier allowance (15 RPM).

### Step 1.4: Cloudflare Workers AI Single-Call Smoke Test (Call 4)
1. **Configuration:** Set `PROVIDER_ORDER=cloudflare`.
2. **Payload:** Standard syllabus topic (`22MT103`, `Inverse of a Matrix`, student `staging_gate1_cloudflare_01`).
3. **Execution:** Dispatch `POST /api/quiz/generate`.
4. **Verification:**
   - Status code: `200 OK`.
   - Latency recorded: Target < 25.0s (Timeout: 25.0s).
   - Questions count: Exactly 10.
   - Check Cloudflare dashboard: verify Neurons consumed under daily free allowance (10,000 Neurons).

---

## 5. Pass / Fail Evaluation Gates

To pass Gate 1 and become eligible to consider Gate 2:
- [ ] All 4 providers return HTTP 200 OK on their single test call.
- [ ] 100% of responses contain exactly 10 multiple-choice questions with 4 distinct options (`A`, `B`, `C`, `D`).
- [ ] Exactly 0 responses contain `correct_answer` or `explanation` in the client-facing response.
- [ ] Zero database lock contention or SQLite corruption occurs on `staging_gate1.db`.
- [ ] Provider developer consoles confirm token usage aligns with expectations (~3,000–3,500 tokens per call).
- [ ] Zero unexpected billing or credit drain is detected.

**Immediate Stop Triggers:**
- Any provider returns HTTP 401 Unauthorized (invalid key).
- Any provider returns HTTP 429 Rate Limited on the first call.
- Latency exceeds provider timeout.
- Response contains fewer than 10 questions or malformed JSON.
- Any production database or production environment variable is referenced.

---

## 6. Actionable Next Steps to Enable Gate 1

Gate 1 cannot execute until the following prerequisite actions are performed:
1. Create `backend/.env.staging` with isolated staging credentials (never commit this file).
2. Point `LEARNAI_DB_PATH` to an isolated SQLite file (`backend/data/staging_gate1.db`).
3. Obtain explicit user authorization to execute the 4 single-call requests.
