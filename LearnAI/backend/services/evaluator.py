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

        details.append({
            "question": q["question"],
            "selected_answer": selected,
            "correct_answer": q["correct_answer"],
            "is_correct": is_correct,
            "explanation": q.get("explanation", ""),
            "concept": concept,
            "difficulty": difficulty
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
