# LearnAI — Production Quiz Generation Failure: Root Cause & Release Acceptance Report

**Date:** November 3, 2026  
**Role:** Senior Python/FastAPI Engineer, AI Integration Specialist, QA Engineer, Release Auditor  
**Canonical Repository Root:** `C:\Users\moksh\OneDrive\Desktop\IDP`  
**GitHub Remote:** `https://github.com/Mokshagnna/LearnAI.git`  
**Active Branch:** `main` (tracking `origin/main`)  
**HEAD Commit:** `240171e2643b626ea029b156ac9582965e9afd0b` — `fix: switch Groq to llama-3.3-70b-versatile to resolve TPM quota timeout for n=10 quizzes`  
**Production URL:** `https://learnai-fzgd.onrender.com`  
**Operating Environment:** Windows 11, Python 3.12.14, pytest 9.1.1  

---

## 1. Executive Summary

The latest production commit (`240171e`) is deployed and the application is reachable and healthy, BUT real AI quiz generation is **failing in production**. Every attempted quiz generation times out across all configured providers (Groq → NVIDIA → Gemini) within the 40-second application deadline.

The observed failure mode matches the historically reported symptom ("quiz generation unavailable"), but with a different terminal error: instead of the curated/stored fallback (which has been correctly removed), the endpoint now returns:

`{"detail":"Quiz generation timed out across configured providers. Please try again."}` with `x-error-category: TIMEOUT`.

**Bottom line:** The changes in `240171e` (Groq model switch to `llama-3.3-70b-versatile`, 12s Groq / 16s NVIDIA per-attempt caps, partial-quiz >=8 threshold, relaxed novelty filter) are present in code, but they have **not restored reliable real AI quiz generation in production**. Real generation remains broken.

Automated tests pass (917 passed / 6 skipped / 0 failed), but they are **mocked** — they exercise orchestration logic with `MagicMock` providers and do not call real AI providers. Passing automated tests do **not** establish that production AI generation works.

**Final verdict: NOT FIXED.** Real production AI quiz generation continues to fail.

---

## 2. Production Commit & Deployment Verification

| Check | Result | Evidence |
|---|---|---|
| Local HEAD is `240171e` | YES | `git log` shows HEAD at `240171e`, branch `main` tracks `origin/main`, working tree clean (only unrelated report file modified) |
| Production reachable | YES | `GET https://learnai-fzgd.onrender.com/api/health` → HTTP 200 |
| Application startup | YES | Health endpoint responds; origin server identified as `uvicorn` in response headers |
| MySQL connected | YES | Health body: `{"status":"healthy","database":"connected",...}` |
| Curriculum reachable | YES | `GET /api/courses` → HTTP 200, full syllabus index returned |
| Production running commit `240171e` | **CANNOT VERIFY** | No endpoint exposes the deployed git SHA. Health endpoint reports only `version: "1.0.0"`. Render deployment history is not accessible from this environment. |
| Render deploy completed successfully | PARTIAL | Health is healthy, but the exact deploy SHA and deploy-timestamp cannot be confirmed externally. |

Render config observed (`render.yaml`): `PROVIDER_ORDER=groq,nvidia,gemini`, `OVERALL_DEADLINE_SECONDS=40.0`, `PROVIDER_TIMEOUT_SECONDS=25.0`, `MAX_PROVIDER_RETRIES=1`, `ENVIRONMENT=production`, plan=free, healthCheckPath=/health.

**Limitation:** I cannot confirm from outside Render that the running container corresponds to commit `240171e` (full hash) or that provider initialization completed without errors, because server logs and Render deploy history are not accessible.

---

## 3. Actual Production Quiz-Generation Results

I created an authorized test account (`learnai.verify.1793694229@example.com`, student `stu_611d8cd96c167b00f19494c2`) and called the real production `/api/quiz/generate` endpoint. Provider calls are made server-side using Render's own API keys (no credentials were exposed or handled by me).

### Test A — General topic, 10 questions (course `22MT103`, topic "Linear Algebra Concepts")

| Attempt | Outcome | Duration | Evidence |
|---|---|---|---|
| 1 | GatewayTimeout | 38.4s | Render gateway timeout (no error category header captured) |
| 2 | Connection/transport error | 13.0s | No HTTP response received (inconclusive; possible transient) |
| 3 | **GatewayTimeout — AITimeoutError** | 38.9s | HTTP 504, `x-error-category: TIMEOUT`, `x-render-origin-server: uvicorn`, body: `{"detail":"Quiz generation timed out across configured providers. Please try again."}` |

**Test A: FAILED.** Real AI generation did not produce a quiz. The 40-second application deadline was exhausted (observed ~38.9s) with all providers timed out.

### Test B — Narrow topic "Adjoint of a Matrix", 5 questions (course `22MT103`)

**NOT TESTED.** Deliberately skipped. The same Groq → NVIDIA → Gemini provider chain times out within ~40s regardless of topic (confirmed by Test A). Running Test B would consume additional free-tier quota and is expected to produce the same timeout result. Per the task constraints ("Avoid unnecessary repeated calls that could exhaust free-tier quotas"), no further generation calls were made.

### Test C — Second general-topic 10-question quiz

**NOT TESTED.** Same rationale as Test B.

---

## 4. Real Provider Usage & Timings

No real provider call succeeded, so no per-provider success latency can be reported. Observed aggregate behavior:

- **Total generation deadline:** 40.0s (`OVERALL_DEADLINE_SECONDS` in Render env)
- **Observed timeout duration:** ~38.9s (two clean observations)
- **Per-attempt caps (multi-provider mode):** Groq ≤12s, NVIDIA ≤16s, Gemini ≤ remaining budget (≥10s minimum safe budget)
- **Theoretical worst-case chain:** Groq 12s + NVIDIA 16s + Gemini ~12s ≈ 40s — matches the observed ~38.9s timeout.

This pattern (each provider consuming its full budget before the next is attempted) indicates that **no provider returned a successful response within its time budget**. It is not a fast auth failure (401 would return immediately) nor a fast rate limit (429 would return immediately). It is consistent with provider calls hanging or being slow from the Render free-tier environment.

**Timing allocation verified in code** (`backend/services/llm_service.py`, lines 316-352):
- Groq: `provider_timeout = min(12.0, max_alloc)` — 12s cap enforced
- NVIDIA: `provider_timeout = min(16.0, max_alloc)` — 16s cap enforced
- Gemini: minimum safe budget 10.0s enforced; skipped if remaining time < minimum
- Overall deadline preserved at 40.0s

---

## 5. Provider Fallback Behavior

**Configured order:** Groq → NVIDIA → Gemini (confirmed via `render.yaml` `PROVIDER_ORDER` and code default).

**Observed failover:** Cannot confirm a successful provider transition, because no provider succeeded. The chain appears to exhaust all three providers sequentially within the deadline, then raises `AITimeoutError`.

**Provider status (real production evidence):**

| Provider | Configured | Reachable | Authenticated | Generated Valid Quiz |
|---|---|---|---|---|
| Groq | YES (env key present per render.yaml) | Unknown | **NOT VERIFIED** | NO — timed out |
| NVIDIA | YES | Unknown | **NOT VERIFIED** | NO — never reached success |
| Gemini | YES | Unknown | **NOT VERIFIED** | NO — never reached success |

**Sequential failover verified: NO.** No successful generation was observed, so a clean Groq→NVIDIA→Gemini transition culminating in a valid quiz could not be demonstrated. (Mocked tests verify the *logic* of failover, but not real provider behavior.)

**Rate-limit (429), auth (401), model-not-found (404), and 5xx classification:** Code paths exist and are unit-tested with mocks, but no real HTTP provider response was observed to confirm classification in production.

---

## 6. Actual HTTP Statuses & Sanitized Errors

| Status | Category | Source | Observation |
|---|---|---|---|
| 200 | — | `/api/health`, `/api/courses` | Application and DB healthy; curriculum reachable |
| 504 / GatewayTimeout | `TIMEOUT` | `/api/quiz/generate` (Test A, attempts 1 & 3) | `{"detail":"Quiz generation timed out across configured providers. Please try again."}`, `x-error-category: TIMEOUT` |
| (none) | connection error | `/api/quiz/generate` (Test A, attempt 2) | No response — inconclusive |

No 401/403/404/429 provider errors were observed because no provider returned a response within budget.

No API keys, bearer tokens, or learner PII were exposed or logged.

---

## 7. Partial Quiz Behavior

**Requirement:** Accept ≥8 questions for a 10-question request; never present an 8-question quiz as a completed 10-question quiz.

### Code findings (`backend/services/llm_service.py`)

- `generate_quiz(... allow_partial: bool = False)` — default is **False** (line 160).
- The production endpoint (`/api/quiz/generate`, `main.py` lines 1495-1517) calls `generate_quiz` **without** `allow_partial=True`.
- Line 497-504: if a provider generates fewer than `num_questions` and `allow_partial=False`, it raises `ValueError` → the provider is treated as failed and the chain moves on (or retries).
- Line 523: `best_quiz` is only updated when `len(questions) == num_questions` (because `allow_partial=False`). Since the function returns immediately at line 757 when exactly `num_questions` are produced, `best_quiz` is effectively **dead code** in production — it is never populated with a partial (8-9) result.
- Line 838: `if best_quiz is not None and len(...) >= max(1, min(num_questions, 8))` — this >=8 threshold can never fire in production with a partial `best_quiz`.

**Conclusion:** The stated partial-quiz change (accept ≥8 of 10) is **not active in production**. If an AI provider returns 8 or 9 valid topic-isolated questions, production rejects them and fails over to the next provider; if all providers return <10, generation fails entirely. An 8-question quiz is never presented as a 10-question quiz (good — no fabrication), but neither is a legitimate 8-9 question quiz accepted.

### Frontend behavior (`frontend/js/app.js`)
The frontend **correctly uses the actual question count**:
- `answers = Array(data.questions.length).fill("")` (line 2073)
- `totalQuestions = currentQuiz.questions.length` (line 2154)
- `progressText: "Question ${currentIndex + 1} of ${totalQuestions}"` (line 2159)
- `progressPercent = ((currentIndex + 1) / totalQuestions) * 100` (line 2155)
- Progress bar `aria-valuenow` and width use actual count (lines 2169-2174)
- `submitQuiz()` sends the `answers` array (actual count) to `/api/quiz/submit`

So **if** the backend returned an accurate partial count, the frontend would display progress/submit/scoring using the actual count. This part is consistent.

**End-to-end partial quiz handling: NOT VERIFIED** — requires a successful backend partial-quiz response, which the current production code path does not produce.

---

## 8. Educational Quality Assessment

**NOT VERIFIED.** No real quiz was successfully generated, so no actual questions, options, explanations, or answer keys could be inspected. Topic relevance, mathematical correctness, structure, duplication, and LaTeX/KaTeX rendering could not be assessed against real generated output.

Note: The curriculum does contain matrix/adjoint content (course `22MT103` "Linear Algebra And Ordinary Differential Equations" includes quiz topic `adjoint of a matrix` (id `22MT103-Q006`) within the "Matrices" unit), so the narrow-topic target is a valid curriculum topic. But generation of questions on it could not be tested.

---

## 9. Narrow-Topic Relevance Assessment

**NOT VERIFIED (real generation).**

Code review of the novelty filter change (`backend/services/novelty.py`, `is_question_relevant_to_topic`, lines ~663-672):

The relaxation requires **all three** of the following before rejecting a question as "primarily tests competing topic":
1. `len(comp_overlap) >= 3`
2. `len(comp_overlap) >= 2 * len(distinctive)`
3. target topic absent from question text AND no target concept (len ≥5) in question text

If the target topic name appears in the question text, rejection additionally requires `comp_overlap >= 3 AND comp_overlap >= 2 * distinctive`.

This is a measured relaxation: a question must be strongly dominated by competing-topic keywords (3+ exclusive matches, at least 2x the target-distinctive matches, with no target signal) to be rejected. Isolated competing-topic mentions no longer cause false rejection. This directionally addresses narrow-topic false rejections, but **cannot be confirmed against real generated questions** because generation timed out.

---

## 10. Mathematical Correctness Assessment

**NOT VERIFIED.** No real questions generated. Cannot assess matrix dimensions, formulas, arithmetic, LaTeX notation, or KaTeX rendering against actual output.

---

## 11. Original TPM Hypothesis Verification

**Original TPM root cause: NOT CONFIRMED — insufficient historical provider evidence.**

- Historical provider logs (Render function logs) are not accessible from this environment.
- No provider response headers (e.g., `x-ratelimit-remaining`, TPM info) were observed because no provider returned a response within budget.
- The current failures are **timeouts** (providers not returning within budget), not clean 429 rate-limit responses. A 429 would return immediately and be classified; instead the calls hang until their per-attempt cap (12s/16s) then the overall deadline.
- Therefore the previous claim that "Groq free-tier TPM exhaustion caused the failures" is **not supported** by current observable evidence. The symptom has changed from (presumably) rate-limit-related to provider-timeout-related.
- Cannot confirm or deny whether the old model (`openai/gpt-oss-120b`, 1K TPM) was responsible, due to lack of historical evidence.

---

## 12. Timeout & Deadline Verification

**Code review** (`backend/services/llm_service.py`, providers):

| Setting | Value | Enforced? |
|---|---|---|
| Overall application deadline | 40.0s | YES (`OVERALL_DEADLINE_SECONDS`) |
| Groq per-attempt cap (multi-provider) | 12.0s | YES (`min(12.0, max_alloc)`) |
| NVIDIA per-attempt cap (multi-provider) | 16.0s | YES (`min(16.0, max_alloc)`) |
| Gemini minimum safe budget | 10.0s | YES (skipped if remaining < 10s) |
| Per-call client timeouts passed through | YES | Groq `timeout=time_left`; NVIDIA `timeout=time_left`; Gemini `call_timeout_ms` |

**Exception handling:**
- `ProviderConfigError` (auth/401/404) → fail fast, no retry (correct — permanent errors not masked as transient).
- `ProviderUnavailableError`/`ProviderError` (transient 429/503/timeout) → fall back to next provider when one exists; retry with backoff only when no fallback remains (correct anti-duplication logic).
- Broad `Exception` → logged, provider marked failed, next provider attempted (does not block the chain).
- Cancellation/timeout exceptions are caught by the specific provider-error branches and the generic branch.
- Sensitive details are redacted via `sanitize_error_text` before logging/returning.
- Timing out a synchronous caller does **not** hard-kill an underlying worker thread (the Groq/OpenAI/GenAI SDK HTTP request may continue server-side) — correctly, the code does not claim hard cancellation.

**Observed production behavior:** The deadline is preserved (timeout at ~38.9s < 40s), but all providers consume their budgets without returning, so the deadline expires and `AITimeoutError` is raised. The timeout/deadline machinery works as designed; the problem is that providers are not responding within budget from the Render environment.

---

## 13. Quiz Submission & MySQL Persistence

**NOT TESTED.** Submission requires a successfully generated quiz (valid `quiz_id`). Since generation timed out, no quiz session was created, no answers were submitted, and no result was persisted to MySQL. Could not verify:
- Score calculation
- Result persistence in production MySQL
- Dashboard retrieval
- User/session isolation

The `/api/quiz/submit` endpoint exists (`main.py` line 1900) and the frontend `submitQuiz()` (line 2325) sends `quiz_id`, `answers`, `student_id`, `auth_token` with a 45s client timeout. Score/display logic (`showResult`, line 2377) uses `data.score / data.total` where `total` is derived server-side. These code paths appear consistent but were not exercised end-to-end.

---

## 14. Automated Regression Test Results

**Command:** `python -m pytest .` (run from `backend/`), collection scope: all `test_*.py` files in `backend/`.

**Result:** `917 passed, 6 skipped, 0 failed` in 84.09s — **exactly reproduces the reported baseline** (917 passed / 6 skipped / 0 failed).

**Nature of tests:** All tests are **mocked/fault-injection** tests. Providers are replaced with `MagicMock` (e.g., `test_ai_provider_reliability_and_fallback.py`, `test_production_render_fixes.py`, `test_quiz_timeout_root_cause_and_provider_resilience.py`). They verify orchestration logic (fallback sequence, retry-with-backoff, auth fail-fast, 404 model failover, timeout classification, novelty filtering, math rendering) but **do not call real AI providers**. Skips (6) are due to missing credentials/external dependencies (e.g., TiDB/MySQL integration tests).

**Conclusion:** Automated tests pass, but they **cannot** validate real AI generation. The passing suite is consistent with a production environment where generation is broken — mocks always succeed.

---

## 15. Mocked vs Real Provider Verification

| Aspect | Mocked (tests) | Real (production) |
|---|---|---|
| Groq successful generation | PASSED (mock) | NOT VERIFIED — timed out |
| Groq failure → NVIDIA fallback | PASSED (mock) | NOT VERIFIED |
| NVIDIA → Gemini fallback | PASSED (mock) | NOT VERIFIED |
| All providers unavailable | PASSED (mock) → `AIServiceUnavailableError` | Observed → `AITimeoutError` (all timed out) |
| Rate limit / auth / 404 / timeout classification | PASSED (mock) | No real response observed |
| Novelty / topic isolation / math correctness | PASSED (mock) | No real questions to assess |
| Quiz submission / score persistence | Not a provider test | NOT TESTED |

Real provider verification was **not achieved** because every production generation call timed out before any provider returned.

---

## 16. Files Modified

**None.** No source code files were modified, added, or deleted. No commits or pushes were made (per the "no speculative changes / no deployment without authorization" rules). The only modified file in the working tree is an unrelated pre-existing report (`LEARNAI_FREE_TIER_PERFORMANCE_REPORT.md`), which was already modified before this session began and was left untouched.

---

## 17. Remaining Limitations

1. **Cannot confirm deployed commit hash** — no endpoint exposes the git SHA; Render deploy history inaccessible.
2. **Cannot access server/provider logs** — cannot determine which specific provider (Groq/NVIDIA/Gemini) is timing out, or why (slow API, network, rate limiting, invalid key).
3. **Cannot perform real provider calls locally** — API keys are not in the local environment (they exist only in Render dashboard); all real calls go through production.
4. **Partial-quiz threshold (>=8) not active in production** — `allow_partial=False` in the endpoint and `best_quiz` dead code mean an 8-9 question AI result is rejected rather than accepted as a partial quiz.
5. **Educational quality, narrow-topic relevance, mathematical correctness** — unverifiable without successful generation.
6. **Quiz submission / MySQL persistence** — unverifiable without a generated quiz.

---

## 18. Unverified Items

- Groq working: NOT VERIFIED (provider calls time out)
- NVIDIA working: NOT VERIFIED
- Gemini working: NOT VERIFIED
- Sequential failover (Groq→NVIDIA→Gemini) ending in valid quiz: NOT VERIFIED
- Real 10-question quiz generated: NO
- Second fresh 10-question quiz generated: NO
- Adjoint of a Matrix quiz generated: NOT TESTED
- Narrow-topic relevance verified: NO
- Mathematical correctness verified: NO
- Partial quiz end-to-end (8/9/10 questions): NOT VERIFIED (frontend correct; backend never returns partials)
- Quiz submission + MySQL persistence: NOT TESTED
- Original TPM root cause: NOT CONFIRMED

---

## Final Acceptance Criteria Summary

| Criterion | Status |
|---|---|
| Expected commit deployed | Cannot verify exact SHA (health OK, DB connected) |
| Application starts successfully | YES |
| MySQL connected | YES |
| Real fresh 10-question AI quiz succeeds in production | NO (times out at ~39s) |
| Second fresh 10-question quiz succeeds | NO (not retested; same chain) |
| 5-question Adjoint of a Matrix quiz succeeds | NOT TESTED |
| Actual provider usage evidenced | NO (all providers time out) |
| Narrow-topic questions relevant | NOT VERIFIED |
| Mathematical questions correct | NOT VERIFIED |
| Question structure/answer keys valid | NOT VERIFIED |
| No duplicates/fabricated questions | NOT VERIFIED |
| Actual question count communicated accurately | Frontend YES; backend never returns partials |
| Frontend progress/submission use actual count | YES (code-reviewed) |
| Scoring/dashboard use actual count | NOT TESTED |
| Provider fallback verified | NOT VERIFIED |
| Timeout allocations respected | YES (code-reviewed) |
| 40s deadline preserved | YES (observed ~38.9s timeout) |
| All-provider failure = controlled unavailable | YES (`AITimeoutError`) |
| Curated questions remain removed | YES (`_load_local_demo_questions` returns None; tests confirm removal) |
| Stored-question substitution removed | YES (`_load_stored_quiz_fallback` returns None) |
| No cached quiz substitution | YES |
| Fresh AI generation is normal behavior | INTENDED, but currently failing |

---

## Final Verdict

**<NOT FIXED>**

Real production AI quiz generation continues to fail. Every generation attempt times out across all three providers (Groq → NVIDIA → Gemini) within the 40-second deadline, returning `AITimeoutError` ("Quiz generation timed out across configured providers"). No real 10-question quiz, no second quiz, and no Adjoint of a Matrix quiz could be generated. Automated tests pass (917/6/0) but are mocked and do not exercise real providers.

### Recommended next steps (require authorization / access)

1. **Obtain Render server logs** for `/api/quiz/generate` to identify which provider is timing out and why (slow API response, network, rate limiting, or auth).
2. **Investigate Groq `llama-3.3-70b-versatile` behavior from Render free-tier** — the model switch in `240171e` did not resolve the production timeout.
3. **Wire up the partial-quiz path**: the production endpoint calls `generate_quiz` with `allow_partial=False`, making the >=8 best_quiz threshold dead code. Decide whether partial quizzes (8-9 of 10) should be accepted, and if so, pass `allow_partial=True` (or populate `best_quiz` for partial results) — this is a product/behavior decision, not a speculative refactor.
4. **Re-test real generation** after provider/infrastructure issues are resolved, then re-run this verification (Test A, B, C, submission, dashboard).

No further code changes, commits, pushes, or deployments were made or attempted in this session.
