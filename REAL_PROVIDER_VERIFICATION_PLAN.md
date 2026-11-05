# LearnAI — Real-Provider Verification Plan

**Plan Date:** November 3, 2026  
**Auditor:** Senior Backend Architect & Production Reliability Engineer  
**Repository:** `C:\Users\moksh\OneDrive\Desktop\IDP` (`Mokshagnna/LearnAI`)  
**Target Environment:** Isolated Staging Environment (Render Staging / Local Sandbox with Staging Secrets)  
**Cost Boundary:** Strict Budget Caps & Zero Unbudgeted Spend Protocol  
**Purpose:** Provide an actionable, safe, step-by-step verification protocol to validate real AI inference performance across all four supported providers without risking production disruption or unexpected API spend.

---

## 1. Executive Summary

Previous benchmarking in Module 6 validated the algorithmic performance, quota tracking, selective replacement, and failover mechanics of LearnAI using simulated provider latency envelopes derived from historical production records.

This document establishes the **Real-Provider Verification Protocol** to safely validate actual external inference against live AI provider APIs in a controlled staging environment. No live inference requests are executed during this audit; all steps described herein require explicit user authorization and isolated staging credentials before execution.

---

## 2. Provider-by-Provider Test Matrix

| Provider | Target Production Model | Fallback Staging Model | Rate Limit / Quota Constraints | Primary Role |
| :--- | :--- | :--- | :--- | :--- |
| **Groq** | `llama-3.3-70b-versatile` | `llama-3.1-8b-instant` | Free tier: 30 RPM, 6,000 TPM, 100k TPD | Primary Default |
| **NVIDIA NIM** | `meta/llama-3.3-70b-instruct` | `meta/llama-3.1-8b-instruct` | 1,000 credits trial / tier bounds | Secondary Failover |
| **Google Gemini** | `gemini-2.5-flash` | `gemini-1.5-flash` | Free tier: 15 RPM, 1,500 RPD | Tertiary Fallback |
| **Cloudflare Workers AI** | `@cf/meta/llama-3.1-8b-instruct` | `@cf/meta/llama-3-8b-instruct` | Free tier: 10,000 Neurons/day (~30–50 calls) | Last-Resort Fallback |

---

## 3. Required Environment Variable Names

When configuring the isolated staging environment, only configure the following variable **NAMES**. *Never commit, print, or expose secret values in Git or logs:*

### 3.1 Core Secrets
- `DATABASE_URL` (Dedicated staging SQLite or PostgreSQL instance)
- `SESSION_SECRET_KEY` (Dedicated staging secret for HMAC signing)

### 3.2 Provider API Keys & Tokens
- **Groq:**
  - `GROQ_API_KEY` (Primary staging key)
  - `GROQ_SECONDARY_API_KEY` (Secondary test key)
  - `GROQ_BACKUP_API_KEY` (Backup test key)
- **NVIDIA NIM:**
  - `NVIDIA_API_KEY` (Primary staging key)
  - `NVIDIA_SECONDARY_API_KEY` (Secondary test key)
  - `NVIDIA_BACKUP_API_KEY` (Backup test key)
- **Google Gemini:**
  - `GEMINI_API_KEY` (Primary staging key)
  - `GEMINI_SECONDARY_API_KEY` (Secondary test key)
  - `GEMINI_BACKUP_API_KEY` (Backup test key)
- **Cloudflare Workers AI:**
  - `CLOUDFLARE_API_TOKEN` (API token with Workers AI read/write permission)
  - `CLOUDFLARE_ACCOUNT_ID` (Cloudflare account identifier)

### 3.3 Orchestration & Budget Controls
- `PROVIDER_ORDER`: `groq,nvidia,gemini,cloudflare`
- `OVERALL_DEADLINE_SECONDS`: `40.0`
- `DEADLINE_TARGET_SECONDS`: `15.0`
- `PROVIDER_TIMEOUT_SECONDS`: `25.0`
- `GROQ_TIMEOUT_SECONDS`: `11.0`
- `NVIDIA_TIMEOUT_SECONDS`: `16.0`
- `GEMINI_TIMEOUT_SECONDS`: `20.0`
- `CLOUDFLARE_TIMEOUT_SECONDS`: `25.0`
- `MAX_PROVIDER_RETRIES`: `1`
- `MAX_REPLACEMENT_ROUNDS`: `2`

---

## 4. Safe Staging Test Scenarios

The real-provider verification protocol executes six safe test scenarios under strict sample limits:

### Scenario 1: Clean Initial Generation (Per-Provider Baseline)
- **Objective:** Measure real inference latency, token generation speed, and schema compliance for each provider individually.
- **Method:** Isolate each provider using `PROVIDER_ORDER=<single_provider>`:
  1. Groq (`llama-3.3-70b-versatile`): 5 requests.
  2. NVIDIA NIM (`meta/llama-3.3-70b-instruct`): 5 requests.
  3. Gemini (`gemini-2.5-flash`): 5 requests.
  4. Cloudflare (`@cf/meta/llama-3.1-8b-instruct`): 3 requests.
- **Workload:** Course `22MT103`, Topic `Inverse of a Matrix`, Question Count: 10.
- **Verification:** Schema validity, options A–D presence, single correct answer, answer key exclusion from student response payload.

### Scenario 2: Adaptive Retake & Novelty Validation
- **Objective:** Verify real provider adherence to novelty exclusion instructions during retakes.
- **Method:**
  1. Generate Attempt 1 (10 questions) via Groq. Submit answers with score 10/10.
  2. Request Retake Attempt 2 via Groq with student auth token.
  3. Inspect Attempt 2 questions: verify that all 10 questions are academically distinct from Attempt 1, that cognitive operations differ, and that no identical numerical templates are reused.
- **Sample:** 5 retake pairs (10 total requests).

### Scenario 3: Real Provider Failover (Outage Resilience)
- **Objective:** Verify live failover when the primary provider encounters an outage or rate limit.
- **Method:** Configure `PROVIDER_ORDER=groq,nvidia`:
  1. Temporarily configure an invalid `GROQ_API_KEY` (simulating authentication/upstream outage).
  2. Send quiz generation request.
  3. Verify that Groq fails fast (`ProviderConfigError` / `AUTH_CONFIG_ERROR`), the orchestrator captures the failure, and NVIDIA NIM successfully generates the complete quiz within the remaining deadline.
- **Sample:** 3 failover requests.

### Scenario 4: Selective Question Replacement with Real Providers
- **Objective:** Verify that real providers correctly interpret the `[ALREADY ACCEPTED]` and `[PRIOR ATTEMPT]` negative constraints in targeted replenishment prompts.
- **Method:**
  1. Inject 1 synthetic defective question into an 8-question valid batch.
  2. Trigger `build_selective_replacement_prompt()` requesting exactly 2 replacement questions.
  3. Verify that the provider returns 2 questions that do not duplicate the 8 preserved items.
- **Sample:** 3 replacement rounds.

### Scenario 5: Quota Exhaustion & Fast-Skip Recovery
- **Objective:** Verify that QuotaManager detects live HTTP 429 errors from real providers and enters fast-skip cooldown.
- **Method:**
  1. Force a rate limit (or inject a test 429 response).
  2. Verify that subsequent requests return HTTP 429 in < 0.60 seconds without executing external network calls.
  3. Wait for cooldown expiration (or trigger reset); verify that the provider recovers immediately.
- **Sample:** 5 fast-skip requests + 1 recovery request.

### Scenario 6: Concurrent Load Verification
- **Objective:** Verify that concurrent real requests do not cause database deadlocks or thread starvation.
- **Method:** Dispatch 3 concurrent requests simultaneously via `ThreadPoolExecutor` to the staging backend.
- **Sample:** 1 batch of 3 requests.

---

## 5. Cost Controls and Spend Budget Caps

To eliminate the risk of unexpected spend or runaway API charges during staging tests:

1. **Total Request Cap:** The entire verification suite across all 6 scenarios is strictly capped at **no more than 40 total API calls**:
   - Groq: Max 15 calls (~$0.02 or free tier).
   - NVIDIA: Max 12 calls (covered by developer trial credits).
   - Gemini: Max 8 calls (covered by free tier 15 RPM).
   - Cloudflare: Max 5 calls (covered by free tier 10,000 daily Neurons).
2. **Estimated Spend Ceiling:** Total anticipated cost across all providers is **< $0.15 USD**.
3. **Hard Token Limits:** All calls enforce `max_tokens` between 1,800 and 2,400 tokens via `calculate_max_output_tokens()`.
4. **Rate Limit Throttling:** Calls are spaced by at least 2.5 seconds to prevent triggering free-tier rate limits (e.g. Gemini 15 RPM).
5. **No Automatic Retries:** `MAX_PROVIDER_RETRIES=1` prevents infinite looping or unbounded retry spending.

---

## 6. Expected Response Schema & Student Data Protection

Every successful API response from `/api/quiz/generate` must strictly adhere to the validated production schema:

```json
{
  "quiz_id": "uuid-string",
  "attempt_id": "uuid-string",
  "attempt_number": 1,
  "student_id": "student-id-string",
  "auth_token": "hmac-jwt-token",
  "course_code": "22MT103",
  "subject": "Linear Algebra And Ordinary Differential Equations",
  "topic": "Inverse of a Matrix",
  "questions": [
    {
      "id": 0,
      "question": "Question text testing the topic...",
      "options": {
        "A": "Option text A",
        "B": "Option text B",
        "C": "Option text C",
        "D": "Option text D"
      },
      "difficulty": "Easy",
      "concept": "Matrix Inversion"
    }
  ]
}
```

### Critical Security Assertions:
- Under NO circumstances may `correct_answer` appear in the student response.
- Under NO circumstances may `explanation` appear in the student response before quiz submission.
- Complete answers and explanations must exist solely in the server-side SQLite session.

---

## 7. Pass / Fail Evaluation Criteria

| Evaluation Check | Requirement | Pass Condition | Fail Condition |
| :--- | :--- | :--- | :--- |
| **Schema Compliance** | Exactly 10 questions with 4 options (A–D) | 100% of successful responses match schema | Any malformed or incomplete quiz |
| **Answer Key Protection** | Zero answer keys in student payload | 100% of public questions exclude `correct_answer` | Any answer key leakage in response |
| **Groq Latency Target** | Primary provider inference speed | p50 < 8.0s, p95 < 14.0s (under 15s budget) | p50 > 15.0s |
| **Failover Seamlessness** | Automatic failover to secondary on error | Secondary succeeds within remaining deadline | Unhandled 500 or timeout exceeding 40s |
| **Novelty Rate** | Academic distinctness during retakes | 10/10 questions distinct from Attempt 1 | Any duplicate question flagged |
| **Quota Fast-Skip** | Instant abort when in cooldown | Cooldown responses return in < 0.60s | Thread blocked on cooling provider |
| **Database Consistency** | Single session per quiz, idempotent submit | Exactly 1 session, repeated submits idempotent | Orphaned sessions or double-scoring |

---

## 8. Steps Requiring Explicit User Authorization

Before initiating any phase of the Real-Provider Verification Protocol, the user must explicitly authorize:

1. **Authorization 1:** Approval of the staging environment target (Render Staging service URL or local staging process with staging `.env`).
2. **Authorization 2:** Provisioning of staging API credentials (configured securely in the staging environment, never shared in chat).
3. **Authorization 3:** Approval to execute the 40-request verification suite.

*No external API calls or network requests will be executed without these three explicit approvals.*
