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

# Configurable policy constants
ADAPTIVE_MIN_QUESTIONS = ADAPTIVE_MIN_QUESTIONS_THRESHOLD  # 5 questions
ADAPTIVE_DEFAULT_HISTORY_WINDOW = 10                       # Recent 10 graded questions
PROMOTION_THRESHOLD = ADAPTIVE_PROMOTION_THRESHOLD         # 0.80 (80%)
DEMOTION_THRESHOLD = ADAPTIVE_DEMOTION_THRESHOLD           # 0.40 (40%)


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
    
    Returns:
        total_graded: Number of valid graded questions in window
        total_correct: Number of correct answers in window
        accuracy: Float 0.0 to 1.0 (or 0.0 if total_graded == 0)
        source: 'student_answers' | 'quiz_attempts' | 'topic_mastery' | 'none'
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
    
    Policy Rules:
    1. Topic-Specific Isolation: Only evaluates performance for the selected student & topic.
    2. Minimum Evidence: Requires >= min_questions (default 5) before evaluating promotion/demotion.
    3. Step-Wise Transitions:
       - Foundational <-> Mixed <-> Advanced
       - Moves at most one level per decision. Never jumps directly between Foundational and Advanced.
    4. Thresholds:
       - Recent accuracy >= 80%: Promote 1 level (or stay at Advanced ceiling).
       - Recent accuracy <= 40%: Demote 1 level (or stay at Foundational floor).
       - Between 40% and 80%: Retain current level.
    5. Insufficient History: Retains prior level if established, or starts at 'mixed'.
    6. Complete Traceability: Returns an audit trail with sample size, accuracy, and reason.
    """
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

    # 2. Fetch recent topic performance window
    perf = get_recent_topic_performance(
        student_id=student_id,
        course_code=course_code,
        topic_name=topic_name,
        history_window=history_window,
        conn=conn
    )

    total_graded = perf["total_graded"]
    accuracy = perf["accuracy"]
    now_iso = get_utc_now_iso()

    # 3. Apply Decision Heuristic
    if total_graded < min_questions:
        recommended = prior_level
        is_active = False
        if not mastery and total_graded == 0:
            reason = "New student or topic without practice history. Starting at Mixed difficulty baseline."
        else:
            reason = (
                f"Insufficient practice history ({total_graded}/{min_questions} valid graded questions). "
                f"Retaining current level ({prior_level.capitalize()})."
            )
    else:
        is_active = True
        recommended = compute_next_adaptive_level(
            current_level=prior_level,
            total_questions=total_graded,
            mastery_score=accuracy,
            promotion_threshold=PROMOTION_THRESHOLD,
            demotion_threshold=DEMOTION_THRESHOLD,
            min_questions=min_questions
        )

        if accuracy >= PROMOTION_THRESHOLD:
            if prior_level == ADAPTIVE_LEVEL_FOUNDATIONAL:
                reason = f"High recent accuracy ({accuracy:.0%} >= 80%). Promoted from Foundational to Mixed."
            elif prior_level == ADAPTIVE_LEVEL_MIXED:
                reason = f"High recent accuracy ({accuracy:.0%} >= 80%). Promoted from Mixed to Advanced."
            else:
                reason = f"Consistent high accuracy ({accuracy:.0%}). Maintained Advanced difficulty at ceiling."
        elif accuracy <= DEMOTION_THRESHOLD:
            if prior_level == ADAPTIVE_LEVEL_ADVANCED:
                reason = f"Recent accuracy ({accuracy:.0%} <= 40%) indicates reinforcement needed. Demoted from Advanced to Mixed."
            elif prior_level == ADAPTIVE_LEVEL_MIXED:
                reason = f"Recent accuracy ({accuracy:.0%} <= 40%) indicates foundational review needed. Demoted from Mixed to Foundational."
            else:
                reason = f"Low recent accuracy ({accuracy:.0%}). Maintained Foundational difficulty at baseline."
        else:
            reason = f"Steady recent accuracy ({accuracy:.0%}). Maintained {prior_level.capitalize()} difficulty."

    decision = {
        "student_id": student_id,
        "course_code": course_code,
        "topic_name": topic_name,
        "previous_level": prior_level,
        "recommended_difficulty": recommended,
        "history_window": history_window,
        "graded_answers_count": total_graded,
        "accuracy": accuracy,
        "reason": reason,
        "is_decision_active": is_active,
        "weak_concepts": known_weak,
        "strong_concepts": known_strong,
        "policy_version": "1.0-stepwise",
        "timestamp": now_iso
    }

    logger.info(
        "[ADAPTIVE DECISION] student=%s topic='%s' prev=%s next=%s graded=%d acc=%.2f reason='%s'",
        student_id, topic_name, prior_level, recommended, total_graded, accuracy, reason
    )
    return decision


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