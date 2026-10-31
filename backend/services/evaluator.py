from typing import Dict, Any, Optional, List


def build_effective_option_explanations(
    options: Dict[str, str],
    correct_answer: str,
    explanation: str,
    concept: str,
    existing_explanations: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """
    Ensures every option in `options` has an educational explanation.
    Preserves existing option explanations when present.
    For any option missing an explicit explanation:
      - The correct option receives the main question explanation.
      - Each distractor receives clear reasoning explaining why it is incorrect.
    """
    result: Dict[str, str] = {}
    existing = existing_explanations if isinstance(existing_explanations, dict) else {}

    clean_corr = str(correct_answer or "").strip().upper()
    clean_expl = str(explanation or "").strip()
    clean_concept = str(concept or "this topic").strip()

    if not isinstance(options, dict):
        return result

    for opt_key, opt_val in options.items():
        opt_key_upper = str(opt_key).strip().upper()
        opt_text = str(opt_val or "").strip()

        # Check existing explanations first
        if opt_key_upper in existing and existing[opt_key_upper]:
            result[opt_key_upper] = str(existing[opt_key_upper]).strip()
            continue

        if opt_key_upper == clean_corr:
            if clean_expl:
                result[opt_key_upper] = clean_expl
            else:
                result[opt_key_upper] = f"Option {opt_key_upper} is the correct answer according to {clean_concept} principles."
        else:
            if opt_text:
                result[opt_key_upper] = (
                    f"Option {opt_key_upper} ('{opt_text}') is incorrect. It does not satisfy the requirements of {clean_concept}."
                )
            else:
                result[opt_key_upper] = f"Option {opt_key_upper} is an incorrect distractor for {clean_concept}."

    return result


def evaluate_answers(questions, answers):
    correct = 0
    details = []
    concept_stats = {}
    difficulty_stats = {}

    for i, q in enumerate(questions):
        selected = answers[i] if i < len(answers) else ""
        is_correct = selected == q["correct_answer"]

        if is_correct:
            correct += 1

        raw_concept = str(q.get("concept") or "").strip()
        concept = raw_concept if raw_concept and len(raw_concept) > 1 else "General"
        if concept not in concept_stats:
            concept_stats[concept] = {"correct": 0, "total": 0}

        concept_stats[concept]["total"] += 1
        if is_correct:
            concept_stats[concept]["correct"] += 1

        difficulty = str(q.get("difficulty") or "Medium").capitalize()
        if difficulty not in {"Easy", "Medium", "Hard"}:
            difficulty = "Medium"
        if difficulty not in difficulty_stats:
            difficulty_stats[difficulty] = {"correct": 0, "total": 0}

        difficulty_stats[difficulty]["total"] += 1
        if is_correct:
            difficulty_stats[difficulty]["correct"] += 1

        options = q.get("options") or {}
        correct_answer = q.get("correct_answer", "")
        explanation = q.get("explanation", "")
        raw_opt_expl = q.get("option_explanations") or {}

        option_explanations = build_effective_option_explanations(
            options=options,
            correct_answer=correct_answer,
            explanation=explanation,
            concept=concept,
            existing_explanations=raw_opt_expl
        )

        details.append({
            "question": q["question"],
            "selected_answer": selected,
            "correct_answer": correct_answer,
            "is_correct": is_correct,
            "explanation": explanation,
            "concept": concept,
            "difficulty": difficulty,
            "options": options,
            "option_explanations": option_explanations
        })

    total = len(questions)
    percentage = round((correct / total) * 100, 2) if total else 0

    if percentage < 30:
        status = "Needs Strong Revision"
    elif percentage < 50:
        status = "Needs Revision"
    elif percentage < 70:
        status = "Developing"
    elif percentage < 85:
        status = "Good"
    else:
        status = "Excellent"

    weak_concepts = []
    strong_concepts = []

    # If student scored 100%, all tested concepts are strong
    if total > 0 and correct == total:
        weak_concepts = []
        strong_concepts = list(concept_stats.keys())
    else:
        for concept, stats in concept_stats.items():
            total_c = stats["total"]
            correct_c = stats["correct"]
            accuracy = correct_c / total_c if total_c > 0 else 0
            if total_c > 0 and correct_c < total_c and accuracy < 0.60:
                weak_concepts.append(concept)
            else:
                strong_concepts.append(concept)

    difficulty_accuracy = {}
    for diff, stats in difficulty_stats.items():
        accuracy = stats["correct"] / stats["total"] if stats["total"] > 0 else 0
        difficulty_accuracy[diff] = {
            "correct": stats["correct"],
            "total": stats["total"],
            "accuracy": round(accuracy * 100, 2)
        }

    concept_accuracy = {}
    for concept, stats in concept_stats.items():
        accuracy = stats["correct"] / stats["total"] if stats["total"] > 0 else 0
        concept_accuracy[concept] = {
            "correct": stats["correct"],
            "total": stats["total"],
            "accuracy": round(accuracy * 100, 2)
        }

    return {
        "score": correct,
        "total": total,
        "percentage": percentage,
        "status": status,
        "weak_concepts": weak_concepts,
        "strong_concepts": strong_concepts,
        "difficulty_accuracy": difficulty_accuracy,
        "concept_accuracy": concept_accuracy,
        "details": details
    }
