import os
import time
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from dotenv import load_dotenv

from services.providers.base import (
    BaseAIProvider,
    ProviderUnavailableError,
    ProviderConfigError,
    ProviderError,
    extract_json_from_text,
    validate_quiz_data,
    get_quiz_prompt
)
from services.providers.cloudflare_provider import CloudflareProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.groq_provider import GroqProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.database import (
    get_or_create_student,
    get_topic_history,
    get_concept_performance,
    get_question_fingerprints,
    get_question_concepts,
    get_question_history_full
)
from services.adaptive import (
    compute_adaptive_decision,
    get_recommended_difficulty,
    get_difficulty_distribution,
    get_adaptive_prompt_additions,
    get_adaptive_explanation
)
from services.novelty import (
    extract_fingerprints_from_history,
    extract_concepts_from_history,
    evaluate_quiz_novelty,
    build_exclusion_prompt_block,
    is_question_near_duplicate,
    normalise_text
)
from services.logger_utils import sanitize_error_text
from services.metrics import metrics
from services.quota_manager import quota_manager

from services.env_loader import resolve_and_load_env

# Ensure backend/.env (or LEARNAI_ENV_FILE override) is reliably loaded
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = resolve_and_load_env(_BACKEND_DIR)

logger = logging.getLogger("learnai.orchestrator")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )


MAX_NOVELTY_REPAIR_ATTEMPTS = int(os.getenv("MAX_NOVELTY_REPAIR_ATTEMPTS", "2"))

# ── Shared End-to-End Deadline & Budget Configuration (Module 3) ──────────────
OVERALL_DEADLINE_SECONDS = float(os.getenv("OVERALL_DEADLINE_SECONDS", "40.0"))
DEADLINE_TARGET_SECONDS = float(os.getenv("DEADLINE_TARGET_SECONDS", "15.0"))
PROVIDER_TIMEOUT_SECONDS = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "25.0"))
GROQ_TIMEOUT_SECONDS = float(os.getenv("GROQ_TIMEOUT_SECONDS", "11.0"))
CLOUDFLARE_TIMEOUT_SECONDS = float(os.getenv("CLOUDFLARE_TIMEOUT_SECONDS", "20.0"))
NVIDIA_TIMEOUT_SECONDS = float(os.getenv("NVIDIA_TIMEOUT_SECONDS", "16.0"))

PROVIDER_MIN_TIME_REQUIREMENTS: Dict[str, float] = {
    "gemini": float(os.getenv("GEMINI_MIN_TIMEOUT", "10.0")),
    "nvidia": float(os.getenv("NVIDIA_MIN_TIMEOUT", "4.0")),
    "groq": float(os.getenv("GROQ_MIN_TIMEOUT", "2.5")),
    "cloudflare": float(os.getenv("CLOUDFLARE_MIN_TIMEOUT", "3.0")),
}
DEFAULT_MIN_TIMEOUT = float(os.getenv("DEFAULT_MIN_TIMEOUT", "2.5"))

REPLACEMENT_MIN_TIME_REQUIREMENTS: Dict[str, float] = {
    "gemini": float(os.getenv("GEMINI_MIN_REPLACEMENT_TIMEOUT", "6.0")),
    "nvidia": float(os.getenv("NVIDIA_MIN_REPLACEMENT_TIMEOUT", "2.5")),
    "groq": float(os.getenv("GROQ_MIN_REPLACEMENT_TIMEOUT", "2.0")),
    "cloudflare": float(os.getenv("CLOUDFLARE_MIN_REPLACEMENT_TIMEOUT", "2.5")),
}
DEFAULT_MIN_REPLACEMENT_TIMEOUT = float(os.getenv("DEFAULT_MIN_REPLACEMENT_TIMEOUT", "2.0"))

MAX_REPLACEMENT_ROUNDS = int(os.getenv("MAX_REPLACEMENT_ROUNDS", "2"))


class RequestDeadline:
    """Manages a single shared end-to-end deadline across all generation, validation, fallback, and replacement stages."""

    def __init__(self, overall_deadline: Optional[float] = None, start_time: Optional[float] = None):
        if overall_deadline is not None:
            self.overall_deadline = float(overall_deadline)
        else:
            self.overall_deadline = float(os.getenv("OVERALL_DEADLINE_SECONDS", "40.0"))
        self.start_time = start_time if start_time is not None else time.perf_counter()

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.start_time

    @property
    def remaining(self) -> float:
        return max(0.0, self.overall_deadline - self.elapsed)

    def is_expired(self) -> bool:
        return self.remaining <= 0.0

    def can_attempt(self, min_time: float) -> bool:
        return self.remaining > min_time

    def allocate_timeout(self, desired_timeout: float, min_required: float = 1.0) -> float:
        rem = self.remaining
        if rem <= min_required:
            return 0.0
        return min(desired_timeout, rem)


def extract_candidate_questions(data: Any) -> List[Dict[str, Any]]:
    """Extracts raw question objects from JSON structures (dict or list)."""
    if isinstance(data, list):
        return [q for q in data if isinstance(q, dict)]
    if isinstance(data, dict):
        for k in ["questions", "quiz", "quiz_questions", "items"]:
            v = data.get(k)
            if isinstance(v, list):
                return [q for q in v if isinstance(q, dict)]
            if isinstance(v, dict) and isinstance(v.get("questions"), list):
                return [q for q in v["questions"] if isinstance(q, dict)]
    return []


def classify_and_validate_candidate(
    cand: Any,
    topic_name: str,
    course_name: str,
    course_code: str,
    concepts: Optional[List[str]],
    syllabus_context: Optional[str],
    competing_topics: Optional[List[Any]],
    accepted_questions: List[Dict[str, Any]],
    history_questions: List[Dict[str, Any]],
    all_fingerprints: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Validates a single candidate question through all verification layers:
    1. Schema & options calibration
    2. Topic isolation & relevance
    3. Academic & mathematical correctness
    4. Internal uniqueness against already-accepted questions in this quiz
    5. Historical novelty against student attempt history

    Returns:
        (valid_question_dict, None, None) on success
        (None, reason, category) on failure
    """
    from services.providers.base import validate_single_question
    from services.novelty import is_question_relevant_to_topic, normalize_question_concept

    val_q, fail_reason, fail_cat = validate_single_question(
        cand,
        topic_name=topic_name,
        return_details=True
    )
    if not val_q:
        return None, fail_reason, fail_cat

    is_rel, rel_reason = is_question_relevant_to_topic(
        val_q,
        topic_name,
        concepts=concepts,
        syllabus_context=syllabus_context,
        course_code=course_code,
        competing_topics=competing_topics
    )
    if not is_rel:
        return None, rel_reason, "TOPIC_ISOLATION"

    val_q["concept"] = normalize_question_concept(val_q, topic_name, concepts)

    for ex_q in accepted_questions:
        is_dup, dup_reason = is_question_near_duplicate(ex_q, val_q, topic_name=topic_name)
        if is_dup:
            return None, f"Duplicates accepted question in quiz: {dup_reason}", "DUPLICATE_INTERNAL"

    if history_questions:
        for hist_q in history_questions:
            is_dup, dup_reason = is_question_near_duplicate(hist_q, val_q, topic_name=topic_name)
            if is_dup:
                return None, f"Duplicates student history question: {dup_reason}", "DUPLICATE_HISTORY"

    if all_fingerprints:
        q_norm = normalise_text(val_q.get("question", ""))
        for fp in all_fingerprints:
            if normalise_text(fp) == q_norm:
                return None, f"Exact hash match with student history question: '{fp[:50]}...'", "DUPLICATE_HISTORY"

    return val_q, None, None


def build_selective_replacement_prompt(
    course_code: str,
    course_name: str,
    topic_name: str,
    needed: int,
    preserved_questions: List[Dict[str, Any]],
    rejected_records: List[Dict[str, Any]],
    history_questions: List[Dict[str, Any]],
    missing_difficulties: List[str],
    concepts: Optional[List[str]] = None,
    disallowed_topics: Optional[List[str]] = None,
    syllabus_context: Optional[str] = None,
) -> str:
    concept_str = ", ".join(concepts) if concepts else topic_name
    disallowed_str = f"\nNEGATIVE CONSTRAINT: Absolutely do NOT generate questions on competing topics ({', '.join(disallowed_topics)})." if disallowed_topics else ""
    diff_req_str = ", ".join(missing_difficulties) if missing_difficulties else "Medium"
    ask_count = max(needed + 1, 2)

    accepted_texts = [q.get("question", "") for q in preserved_questions if q.get("question")]
    accepted_block = "\n".join(f"  - [ALREADY ACCEPTED]: {t}" for t in accepted_texts[:10]) if accepted_texts else "None."

    rejected_texts = [r.get("reason", "") for r in rejected_records[-5:] if r.get("reason")]
    rejected_block = "\n".join(f"  - [REJECTED REASON]: {r}" for r in rejected_texts) if rejected_texts else "None."

    history_texts = [q.get("question", "") for q in history_questions[:8] if q.get("question")]
    history_block = "\n".join(f"  - [PRIOR ATTEMPT]: {t}" for t in history_texts) if history_texts else "None."

    has_academic_rejections = any(
        (r.get("category") == "ACADEMIC") or
        ("arithmetic" in (r.get("reason") or "").lower()) or
        ("hallucination" in (r.get("reason") or "").lower())
        for r in rejected_records
    )

    academic_remedy_block = ""
    if has_academic_rejections:
        academic_remedy_block = """
CRITICAL ACADEMIC & MATHEMATICAL RIGOR CORRECTION:
- Previous candidate questions were rejected by academic verification due to arithmetic errors, false equality statements, or contradictory explanations.
- Every arithmetic calculation, formula evaluation, and equality chain (e.g. A = B = C) in both questions and explanations MUST be 100% mathematically correct.
- Self-check: Never assert false equalities (such as claiming 2 = 0.25^2 or 15 - 4 = 7). Verify that every expression evaluates to the identical mathematical result.
- Exactly ONE option must be conceptually and mathematically defensible; all distractors must be strictly incorrect.
"""

    return f"""You are an expert academic AI quiz generator for {course_code} - {course_name}.
Selected Topic: {topic_name}
Authorized Syllabus Concepts: {concept_str}

STRICT TOPIC MANDATE: Every question must test ONLY '{topic_name}'.
{disallowed_str}

We have already accepted {len(preserved_questions)} valid questions for this quiz.
We need at least {needed} additional replacement multiple-choice questions for '{topic_name}' testing different angles/learning objectives to complete the requested set.
Required difficulty distribution for replacements: {diff_req_str}.

CRITICAL: Do NOT duplicate or rephrase any of these already accepted questions:
{accepted_block}

CRITICAL: Do NOT repeat the errors or topics of these rejected questions:
{rejected_block}
{academic_remedy_block}
CRITICAL: Do NOT duplicate questions from the student's prior attempts:
{history_block}

ACADEMIC & MATHEMATICAL RIGOR (MANDATORY):
- EXACTLY ONE DEFENSIBLE ANSWER: Exactly one option must satisfy the problem statement.
- CALCULATION & ARITHMETIC RIGOR: Every arithmetic step in questions and explanations must be 100% mathematically correct. Verify all basic calculations.
- THEOREMS & DEFINITIONS: Adhere strictly to proven academic theorems and verified syllabus definitions.

REQUIREMENTS:
- Return {ask_count} candidate questions strictly assessing '{topic_name}'.
- Each question must have 4 distinct options (A, B, C, D) with non-empty text.
- Exactly ONE option must be mathematically and conceptually correct.
- Valid correct_answer letter (A, B, C, or D).
- Specify the tested concept in the 'concept' field.
- Concise step-by-step 'explanation' for why the correct answer is correct.
- Return ONLY valid JSON:
{{
  "questions": [
    {{
      "question": "Question text directly testing {topic_name}",
      "options": {{"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"}},
      "correct_answer": "A",
      "difficulty": "{missing_difficulties[0] if missing_difficulties else 'Medium'}",
      "concept": "{concepts[0] if concepts else topic_name}",
      "explanation": "Concise step-by-step reason why A is correct."
    }}
  ]
}}"""


class AIError(Exception):
    """Base exception for AI operations."""
    def __init__(self, message: str, category: str = "AI_ERROR", status_code: int = 500, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.category = category
        self.status_code = status_code
        self.retryable = retryable


class AIServiceUnavailableError(AIError):
    """Raised when all configured AI providers are temporarily unavailable or overloaded."""
    def __init__(self, message: str = "Quiz generation is temporarily unavailable. Please try again shortly.", category: str = "SERVICE_UNAVAILABLE", status_code: int = 503, retryable: bool = True):
        super().__init__(message, category=category, status_code=status_code, retryable=retryable)


class AIConfigError(AIError):
    """Raised when there is an authentication, API key, model ID, or permanent configuration error."""
    def __init__(self, message: str, category: str = "CONFIG_ERROR", status_code: int = 500):
        super().__init__(message, category=category, status_code=status_code, retryable=False)


class AIRateLimitError(AIServiceUnavailableError):
    """Raised when AI providers exceed rate limits or quota exhausted."""
    def __init__(self, message: str = "AI service rate limit or quota exceeded across configured providers. Please wait a moment and retry.", category: str = "RATE_LIMIT_429"):
        super().__init__(message, category=category, status_code=429, retryable=True)


class AITimeoutError(AIServiceUnavailableError):
    """Raised when AI providers exceed deadline or timeouts."""
    def __init__(self, message: str = "Quiz generation timed out across configured providers. Please try again.", category: str = "TIMEOUT"):
        super().__init__(message, category=category, status_code=504, retryable=True)


_CACHED_PROVIDERS: Dict[str, BaseAIProvider] = {}


def _get_providers() -> List[BaseAIProvider]:
    """
    Returns providers in prioritized order based on PROVIDER_ORDER env var.
    Provider instances are cached to reuse HTTP sessions and avoid per-request client re-allocation.
    Default order: groq → nvidia → gemini
    Configurable via PROVIDER_ORDER=groq,nvidia,gemini (comma-separated)
    """
    global _CACHED_PROVIDERS
    if not _CACHED_PROVIDERS:
        _CACHED_PROVIDERS = {
            "gemini": GeminiProvider(),
            "groq": GroqProvider(),
            "nvidia": NvidiaProvider(),
            "cloudflare": CloudflareProvider(),
        }
    provider_map = _CACHED_PROVIDERS

    order_str = os.getenv("PROVIDER_ORDER", "groq,nvidia,gemini,cloudflare")
    order = [p.strip().lower() for p in order_str.split(",") if p.strip()]

    ordered = []
    seen = set()
    for name in order:
        if name in provider_map and name not in seen:
            provider = provider_map[name]
            if provider.is_configured():
                ordered.append(provider)
                seen.add(name)
                logger.info(
                    f"[PROVIDER SELECTED] provider={name} position={len(ordered)} "
                    f"configured=True"
                )
            else:
                logger.info(
                    f"[PROVIDER SKIPPED] provider={name} reason=missing_credentials "
                    f"configured=False"
                )
        elif name not in provider_map:
            logger.warning(
                f"[PROVIDER UNKNOWN] provider={name} not in registered providers "
                f"({', '.join(provider_map.keys())})"
            )

    # Append any not listed in PROVIDER_ORDER
    for name, provider in provider_map.items():
        if name not in seen:
            if provider.is_configured():
                ordered.append(provider)
                seen.add(name)
                logger.info(
                    f"[PROVIDER SELECTED] provider={name} position={len(ordered)} "
                    f"configured=True (appended from default map)"
                )
            else:
                logger.info(
                    f"[PROVIDER SKIPPED] provider={name} reason=missing_credentials "
                    f"configured=False (not in PROVIDER_ORDER)"
                )

    logger.info(
        f"[PROVIDER CHAIN] effective_order={[p.name for p in ordered]} "
        f"total_configured={len(ordered)}"
    )
    return ordered






def _load_stored_quiz_fallback(*args, **kwargs):
    """Deprecated: Stored quiz fallback has been permanently removed."""
    return None


def _load_local_demo_questions(*args, **kwargs):
    """Deprecated: Curated demo question fallback has been permanently removed."""
    return None

def generate_quiz(
    course_code: str,
    course_name: str,
    topic_name: str,
    syllabus_context: str,
    student_id: Optional[str] = None,
    concepts: Optional[List[str]] = None,
    difficulty: Optional[str] = None,
    competing_topics: Optional[List[Any]] = None,
    disallowed_topics: Optional[List[str]] = None,
    num_questions: int = 10,
    allow_partial: bool = False,
    deadline_seconds: Optional[float] = None
) -> Dict[str, Any]:
    """
    Multi-provider quiz generation with:
    - Configurable provider priority order (PROVIDER_ORDER env var)
    - Per-provider timeout budgets
    - Bounded overall deadline
    - Comprehensive Question & Concept Novelty checking (detects text and concept duplicates)
    - Adaptive difficulty based on student history (or optional manual override)
    - Anti-repetition prompt enrichment with previously tested concepts
    - Automatic fallback to local demo questions
    - Strict topic isolation preventing off-topic questions from neighboring or co-occurring syllabus topics
    """
    attempt_id = str(uuid.uuid4())
    num_questions = int(num_questions) if (num_questions and int(num_questions) > 0) else 10

    # Ensure competing and disallowed topics are populated for strict topic isolation
    if competing_topics is None:
        try:
            from services.curriculum_service import curriculum_service
            crs = curriculum_service.get_course(course_code)
            if crs:
                cur_parent = ""
                for t in crs.get("topics", []):
                    if t.get("name") and t["name"].strip().lower() == topic_name.strip().lower():
                        cur_parent = (t.get("parent_topic") or t.get("parent_section") or "").strip().lower()
                        break
                competing_topics = [
                    t["name"] for t in crs.get("topics", [])
                    if t.get("name") and t["name"].strip().lower() != topic_name.strip().lower()
                    and not (cur_parent and (t.get("parent_topic") or t.get("parent_section") or "").strip().lower() == cur_parent)
                ]
        except Exception:
            competing_topics = []

    if disallowed_topics is None and competing_topics:
        disallowed_topics = [
            (ct if isinstance(ct, str) else ct.get("name", ""))
            for ct in competing_topics
            if (ct if isinstance(ct, str) else ct.get("name"))
        ][:6]

    # ── Student context & historical queries (single connection reuse) ────────
    from services.database import get_connection

    t_db_start = time.monotonic()
    conn = None
    try:
        conn = get_connection()
        student_id = get_or_create_student(student_id, conn=conn)
        topic_history = get_topic_history(student_id, course_code, topic_name, conn=conn)
        attempt_number = len(topic_history) + 1

        # Deduplicated weak concepts from history
        seen_concepts: Dict[str, None] = {}
        for attempt in topic_history:
            for c in attempt.get("weak_concepts", []):
                seen_concepts[c] = None
        weak_concepts = list(seen_concepts.keys())

        # Adaptive difficulty calculation
        from services.adaptive import normalize_level
        adaptive_decision = compute_adaptive_decision(
            student_id=student_id,
            course_code=course_code,
            topic_name=topic_name,
            conn=conn
        )

        if difficulty and difficulty.strip():
            recommended_difficulty = normalize_level(difficulty.strip())
            adaptive_applied = False
            override_tag = f"[MANUAL OVERRIDE: {recommended_difficulty}]"
        else:
            recommended_difficulty = adaptive_decision["recommended_difficulty"]
            adaptive_applied = True
            override_tag = f"[ADAPTIVE: {recommended_difficulty}]"

        difficulty_dist = get_difficulty_distribution(recommended_difficulty, total_questions=num_questions)

        # Combine historical weak concepts from attempts and adaptive decision
        combined_weak = list(dict.fromkeys(weak_concepts + adaptive_decision.get("weak_concepts", [])))

        logger.info(
            f"{override_tag} student={student_id} course={course_code} topic='{topic_name}' "
            f"attempt=#{attempt_number} requested={num_questions} recommended={recommended_difficulty} dist={difficulty_dist} "
            f"active={adaptive_decision['is_decision_active']} reason='{adaptive_decision['reason']}'"
        )

        adaptive_context = get_adaptive_prompt_additions(
            student_id, course_code, topic_name, recommended_difficulty, combined_weak
        )

        # Get adaptive explanation for UI
        adaptive_explanation = get_adaptive_explanation(adaptive_decision)

        # ── Novelty & Concept History ─────────────────────────────────────────────
        history_fingerprints_from_attempts = extract_fingerprints_from_history(topic_history)
        history_concepts_from_attempts = extract_concepts_from_history(topic_history)

        history_fingerprints_from_metadata = get_question_fingerprints(student_id, course_code, topic_name, conn=conn)
        history_concepts_from_metadata = get_question_concepts(student_id, course_code, topic_name, conn=conn)

        all_fingerprints = history_fingerprints_from_attempts + [
            fp for fp in history_fingerprints_from_metadata if fp not in history_fingerprints_from_attempts
        ]
        all_concepts = history_concepts_from_attempts + [
            c for c in history_concepts_from_metadata if c not in history_concepts_from_attempts
        ]
        history_questions_full = get_question_history_full(student_id, course_code, topic_name, conn=conn)

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    t_db_context = time.monotonic() - t_db_start

    t_llm_primary_total = 0.0
    t_json_val_total = 0.0
    t_replenishment_total = 0.0
    t_novelty_repair_total = 0.0
    
    novelty_block = build_exclusion_prompt_block(
        history_fingerprints=all_fingerprints,
        history_concepts=all_concepts,
        attempt_id=attempt_id,
        attempt_number=attempt_number
    )

    logger.info(
        f"[NOVELTY] attempt_id={attempt_id} student={student_id} attempt=#{attempt_number} "
        f"history_questions={len(all_fingerprints)} history_concepts={len(all_concepts)}"
    )

    # ── Provider orchestration (Module 3 Shared Deadline & Selective Replacement) ──
    start_time = time.perf_counter()
    effective_deadline = float(deadline_seconds if deadline_seconds is not None else os.getenv("OVERALL_DEADLINE_SECONDS", "40.0"))
    deadline = RequestDeadline(overall_deadline=effective_deadline, start_time=start_time)

    providers = _get_providers()
    configured_providers = [p for p in providers if p.is_configured()]

    if not configured_providers:
        raise AIConfigError(
            "No AI provider is configured. Set GROQ_API_KEY, NVIDIA_API_KEY, "
            "GEMINI_API_KEY, or CLOUDFLARE_API_TOKEN (with CLOUDFLARE_ACCOUNT_ID) in .env.",
            category="CONFIG_ERROR",
        )

    logger.info(
        f"[QUIZ GEN START] attempt_id={attempt_id} course={course_code} topic='{topic_name}' "
        f"providers={[p.name for p in configured_providers]} deadline={effective_deadline:.1f}s "
        f"target_budget={DEADLINE_TARGET_SECONDS:.1f}s"
    )

    last_error: Optional[Exception] = None
    all_provider_errors: List[Exception] = []
    best_quiz: Optional[Dict[str, Any]] = None
    best_novelty_score = -1

    num_configured = len(configured_providers)
    max_retries = int(os.getenv("MAX_PROVIDER_RETRIES") or os.getenv("AI_MAX_RETRIES", "1"))

    preserved_questions: List[Dict[str, Any]] = []
    replacement_records: List[Dict[str, Any]] = []
    initial_provider_calls = 0
    replacement_calls = 0
    total_provider_calls = 0
    initial_valid_count = 0
    provider_used_name: Optional[str] = None

    for p_idx, provider in enumerate(configured_providers):
        if len(preserved_questions) >= num_questions:
            break

        min_required_time = PROVIDER_MIN_TIME_REQUIREMENTS.get(provider.name, DEFAULT_MIN_TIMEOUT)
        if not deadline.can_attempt(min_required_time):
            logger.warning(
                f"[DEADLINE REACHED] Only {deadline.remaining:.1f}s remaining (min required: {min_required_time:.1f}s for {provider.name}). "
                f"Skipping provider={provider.name}."
            )
            continue

        for attempt_idx in range(max_retries + 1):
            if len(preserved_questions) >= num_questions:
                break

            if not deadline.can_attempt(min_required_time):
                logger.warning(
                    f"[DEADLINE REACHED] Only {deadline.remaining:.1f}s remaining for attempt {attempt_idx + 1}. "
                    f"Skipping provider={provider.name}."
                )
                break

            # Calculate provider timeout
            time_remaining = deadline.remaining
            if num_configured == 1:
                target_budget = max(PROVIDER_TIMEOUT_SECONDS, 16.0) if provider.name == "gemini" else PROVIDER_TIMEOUT_SECONDS
                provider_timeout = min(target_budget, max(time_remaining - 3.0, min_required_time))
            else:
                remaining_providers = configured_providers[p_idx + 1:]
                reserve_needed = sum(
                    10.0 if p.name == "gemini"
                    else (12.0 if p.name == "nvidia"
                    else (8.0 if p.name == "cloudflare" else 5.0))
                    for p in remaining_providers
                )
                if reserve_needed > 0 and time_remaining > min_required_time:
                    max_alloc = max(time_remaining - reserve_needed, min_required_time)
                    if provider.name == "groq":
                        groq_budget = max(time_remaining - 8.0, min(10.5, time_remaining - 1.0))
                        provider_timeout = min(GROQ_TIMEOUT_SECONDS, groq_budget)
                    elif provider.name == "nvidia":
                        provider_timeout = min(NVIDIA_TIMEOUT_SECONDS, max_alloc)
                    elif provider.name == "cloudflare":
                        provider_timeout = min(CLOUDFLARE_TIMEOUT_SECONDS, max_alloc)
                    else:
                        provider_timeout = min(PROVIDER_TIMEOUT_SECONDS, max_alloc)
                else:
                    provider_timeout = min(PROVIDER_TIMEOUT_SECONDS, time_remaining)

            if provider.name == "gemini":
                provider_timeout = min(max(provider_timeout, 10.0), time_remaining)

            logger.info(
                f"[PROVIDER ATTEMPT] provider={provider.name} attempt={attempt_idx + 1}/{max_retries + 1} "
                f"budget={provider_timeout:.1f}s time_remaining={time_remaining:.1f}s "
                f"fallback_count={p_idx}"
            )

            t_p_start = time.perf_counter()
            is_retake = attempt_number > 1 or bool(all_fingerprints)

            # ── Pre-flight cooldown check ───
            provider_default_models = {
                "groq": "llama-3.1-8b-instant",
                "nvidia": "meta/llama-3.1-8b-instruct",
                "gemini": "gemini-1.5-flash",
                "cloudflare": "@cf/meta/llama-3.1-8b-instruct",
            }
            provider_model = provider_default_models.get(provider.name, provider.name)
            provider_api_key = None
            if provider.name == "groq":
                provider_api_key = os.getenv("GROQ_API_KEY")
            elif provider.name == "nvidia":
                provider_api_key = os.getenv("NVIDIA_API_KEY")
            elif provider.name == "gemini":
                provider_api_key = os.getenv("GEMINI_API_KEY")
            elif provider.name == "cloudflare":
                provider_api_key = os.getenv("CLOUDFLARE_API_TOKEN")

            is_avail, cooldown_remaining = quota_manager.is_model_available(
                provider.name, provider_model, provider_api_key
            )
            if not is_avail:
                logger.info(
                    f"[PROVIDER SKIPPED] provider={provider.name} model={provider_model} "
                    f"reason=cooldown remaining={cooldown_remaining:.1f}s"
                )
                continue

            try:
                # ── Phase 1: Initial Batch Generation (if preserved_questions is empty) ───
                if len(preserved_questions) == 0:
                    target_gen_questions = (num_questions + 2 if is_retake else num_questions + 1) if num_questions == 10 else num_questions
                    prompt_dist = get_difficulty_distribution(recommended_difficulty, total_questions=target_gen_questions)
                    base_prompt = get_quiz_prompt(
                        course_code=course_code,
                        course_name=course_name,
                        topic_name=topic_name,
                        syllabus_context=syllabus_context,
                        difficulty_dist=prompt_dist,
                        concepts=concepts,
                        disallowed_topics=disallowed_topics,
                        num_questions=target_gen_questions
                    )
                    extra_parts = []
                    if novelty_block:
                        extra_parts.append(novelty_block)
                    if adaptive_context:
                        extra_parts.append(adaptive_context)
                    full_prompt = base_prompt + ("\n\n" + "\n\n".join(extra_parts) if extra_parts else "")

                    t_p_start = time.perf_counter()
                    raw_text = provider.generate_quiz_raw(
                        course_code=course_code,
                        course_name=course_name,
                        topic_name=topic_name,
                        syllabus_context=syllabus_context,
                        timeout_seconds=provider_timeout,
                        custom_prompt=full_prompt
                    )
                    initial_provider_calls += 1
                    total_provider_calls += 1
                    t_llm_primary_total += (time.perf_counter() - t_p_start)

                    t_v_start = time.perf_counter()
                    data = extract_json_from_text(raw_text)
                    raw_candidates = extract_candidate_questions(data)

                    for cand in raw_candidates:
                        if len(preserved_questions) >= num_questions:
                            break
                        valid_q, reason, cat = classify_and_validate_candidate(
                            cand=cand,
                            topic_name=topic_name,
                            course_name=course_name,
                            course_code=course_code,
                            concepts=concepts,
                            syllabus_context=syllabus_context,
                            competing_topics=competing_topics,
                            accepted_questions=preserved_questions,
                            history_questions=history_questions_full,
                            all_fingerprints=all_fingerprints
                        )
                        if valid_q:
                            preserved_questions.append(valid_q)
                        else:
                            replacement_records.append({
                                "question": cand.get("question", "") if isinstance(cand, dict) else str(cand),
                                "reason": reason,
                                "category": cat,
                                "stage": "initial"
                            })
                    t_json_val_total += (time.perf_counter() - t_v_start)
                    provider_used_name = provider.name
                    initial_valid_count = len(preserved_questions)
                    if p_idx > 0:
                        logger.info(
                            f"[PROVIDER FALLBACK] succeeded_with={provider.name} "
                            f"after_failed_providers={[p.name for p in configured_providers[:p_idx]]}"
                        )

                # ── Phase 2: Selective Question Replacement (if needed < num_questions) ───
                rep_round = 0
                while len(preserved_questions) < num_questions and rep_round < MAX_REPLACEMENT_ROUNDS:
                    needed = num_questions - len(preserved_questions)
                    min_rep_time = REPLACEMENT_MIN_TIME_REQUIREMENTS.get(provider.name, DEFAULT_MIN_REPLACEMENT_TIMEOUT)
                    if not deadline.can_attempt(min_rep_time):
                        logger.warning(
                            f"[DEADLINE] Insufficient time remaining ({deadline.remaining:.2f}s < {min_rep_time:.2f}s) "
                            f"for selective replacement round {rep_round + 1}. Halting replacement loop."
                        )
                        break

                    rep_round += 1
                    rep_timeout = deadline.allocate_timeout(
                        desired_timeout=min(deadline.remaining * 0.8, 10.0),
                        min_required=min_rep_time
                    )

                    # Compute missing difficulties
                    curr_diffs = [q.get("difficulty", "Medium") for q in preserved_questions]
                    expected_diff_list = []
                    for diff_name, count in difficulty_dist.items():
                        expected_diff_list.extend([diff_name] * count)
                    missing_diffs = []
                    for exp_d in expected_diff_list:
                        if exp_d in curr_diffs:
                            curr_diffs.remove(exp_d)
                        else:
                            missing_diffs.append(exp_d)

                    tr_prompt = build_selective_replacement_prompt(
                        course_code=course_code,
                        course_name=course_name,
                        topic_name=topic_name,
                        needed=needed,
                        preserved_questions=preserved_questions,
                        rejected_records=replacement_records,
                        history_questions=history_questions_full,
                        missing_difficulties=missing_diffs,
                        concepts=concepts,
                        disallowed_topics=disallowed_topics,
                        syllabus_context=syllabus_context
                    )

                    logger.info(
                        f"[SELECTIVE REPLACEMENT round {rep_round}] provider={provider.name} "
                        f"preserved={len(preserved_questions)}/{num_questions}, requesting {needed} replacements..."
                    )
                    t_rep_start = time.perf_counter()
                    raw_rep = provider.generate_quiz_raw(
                        course_code=course_code,
                        course_name=course_name,
                        topic_name=topic_name,
                        syllabus_context=syllabus_context,
                        timeout_seconds=rep_timeout,
                        custom_prompt=tr_prompt
                    )
                    replacement_calls += 1
                    total_provider_calls += 1
                    t_replenishment_total += (time.perf_counter() - t_rep_start)

                    data_rep = extract_json_from_text(raw_rep)
                    rep_candidates = extract_candidate_questions(data_rep)

                    for cand in rep_candidates:
                        if len(preserved_questions) >= num_questions:
                            break
                        valid_q, reason, cat = classify_and_validate_candidate(
                            cand=cand,
                            topic_name=topic_name,
                            course_name=course_name,
                            course_code=course_code,
                            concepts=concepts,
                            syllabus_context=syllabus_context,
                            competing_topics=competing_topics,
                            accepted_questions=preserved_questions,
                            history_questions=history_questions_full,
                            all_fingerprints=all_fingerprints
                        )
                        if valid_q:
                            if missing_diffs:
                                valid_q["difficulty"] = missing_diffs.pop(0)
                            preserved_questions.append(valid_q)
                        else:
                            replacement_records.append({
                                "question": cand.get("question", "") if isinstance(cand, dict) else str(cand),
                                "reason": reason,
                                "category": cat,
                                "stage": f"replacement_round_{rep_round}"
                            })

                # ── Phase 3: Finalization if requested count achieved or allow_partial ──
                if len(preserved_questions) >= num_questions or (allow_partial and len(preserved_questions) > 0 and rep_round >= MAX_REPLACEMENT_ROUNDS):
                    final_questions = preserved_questions[:num_questions]
                    expected_difficulties = []
                    for diff, count in difficulty_dist.items():
                        expected_difficulties.extend([diff] * count)
                    for index, q in enumerate(final_questions):
                        if not q.get("difficulty") or q["difficulty"] not in ("Easy", "Medium", "Hard"):
                            if index < len(expected_difficulties):
                                q["difficulty"] = expected_difficulties[index]
                            else:
                                q["difficulty"] = "Medium"

                    total_duration = time.perf_counter() - start_time
                    logger.info(
                        f"[QUIZ GENERATION COMPLETE] provider={provider.name} "
                        f"total_duration={total_duration:.2f}s "
                        f"questions={len(final_questions)}/{num_questions} "
                        f"deadline_remaining={deadline.remaining:.1f}s"
                    )
                    timing_metrics = {
                        "db_context_sec": round(t_db_context, 3),
                        "llm_primary_sec": round(t_llm_primary_total, 3),
                        "json_validation_sec": round(t_json_val_total, 3),
                        "replenishment_sec": round(t_replenishment_total, 3),
                        "novelty_repair_sec": round(t_novelty_repair_total, 3),
                        "total_service_sec": round(total_duration, 3),
                        "deadline_remaining_sec": round(deadline.remaining, 3)
                    }

                    actual_replaced = max(0, len(final_questions) - initial_valid_count)
                    actual_preserved = len(final_questions) - actual_replaced

                    validated_quiz = {
                        "subject": course_name,
                        "topic": topic_name,
                        "questions": final_questions,
                        "unique_count": len(final_questions),
                        "recommended_difficulty": recommended_difficulty,
                        "adaptive_applied": adaptive_applied,
                        "adaptive_explanation": adaptive_explanation,
                        "adaptive_metadata": adaptive_decision,
                        "attempt_id": attempt_id,
                        "attempt_number": attempt_number,
                        "provider_used": provider_used_name or provider.name,
                        "novelty_repairs": replacement_calls,
                        "novelty_warning": False,
                        "novel_count": len(final_questions),
                        "timing_metrics": timing_metrics,
                        # Module 3 selective replacement and deadline metrics
                        "initial_provider_calls": initial_provider_calls,
                        "additional_replacement_calls": replacement_calls,
                        "total_provider_calls": total_provider_calls,
                        "preserved_count": actual_preserved,
                        "replaced_count": actual_replaced,
                        "replacement_records": replacement_records
                    }
                    metrics.record_provider_attempt(validated_quiz["provider_used"], success=True, duration=t_llm_primary_total)
                    return validated_quiz

            except ProviderConfigError as pce:
                last_error = pce
                all_provider_errors.append(pce)
                metrics.record_provider_attempt(provider.name, success=False, duration=time.perf_counter() - t_p_start, error=sanitize_error_text(str(pce)))
                logger.error(f"[PROVIDER CONFIG ERROR] provider={provider.name}: {pce}")
                if len(configured_providers) == 1:
                    raise AIConfigError(str(pce), category=pce.category) from pce
                break

            except ValueError as ve:
                last_error = ve
                all_provider_errors.append(ve)
                time_left = deadline.remaining
                if attempt_idx < max_retries and time_left > min_required_time:
                    logger.warning(f"[PROVIDER MALFORMED OUTPUT] provider={provider.name} attempt={attempt_idx + 1}: {ve}. Retrying...")
                    continue
                metrics.record_provider_attempt(provider.name, success=False, duration=time.perf_counter() - t_p_start, error=sanitize_error_text(str(ve)))
                break

            except (ProviderUnavailableError, ProviderError) as pe:
                last_error = pe
                all_provider_errors.append(pe)
                clean_pe = sanitize_error_text(str(pe))
                cat = getattr(pe, "category", "ERROR")
                metrics.record_provider_attempt(provider.name, success=False, duration=time.perf_counter() - t_p_start, error=clean_pe)
                logger.warning(f"[PROVIDER FAILED] provider={provider.name} category={cat}: {clean_pe}")

                time_left_now = deadline.remaining
                has_next = (p_idx + 1) < num_configured
                if not has_next and getattr(pe, "retryable", False) and attempt_idx < max_retries and time_left_now > min_required_time + 1.0:
                    import random
                    backoff = min(1.5, 0.4 * (2 ** attempt_idx)) + random.uniform(0.05, 0.15)
                    if time_left_now > min_required_time + backoff:
                        time.sleep(backoff)
                        continue

                if len(configured_providers) == 1 and not getattr(pe, "retryable", False) and cat in ("MALFORMED_REQUEST_400", "MODEL_NOT_FOUND_404", "AUTH_CONFIG_ERROR"):
                    raise AIConfigError(clean_pe, category=cat) from pe
                break

            except Exception as e:
                last_error = e
                all_provider_errors.append(e)
                clean_err = sanitize_error_text(str(e))
                logger.warning(f"[PROVIDER UNEXPECTED ERROR] provider={provider.name}: {clean_err}")
                break

    # ── All providers failed ──────────────────────────────────────────────────
    if best_quiz is not None and len(best_quiz.get("questions", [])) >= max(1, int(num_questions * 0.8)):
        total_duration = time.perf_counter() - start_time
        logger.warning(
            f"[NOVELTY FALLBACK] Returning best available quiz across all attempts. "
            f"total_duration={total_duration:.2f}s best_novelty={best_novelty_score}/{num_questions} "
            f"providers_tried={len(configured_providers)}"
        )
        best_quiz["novelty_warning"] = True
        best_quiz["attempt_id"] = attempt_id
        best_quiz["attempt_number"] = attempt_number
        best_quiz["recommended_difficulty"] = recommended_difficulty
        best_quiz["adaptive_applied"] = adaptive_applied
        best_quiz["adaptive_explanation"] = adaptive_explanation
        best_quiz["adaptive_metadata"] = adaptive_decision
        return best_quiz

    total_duration = time.perf_counter() - start_time
    last_error_category = getattr(last_error, 'category', 'UNKNOWN') if last_error else 'UNKNOWN'
    logger.error(
        f"[QUIZ GEN FAILED] All providers exhausted course={course_code} "
        f"topic='{topic_name}' total_duration={total_duration:.2f}s "
        f"last_error_category={last_error_category} providers_tried={len(configured_providers)}"
    )

    # Check if all errors were permanent/configuration/malformed errors
    if all_provider_errors and all(
        isinstance(e, ProviderConfigError)
        or not getattr(e, "retryable", False)
        or getattr(e, "category", "") in ("AUTH_CONFIG_ERROR", "MODEL_NOT_FOUND_404", "MALFORMED_REQUEST_400")
        for e in all_provider_errors
    ):
        clean_msg = sanitize_error_text(str(last_error)) if last_error else "Configuration error"
        raise AIConfigError(
            f"AI provider configuration or permanent request error across configured providers: {clean_msg}",
            category=last_error_category
        ) from last_error

    # Diagnostic response when sole provider fails
    if num_configured == 1:
        sole_name = configured_providers[0].name.capitalize()
        all_possible = ["Groq", "NVIDIA", "Gemini", "Cloudflare"]
        unconfigured = [p for p in all_possible if p.lower() != configured_providers[0].name.lower()]
        unconfigured_str = "/".join(unconfigured)

        if (
            last_error_category in ("TIMEOUT", "CLIENT_TIMEOUT", "DEADLINE_EXCEEDED", "DEADLINE_EXCEEDED_504", "SERVER_ERROR_504", "REQUEST_TIMEOUT_408")
            or "504" in last_error_category
            or "deadline" in str(last_error).lower()
            or "timeout" in str(last_error).lower()
        ):
            raise AITimeoutError(
                f"AI provider '{sole_name}' timed out generating the quiz. No fallback providers ({unconfigured_str}) are configured in Render.",
                category="TIMEOUT"
            ) from last_error
        elif last_error_category in ("RATE_LIMIT_429", "RESOURCE_EXHAUSTED", "QUOTA_EXHAUSTED") or "429" in last_error_category:
            raise AIRateLimitError(
                f"AI provider '{sole_name}' rate limit or quota exceeded. No fallback providers ({unconfigured_str}) are configured in Render.",
                category="RATE_LIMIT_429"
            ) from last_error
        elif "503" in last_error_category or "SERVICE_UNAVAILABLE" in last_error_category:
            raise AIServiceUnavailableError(
                f"AI provider '{sole_name}' is temporarily unavailable (503). No fallback providers ({unconfigured_str}) are configured in Render.",
                category="SERVICE_UNAVAILABLE_503",
                status_code=503,
                retryable=True
            ) from last_error
        elif "502" in last_error_category or "500" in last_error_category or "SERVER_ERROR" in last_error_category:
            code = 502 if "502" in last_error_category else 500
            raise AIServiceUnavailableError(
                f"AI provider '{sole_name}' encountered a server or gateway error ({code}). Please retry.",
                category=f"SERVER_ERROR_{code}",
                status_code=code,
                retryable=True
            ) from last_error

    # Differentiate failure categories across multi-provider attempts
    if (
        deadline.is_expired()
        or not any(deadline.can_attempt(PROVIDER_MIN_TIME_REQUIREMENTS.get(p.name, DEFAULT_MIN_TIMEOUT)) for p in configured_providers)
        or last_error_category in ("TIMEOUT", "CLIENT_TIMEOUT", "DEADLINE_EXCEEDED", "DEADLINE_EXCEEDED_504", "SERVER_ERROR_504", "REQUEST_TIMEOUT_408")
        or "504" in last_error_category
        or "deadline" in str(last_error).lower()
        or "timeout" in str(last_error).lower()
        or any("timeout" in str(e).lower() or "deadline" in str(e).lower() for e in all_provider_errors)
    ):
        raise AITimeoutError(
            "Quiz generation timed out across configured providers. Please try again.",
            category="TIMEOUT"
        ) from last_error
    elif last_error_category in ("RATE_LIMIT_429", "RESOURCE_EXHAUSTED", "QUOTA_EXHAUSTED") or "429" in last_error_category:
        raise AIRateLimitError(
            "AI service rate limit or quota exceeded across configured providers. Please wait a moment and retry.",
            category="RATE_LIMIT_429"
        ) from last_error
    elif "503" in last_error_category or "SERVICE_UNAVAILABLE" in last_error_category:
        raise AIServiceUnavailableError(
            "Quiz generation is temporarily unavailable. Please try again shortly.",
            category="SERVICE_UNAVAILABLE_503",
            status_code=503,
            retryable=True
        ) from last_error
    elif "502" in last_error_category or last_error_category in ("NETWORK_ERROR", "CONNECTION_ERROR"):
        raise AIServiceUnavailableError(
            "Quiz generation is temporarily unavailable. Please try again shortly.",
            category="SERVER_ERROR_502",
            status_code=502,
            retryable=True
        ) from last_error
    elif "500" in last_error_category or "SERVER_ERROR_500" in last_error_category:
        raise AIServiceUnavailableError(
            "Quiz generation is temporarily unavailable. Please try again shortly.",
            category="SERVER_ERROR_500",
            status_code=500,
            retryable=True
        ) from last_error

    unmet_count = len(preserved_questions)
    prov_label = provider_used_name or (configured_providers[0].name if configured_providers else 'AI')
    raise AIServiceUnavailableError(
        f"Provider {prov_label} generated only {unmet_count}/{num_questions} questions strictly isolating '{topic_name}'. Please try again.",
        category="SERVICE_UNAVAILABLE",
        status_code=503,
        retryable=True
    ) from last_error


def generate_analysis(subject: str, topic: str, weak_concepts: List[str], score: int, total: int) -> Dict[str, str]:
    """
    Personalized feedback generation across providers.
    Never raises an unhandled error that breaks quiz scoring.
    """
    if not weak_concepts:
        return {
            "summary": f"You performed well in {topic} with {score}/{total}.",
            "revision": "Review the explanations for any questions you were unsure about.",
            "recommendation": "Try another AI-generated quiz or move on to the next syllabus topic."
        }

    weak_text = ", ".join(weak_concepts)
    providers = _get_providers()
    deadline = 12.0
    start_time = time.perf_counter()

    for provider in providers:
        remaining = deadline - (time.perf_counter() - start_time)
        min_feedback_time = 10.0 if provider.name == "gemini" else 1.5
        if remaining <= min_feedback_time or not provider.is_configured():
            continue

        try:
            feedback_timeout = min(remaining, 12.0)
            raw_text = provider.generate_analysis_raw(
                subject=subject,
                topic=topic,
                weak_concepts=weak_concepts,
                score=score,
                total=total,
                timeout_seconds=feedback_timeout
            )
            data = extract_json_from_text(raw_text)
            if isinstance(data, dict) and "summary" in data and "revision" in data and "recommendation" in data:
                return {
                    "summary": str(data["summary"]),
                    "revision": str(data["revision"]),
                    "recommendation": str(data["recommendation"])
                }
        except Exception as e:
            logger.warning(f"[FEEDBACK PROVIDER FAILED] provider={provider.name}: {e}")

    logger.warning("[FEEDBACK FALLBACK] All feedback providers failed or timed out.")
    return {
        "summary": "AI feedback is temporarily unavailable. Please try again later.",
        "revision": f"Focus on revising these key concepts: {weak_text}.",
        "recommendation": "Review the question explanations below to strengthen your understanding."
    }