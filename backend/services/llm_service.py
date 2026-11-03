import os
import time
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

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

# Ensure backend/.env is reliably loaded regardless of working directory
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"
if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=True)
else:
    load_dotenv(override=True)

logger = logging.getLogger("learnai.orchestrator")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )


MAX_NOVELTY_REPAIR_ATTEMPTS = int(os.getenv("MAX_NOVELTY_REPAIR_ATTEMPTS", "2"))


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

    order_str = os.getenv("PROVIDER_ORDER", "groq,nvidia,gemini")
    order = [p.strip().lower() for p in order_str.split(",") if p.strip()]

    ordered = []
    seen = set()
    for name in order:
        if name in provider_map and name not in seen:
            ordered.append(provider_map[name])
            seen.add(name)

    # Append any not listed
    for name, provider in provider_map.items():
        if name not in seen:
            ordered.append(provider)

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
    allow_partial: bool = False
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

    # ── Provider orchestration ────────────────────────────────────────────────
    start_time = time.perf_counter()
    overall_deadline = float(os.getenv("OVERALL_DEADLINE_SECONDS", "40.0"))

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
        f"providers={[p.name for p in configured_providers]} deadline={overall_deadline:.1f}s "
        f"max_repairs={MAX_NOVELTY_REPAIR_ATTEMPTS}"
    )

    last_error: Optional[Exception] = None
    all_provider_errors: List[Exception] = []
    novelty_repair_count = 0
    best_quiz: Optional[Dict[str, Any]] = None
    best_novelty_score = -1

    num_configured = len(configured_providers)
    provider_timeout_budget = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "25.0"))
    max_retries = int(os.getenv("MAX_PROVIDER_RETRIES") or os.getenv("AI_MAX_RETRIES", "1"))

    for p_idx, provider in enumerate(configured_providers):
        for attempt_idx in range(max_retries + 1):
            elapsed = time.perf_counter() - start_time
            time_remaining = overall_deadline - elapsed

            min_required_time = 10.0 if provider.name == "gemini" else (4.0 if provider.name == "nvidia" else 3.0)
            if time_remaining <= min_required_time:
                logger.warning(
                    f"[DEADLINE REACHED] Only {time_remaining:.1f}s remaining (min required: {min_required_time:.1f}s for {provider.name}). "
                    f"Skipping provider={provider.name}."
                )
                break

            # Calculate provider timeout
            if num_configured == 1:
                # Single provider mode (e.g., only Gemini configured in Render dashboard):
                # Allocate a realistic budget (16s default for Gemini) so it doesn't prematurely 504 on 10 MCQs
                target_budget = max(provider_timeout_budget, 16.0) if provider.name == "gemini" else provider_timeout_budget
                provider_timeout = min(target_budget, max(time_remaining - 3.0, min_required_time))
            else:
                # Multi-provider mode: reserve budget for subsequent configured providers
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
                        # Groq generates 10 MCQs in ~5.0-7.5s via Qwen/Llama over internet TLS.
                        # Ensure Groq is given up to 11.0s to complete within the sub-10s mission target.
                        target_groq = float(os.getenv("GROQ_TIMEOUT_SECONDS", "11.0"))
                        groq_budget = max(time_remaining - 8.0, min(10.5, time_remaining - 1.0))
                        provider_timeout = min(target_groq, groq_budget)
                    elif provider.name == "nvidia":
                        provider_timeout = min(16.0, max_alloc)
                    elif provider.name == "cloudflare":
                        provider_timeout = min(
                            float(os.getenv("CLOUDFLARE_TIMEOUT_SECONDS", "20.0")),
                            max_alloc,
                        )
                    else:
                        provider_timeout = min(provider_timeout_budget, max_alloc)
                else:
                    provider_timeout = min(provider_timeout_budget, time_remaining)

            # Ensure provider_timeout respects provider's minimum allowed deadline without exceeding remaining budget
            if provider.name == "gemini":
                provider_timeout = min(max(provider_timeout, 10.0), time_remaining)

            logger.info(
                f"[PROVIDER ATTEMPT] provider={provider.name} attempt={attempt_idx + 1}/{max_retries + 1} "
                f"budget={provider_timeout:.1f}s time_remaining={time_remaining:.1f}s "
                f"fallback_count={p_idx}"
            )

            t_p_start = time.perf_counter()
            is_retake = attempt_number > 1 or bool(all_fingerprints)
            target_gen_questions = (num_questions + 2 if is_retake else num_questions + 1) if num_questions == 10 else num_questions
            prompt_dist = get_difficulty_distribution(recommended_difficulty, total_questions=target_gen_questions)
            try:
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
                t_llm_primary_total += (time.perf_counter() - t_p_start)

                t_v_start = time.perf_counter()
                data = extract_json_from_text(raw_text)
                validated_quiz = validate_quiz_data(
                    data,
                    course_name,
                    topic_name,
                    concepts=concepts,
                    syllabus_context=syllabus_context,
                    difficulty_dist=difficulty_dist,
                    course_code=course_code,
                    competing_topics=competing_topics,
                    expected_count=num_questions
                )
                t_json_val_total += (time.perf_counter() - t_v_start)

                # ── Bounded Replenishment for missing unique questions ─────────
                current_unique = validated_quiz["questions"]
                rep_attempts = 0
                while len(current_unique) < num_questions and rep_attempts < 2:
                    rep_attempts += 1
                    needed = num_questions - len(current_unique)
                    time_left = overall_deadline - (time.perf_counter() - start_time)
                    min_rep_time = 8.0 if provider.name == "gemini" else 2.5
                    if time_left <= min_rep_time:
                        break
                    ask_count = min(needed + 2, max(needed + 1, 3))
                    logger.info(
                        f"[REPLENISHMENT round {rep_attempts}] provider={provider.name} generated {len(current_unique)}/{num_questions} unique questions. "
                        f"Requesting {ask_count} replenishment questions (needed={needed}) for '{topic_name}'..."
                    )
                    try:
                        existing_texts = [q["question"] for q in current_unique]
                        concept_str = ", ".join(concepts) if concepts else topic_name
                        disallowed_str = f"\nNEGATIVE CONSTRAINT: Absolutely do NOT generate questions on competing topics ({', '.join(disallowed_topics)})." if disallowed_topics else ""
                        rep_full_prompt = f"""You are an expert academic AI quiz generator for {course_code} - {course_name}.
Selected Topic: {topic_name}
Authorized Syllabus Concepts: {concept_str}

STRICT TOPIC MANDATE: Every question must test ONLY '{topic_name}'.
{disallowed_str}

We need AT LEAST {ask_count} additional, completely NEW and UNIQUE multiple-choice questions for '{topic_name}' to complete a {num_questions}-question set.
CRITICAL: Do NOT duplicate or rephrase any of these already accepted questions:
""" + "\n".join(f"  - {t}" for t in existing_texts) + f"""

REQUIREMENTS:
- Return AT LEAST {ask_count} questions strictly assessing '{topic_name}'.
- Each question must have 4 distinct options (A, B, C, D) with non-empty text.
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
      "difficulty": "Medium",
      "concept": "{concepts[0] if concepts else topic_name}",
      "explanation": "Concise step-by-step reason why A is correct."
    }}
  ]
}}"""
                        t_rep_start = time.perf_counter()
                        rep_max_tokens = 4000 if "gpt-oss" in getattr(provider, "name", "") else max(1500, 180 * ask_count + 200)
                        raw_rep = provider.generate_quiz_raw(
                            course_code=course_code,
                            course_name=course_name,
                            topic_name=topic_name,
                            syllabus_context=syllabus_context,
                            timeout_seconds=min(time_left, 12.0) if provider.name == "gemini" else min(time_left * 0.8, 8.0),
                            custom_prompt=rep_full_prompt,
                            max_tokens=rep_max_tokens
                        )
                        rep_data = extract_json_from_text(raw_rep)
                        rep_validated = validate_quiz_data(
                            rep_data,
                            course_name,
                            topic_name,
                            concepts=concepts,
                            syllabus_context=syllabus_context,
                            min_questions=1,
                            difficulty_dist=difficulty_dist,
                            course_code=course_code,
                            competing_topics=competing_topics
                        )
                        for cand_q in rep_validated["questions"]:
                            if len(current_unique) >= num_questions:
                                break
                            if (not any(is_question_near_duplicate(ex_q, cand_q, topic_name=topic_name)[0] for ex_q in current_unique) and
                                not any(is_question_near_duplicate(hist_q, cand_q, topic_name=topic_name)[0] for hist_q in history_questions_full)):
                                current_unique.append(cand_q)
                        t_replenishment_total += (time.perf_counter() - t_rep_start)
                        validated_quiz["questions"] = current_unique
                        logger.info(
                            f"[REPLENISHMENT SUCCESS round {rep_attempts}] provider={provider.name} total unique questions: {len(current_unique)}/{num_questions}"
                        )
                    except Exception as rep_err:
                        logger.warning(f"[REPLENISHMENT FAILED round {rep_attempts}] provider={provider.name}: {rep_err}")


                if len(validated_quiz["questions"]) < num_questions:
                    if allow_partial:
                        logger.info(
                            f"[QUIZ GEN TOPIC ISOLATION] Provider {provider.name} generated {len(validated_quiz['questions'])} "
                            f"verified topic-isolated questions for '{topic_name}' (returning without off-topic padding)."
                        )
                    else:
                        raise ValueError(f"Provider {provider.name} generated only {len(validated_quiz['questions'])}/{num_questions} questions strictly isolating '{topic_name}'.")

                # Novelty-aware candidate selection: Prioritize novel candidates from buffer BEFORE slicing
                if (all_fingerprints or all_concepts or history_questions_full) and len(validated_quiz["questions"]) > num_questions:
                    norm_fps = {normalise_text(fp) for fp in all_fingerprints if normalise_text(fp)}
                    novel_pool = []
                    collided_pool = []
                    for cand_q in validated_quiz["questions"]:
                        c_fp = normalise_text(cand_q.get("question", ""))
                        is_collided = c_fp in norm_fps
                        if not is_collided and history_questions_full:
                            for h_q in history_questions_full:
                                near_dup, _ = is_question_near_duplicate(h_q, cand_q, topic_name=topic_name)
                                if near_dup:
                                    is_collided = True
                                    break
                        if is_collided:
                            collided_pool.append(cand_q)
                        else:
                            novel_pool.append(cand_q)

                    if len(novel_pool) >= num_questions:
                        validated_quiz["questions"] = novel_pool[:num_questions]
                    else:
                        validated_quiz["questions"] = (novel_pool + collided_pool)[:num_questions]
                else:
                    validated_quiz["questions"] = validated_quiz["questions"][:num_questions]

                # ── Comprehensive Novelty Evaluation (text + concepts) ─────────
                novelty_result = evaluate_quiz_novelty(
                    questions=validated_quiz["questions"],
                    history_fingerprints=all_fingerprints,
                    history_concepts=all_concepts,
                    history_questions=history_questions_full,
                    topic_name=topic_name
                )

                is_novel = novelty_result["is_fully_novel"]
                flagged_indices = novelty_result["all_flagged_indices"]
                text_dup_indices = novelty_result.get("text_duplicate_indices", [])
                novel_count = novelty_result["novel_count"]

                if novel_count > best_novelty_score and (len(validated_quiz["questions"]) == num_questions or allow_partial):
                    best_quiz = validated_quiz
                    best_novelty_score = novel_count

                if not is_novel:
                    novelty_repair_count += 1
                    logger.warning(
                        f"[NOVELTY EVAL] provider={provider.name} "
                        f"novel={novel_count}/{num_questions}, flagged={len(flagged_indices)} "
                        f"reasons={novelty_result['duplicate_reasons'][:3]} "
                        f"Repair attempt {novelty_repair_count}/{MAX_NOVELTY_REPAIR_ATTEMPTS}."
                    )

                    # Targeted Replacement: Retain novel questions and replace all flagged duplicate questions (Level 1, 2, 3)
                    if flagged_indices and novelty_repair_count <= MAX_NOVELTY_REPAIR_ATTEMPTS:
                        elapsed_now = time.perf_counter() - start_time
                        repair_budget = overall_deadline - elapsed_now
                        min_repair_budget = 8.0 if provider.name == "gemini" else 2.5
                        if repair_budget > min_repair_budget:
                            try:
                                novel_kept = [q for idx, q in enumerate(validated_quiz["questions"]) if idx not in flagged_indices]
                                needed_novel = num_questions - len(novel_kept)
                                missing_difficulties = [validated_quiz["questions"][idx].get("difficulty", "Medium") for idx in flagged_indices]
                                rejected_questions = [validated_quiz["questions"][idx].get("question", "") for idx in flagged_indices]
                                if needed_novel > 0:
                                    logger.info(
                                        f"[TARGETED REPLACEMENT] Retaining {len(novel_kept)} novel questions. Requesting {needed_novel} replacements..."
                                    )
                                    concept_str = ", ".join(concepts) if concepts else topic_name
                                    rejected_block = "\n".join(f"  - [REJECTED DUPLICATE]: {q}" for q in rejected_questions if q)
                                    avoid_block = "\n".join(f"  - [ALREADY COVERED]: {q.get('question', '')}" for q in (novel_kept + history_questions_full[:8]) if q.get("question"))
                                    diff_req_str = ", ".join(missing_difficulties)
                                    tr_prompt = f"""You are an expert academic AI quiz generator for {course_code} - {course_name}.
Selected Topic: {topic_name}
Authorized Syllabus Concepts: {concept_str}

STRICT TOPIC MANDATE: Every question must test ONLY '{topic_name}'.

We need EXACTLY {needed_novel} additional replacement multiple-choice questions for '{topic_name}' testing different angles/learning objectives.
Required difficulty distribution for replacements: {diff_req_str}.

DO NOT duplicate, rephrase, or test the same learning objective as any of these questions:
{rejected_block}
{avoid_block}

REQUIREMENTS:
- Return EXACTLY {needed_novel} questions strictly assessing '{topic_name}'.
- Each question must have 4 distinct options (A, B, C, D) with non-empty text.
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
                                    raw_tr = provider.generate_quiz_raw(
                                        course_code=course_code,
                                        course_name=course_name,
                                        topic_name=topic_name,
                                        syllabus_context=syllabus_context,
                                        timeout_seconds=min(repair_budget * 0.8, 10.0),
                                        custom_prompt=tr_prompt,
                                        max_tokens=min(1200, 200 * needed_novel + 100)
                                    )
                                    data_tr = extract_json_from_text(raw_tr)
                                    val_tr = validate_quiz_data(
                                        data_tr,
                                        course_name,
                                        topic_name,
                                        concepts=concepts,
                                        syllabus_context=syllabus_context,
                                        min_questions=1,
                                        difficulty_dist=difficulty_dist,
                                        course_code=course_code,
                                        competing_topics=competing_topics
                                    )
                                    for cand_q in val_tr.get("questions", []):
                                        if len(novel_kept) >= num_questions:
                                            break
                                        if (not any(is_question_near_duplicate(ex_q, cand_q, topic_name=topic_name)[0] for ex_q in novel_kept) and
                                            not any(is_question_near_duplicate(hist_q, cand_q, topic_name=topic_name)[0] for hist_q in history_questions_full)):
                                            if missing_difficulties:
                                                cand_q["difficulty"] = missing_difficulties.pop(0)
                                            novel_kept.append(cand_q)
                                    if len(novel_kept) == num_questions:
                                        validated_quiz["questions"] = novel_kept
                                        novelty_result = evaluate_quiz_novelty(
                                            questions=validated_quiz["questions"],
                                            history_fingerprints=all_fingerprints,
                                            history_concepts=all_concepts,
                                            history_questions=history_questions_full,
                                            topic_name=topic_name
                                        )
                                        is_novel = novelty_result["is_fully_novel"]
                                        novel_count = novelty_result["novel_count"]
                                        if novel_count > best_novelty_score:
                                            best_quiz = validated_quiz
                                            best_novelty_score = novel_count
                                        logger.info(
                                            f"[TARGETED REPLACEMENT SUCCESS] provider={provider.name} "
                                            f"improved novelty to {novel_count}/{num_questions} (is_novel={is_novel})"
                                        )
                            except Exception as tr_err:
                                logger.warning(f"[TARGETED REPLACEMENT ERROR] provider={provider.name}: {tr_err}")

                    elif novelty_repair_count <= MAX_NOVELTY_REPAIR_ATTEMPTS:
                        elapsed_now = time.perf_counter() - start_time
                        repair_budget = overall_deadline - elapsed_now
                        min_repair_budget = 10.0 if provider.name == "gemini" else 4.5
                        if repair_budget > min_repair_budget:
                            try:
                                # Specific repair prompt targeting repeated concepts
                                reasons_str = "\n".join(f"- {r}" for r in novelty_result["duplicate_reasons"][:5])
                                e_c = difficulty_dist.get("Easy", max(1, round(num_questions * 0.4)))
                                m_c = difficulty_dist.get("Medium", max(1, round(num_questions * 0.4)))
                                h_c = difficulty_dist.get("Hard", num_questions - e_c - m_c)
                                repair_feedback = (
                                    f"CRITICAL REPAIR REQUIRED FOR RETAKE #{attempt_number}:\n"
                                    f"The previously generated questions duplicated previous questions or concepts:\n"
                                    f"{reasons_str}\n\n"
                                    f"You MUST replace the questions at 1-based indices {[i + 1 for i in flagged_indices]} with completely NEW questions "
                                    f"testing DIFFERENT learning objectives, application problems, or circuit examples from the topic context: '{syllabus_context}'.\n"
                                    f"Return all {num_questions} questions in valid JSON meeting the required {e_c} Easy, {m_c} Medium, {h_c} Hard distribution.\n"
                                )
                                repair_prompt = base_prompt + "\n\n" + repair_feedback + "\n\n" + novelty_block

                                if provider.name == "gemini":
                                    repair_timeout = min(repair_budget, max(repair_budget * 0.8, 10.0))
                                else:
                                    repair_timeout = min(repair_budget * 0.8, 9.0)

                                raw_repair = provider.generate_quiz_raw(
                                    course_code=course_code,
                                    course_name=course_name,
                                    topic_name=topic_name,
                                    syllabus_context=syllabus_context,
                                    timeout_seconds=repair_timeout,
                                    custom_prompt=repair_prompt
                                )
                                data_repair = extract_json_from_text(raw_repair)
                                validated_repair = validate_quiz_data(
                                    data_repair,
                                    course_name,
                                    topic_name,
                                    concepts=concepts,
                                    syllabus_context=syllabus_context,
                                    difficulty_dist=difficulty_dist,
                                    course_code=course_code,
                                    competing_topics=competing_topics,
                                    expected_count=num_questions
                                )
                                if len(validated_repair.get("questions", [])) == num_questions:
                                    novelty_repair = evaluate_quiz_novelty(
                                        questions=validated_repair["questions"],
                                        history_fingerprints=all_fingerprints,
                                        history_concepts=all_concepts,
                                        history_questions=history_questions_full,
                                        topic_name=topic_name
                                    )

                                    if novelty_repair["novel_count"] >= novel_count:
                                        validated_quiz = validated_repair
                                        is_novel = novelty_repair["is_fully_novel"]
                                        novel_count = novelty_repair["novel_count"]
                                        if novel_count > best_novelty_score:
                                            best_quiz = validated_quiz
                                            best_novelty_score = novel_count
                                        logger.info(
                                            f"[NOVELTY REPAIR RESULT] provider={provider.name} "
                                            f"improved novelty to {novel_count}/{num_questions} (is_novel={is_novel})"
                                        )
                            except Exception as repair_err:
                                logger.warning(f"[NOVELTY REPAIR ERROR] provider={provider.name}: {repair_err}")

                # ── Ensure all questions have valid difficulty metadata ───────
                expected_difficulties = []
                for diff, count in difficulty_dist.items():
                    expected_difficulties.extend([diff] * count)
                for index, q in enumerate(validated_quiz["questions"][:num_questions]):
                    if not q.get("difficulty") or q["difficulty"] not in ("Easy", "Medium", "Hard"):
                        if index < len(expected_difficulties):
                            q["difficulty"] = expected_difficulties[index]
                        else:
                            q["difficulty"] = "Medium"

                validated_quiz["recommended_difficulty"] = recommended_difficulty
                validated_quiz["adaptive_applied"] = adaptive_applied
                validated_quiz["adaptive_explanation"] = adaptive_explanation
                validated_quiz["adaptive_metadata"] = adaptive_decision
                validated_quiz["attempt_id"] = attempt_id
                validated_quiz["attempt_number"] = attempt_number
                validated_quiz["novelty_repairs"] = novelty_repair_count
                validated_quiz["provider_used"] = provider.name
                validated_quiz["novelty_warning"] = not is_novel
                validated_quiz["novel_count"] = novel_count

                total_duration = time.perf_counter() - start_time
                timing_metrics = {
                    "db_context_sec": round(t_db_context, 3),
                    "llm_primary_sec": round(t_llm_primary_total, 3),
                    "json_validation_sec": round(t_json_val_total, 3),
                    "replenishment_sec": round(t_replenishment_total, 3),
                    "novelty_repair_sec": round(t_novelty_repair_total, 3),
                    "total_service_sec": round(total_duration, 3),
                }
                validated_quiz["timing_metrics"] = timing_metrics
                logger.info(
                    f"[STAGE TIMING] quiz_gen attempt_id={attempt_id} "
                    f"db_context={t_db_context:.3f}s llm_primary={t_llm_primary_total:.3f}s "
                    f"json_validation={t_json_val_total:.3f}s replenishment={t_replenishment_total:.3f}s "
                    f"novelty_repair={t_novelty_repair_total:.3f}s total_service={total_duration:.3f}s"
                )
                logger.info(
                    f"[QUIZ GEN SUCCESS] provider={provider.name} course={course_code} "
                    f"topic='{topic_name}' attempt=#{attempt_number} questions={len(validated_quiz['questions'])} "
                    f"total_duration={total_duration:.2f}s adaptive={recommended_difficulty} "
                    f"novelty={novel_count}/{num_questions} novel (warning={not is_novel})"
                )
                metrics.record_provider_attempt(provider.name, success=True, duration=t_llm_primary_total)
                return validated_quiz

            except ProviderConfigError as pce:
                last_error = pce
                all_provider_errors.append(pce)
                metrics.record_provider_attempt(provider.name, success=False, duration=time.perf_counter() - t_p_start, error=sanitize_error_text(str(pce)))
                logger.error(
                    f"[PROVIDER CONFIG ERROR] provider={provider.name} "
                    f"category={pce.category} retryable={pce.retryable}: {pce}"
                )
                if len(configured_providers) == 1:
                    raise AIConfigError(str(pce), category=pce.category) from pce
                break
            except ValueError as ve:
                last_error = ve
                all_provider_errors.append(ve)
                time_left = overall_deadline - (time.perf_counter() - start_time)
                if attempt_idx < max_retries and time_left > 4.5:
                    logger.warning(
                        f"[PROVIDER MALFORMED OUTPUT] provider={provider.name} "
                        f"attempt={attempt_idx + 1}/{max_retries + 1}: {ve}. Retrying..."
                    )
                    continue
                metrics.record_provider_attempt(provider.name, success=False, duration=time.perf_counter() - t_p_start, error=sanitize_error_text(str(ve)))
                logger.warning(
                    f"[PROVIDER FAILED] provider={provider.name} category=MALFORMED_OUTPUT: {ve}"
                )
                break
            except (ProviderUnavailableError, ProviderError) as pe:
                last_error = pe
                all_provider_errors.append(pe)
                cat = getattr(pe, "category", "ERROR")
                retryable = getattr(pe, "retryable", False)
                has_next = (p_idx + 1) < num_configured
                next_provider_name = configured_providers[p_idx + 1].name if has_next else "none"
                clean_pe = sanitize_error_text(str(pe))

                metrics.record_provider_attempt(provider.name, success=False, duration=time.perf_counter() - t_p_start, error=clean_pe)
                logger.warning(
                    f"[PROVIDER FAILED] provider={provider.name} "
                    f"category={cat} retryable={retryable} "
                    f"attempt={attempt_idx + 1}/{max_retries + 1} "
                    f"proceeding_to_next_provider={has_next} (next={next_provider_name}): {clean_pe}"
                )

                # Bounded retry with exponential backoff for temporary errors (503, 429, 500, 502) if time permits
                time_left_now = overall_deadline - (time.perf_counter() - start_time)
                min_time_needed = 10.0 if provider.name == "gemini" else (4.0 if provider.name == "nvidia" else 3.0)
                is_transient = retryable or any(term in cat for term in ["SERVICE_UNAVAILABLE", "RATE_LIMIT", "SERVER_ERROR"])

                # If there are fallback providers available, prefer fallback sequence to avoid duplicate requests;
                # Only retry when no fallback provider exists (sole provider or last provider in sequence).
                if not has_next and is_transient and attempt_idx < max_retries and time_left_now > min_time_needed + 1.0:
                    import random
                    backoff = min(1.5, 0.4 * (2 ** attempt_idx)) + random.uniform(0.05, 0.15)
                    if time_left_now > min_time_needed + backoff:
                        logger.info(
                            f"[PROVIDER RETRY] provider={provider.name} retrying after {backoff:.2f}s backoff "
                            f"(attempt {attempt_idx + 2}/{max_retries + 1}, category={cat})..."
                        )
                        time.sleep(backoff)
                        continue

                # If only 1 provider is configured and error is non-retryable permanent error, fail fast without masking
                if (
                    len(configured_providers) == 1
                    and not getattr(pe, "retryable", False)
                    and getattr(pe, "category", "") in ("MALFORMED_REQUEST_400", "MODEL_NOT_FOUND_404", "AUTH_CONFIG_ERROR")
                ):
                    raise AIConfigError(clean_pe, category=getattr(pe, "category", "CONFIG_ERROR")) from pe
                break
            except Exception as e:
                last_error = e
                all_provider_errors.append(e)
                has_next = (p_idx + 1) < num_configured
                next_provider_name = configured_providers[p_idx + 1].name if has_next else "none"
                clean_err = sanitize_error_text(str(e))
                logger.warning(
                    f"[PROVIDER UNEXPECTED ERROR] provider={provider.name} "
                    f"proceeding_to_next_provider={has_next} (next={next_provider_name}): {clean_err}"
                )
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
        last_error_category in ("TIMEOUT", "CLIENT_TIMEOUT", "DEADLINE_EXCEEDED", "DEADLINE_EXCEEDED_504", "SERVER_ERROR_504", "REQUEST_TIMEOUT_408")
        or "504" in last_error_category
        or "deadline" in str(last_error).lower()
        or "timeout" in str(last_error).lower()
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

    raise AIServiceUnavailableError(
        "Quiz generation is temporarily unavailable. Please try again shortly.",
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