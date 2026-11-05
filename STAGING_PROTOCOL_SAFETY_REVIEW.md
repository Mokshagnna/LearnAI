# LearnAI — Staging Protocol Safety Review

**Review Date:** November 3, 2026  
**Auditor:** Senior Backend Architect & Production Reliability Engineer  
**Repository:** `C:\Users\moksh\OneDrive\Desktop\IDP` (`Mokshagnna/LearnAI`)  
**Target Environment:** Isolated Staging Environment (Render Staging or Local Staging Sandbox)  
**Documents Reviewed:** `REAL_PROVIDER_VERIFICATION_PLAN.md` and `POST_COMMIT_PRODUCTION_READINESS_AUDIT.md`  
**Overall Readiness Classification:** **NOT READY — STAGING ISOLATION INCOMPLETE**  
*(Secondary blocker: **NOT READY — PROVIDER CONFIGURATION INCOMPLETE** due to absent staging API keys)*

---

## 1. Executive Summary

This safety review evaluated the feasibility, cost exposure, quota safety, security isolation, arithmetic consistency, and operational risks of executing the proposed real-provider verification protocol.

### Key Conclusions:
1. **Protocol Viability:** The test architecture is well-structured and technically capable of verifying real-provider inference, schema compliance, failover, novelty evaluation, selective replacement, and database persistence once staging isolation is provisioned.
2. **Current Staging Readiness:** Testing cannot be authorized at this time. An inspection of the local and staging environment confirms that no isolated staging credentials (`GROQ_API_KEY`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, `CLOUDFLARE_API_TOKEN`), no staging database (`staging_gate1.db`), and no staging `.env.staging` file currently exist.
3. **Required In-Code Model Clarifications:**
   - Groq defaults to `llama-3.1-8b-instant` with fallback `llama-3.3-70b-versatile`.
   - NVIDIA NIM defaults to `meta/llama-3.1-8b-instruct`. Notice: `meta/llama-3.3-70b-instruct` is explicitly blacklisted in `KNOWN_INVALID_NVIDIA_MODELS` in `backend/services/providers/nvidia_provider.py`.
   - Google Gemini and Cloudflare do not use the shared `QuotaManager`; they rely on independent exception catching and orchestrator failover.
4. **Corrected Test Accounting & Arithmetic:**
   - **Gate 6 (Selective Replacement):** In `llm_service.py`, `needed = num_questions - len(preserved_questions)`. For a 10-question quiz where 8 valid questions are preserved (2 rejected for defects/duplication), `needed = 10 - 8 = 2` replacement questions (not 3).
   - **Gate 7 (DB Concurrency):** Separates external provider inferences (0 calls) from local HTTP/database transactions (3 local requests) to ensure zero unintended API spend.
   - **Gate 8 (Latency Distribution):** Small sample sizes ($\le 14$ calls) are strictly exploratory and cannot be used to certify production p95 SLAs ($< 15$s).
5. **Cost Guarantee Boundary:** Flat request counts cannot mathematically guarantee spend below $0.15 without verified billing caps on provider accounts. The request cap is strictly bounded at $\le 28$ total calls, starting with an isolated 4-call smoke test (Gate 1, upper bound $< \$0.03$).

---

## 2. Staging Readiness Status Table

| Requirement | Status | Audit Findings & Blocking Factors |
| :--- | :---: | :--- |
| **Separate Staging Environment** | **NOT VERIFIED** | No separate staging service or dedicated staging sandbox running. |
| **Separate Staging Credentials** | **NOT VERIFIED** | Provider API keys are completely absent from local staging environment; only `backend/.env.example` exists. |
| **Separate Staging Database** | **NOT VERIFIED** | `LEARNAI_DB_PATH` is not configured to an isolated staging SQLite file (`staging_gate1.db`). |
| **Provider In-Code Configuration** | **VERIFIED** | Code inspected: models, fallbacks, timeouts, and `KNOWN_INVALID_NVIDIA_MODELS` verified. |
| **Cost Controls & Spend Safeguards** | **VERIFIED** | Hard stop rules defined; Gate 1 ceiling $< \$0.03$; maximum protocol ceiling $\le 28$ calls. |
| **Gate 6 Replenishment Arithmetic** | **CORRECTED** | Corrected formula: $10 - 8 = 2$ replacement questions required. |
| **Gate 7 Call Separation** | **CORRECTED** | Disentangled 0 external provider calls from 3 local DB concurrency requests. |
| **Gate 8 Latency Certification Scope** | **DOCUMENTED** | Flagged as exploratory baseline only; formal p95 certification requires $\ge 20$ steady-state calls. |

---

## 3. Provider Test Coverage Analysis

| Provider | Model & Config | Response Schema | Timeout & Budget | Rate Limit & Quota Mechanism | Fallback & Retry Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Groq** | `llama-3.1-8b-instant`<br>(Fallback: `llama-3.3-70b-versatile`)<br>`GROQ_API_KEY` | Clean JSON with 10 questions (options A–D, difficulty, concept, explanation). | `timeout=11.0s`<br>Dynamic ceiling (~2,610 tokens). | **Shared QuotaManager:** Catches 429, calls `parse_rate_limit()`, fast-skips cooling models in < 1ms. | Retries fallback model (`llama-3.3-70b-versatile`) once, then raises `ProviderUnavailableError` triggering failover to NVIDIA. |
| **NVIDIA NIM** | `meta/llama-3.1-8b-instruct`<br>(Fallback: `meta/llama-3.1-70b-instruct`)<br>`NVIDIA_API_KEY` | OpenAI-compatible chat completion JSON containing questions array. | `timeout=16.0s`<br>Dynamic ceiling (~2,610 tokens). | **Shared QuotaManager:** Catches 429, records in `QuotaManager`, fast-skips cooling models. | Attempts fallback model (`meta/llama-3.1-70b-instruct`), then fails over to Gemini. (`meta/llama-3.3-70b-instruct` is blocked). |
| **Google Gemini** | `gemini-1.5-flash`<br>(Fallback: `gemini-1.5-pro`)<br>`GEMINI_API_KEY` | Structured JSON with questions array. | `timeout=20.0s`<br>Dynamic ceiling (~2,610 tokens). | **Independent Error Mapping:** Does *not* check or record in `QuotaManager`. Catches `errors.APIError` (code 429) and maps to `ProviderUnavailableError`. | Fails over to Cloudflare via orchestrator catch block. Auth errors fail fast without backup retries. |
| **Cloudflare Workers AI** | `@cf/meta/llama-3.1-8b-instruct`<br>`CLOUDFLARE_API_TOKEN`<br>`CLOUDFLARE_ACCOUNT_ID` | `{ "result": { "response": "..." } }` | `timeout=25.0s`<br>Capped at 3,000 completion tokens. | **Independent Error Mapping:** Does *not* check or record in `QuotaManager`. Catches `httpx.HTTPStatusError` and maps to `ProviderUnavailableError`. | Last provider in sequence; if it fails, orchestrator evaluates best available fallback or raises `AITimeoutError` / `AIServiceUnavailableError`. |

---

## 4. Cost Protection and Budget Risk Assessment

### 4.1 Token Consumption and Pricing Assumptions
- **Average Prompt Input:** ~1,000–1,500 tokens (curriculum context, syllabus lines, instructions).
- **Average Completion Output:** ~1,800–2,400 tokens (10 multiple-choice questions with 4 options and detailed explanations).
- **Average Combined Tokens per Call:** ~3,200–3,900 tokens.
- **Provider Pricing Assumptions:**
  - **Groq:** Free tier (30 RPM, 6,000 TPM, 100k TPD) $\to$ $0.00. (On paid tier: $0.59 / $0.79 per 1M tokens $\approx$ $0.0025/call).
  - **NVIDIA NIM:** Free developer trial (1,000 credits). (On paid tier: ~$0.70 per 1M tokens $\approx$ $0.0025/call).
  - **Google Gemini:** Free tier (15 RPM, 1,500 RPD) $\to$ $0.00.
  - **Cloudflare Workers AI:** Free tier (10,000 Neurons/day) $\to$ $0.00.

### 4.2 Spend Guarantee Reality & Mitigations
- **Finding:** A spend ceiling below $0.15 **cannot be guaranteed purely by request count** if:
  1. An account has active pay-as-you-go billing with auto-recharge.
  2. A provider modifies pricing tiers or free-tier quotas.
  3. Upstream retries or unexpected prompt expansions occur.
- **Mandatory Safety Mitigation:**
  - **Reduced Call Ceiling:** Lower the maximum total call count from 40 to **$\le 28$ requests**.
  - **Single-Call Smoke Gate:** Execute exactly **one single test call per provider** first. Verify billing telemetry on the provider dashboards before proceeding to any subsequent calls.
  - **Immediate Abort Rule:** If any provider account registers unexpected billing charges or lacks spending caps, testing on that provider terminates immediately.

---

## 5. Staging Environment Isolation Verification

To guarantee zero impact on production, the staging environment must satisfy six isolation invariants:

1. **Dedicated Credentials:** Staging must use independent test API keys. Production keys configured on Render must **never** be used for staging tests.
2. **Isolated Database:** Staging must point to an isolated database (`backend/data/staging_gate1.db` or dedicated staging database instance). Production database tables must never be touched.
3. **Synthetic Student Identity:** All tests must use synthetic student IDs (`staging_student_<timestamp>`). No production student records or personal data may be queried or created.
4. **Quota Pool Separation:** Testing must use separate provider accounts or keys so staging calls do not consume production TPM/RPD quotas.
5. **Traffic Isolation:** Staging requests must target only the staging service or local test harness. Production web traffic is completely unaffected.
6. **Environment Variable Protection:** Production Render dashboard settings remain locked and unmodified.

---

## 6. Recommended Phased Execution Sequence (8 Gates)

Testing must proceed through eight sequential gates. If any gate fails, testing halts immediately:

```
[Gate 1: Single Call] ──> [Gate 2: Schema Check] ──> [Gate 3: Failover Test] ──> [Gate 4: Quota Fast-Skip]
          │
          v
[Gate 5: Retake Novelty] ──> [Gate 6: Selective Repl] ──> [Gate 7: DB Concurrency] ──> [Gate 8: Sample Latency]
```

### Stage 1: Single Controlled Smoke Test (4 calls total)
- Execute exactly 1 request on Groq, 1 on NVIDIA, 1 on Gemini, 1 on Cloudflare.
- Inspect HTTP status (must be 200), latency, and provider dashboard billing.

### Stage 2: Schema & Academic Correctness Verification (0 additional calls)
- Inspect the 4 generated quizzes from Stage 1:
  - Exactly 10 questions per quiz.
  - Exactly 4 options (`A`, `B`, `C`, `D`).
  - Exactly one correct answer.
  - `correct_answer` and `explanation` stripped from student-facing payload.
  - Mathematical correctness verified by academic verifiers.

### Stage 3: Outage Failover Verification (2 calls)
- Set invalid primary key; execute generation request.
- Verify automatic failover to secondary provider within remaining deadline.

### Stage 4: Quota Handling & Fast-Skip Verification (2 calls)
- Trigger rate limit; verify sub-second 429 response on Groq/NVIDIA.
- Verify graceful fallback on Gemini/Cloudflare.

### Stage 5: Retake Novelty Verification (2 calls: 1 initial + 1 retake)
- Submit initial attempt; generate retake.
- Verify that Attempt 2 contains 0 duplicate questions, distinct cognitive operations, and non-overlapping numerical templates.

### Stage 6: Selective Question Replacement (1 call)
- Inject 2 defective/rejected questions into an initial 10-question batch (leaving 8 valid questions preserved).
- Verify that targeted replenishment calculates `needed = 10 - 8 = 2` replacement questions with negative constraints and recovers to 10 valid questions.

### Stage 7: Database Concurrency & Isolation Verification (0 external inference calls, 3 local DB calls)
- Verify single session creation, idempotent submission, and run 3 concurrent requests against the staging database without locking errors.
- External provider calls remain 0 to prevent unnecessary API spend.

### Stage 8: Measured Latency Distribution (Max 14 additional calls)
- Execute small batches (e.g. 5 on primary Groq) to record exploratory latency distribution (min, p50, p95, max).
- *Statistical Limitation:* This small exploratory sample ($\le 14$ calls) cannot formally certify production p95 latency ($< 15$s).
- **Total Provider Inference Calls Across All 8 Stages:** $\le 25$ calls (well within the $\le 28$ request hard ceiling).

---

## 7. CORS Configuration & Security Hardening

### 7.1 Current Code Implementation (`backend/main.py:259–269`)
```python
is_wildcard = (len(allowed_origins) == 1 and allowed_origins[0] == "*") or not allowed_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins else ["*"],
    allow_credentials=False if is_wildcard else True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
    allow_headers=["*"],
)
```

### 7.2 Security Evaluation
- **Credentials Protection:** When `ALLOWED_ORIGINS` is `*` (as in `render.yaml`), `is_wildcard` is `True`, which sets `allow_credentials=False`. This safely satisfies browser CORS standards (browsers reject responses that combine `Access-Control-Allow-Origin: *` with `Access-Control-Allow-Credentials: true`).
- **Authentication Model:** LearnAI does not use HTTP-only cookies; it uses `Authorization: Bearer <token>` headers stored in `localStorage`.
- **Cross-Origin Content Exposure:** A wildcard origin permits any external website in a student's browser to read quiz responses.
- **Recommended Hardening (Future Configuration Change):**  
  In the Render environment settings, update:
  ```bash
  ALLOWED_ORIGINS=https://learnai-fzgd.onrender.com,https://your-custom-domain.com
  ```
  This restricts cross-origin access strictly to the official frontend.

---

## 8. Specific Required Changes Before Execution

The verification protocol is **APPROVED AFTER SPECIFIC CHANGES**. The following five changes must be incorporated prior to test execution:

1. **Enforce 8-Gate Phased Sequence:** Replace bulk execution with the sequential 8-stage gating process, requiring Stage 1 single-call verification before proceeding.
2. **Incorporate Gemini/Cloudflare Quota Reality:** Acknowledge in test assertions that Gemini and Cloudflare do not use QuotaManager fast-skips and must be verified via exception failover.
3. **Lower Maximum Call Ceiling:** Cap total staging API requests at **$\le 28$ calls** (down from 40).
4. **Mandatory Billing Telemetry Check:** Require a manual verification of provider billing balances immediately after Stage 1 (the single-call smoke test).
5. **Explicit Staging Credential Verification:** Confirm that all API keys configured are staging-specific test keys, never production keys.

---

## 9. Required User Authorizations

Before any network request is initiated against external provider APIs, the user must explicitly provide:

- [ ] **Authorization 1:** Approval of the staging target (isolated local staging sandbox or Render staging service).
- [ ] **Authorization 2:** Confirmation that staging-specific API credentials have been provisioned in the staging environment (`backend/.env.staging`).
- [ ] **Authorization 3:** Approval to execute Stage 1 (the 4-call smoke test).

---

## 10. Audit Confirmation

- **No commits created** during this turn.
- **No GitHub push** was performed.
- **No deployment** was initiated.
- **No production settings or environment variables were changed.**
- **No production credentials were accessed or exposed.**
- **No real provider requests were executed ($0.00 spend strictly preserved).**
- **All uncommitted frontend modifications and untracked artifacts remain intact.**


