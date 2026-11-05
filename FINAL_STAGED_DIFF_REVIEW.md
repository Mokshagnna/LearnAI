# LearnAI — Final Staged Diff Review

**Review Date:** November 3, 2026  
**Auditor:** Independent Senior Backend Architect and Production Reliability Auditor  
**Repository:** `C:\Users\moksh\OneDrive\Desktop\IDP`  
**Git Branch:** `main`  
**Staged Files Count:** 12 files (+3,443 insertions, -516 deletions)  
**Whitespace Check (`git diff --cached --check`):** Clean (Exit code 0)  
**Test Suite Status:** 113 / 113 Tests Pass (100%) in 5.81s  
**Recommendation:** **APPROVE FOR COMMIT**

---

## 1. Executive Summary

A comprehensive, line-by-line review of all 12 files currently staged in the Git index was conducted. Particular attention was paid to the 946-line diff in `backend/services/llm_service.py` to ensure that no existing functionality was inadvertently removed, that deadline enforcement and selective question replacement operate correctly, and that all provider failovers and quota interactions are deterministic.

**Key Findings:**
1. **Zero Accidental Deletions:** All curriculum grounding, notes integration, syllabus topic isolation, adaptive engine scoring, academic theorem verification, and response schema contracts are fully preserved.
2. **Clean Architectural Refactoring:** The large change in `backend/services/llm_service.py` replaces a tangled, duplicate-repair loop with a clean two-phase architecture: Phase 1 (initial generation) and Phase 2 (targeted selective replacement), unified under a shared `RequestDeadline`.
3. **Zero Secrets or Debug Artifacts:** Automated scanning of all 3,443 added lines confirmed zero hardcoded API keys, zero `print()` statements, zero `breakpoint()`/`pdb` calls, and zero extraneous dependencies.
4. **Complete Test Integrity:** All 73 newly added tests in Group B, plus the 40 pre-existing tests (113 total), pass cleanly with zero warnings.
5. **Strict Scope Isolation:** Unstaged frontend styling adjustments and local benchmark scratch artifacts remain completely untouched in the working directory.

---

## 2. Deep Dive: `backend/services/llm_service.py` (946-Line Change)

### 2.1 Why the Diff is 946 Lines
The diff in `llm_service.py` comprises 946 lines (+477 insertions, -469 deletions) across 9 discrete hunks. This diff is not a rewrite of business logic, but a targeted structural refactoring that eliminated duplicate code paths:
- **Prior Code:** Contained two separate, redundant repair loops (`replenishment` loop and `novelty_repair` loop), each independently re-prompting providers, re-parsing JSON, and re-validating questions with divergent error handling.
- **Refactored Code:** Consolidated all candidate question validation into a single authoritative function (`classify_and_validate_candidate`) and structured quiz generation into two clean phases governed by a unified deadline.

### 2.2 Structural Verification of Hunks

#### Hunk 1 (Lines 3–7) & Hunk 2 (Lines 64–270): Architecture Foundations
- **Deadline Configuration:** Sets `OVERALL_DEADLINE_SECONDS = 40.0`, `DEADLINE_TARGET_SECONDS = 15.0`, and per-provider minimum time budgets (`PROVIDER_MIN_TIME_REQUIREMENTS`, `REPLACEMENT_MIN_TIME_REQUIREMENTS`).
- **`RequestDeadline` Class:** Tracks `elapsed`, `remaining`, `is_expired()`, `can_attempt(min_time)`, and `allocate_timeout(desired, min_required)`. Starts at the very beginning of the API request and propagates across all downstream calls.
- **`classify_and_validate_candidate()`:** Unified validation pipeline executing in strict priority order:
  1. `validate_single_question(return_details=True)` — validates schema, options (A–D), and academic correctness.
  2. `is_question_relevant_to_topic()` — verifies curriculum topic isolation.
  3. `normalize_question_concept()` — normalizes concept tag against syllabus.
  4. Internal uniqueness — verifies candidate against already accepted questions in `accepted_questions` via `is_question_near_duplicate`.
  5. Historical novelty — verifies candidate against student attempt history (`history_questions` and `all_fingerprints`).
- **`build_selective_replacement_prompt()`:** Prompts provider for *only* the missing question count (`needed`), explicitly injecting accepted questions under `[ALREADY ACCEPTED]` and prior attempt questions under `[PRIOR ATTEMPT]` as negative constraints.

#### Hunks 3–6 (Lines 368–602): Orchestration Setup & Budgeting
- Preserves database history retrieval (`get_topic_history`, `get_question_history_full`, `get_question_fingerprints`, `get_question_concepts`).
- Preserves adaptive engine decision calculation (`compute_adaptive_decision`, `get_recommended_difficulty`, `get_difficulty_distribution`).
- Instantiates `deadline = RequestDeadline(overall_deadline=effective_deadline, start_time=start_time)`.
- Calculates per-provider timeout allocation, skipping any provider if `deadline.remaining <= min_required_time`.

#### Hunk 7 (Lines 603–867): Two-Phase Generation & Selective Replacement Loop
- **Phase 1 (Initial Generation):**
  - Generates initial batch of questions.
  - Passes all candidates through `classify_and_validate_candidate`.
  - Valid candidates are added to `preserved_questions`.
  - Rejected candidates are recorded in `rejected_records` with their specific rejection category (`SCHEMA`, `OPTIONS`, `ACADEMIC`, `TOPIC_ISOLATION`, `DUPLICATE_INTERNAL`, `DUPLICATE_HISTORY`).
- **Phase 2 (Selective Replacement):**
  - If `len(preserved_questions) < num_questions`:
    - Computes `needed = num_questions - len(preserved_questions)`.
    - Checks `deadline.can_attempt(replacement_min_time)`. If remaining time is insufficient, breaks cleanly rather than risking a timeout.
    - Calls provider with `custom_prompt = build_selective_replacement_prompt(...)`.
    - Validates returned replacement candidates; appends valid ones to `preserved_questions`.
  - **Question Count & Difficulty Enforcement:**
    - If `len(preserved_questions) >= num_questions`, selects the required question count, verifies difficulty distribution, and constructs the final quiz payload.
- **Failover Handling:**
  - If a provider encounters a retryable or server error (`ProviderUnavailableError`, `ProviderError`), elapsed time is deducted from the shared deadline, and the pipeline seamlessly fails over to the next configured provider.
  - If questions were already preserved by Provider 1, Provider 2 can be prompted for only the remaining questions needed, preserving valid work across providers.

#### Hunks 8–9 (Lines 868–970): Error Classification & Fallback Return
- When all providers are exhausted or deadline expires:
  - If at least 80% of questions were preserved (`best_quiz`), returns the best available quiz with `novelty_warning = True` rather than failing the student's request.
  - Otherwise, classifies the error into distinct exception types:
    - `AITimeoutError` (category `TIMEOUT`) $\to$ maps to HTTP 504.
    - `AIRateLimitError` (category `RATE_LIMIT_429`) $\to$ maps to HTTP 429.
    - `AIServiceUnavailableError` (category `SERVICE_UNAVAILABLE_503`) $\to$ maps to HTTP 503.
    - `AIConfigError` $\to$ maps to HTTP 500.

### 2.3 Verification Against Accidental Functional Deletions

| Feature / Subsystem | Verification Status | Source Reference |
| :--- | :---: | :--- |
| **Curriculum & Syllabus Context** | **PRESERVED** | `llm_service.py` L586–598 (passed to `get_quiz_prompt`) |
| **Notes Grounding & Untrusted Sanitization** | **PRESERVED** | `llm_service.py` L589, L1475 in `main.py` |
| **Competing / Disallowed Topics Filter** | **PRESERVED** | `llm_service.py` L592, L218 |
| **Adaptive Engine & Weak Concept History** | **PRESERVED** | `llm_service.py` L421–466 |
| **Academic & Mathematical Verifiers** | **PRESERVED** | `base.py` L568 (`verify_academic_correctness`) |
| **C/Python Code Snippet Evaluators** | **PRESERVED** | `base.py` L452–520 |
| **Timing Telemetry (`timing_metrics`)** | **PRESERVED** | `llm_service.py` L807 |
| **Persistence Integration (`main.py`)** | **PRESERVED** | Quiz session persistence in `main.py` L1710–1740 |

---

## 3. Review of Other Staged Implementation Files

### 3.1 `backend/services/quota_manager.py` (New, 380 lines)
- **Architecture:** Implements `QuotaManager` singleton managing per-provider, per-model, and per-API-key-hash rate limit states.
- **Classification:** Distinguishes `TPM` (transient tokens per minute), `RPM` (transient requests per minute), and `TPD` (daily quota exhaustion).
- **Security:** Scopes keys via `hashlib.sha256(key.encode()).hexdigest()[:12]`. Raw keys are never written to disk or logged.
- **Persistence Safety:** Uses atomic file replacement (`os.replace` from a temporary file) to eliminate partial-write corruption on system crashes.
- **Invariant:** `record_success()` clears transient `TPM` cooldowns, but **never clears a genuine daily lockout (`TPD`)**.

### 3.2 `backend/services/novelty.py` (Modified, +243 lines)
- **Cognitive Operations:** Adds `CognitiveOperation` enum with 7 distinct reasoning skills (`CALCULATION`, `DEFINITION_RECALL`, `THEORETICAL_PROPERTY`, `COMPARISON_CONTRAST`, `APPLICATION_SCENARIO`, `PROCESS_MECHANISM`, `GENERAL`).
- **Numerical Problem Templates:** Implements `get_numerical_template()` (replacing numbers with `<num>`) to distinguish identical problem structures from genuinely different mathematical problems.
- **Root Cause Fix:** Solves false-positive duplicate rejections during retakes. Questions sharing a syllabus concept tag are no longer rejected as duplicates unless they exhibit demonstrated semantic similarity ($\ge 0.48$) against an attempted question.

### 3.3 `backend/services/providers/base.py` (Modified, +62 lines)
- **Token Budgeting:** Implements `calculate_max_output_tokens()` dynamically setting output completion budgets based on question count and model completion ceilings.
- **Diagnostic Return:** Enhances `validate_single_question(return_details=True)` to return `(calibrated, reason, category)` tuples, feeding granular failure categories into the selective replacement engine.
- **Rejection Tracking:** Enhances `validate_quiz_data()` to return `rejection_reasons` for targeted reporting.

### 3.4 `backend/services/providers/groq_provider.py` & `nvidia_provider.py` (Modified)
- Integrates `quota_manager.is_model_available()` check before initiating network calls, executing fast-skips on cooling models.
- Integrates `calculate_max_output_tokens()` to prevent truncation.
- Detects `finish_reason == 'length'` and emits actionable warning logs.
- Records `quota_manager.record_rate_limit()` on HTTP 429 and `quota_manager.record_success()` on HTTP 200.

### 3.5 `backend/conftest.py` (Modified, +8 lines)
- Adds `quota_manager.reset_all()` to the pytest autouse fixture, guaranteeing complete quota isolation between test executions.

---

## 4. Review of Staged Test Files (Group B)

The 5 staged test files add 73 deterministic tests:

1. **`test_quota_manager.py` (19 tests):** Header parsing (`retry-after`, `x-ratelimit-reset-tokens`), reset hint text parsing, TPM vs. TPD classification, dynamic token budgeting, and fast-skip verification.
2. **`test_quota_manager_edge_cases.py` (7 tests):** Missing JSON initialization, corrupt JSON recovery, interrupted persistence tolerance, concurrent thread writes, key hash scoping, and TPD lockout preservation.
3. **`test_novelty_architecture_remediation.py` (11 tests):** Cognitive operation classification, numerical template matching, same-topic different-concept acceptance, superficial wording duplicate detection, and multiple-retake stability.
4. **`test_selective_replacement_and_deadline.py` (12 tests):** Partial failure replacement (only invalid questions replaced), valid question preservation, duplicate rejection during replenishment, deadline exhaustion, provider fallback, and transactional persistence.
5. **`test_module4_cross_integration.py` (24 tests):** Cross-module resilience: quota cooldown + failover, novelty + replacement, academic validation + fallback, deadline budget enforcement, and SQLite persistence integrity.

**Cleanliness:** Zero `print()` statements, zero debugging breakpoints, zero flaky time-dependent sleeps.

---

## 5. Findings by Severity

| Severity | Count | Summary of Findings | Release Blocker? |
| :--- | :---: | :--- | :---: |
| **CRITICAL** | **0** | No syntax errors, broken imports, missing dependencies, or data loss risks. | **No** |
| **HIGH** | **0** | No security vulnerabilities, credential leaks, or API contract breaks. | **No** |
| **MEDIUM** | **0** | No edge cases unaccounted for; all fallback and quota recovery paths verified. | **No** |
| **LOW** | **2** | 1. Single-worker deployment assumption (documented and supported on Render).<br>2. Cloudflare inference latency (documented; strictly placed as tertiary fallback). | **No** |
| **INFORMATIONAL** | **1** | Git line-ending notification on Windows (`LF will be replaced by CRLF`). Normal behavior. | **No** |

---

## 6. Release Blockers

- **Release Blockers Identified:** **None (0)**.
- All architectural objectives across Modules 1 through 4 have been implemented cleanly, verified through 113 automated tests, confirmed through end-to-end browser journey testing (Module 5), and benchmarked across 75 requests (Module 6).

---

## 7. Recommendation

### **RECOMMENDATION: APPROVE FOR COMMIT**

The 12 staged files constitute a self-contained, fully verified, backward-compatible backend release. The staging area is clean and ready to be committed.

#### Suggested Phased Commit Structure:
- **Commit 1 (Implementation):**  
  `feat(backend): implement quota manager, novelty remediation, and selective replacement with unified deadline`  
  *(Stages Group A files)*
- **Commit 2 (Tests):**  
  `test(backend): add comprehensive unit, edge-case, and integration tests for Modules 1-4`  
  *(Stages Group B files)*
- *Or a single combined commit for the 12 files:*  
  `feat(backend): complete Modules 1-4 remediation with quota management, novelty evaluation, selective replacement, and cross-module tests`

---

*Git state remains unchanged. Awaiting explicit user instruction before committing.*
