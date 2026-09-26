def evaluate_answers(questions, answers):
    correct = 0
    details = []
    concept_stats = {}

    for i, q in enumerate(questions):
        selected = answers[i] if i < len(answers) else ""
        is_correct = selected == q["correct_answer"]

        if is_correct:
            correct += 1

        concept = q["concept"]
        if concept not in concept_stats:
            concept_stats[concept] = {"correct": 0, "total": 0}

        concept_stats[concept]["total"] += 1
        if is_correct:
            concept_stats[concept]["correct"] += 1

        details.append({
            "question": q["question"],
            "selected_answer": selected,
            "correct_answer": q["correct_answer"],
            "is_correct": is_correct,
            "explanation": q["explanation"],
            "concept": concept,
            "difficulty": q["difficulty"]
        })

    total = len(questions)
    percentage = round((correct / total) * 100, 2) if total else 0

    if correct <= 2:
        status = "Needs Strong Revision"
    elif correct <= 4:
        status = "Needs Revision"
    elif correct <= 6:
        status = "Developing"
    elif correct <= 8:
        status = "Good"
    else:
        status = "Excellent"

    weak_concepts = []
    strong_concepts = []

    for concept, stats in concept_stats.items():
        accuracy = stats["correct"] / stats["total"]
        if accuracy < 0.5:
            weak_concepts.append(concept)
        else:
            strong_concepts.append(concept)

    return {
        "score": correct,
        "total": total,
        "percentage": percentage,
        "status": status,
        "weak_concepts": weak_concepts,
        "strong_concepts": strong_concepts,
        "details": details
    }
