import logging
from typing import Optional, Dict, Any, List
from services.database import get_concept_performance, get_difficulty_performance

logger = logging.getLogger("learnai.adaptive")


def calculate_mastery_level(performance: Dict[str, Dict[str, int]]) -> float:
    """Calculate overall mastery level from performance data (0.0 to 1.0)."""
    total_correct = sum(stats["correct"] for stats in performance.values())
    total_attempts = sum(stats["total"] for stats in performance.values())
    if total_attempts == 0:
        return 0.0
    return total_correct / total_attempts


def get_recommended_difficulty(
    student_id: str,
    course_code: str,
    topic_weak_concepts: List[str]
) -> str:
    """
    Rule-based adaptive difficulty recommendation.
    
    Logic:
    - If no history: return "mixed" (4 Easy, 4 Medium, 2 Hard)
    - If overall mastery >= 0.8: increase difficulty (2 Easy, 4 Medium, 4 Hard)
    - If overall mastery >= 0.6: standard mix (4 Easy, 4 Medium, 2 Hard)
    - If overall mastery < 0.6: decrease difficulty (6 Easy, 3 Medium, 1 Hard)
    - If specific weak concepts in this topic: bias toward easier for those concepts
    """
    concept_perf = get_concept_performance(student_id, course_code)
    diff_perf = get_difficulty_performance(student_id, course_code)

    if not concept_perf and not diff_perf:
        logger.info(f"No history for student {student_id}, using default difficulty mix")
        return "mixed"

    overall_mastery = calculate_mastery_level({**concept_perf, **diff_perf})
    logger.info(f"Student {student_id} overall mastery: {overall_mastery:.2f}")

    if overall_mastery >= 0.8:
        recommended = "advanced"
    elif overall_mastery >= 0.6:
        recommended = "mixed"
    else:
        recommended = "foundational"

    if topic_weak_concepts:
        topic_mastery = 0.0
        topic_total = 0
        for concept in topic_weak_concepts:
            if concept in concept_perf:
                stats = concept_perf[concept]
                if stats["total"] > 0:
                    topic_mastery += stats["correct"]
                    topic_total += stats["total"]
        if topic_total > 0:
            topic_rate = topic_mastery / topic_total
            if topic_rate < 0.4:
                logger.info(f"Topic-specific weakness detected (rate={topic_rate:.2f}), recommending foundational")
                return "foundational"

    return recommended


def get_difficulty_distribution(recommended: str) -> Dict[str, int]:
    """Convert recommendation to exact question counts per difficulty."""
    distributions = {
        "foundational": {"Easy": 6, "Medium": 3, "Hard": 1},
        "mixed": {"Easy": 4, "Medium": 4, "Hard": 2},
        "advanced": {"Easy": 2, "Medium": 4, "Hard": 4}
    }
    return distributions.get(recommended, distributions["mixed"])


def get_adaptive_prompt_additions(
    student_id: str,
    course_code: str,
    topic_name: str,
    recommended_difficulty: str,
    weak_concepts: List[str]
) -> str:
    """Generate additional prompt context for adaptive quiz generation."""
    additions = []

    if recommended_difficulty == "foundational":
        additions.append(
            "ADAPTIVE EMPHASIS: The student is developing mastery. Emphasize foundational principles, core calculations, and clear scenarios."
        )
    elif recommended_difficulty == "advanced":
        additions.append(
            "ADAPTIVE EMPHASIS: The student demonstrates high mastery. Emphasize advanced application problems, edge cases, and comparative analysis."
        )

    if weak_concepts:
        additions.append(
            f"WEAK CONCEPTS TO EMPHASIZE: {', '.join(weak_concepts)}. "
            f"Include more questions testing these concepts, especially at easier difficulty levels."
        )

    concept_perf = get_concept_performance(student_id, course_code)
    if concept_perf:
        strong = [c for c, s in concept_perf.items() if s["total"] > 0 and s["correct"] / s["total"] >= 0.7]
        if strong:
            additions.append(
                f"STRONG CONCEPTS (can test at higher difficulty): {', '.join(strong[:5])}."
            )

    return "\n".join(additions) if additions else ""


def get_adaptive_explanation(
    student_id: str,
    course_code: str,
    recommended_difficulty: str,
    topic_weak_concepts: List[str]
) -> Dict[str, str]:
    """
    Returns a human-readable explanation of the adaptive difficulty decision.
    """
    concept_perf = get_concept_performance(student_id, course_code)
    diff_perf = get_difficulty_performance(student_id, course_code)

    if not concept_perf and not diff_perf:
        return {
            "level": "initial",
            "label": "Initial difficulty",
            "description": "Initial difficulty — learning your current level.",
            "detail": "This is your first quiz on this course. We're starting with a balanced mix of easy, medium, and hard questions to assess your knowledge."
        }

    overall_mastery = calculate_mastery_level({**concept_perf, **diff_perf})

    if recommended_difficulty == "foundational":
        if topic_weak_concepts:
            return {
                "level": "foundational",
                "label": "Foundational",
                "description": "Foundational — reinforcing core concepts.",
                "detail": f"Your recent performance suggests some concepts need reinforcement (mastery: {overall_mastery:.0%}). We're emphasizing easier questions to build a strong foundation."
            }
        else:
            return {
                "level": "foundational",
                "label": "Foundational",
                "description": "Foundational — building core understanding.",
                "detail": f"Your overall mastery is {overall_mastery:.0%}. We're focusing on foundational questions to strengthen your base knowledge."
            }
    elif recommended_difficulty == "advanced":
        return {
            "level": "advanced",
            "label": "Advanced",
            "description": "Advanced — challenging your mastery.",
            "detail": f"Your overall mastery is {overall_mastery:.0%}. You're ready for more advanced application problems and edge cases."
        }
    else:
        return {
            "level": "mixed",
            "label": "Balanced",
            "description": "Balanced — steady progress.",
            "detail": f"Your overall mastery is {overall_mastery:.0%}. We're maintaining a balanced mix of question difficulties to support continued growth."
        }