# LearnAI — Gate 1 Controlled Smoke Test Results

**Execution Date:** November 3, 2026  
**Auditor / Runner:** Senior Backend Architect & Production Reliability Engineer  
**Repository:** `C:\Users\moksh\OneDrive\Desktop\IDP` (`Mokshagnna/LearnAI`)  
**Configuration File:** `backend/.env.staging.local` (Loaded via `LEARNAI_ENV_FILE`)  
**Active Database Sandbox:** `C:\Users\moksh\OneDrive\Desktop\IDP\backend\data\staging_gate1.db`  
**Execution Status:** **HALTED AT PRE-EXECUTION CHECK — ZERO EXTERNAL REQUESTS DISPATCHED**  

---

## 1. Executive Summary

Execution of the Gate 1 Controlled Four-Provider Smoke Test was initiated under explicit user authorization. In accordance with strict pre-execution instructions (*"Verify all provider credentials required for the selected test are present without printing their values. If any check fails, STOP without making a provider request."*), a comprehensive automated audit was conducted on the staging environment.

### Primary Outcome:
- **Pre-Flight Isolation Checks:** **100% PASSED.** Active database is strictly `backend/data/staging_gate1.db`, `backend/.env.staging.local` is loaded exclusively, and parent shell variables (`DATABASE_URL`, `MYSQL_DATABASE_URL`, `RENDER`) are absent.
- **Credential Presence Check:** **FAILED (STOP CONDITION TRIGGERED).** As configured during Phase 2 staging provisioning, `backend/.env.staging.local` currently contains empty placeholders for all four providers (`GROQ_API_KEY=`, `NVIDIA_API_KEY=`, `GEMINI_API_KEY=`, `CLOUDFLARE_API_TOKEN=`, `CLOUDFLARE_ACCOUNT_ID=`).
- **Safety Enforcement:** The pre-execution check caught the missing credentials and **halted execution immediately before dispatching any external HTTP network request**.
- **External Requests Dispatched:** **0 calls**.
- **API Credits / Financial Spend:** **$0.00 USD**.

---

## 2. Pre-Execution Gate Verification Matrix

Prior to issuing any network request, the following pre-flight assertions were executed in code:

| Verification Gate | Expected State | Actual Recorded State | Gate Status |
| :--- | :--- | :--- | :---: |
| **1. Explicit Staging File Resolution** | `backend/.env.staging.local` | `C:\Users\moksh\OneDrive\Desktop\IDP\backend\.env.staging.local` | **PASSED** |
| **2. Database Sandbox Isolation** | `backend/data/staging_gate1.db` | `C:\Users\moksh\OneDrive\Desktop\IDP\backend\data\staging_gate1.db` (`PRAGMA database_list;`) | **PASSED** |
| **3. Parent Shell Variable Absence** | `DATABASE_URL=None`<br>`MYSQL_DATABASE_URL=None`<br>`RENDER=None` | `{'DATABASE_URL': True, 'MYSQL_DATABASE_URL': True, 'RENDER': True}` (All absent) | **PASSED** |
| **4. Non-Production Mode Guard** | `is_production_mode() is False` | `is_production_mode() == False` | **PASSED** |
| **5. Retry & Replacement Bounds** | `MAX_PROVIDER_RETRIES=0`<br>`MAX_REPLACEMENT_ROUNDS=0` | `MAX_PROVIDER_RETRIES=0`<br>`MAX_REPLACEMENT_ROUNDS=0` | **PASSED** |
| **6. Non-Cascading Fallback Models** | Primary == Fallback model | Groq: `llama-3.1-8b-instant`<br>NVIDIA: `meta/llama-3.1-8b-instruct`<br>Gemini: `gemini-1.5-flash`<br>Cloudflare: `@cf/meta/llama-3.1-8b-instruct` | **PASSED** |
| **7. Provider Credential Check** | Staging keys populated (`True`) | `{'groq': False, 'nvidia': False, 'gemini': False, 'cloudflare': False}` | 🛑 **STOP TRIGGERED** |

---

## 3. Provider Smoke Test Telemetry

In accordance with the stop trigger, zero requests were sent to external inference endpoints:

### Provider 1: Groq
- **Target Model:** `llama-3.1-8b-instant`
- **Fallback Model:** `llama-3.1-8b-instant` (multi-model cascade disabled)
- **Timeout Budget:** 11.0s
- **Credential Status:** Absent (`GROQ_API_KEY` empty)
- **External Requests Dispatched:** `0`
- **HTTP Status:** `N/A (Halted at Pre-Flight Gate)`
- **Latency:** `N/A`
- **Tokens Consumed:** `0`
- **Response Validation:** `N/A`
- **Stop Reason:** Pre-execution check halted call; credentials not populated.

### Provider 2: NVIDIA NIM
- **Target Model:** `meta/llama-3.1-8b-instruct`
- **Fallback Model:** `meta/llama-3.1-8b-instruct` (multi-model cascade disabled)
- **Timeout Budget:** 16.0s
- **Credential Status:** Absent (`NVIDIA_API_KEY` empty)
- **External Requests Dispatched:** `0`
- **HTTP Status:** `N/A (Halted at Pre-Flight Gate)`
- **Latency:** `N/A`
- **Tokens Consumed:** `0`
- **Response Validation:** `N/A`
- **Stop Reason:** Pre-execution check halted call; credentials not populated.

### Provider 3: Google Gemini
- **Target Model:** `gemini-1.5-flash`
- **Fallback Model:** `gemini-1.5-flash` (multi-model cascade disabled)
- **Timeout Budget:** 20.0s
- **Credential Status:** Absent (`GEMINI_API_KEY` empty)
- **External Requests Dispatched:** `0`
- **HTTP Status:** `N/A (Halted at Pre-Flight Gate)`
- **Latency:** `N/A`
- **Tokens Consumed:** `0`
- **Response Validation:** `N/A`
- **Stop Reason:** Pre-execution check halted call; credentials not populated.

### Provider 4: Cloudflare Workers AI
- **Target Model:** `@cf/meta/llama-3.1-8b-instruct`
- **Timeout Budget:** 20.0s
- **Credential Status:** Absent (`CLOUDFLARE_API_TOKEN` & `CLOUDFLARE_ACCOUNT_ID` empty)
- **External Requests Dispatched:** `0`
- **HTTP Status:** `N/A (Halted at Pre-Flight Gate)`
- **Latency:** `N/A`
- **Tokens Consumed:** `0`
- **Response Validation:** `N/A`
- **Stop Reason:** Pre-execution check halted call; credentials not populated.

---

## 4. Total Accounting & Financial Exposure

- **Total Inferences Dispatched:** **0 calls**
- **Unauthorized / Retry Calls:** **0 calls**
- **Token Consumption:** **0 tokens**
- **Actual Financial Spend:** **$0.00 USD**
- **Staging Database State:** Clean baseline preserved (**0 student records, 0 quiz attempts** in `staging_gate1.db`)
- **Production Database State:** Completely unaffected; zero network connections attempted.

---

## 5. Exact Blocker and Actionable Next Step

The staging sandbox, network bounds, database isolation, and test execution harnesses are completely primed and ready. Gate 1 execution requires exactly one user action:

1. Open [`backend/.env.staging.local`](file:///C:/Users/moksh/OneDrive/Desktop/IDP/backend/.env.staging.local) (local only; Git-ignored).
2. Populate the dedicated staging API test keys for the providers you wish to smoke test:
   ```dotenv
   GROQ_API_KEY=<your_staging_groq_key>
   NVIDIA_API_KEY=<your_staging_nvidia_key>
   GEMINI_API_KEY=<your_staging_gemini_key>
   CLOUDFLARE_API_TOKEN=<your_staging_cloudflare_token>
   CLOUDFLARE_ACCOUNT_ID=<your_staging_cloudflare_account_id>
   ```
3. Run the single-provider smoke tests. The pre-flight checks will then pass and sequentially dispatch exactly **1 request per populated provider**.
