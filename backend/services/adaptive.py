import logging
import sqlite3
from typing import Optional, Dict, Any, List

from services.database import (
    get_topic_mastery,
    get_topic_history,
    get_recent_student_answers_for_topic,
    get_concept_performance,
    get_difficulty_performance,
    compute_next_adaptive_level,
    get_utc_now_iso,
    ADAPTIVE_LEVEL_FOUNDATIONAL,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED,
    ADAPTIVE_PROMOTION_THRESHOLD,
    ADAPTIVE_DEMOTION_THRESHOLD,
    ADAPTIVE_MIN_QUESTIONS_THRESHOLD,
    VALID_ADAPTIVE_LEVELS
)

logger = logging.getLogger("learnai.adaptive")

# Policy constants
ADAPTIVE_BENCHMARK_PERCENTAGE = 50.0                       # 50% benchmark (5/10)
ADAPTIVE_MIN_QUESTIONS = ADAPTIVE_MIN_QUESTIONS_THRESHOLD  # 5 questions
ADAPTIVE_DEFAULT_HISTORY_WINDOW = 10                       # Recent 10 graded questions
PROMOTION_THRESHOLD = ADAPTIVE_PROMOTION_THRESHOLD         # 0.80 (80%)
DEMOTION_THRESHOLD = ADAPTIVE_DEMOTION_THRESHOLD           # 0.40 (40%)

# Canonical difficulty level ordering: foundational (lowest) <-> mixed <-> advanced (highest)
ADAPTIVE_LEVEL_ORDER = [
    ADAPTIVE_LEVEL_FOUNDATIONAL,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED
]

LEVEL_ALIASES = {
    "foundational": ADAPTIVE_LEVEL_FOUNDATIONAL,
    "easy": ADAPTIVE_LEVEL_FOUNDATIONAL,
    "mixed": ADAPTIVE_LEVEL_MIXED,
    "medium": ADAPTIVE_LEVEL_MIXED,
    "moderate": ADAPTIVE_LEVEL_MIXED,
    "advanced": ADAPTIVE_LEVEL_ADVANCED,
    "hard": ADAPTIVE_LEVEL_ADVANCED,
}

LEVEL_DISPLAY_NAMES = {
    ADAPTIVE_LEVEL_FOUNDATIONAL: "Foundational",
    ADAPTIVE_LEVEL_MIXED: "Mixed",
    ADAPTIVE_LEVEL_ADVANCED: "Advanced",
}


def normalize_level(level: Optional[str], default: str = ADAPTIVE_LEVEL_MIXED) -> str:
    """Normalize input difficulty string or alias to canonical application level."""
    if not level:
        return default
    clean = str(level).strip().lower()
    return LEVEL_ALIASES.get(clean, default)


def increase_level(current: str) -> str:
    """Increase difficulty by one level, bounded at application ceiling (Advanced)."""
    norm = normalize_level(current)
    idx = ADAPTIVE_LEVEL_ORDER.index(norm)
    if idx < len(ADAPTIVE_LEVEL_ORDER) - 1:
        return ADAPTIVE_LEVEL_ORDER[idx + 1]
    return ADAPTIVE_LEVEL_ORDER[idx]


def decrease_level(current: str) -> str:
    """Decrease difficulty by one level, bounded at application floor (Foundational)."""
    norm = normalize_level(current)
    idx = ADAPTIVE_LEVEL_ORDER.index(norm)
    if idx > 0:
        return ADAPTIVE_LEVEL_ORDER[idx - 1]
    return ADAPTIVE_LEVEL_ORDER[0]


def calculate_adaptive_progression(
    attempts: List[Dict[str, Any]],
    initial_level: str = ADAPTIVE_LEVEL_MIXED
) -> Dict[str, Any]:
    """
    Step-Wise Adaptive Difficulty Progression Engine.
    
    Evaluates completed, graded attempts chronologically based on a 50% benchmark:
    - Rule A: Score > 50% on a topic increases difficulty by 1 level for the next quiz.
    - Rule B: Score < 50% on TWO CONSECUTIVE attempts decreases difficulty by 1 level.
              A single isolated low score does not decrease difficulty.
    - Rule C: Score == 50% maintains difficulty and resets the consecutive-low streak.
    - Rule D: A recovery score >= 50% resets the consecutive-low streak.
    - Boundaries: Never exceeds highest supported difficulty (Advanced) or falls below
      lowest supported difficulty (Foundational).
    """
    start_lvl = normalize_level(initial_level, default=ADAPTIVE_LEVEL_MIXED)
    if not attempts:
        return {
            "recommended_difficulty": start_lvl,
            "previous_level": start_lvl,
            "is_decision_active": False,
            "consecutive_low_streak": 0,
            "attempt_count": 0,
            "reason": "New student or topic without practice history. Starting at Mixed difficulty baseline.",
            "latest_score": 0,
            "latest_total": 0,
            "latest_percentage": 0.0
        }

    # If the first attempt recorded a specific difficulty, use it as baseline starting level
    first_diff = attempts[0].get("recommended_difficulty")
    if first_diff:
        start_lvl = normalize_level(first_diff, default=start_lvl)

    current_level = start_lvl
    previous_level = start_lvl
    consecutive_low_streak = 0
    reason = "Initial state"
    latest_score = 0
    latest_total = 0
    latest_pct = 0.0

    for idx, att in enumerate(attempts):
        score = float(att.get("score", 0))
        total = float(att.get("total", 0))
        if total <= 0:
            continue

        latest_score = int(score)
        latest_total = int(total)
        latest_pct = round((score / total) * 100.0, 1)

        previous_level = current_level

        if latest_pct > ADAPTIVE_BENCHMARK_PERCENTAGE:
            # Rule A: Score > 50% increases difficulty by one level
            current_level = increase_level(current_level)
            consecutive_low_streak = 0
            if current_level != previous_level:
                reason = (
                    f"High score ({latest_score}/{latest_total} = {latest_pct:.0f}% > 50%). "
                    f"Promoted from {previous_level.capitalize()} to {current_level.capitalize()}."
                )
            else:
                reason = (
                    f"High score ({latest_score}/{latest_total} = {latest_pct:.0f}% > 50%). "
                    f"Maintained {current_level.capitalize()} difficulty at ceiling."
                )
        elif latest_pct < ADAPTIVE_BENCHMARK_PERCENTAGE:
            # Rule B: Score < 50% requires two consecutive low attempts to demote
            consecutive_low_streak += 1
            if consecutive_low_streak >= 2:
                current_level = decrease_level(current_level)
                consecutive_low_streak = 0
                if current_level != previous_level:
                    reason = (
                        f"Consistently low performance (two consecutive attempts below 50%). "
                        f"Demoted from {previous_level.capitalize()} to {current_level.capitalize()}."
                    )
                else:
                    reason = (
                        f"Consistently low performance (two consecutive attempts below 50%). "
                        f"Maintained {current_level.capitalize()} difficulty at baseline."
                    )
            else:
                reason = (
                    f"Score below benchmark ({latest_score}/{latest_total} = {latest_pct:.0f}% < 50%). "
                    f"Single low score recorded; maintaining {current_level.capitalize()} difficulty."
                )
        else:
            # Rule C: Score == 50% benchmark
            consecutive_low_streak = 0
            reason = (
                f"Benchmark score ({latest_score}/{latest_total} = 50%). "
                f"Consecutive low streak reset; maintaining {current_level.capitalize()} difficulty."
            )

    return {
        "recommended_difficulty": current_level,
        "previous_level": previous_level,
        "is_decision_active": True,
        "consecutive_low_streak": consecutive_low_streak,
        "attempt_count": len(attempts),
        "reason": reason,
        "latest_score": latest_score,
        "latest_total": latest_total,
        "latest_percentage": latest_pct
    }


def calculate_mastery_level(performance: Dict[str, Dict[str, int]]) -> float:
    """
    Calculate single aggregate mastery level (0.0 to 1.0) from a non-overlapping performance mapping.
    Avoids double counting by strictly summing non-overlapping counts.
    """
    total_correct = sum(stats.get("correct", 0) for stats in performance.values())
    total_attempts = sum(stats.get("total", 0) for stats in performance.values())
    if total_attempts == 0:
        return 0.0
    return round(total_correct / total_attempts, 4)


def get_recent_topic_performance(
    student_id: str,
    course_code: str,
    topic_name: str,
    history_window: int = ADAPTIVE_DEFAULT_HISTORY_WINDOW,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Retrieve valid graded student performance strictly for (student_id, course_code, topic_name).
    Uses the recent history window from student_answers, with fallback to historical quiz_attempts.
    """
    # 1. Primary: query recent granular answers
    recent_answers = get_recent_student_answers_for_topic(
        student_id=student_id,
        course_code=course_code,
        topic_name=topic_name,
        limit=history_window,
        conn=conn
    )

    if recent_answers:
        valid_graded = [a for a in recent_answers if a.get("is_correct") in (0, 1)]
        if valid_graded:
            total_graded = len(valid_graded)
            total_correct = sum(1 for a in valid_graded if a.get("is_correct") == 1)
            accuracy = round(total_correct / total_graded, 4) if total_graded > 0 else 0.0
            return {
                "total_graded": total_graded,
                "total_correct": total_correct,
                "accuracy": accuracy,
                "source": "student_answers"
            }

    # 2. Secondary fallback for historical attempts without granular student_answers records
    topic_attempts = get_topic_history(student_id, course_code, topic_name, conn=conn)
    if topic_attempts:
        total_graded = 0
        total_correct = 0
        for att in topic_attempts[:2]:  # at most 2 recent attempts (up to ~20 questions)
            q_cnt = att.get("total", 0)
            c_cnt = att.get("score", 0)
            if q_cnt > 0:
                total_graded += q_cnt
                total_correct += c_cnt
        if total_graded > 0:
            return {
                "total_graded": total_graded,
                "total_correct": total_correct,
                "accuracy": round(total_correct / total_graded, 4),
                "source": "quiz_attempts"
            }

    # 3. Tertiary fallback: inspect topic_mastery cumulative counts if present
    mastery = get_topic_mastery(student_id, course_code, topic_name, conn=conn)
    if mastery and mastery.get("total_questions", 0) > 0:
        total_graded = mastery["total_questions"]
        total_correct = mastery["total_correct"]
        return {
            "total_graded": total_graded,
            "total_correct": total_correct,
            "accuracy": mastery["mastery_score"],
            "source": "topic_mastery"
        }

    return {
        "total_graded": 0,
        "total_correct": 0,
        "accuracy": 0.0,
        "source": "none"
    }


def compute_adaptive_decision(
    student_id: str,
    course_code: str,
    topic_name: str,
    conn: Optional[sqlite3.Connection] = None,
    history_window: int = ADAPTIVE_DEFAULT_HISTORY_WINDOW,
    min_questions: int = ADAPTIVE_MIN_QUESTIONS
) -> Dict[str, Any]:
    """
    Deterministic adaptive difficulty progression engine.
    
    Evaluates completed, graded attempts strictly for (student_id, course_code, topic_name).
    Applies step-wise benchmark (50%) adaptation:
    - Rule A: > 50% promotes by 1 level.
    - Rule B: < 50% on 2 consecutive attempts demotes by 1 level.
    - Rule C: == 50% maintains level, resets low streak.
    - Rule D: Recovery >= 50% resets consecutive-low streak.
    - Preserves student and topic isolation.
    """
    close_conn = False
    if conn is None:
        from services.database import get_connection
        conn = get_connection()
        close_conn = True

    try:
        # 1. Fetch existing topic mastery record
        mastery = get_topic_mastery(student_id, course_code, topic_name, conn=conn)
        if mastery and mastery.get("current_level") in VALID_ADAPTIVE_LEVELS:
            prior_level = mastery["current_level"].lower()
            known_weak = mastery.get("weak_concepts") or []
            known_strong = mastery.get("strong_concepts") or []
        else:
            prior_level = ADAPTIVE_LEVEL_MIXED
            known_weak = []
            known_strong = []

        # 2. Fetch completed attempts for this specific student, course, and topic
        topic_attempts = get_topic_history(student_id, course_code, topic_name, conn=conn)
        # get_topic_history returns attempts ordered by created_at DESC -> reverse for chronological order
        chronological = list(reversed(topic_attempts))

        # 3. Calculate step-wise adaptive progression across chronological attempts
        prog = calculate_adaptive_progression(chronological, initial_level=prior_level)

        total_graded = sum(a.get("total", 0) for a in chronological)
        total_correct = sum(a.get("score", 0) for a in chronological)
        overall_accuracy = round(total_correct / total_graded, 4) if total_graded > 0 else 0.0

        if total_graded < min_questions:
            decision = {
                "student_id": student_id,
                "course_code": course_code,
                "topic_name": topic_name,
                "previous_level": prior_level,
                "recommended_difficulty": prior_level,
                "history_window": len(chronological),
                "graded_answers_count": total_graded,
                "accuracy": overall_accuracy,
                "overall_accuracy": overall_accuracy,
                "reason": (
                    f"Insufficient practice history ({total_graded}/{min_questions} valid graded questions). "
                    f"Retaining current level ({prior_level.capitalize()})."
                ) if total_graded > 0 else "New student or topic without practice history. Starting at Mixed difficulty baseline.",
                "is_decision_active": False,
                "consecutive_low_streak": 0,
                "attempt_count": len(chronological),
                "weak_concepts": known_weak,
                "strong_concepts": known_strong,
                "policy_version": "1.0-stepwise",
                "timestamp": get_utc_now_iso()
            }
            return decision

        # 3. Calculate step-wise adaptive progression across chronological attempts
        prog = calculate_adaptive_progression(chronological, initial_level=prior_level)
        latest_accuracy = (prog["latest_percentage"] / 100.0) if prog["attempt_count"] > 0 else overall_accuracy

        decision = {
            "student_id": student_id,
            "course_code": course_code,
            "topic_name": topic_name,
            "previous_level": prog["previous_level"],
            "recommended_difficulty": prog["recommended_difficulty"],
            "history_window": len(chronological),
            "graded_answers_count": total_graded,
            "accuracy": latest_accuracy,
            "overall_accuracy": overall_accuracy,
            "reason": prog["reason"],
            "is_decision_active": prog["is_decision_active"],
            "consecutive_low_streak": prog["consecutive_low_streak"],
            "attempt_count": prog["attempt_count"],
            "weak_concepts": known_weak,
            "strong_concepts": known_strong,
            "policy_version": "1.0-stepwise",
            "timestamp": get_utc_now_iso()
        }

        logger.info(
            "[ADAPTIVE DECISION] student=%s course=%s topic='%s' prev=%s next=%s attempts=%d reason='%s'",
            student_id, course_code, topic_name, prog["previous_level"], prog["recommended_difficulty"],
            len(chronological), prog["reason"]
        )
        return decision
    finally:
        if close_conn:
            conn.close()


def get_recommended_difficulty(
    student_id: str,
    course_code: str,
    topic_name: Optional[str] = None,
    topic_weak_concepts: Optional[List[str]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> str:
    """
    Backward-compatible entry point returning the recommended difficulty string.
    If topic_name is provided, runs full topic-specific adaptive decision.
    """
    if topic_name:
        decision = compute_adaptive_decision(student_id, course_code, topic_name, conn=conn)
        return decision["recommended_difficulty"]

    # Fallback when topic_name is not provided (legacy call): check difficulty performance
    diff_perf = get_difficulty_performance(student_id, course_code, conn=conn)
    if not diff_perf:
        return ADAPTIVE_LEVEL_MIXED

    acc = calculate_mastery_level(diff_perf)
    if acc >= PROMOTION_THRESHOLD:
        return ADAPTIVE_LEVEL_ADVANCED
    elif acc <= DEMOTION_THRESHOLD:
        return ADAPTIVE_LEVEL_FOUNDATIONAL
    return ADAPTIVE_LEVEL_MIXED


def get_difficulty_distribution(recommended: str) -> Dict[str, int]:
    """Convert recommendation to exact question counts per difficulty."""
    lvl = recommended.lower() if recommended else "mixed"
    distributions = {
        ADAPTIVE_LEVEL_FOUNDATIONAL: {"Easy": 6, "Medium": 3, "Hard": 1},
        ADAPTIVE_LEVEL_MIXED: {"Easy": 4, "Medium": 4, "Hard": 2},
        ADAPTIVE_LEVEL_ADVANCED: {"Easy": 2, "Medium": 4, "Hard": 4}
    }
    return distributions.get(lvl, distributions[ADAPTIVE_LEVEL_MIXED])


def get_adaptive_prompt_additions(
    student_id: str,
    course_code: str,
    topic_name: str,
    recommended_difficulty: str,
    weak_concepts: Optional[List[str]] = None
) -> str:
    """Generate pedagogical prompt context for adaptive AI question generation."""
    additions = []
    lvl = recommended_difficulty.lower() if recommended_difficulty else "mixed"

    if lvl == ADAPTIVE_LEVEL_FOUNDATIONAL:
        additions.append(
            "ADAPTIVE DIFFICULTY DIRECTIVE (FOUNDATIONAL):\n"
            "- The student is building core mastery on this topic.\n"
            "- Emphasize foundational definitions, primary principles, clear standard scenarios, and direct applications.\n"
            "- Favor questions that build conceptual clarity and avoid overly convoluted edge cases."
        )
    elif lvl == ADAPTIVE_LEVEL_ADVANCED:
        additions.append(
            "ADAPTIVE DIFFICULTY DIRECTIVE (ADVANCED):\n"
            "- The student demonstrates high mastery on this topic.\n"
            "- Emphasize deep architectural trade-offs, multi-step problem solving, realistic edge cases, and comparative analysis.\n"
            "- Formulate rigorous options that require critical discernment and precise domain understanding."
        )
    else:
        additions.append(
            "ADAPTIVE DIFFICULTY DIRECTIVE (BALANCED MIX):\n"
            "- Provide a well-rounded academic progression covering core knowledge, conceptual application, and moderate problem solving."
        )

    if weak_concepts:
        clean_weak = [c for c in weak_concepts if c and c.strip()]
        if clean_weak:
            additions.append(
                f"PRIORITY WEAK CONCEPTS TO REINFORCE: {', '.join(clean_weak[:5])}.\n"
                f"- Include questions testing these concepts to help the student overcome previous difficulties."
            )

    return "\n\n".join(additions) if additions else ""


def get_adaptive_explanation(
    decision_or_student_id: Any,
    course_code: Optional[str] = None,
    recommended_difficulty: Optional[str] = None,
    topic_weak_concepts: Optional[List[str]] = None
) -> Dict[str, str]:
    """
    Returns a transparent, supportive explanation of the adaptive decision for the student.
    Accepts either an adaptive decision dictionary or legacy argument list.
    """
    if isinstance(decision_or_student_id, dict):
        decision = decision_or_student_id
        lvl = decision.get("recommended_difficulty", ADAPTIVE_LEVEL_MIXED).lower()
        is_active = decision.get("is_decision_active", False)
        graded_count = decision.get("graded_answers_count", 0)
        accuracy = decision.get("accuracy", 0.0)
    else:
        lvl = (recommended_difficulty or ADAPTIVE_LEVEL_MIXED).lower()
        is_active = True
        graded_count = 5
        accuracy = 0.50

    if not is_active and graded_count == 0:
        return {
            "level": "mixed",
            "label": "Initial Assessment",
            "description": "Initial difficulty — learning your current level.",
            "detail": "This is your first quiz on this topic. We're starting with a balanced mix of questions to assess your understanding."
        }

    if not is_active:
        return {
            "level": lvl,
            "label": f"{lvl.capitalize()} Practice",
            "description": f"{lvl.capitalize()} — practicing topic fundamentals.",
            "detail": f"You have completed {graded_count} questions on this topic so far. Complete at least {ADAPTIVE_MIN_QUESTIONS} questions to unlock personalized difficulty progression."
        }

    if lvl == ADAPTIVE_LEVEL_FOUNDATIONAL:
        return {
            "level": "foundational",
            "label": "Foundational Practice",
            "description": "Foundational — reinforcing core syllabus principles.",
            "detail": f"Based on recent practice (accuracy: {accuracy:.0%}), this quiz emphasizes core principles and direct applications to strengthen your foundation."
        }
    elif lvl == ADAPTIVE_LEVEL_ADVANCED:
        return {
            "level": "advanced",
            "label": "Advanced Challenge",
            "description": "Advanced — challenging your in-depth mastery.",
            "detail": f"Excellent work! Based on recent high performance (accuracy: {accuracy:.0%}), this quiz features complex multi-step reasoning and challenging problems."
        }
    else:
        return {
            "level": "mixed",
            "label": "Balanced Mix",
            "description": "Balanced — steady concept progression.",
            "detail": f"Your recent performance (accuracy: {accuracy:.0%}) shows steady progress. This quiz maintains a balanced mix of easy, medium, and hard questions."
        }


def detect_persistent_weak_topics(
    student_id: str,
    course_code: Optional[str] = None,
    min_attempts: int = 2,
    min_questions: int = 15,
    accuracy_threshold: float = 0.60,
    conn: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """
    Multi-attempt persistent weak-topic detector using sample size and recovery guard heuristics.

    Guarantees:
    - Never labels a topic as persistently weak based on an isolated mistake (requires min_attempts >= 2).
    - Requires sufficient question history (min_questions >= 15).
    - Verifies that recent performance has not already recovered above threshold (latest attempt >= 70% suppresses flag).
    - Extracts recurring weak concepts and maps them to curriculum syllabus revision actions.
    - Respects student isolation and optional course scoping.
    - Excludes incomplete, abandoned, ungraded, or zero-total attempts.
    """
    close_conn = False
    if conn is None:
        from services.database import get_connection
        conn = get_connection()
        close_conn = True

    try:
        cursor = conn.cursor()
        clean_course = course_code.strip() if course_code and course_code.strip() else None

        if clean_course:
            cursor.execute("""
                SELECT id, course_code, course_name, topic_name, score, total, percentage,
                       status, weak_concepts, strong_concepts, recommended_difficulty, created_at
                FROM quiz_attempts
                WHERE student_id = ? AND (course_code = ? OR LOWER(TRIM(course_code)) = LOWER(?))
                ORDER BY created_at ASC, id ASC
            """, (student_id, clean_course, clean_course))
        else:
            cursor.execute("""
                SELECT id, course_code, course_name, topic_name, score, total, percentage,
                       status, weak_concepts, strong_concepts, recommended_difficulty, created_at
                FROM quiz_attempts
                WHERE student_id = ?
                ORDER BY created_at ASC, id ASC
            """, (student_id,))

        rows = cursor.fetchall()
        if not rows:
            return []

        # Group attempts by (course_code, topic_name), filtering out incomplete attempts and duplicates
        from services.database import parse_json_safely
        topic_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        seen_ids = set()

        for r in rows:
            r_dict = dict(r)
            att_id = r_dict.get("id")
            if att_id and att_id in seen_ids:
                continue
            if att_id:
                seen_ids.add(att_id)

            status_val = (r_dict.get("status") or "").lower().strip()
            total_val = int(r_dict.get("total") or 0)
            if status_val in ("abandoned", "incomplete", "ungraded") or total_val <= 0:
                continue

            key = (r_dict["course_code"], r_dict["topic_name"])
            if key not in topic_groups:
                topic_groups[key] = []
            topic_groups[key].append(r_dict)

        # Fetch curriculum for syllabus context mapping
        curriculum_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
        try:
            from services.curriculum_service import curriculum_service
            curr_data = curriculum_service.get_curriculum()
            for c in curr_data.get("courses", []):
                c_code = c.get("code", "")
                for t in c.get("topics", []):
                    t_name = t.get("name", "")
                    curriculum_lookup[(c_code.lower(), t_name.lower())] = {
                        "unit": t.get("unit"),
                        "concepts": t.get("concepts", []),
                        "syllabus_context": t.get("syllabus_context", "")
                    }
        except Exception as e:
            logger.debug(f"Curriculum lookup skipped in weak-topic detector: {e}")

        persistent_weak_topics: List[Dict[str, Any]] = []

        for (c_code, t_name), attempts in topic_groups.items():
            attempt_count = len(attempts)
            total_questions = sum(int(a["total"]) for a in attempts)
            total_correct = sum(int(a["score"]) for a in attempts)

            # Rule 1: Sample size threshold check
            if attempt_count < min_attempts or total_questions < min_questions:
                continue

            aggregate_accuracy = total_correct / total_questions if total_questions > 0 else 0.0

            # Rule 2: Aggregate accuracy below threshold check
            if aggregate_accuracy >= accuracy_threshold:
                continue

            # Rule 3: Recovery check — if the latest attempt scored >= 70%, it is actively recovering
            latest_attempt = attempts[-1]
            if latest_attempt.get("percentage") is not None:
                latest_pct = float(latest_attempt["percentage"])
            else:
                lat_score = int(latest_attempt.get("score") or 0)
                lat_total = int(latest_attempt.get("total") or 0)
                latest_pct = (lat_score / lat_total * 100.0) if lat_total > 0 else 0.0

            if latest_pct >= 70.0:
                continue

            # Identify recurring weak concepts across multiple attempts
            concept_miss_counts: Dict[str, int] = {}
            for a in attempts:
                weak_list = parse_json_safely(a.get("weak_concepts"), default=[])
                for wc in weak_list:
                    wc_clean = str(wc).strip()
                    if wc_clean and wc_clean.lower() != "general":
                        concept_miss_counts[wc_clean] = concept_miss_counts.get(wc_clean, 0) + 1

            # Concepts missed in 2+ attempts are chronic gaps
            chronic_concepts = [c for c, count in concept_miss_counts.items() if count >= 2]
            if not chronic_concepts:
                chronic_concepts = list(concept_miss_counts.keys())[:3]

            # Syllabus reference info
            curr_meta = curriculum_lookup.get((c_code.lower(), t_name.lower()), {})
            unit = curr_meta.get("unit")
            unit_label = f"Unit {unit}" if unit else "Syllabus Topic"

            persistent_weak_topics.append({
                "course_code": c_code,
                "course_name": attempts[-1].get("course_name", c_code),
                "topic_name": t_name,
                "unit": unit,
                "attempt_count": attempt_count,
                "total_questions": total_questions,
                "total_correct": total_correct,
                "accuracy": round(aggregate_accuracy * 100.0, 1),
                "latest_score": f"{latest_attempt['score']}/{latest_attempt['total']} ({latest_pct:.0f}%)",
                "latest_correct": int(latest_attempt["score"]),
                "latest_total": int(latest_attempt["total"]),
                "latest_accuracy": round(latest_pct, 1),
                "severity": "high" if aggregate_accuracy < 0.40 else "moderate",
                "chronic_weak_concepts": chronic_concepts,
                "recommendation": f"Persistent learning gap in {unit_label}. Review fundamental principles for {', '.join(chronic_concepts[:3])} before retaking." if chronic_concepts else f"Persistent learning gap in {unit_label}. Revisit lecture notes and core definitions.",
                "suggested_level": ADAPTIVE_LEVEL_FOUNDATIONAL if aggregate_accuracy < 0.45 else ADAPTIVE_LEVEL_MIXED
            })

        # Sort by accuracy ascending (lowest mastery first)
        persistent_weak_topics.sort(key=lambda x: x["accuracy"])
        return persistent_weak_topics

    finally:
        if close_conn:
            conn.close()
