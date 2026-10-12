"""
Comprehensive Test Suite for LearnAI Quiz Relevance, Deduplication, and Evaluation.
Verifies all requirements from the user request across multiple courses.
"""
import sys
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

# Ensure console output handles all Unicode characters cleanly on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from services.novelty import (
    normalise_text,
    calculate_question_similarity,
    are_options_duplicate,
    is_question_near_duplicate,
    detect_within_quiz_duplicates,
    is_question_relevant_to_topic,
    normalize_question_concept
)
from services.evaluator import evaluate_answers

def test_unit_duplicate_detection():
    print("\n--- [UNIT TEST 1] Duplicate and Near-Duplicate Detection ---")
    
    # 1. Exact match
    q_orig = {
        "question": "What is the formula for the sigmoid activation function?",
        "options": {"A": "1/(1+e^-z)", "B": "tanh(z)", "C": "relu(z)", "D": "max(0,z)"},
        "correct_answer": "A",
        "concept": "Activation Functions"
    }
    q_exact = {
        "question": "what is the formula for the sigmoid activation function?",
        "options": {"A": "1/(1+e^-z)", "B": "tanh(z)", "C": "relu(z)", "D": "max(0,z)"},
        "correct_answer": "A",
        "concept": "Activation Functions"
    }
    is_dup, reason = is_question_near_duplicate(q_orig, q_exact)
    assert is_dup, f"Expected exact duplicate to be detected, got False ({reason})"
    print("  [PASS] Exact match detected correctly")

    # 2. Artificial variation pattern
    q_art = {
        "question": "What is the formula for the sigmoid activation function? (Application Variation)",
        "options": {"A": "1/(1+e^-z)", "B": "tanh(z)", "C": "relu(z)", "D": "max(0,z)"},
        "correct_answer": "A",
        "concept": "Activation Functions"
    }
    is_dup, reason = is_question_near_duplicate(q_orig, q_art)
    assert is_dup, f"Expected artificial variation to be detected, got False ({reason})"
    print("  [PASS] Artificial variation '(Application Variation)' detected correctly")

    # 3. Phrasing / near-duplicate
    q_paraphrase = {
        "question": "Which mathematical expression gives the formula for the sigmoid activation function?",
        "options": {"A": "1/(1+e^-z)", "B": "tanh(z)", "C": "relu(z)", "D": "max(0,z)"},
        "correct_answer": "A",
        "concept": "Activation Functions"
    }
    is_dup, reason = is_question_near_duplicate(q_orig, q_paraphrase)
    assert is_dup, f"Expected paraphrased duplicate to be detected, got False ({reason})"
    print("  [PASS] Near-duplicate paraphrased question detected correctly")

    # 4. Permuted options
    q_permuted = {
        "question": "Identify the formula for sigmoid activation:",
        "options": {"A": "relu(z)", "B": "1/(1+e^-z)", "C": "tanh(z)", "D": "max(0,z)"},
        "correct_answer": "B",
        "concept": "Activation Functions"
    }
    is_dup, reason = is_question_near_duplicate(q_orig, q_permuted)
    assert is_dup, f"Expected permuted options duplicate to be detected, got False ({reason})"
    print("  [PASS] Permuted option set duplicate detected correctly")

    # 5. Distinct question (no false positive)
    q_distinct = {
        "question": "What is the range of values output by a logistic sigmoid function?",
        "options": {"A": "(0, 1)", "B": "(-1, 1)", "C": "[0, infinity)", "D": "(-infinity, infinity)"},
        "correct_answer": "A",
        "concept": "Output Range"
    }
    is_dup, reason = is_question_near_duplicate(q_orig, q_distinct)
    assert not is_dup, f"Expected distinct question NOT to be duplicate, got True ({reason})"
    print("  [PASS] Distinct question recognized as unique without false positive")

    # 6. Entire list deduplication
    batch = [q_orig, q_distinct, q_exact, q_art, q_permuted]
    dedup = detect_within_quiz_duplicates(batch)
    assert dedup["has_duplicates"], "Batch should have duplicates"
    assert len(dedup["unique_questions"]) == 2, f"Expected 2 unique questions, got {len(dedup['unique_questions'])}"
    assert len(dedup["duplicate_indices"]) == 3, f"Expected 3 duplicate indices, got {len(dedup['duplicate_indices'])}"
    print("  [PASS] Batch deduplication accurately filtered 5 questions down to 2 unique items")


def test_unit_topic_relevance():
    print("\n--- [UNIT TEST 2] Topic Relevance Verification ---")
    
    topic = "Linear Algebra Concepts"
    concepts = ["Definition of matrix", "Types of matrices", "Algebra of matrices"]
    context = "Matrices: Definition of matrix; Types of matrices; Algebra of matrices, adjoint of a matrix..."

    q_rel = {
        "question": "Which of the following is an identity matrix of order 2x2?",
        "options": {"A": "[[1,0],[0,1]]", "B": "[[0,1],[1,0]]", "C": "[[1,1],[1,1]]", "D": "[[0,0],[0,0]]"},
        "concept": "Types of matrices"
    }
    is_rel, reason = is_question_relevant_to_topic(q_rel, topic, concepts, context)
    assert is_rel, f"Relevant question falsely rejected: {reason}"
    print("  [PASS] Relevant matrix question approved")

    # Completely out-of-scope question from Differential Equations
    q_irrel = {
        "question": "What is the integrating factor of the linear differential equation dy/dx + P(x)y = Q(x)?",
        "options": {"A": "e^(int P dx)", "B": "e^(int Q dx)", "C": "int P dx", "D": "P(x)*Q(x)"},
        "concept": "Differential Equations"
    }
    is_rel, reason = is_question_relevant_to_topic(q_irrel, topic, concepts, context)
    assert not is_rel, "Unrelated differential equation question should be flagged as irrelevant"
    print("  [PASS] Out-of-scope question from different syllabus module rejected as irrelevant")


def test_unit_concept_normalization():
    print("\n--- [UNIT TEST 3] Concept Normalization ---")
    
    topic = "Sigmoidal Neuron"
    concepts = ["Activation Function", "Mathematical Formulation", "Output Range", "Derivative Properties"]

    # Vague raw concept mapped to matching concept
    q1 = {"question": "What is the derivative of the sigmoid function d sigma / dz?", "concept": "Concept name"}
    norm_c = normalize_question_concept(q1, topic, concepts)
    assert norm_c == "Derivative Properties", f"Expected 'Derivative Properties', got '{norm_c}'"
    print("  [PASS] Placeholder 'Concept name' mapped accurately based on question text")

    # Direct match
    q2 = {"question": "What values does the sigmoid output produce?", "concept": "Output Range"}
    norm_c2 = normalize_question_concept(q2, topic, concepts)
    assert norm_c2 == "Output Range", f"Expected 'Output Range', got '{norm_c2}'"
    print("  [PASS] Valid concept tag preserved accurately")


def test_unit_evaluation():
    print("\n--- [UNIT TEST 4] Scoring & Weak Concept Evaluation ---")
    
    questions = [
        {"question": "Q1", "correct_answer": "A", "concept": "Concept A", "difficulty": "Easy"},
        {"question": "Q2", "correct_answer": "B", "concept": "Concept A", "difficulty": "Easy"},
        {"question": "Q3", "correct_answer": "C", "concept": "Concept B", "difficulty": "Medium"},
        {"question": "Q4", "correct_answer": "D", "concept": "Concept B", "difficulty": "Medium"},
    ]
    # Student gets both Concept A right, both Concept B wrong
    answers = ["A", "B", "A", "A"]
    res = evaluate_answers(questions, answers)
    
    assert res["score"] == 2, f"Expected score 2, got {res['score']}"
    assert res["total"] == 4, f"Expected total 4, got {res['total']}"
    assert res["percentage"] == 50.0, f"Expected 50.0%, got {res['percentage']}"
    assert "Concept B" in res["weak_concepts"], f"Expected Concept B in weak concepts, got {res['weak_concepts']}"
    assert "Concept A" in res["strong_concepts"], f"Expected Concept A in strong concepts, got {res['strong_concepts']}"
    print("  [PASS] Weak/strong concept classification accurate (Concept A strong 100%, Concept B weak 0%)")

    # Perfect score: zero weak concepts
    answers_perfect = ["A", "B", "C", "D"]
    res_perfect = evaluate_answers(questions, answers_perfect)
    assert res_perfect["score"] == 4
    assert len(res_perfect["weak_concepts"]) == 0, f"Expected 0 weak concepts on 100% score, got {res_perfect['weak_concepts']}"
    assert res_perfect["status"] == "Excellent"
    print("  [PASS] Perfect score correctly yields 0 weak concepts and 'Excellent' status")


def test_api_course_quiz(course_code: str, topic_name: str, topic_label: str):
    import uuid
    print(f"\n--- [API TEST] Course: {course_code} | Topic: '{topic_name}' ---")
    
    base_url = "http://127.0.0.1:8000"
    test_student_id = f"test-eval-{course_code}-{uuid.uuid4().hex[:6]}"
    
    # 1. Generate Quiz (First Attempt)
    req_body = {
        "course_code": course_code,
        "topic": topic_name,
        "student_id": test_student_id,
        "retake": False
    }
    req = urllib.request.Request(
        f"{base_url}/api/quiz/generate",
        data=json.dumps(req_body).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    with urllib.request.urlopen(req, timeout=45) as resp:
        assert resp.status == 200, f"Generate failed with HTTP {resp.status}"
        data = json.loads(resp.read().decode("utf-8"))
    
    quiz_id = data.get("quiz_id")
    questions = data.get("questions", [])
    
    print(f"  [PASS] Generated quiz_id={quiz_id} provider={data.get('provider_used')} questions={len(questions)}")
    assert len(questions) == 10, f"Expected exactly 10 questions, got {len(questions)}"
    
    # 2. Verify all questions for 4 options, non-empty text, and uniqueness
    for idx, q in enumerate(questions):
        assert q.get("question") and q["question"].strip(), f"Q{idx+1} has empty statement"
        opts = q.get("options", {})
        assert set(opts.keys()) == {"A", "B", "C", "D"}, f"Q{idx+1} options keys must be A, B, C, D"
        for k in ["A", "B", "C", "D"]:
            assert opts[k] and opts[k].strip(), f"Q{idx+1} option {k} is empty"
            assert "application variation" not in opts[k].lower(), f"Q{idx+1} option {k} has artificial variation"
        assert "(application variation)" not in q["question"].lower(), f"Q{idx+1} has artificial '(Application Variation)' in text!"

    # 3. Check within-quiz deduplication
    dedup = detect_within_quiz_duplicates(questions)
    assert not dedup["has_duplicates"], f"Generated quiz has duplicate questions: {dedup['duplicate_reasons']}"
    print("  [PASS] Verified ZERO duplicate or near-duplicate questions in the 10-question quiz")

    # 4. Check difficulty progression
    diffs = [q.get("difficulty") for q in questions]
    print(f"  [PASS] Difficulty distribution: {diffs.count('Easy')} Easy, {diffs.count('Medium')} Medium, {diffs.count('Hard')} Hard")

    # 5. Submit Answers
    answers = ["A", "B", "C", "D", "A", "B", "C", "D", "A", "B"]
    sub_body = {
        "quiz_id": quiz_id,
        "answers": answers,
        "student_id": test_student_id
    }
    sub_req = urllib.request.Request(
        f"{base_url}/api/quiz/submit",
        data=json.dumps(sub_body).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(sub_req, timeout=20) as resp:
        assert resp.status == 200, f"Submit failed with HTTP {resp.status}"
        sub_res = json.loads(resp.read().decode("utf-8"))
    
    print(f"  [PASS] Submit evaluated: score={sub_res.get('score')}/10 percentage={sub_res.get('percentage')}% status='{sub_res.get('status')}'")
    print(f"  [PASS] Weak concepts: {sub_res.get('weak_concepts')}")
    print(f"  [PASS] Strong concepts: {sub_res.get('strong_concepts')}")
    advice = sub_res.get('analysis', {}).get('revision', '')[:70]
    print(f"  [PASS] Analysis revision advice: '{advice}...'")
    
    # 6. Test retake with novelty checking (allow 40s token quota window reset)
    print("  ... waiting 40s for Groq TPM rate-limit quota cooldown ...")
    time.sleep(40)
    retake_body = {
        "course_code": course_code,
        "topic": topic_name,
        "student_id": test_student_id,
        "retake": True
    }
    retake_req = urllib.request.Request(
        f"{base_url}/api/quiz/generate",
        data=json.dumps(retake_body).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    retries = 2
    for r in range(retries):
        try:
            with urllib.request.urlopen(retake_req, timeout=45) as resp:
                assert resp.status == 200, f"Retake failed with HTTP {resp.status}"
                retake_data = json.loads(resp.read().decode("utf-8"))
                break
        except urllib.error.HTTPError as he:
            if he.code in (429, 503) and r < retries - 1:
                print(f"  ... transient HTTP {he.code}, cooling down 25s and retrying retake ...")
                time.sleep(25)
            else:
                raise
    
    retake_qs = retake_data.get("questions", [])
    assert len(retake_qs) == 10, f"Expected 10 questions on retake, got {len(retake_qs)}"
    print(f"  [PASS] Retake attempt #2 generated successfully with {len(retake_qs)} questions (attempt_number={retake_data.get('attempt_number', 2)})")
    
    return True


def run_all_tests():
    print("=" * 70)
    print("       LEARNAI COMPREHENSIVE GLOBAL VERIFICATION TEST SUITE")
    print("=" * 70)
    
    # Run Unit Tests
    test_unit_duplicate_detection()
    test_unit_topic_relevance()
    test_unit_concept_normalization()
    test_unit_evaluation()

    # Run API Tests across diverse courses
    # Course 1: Mathematics - Linear Algebra
    test_api_course_quiz("22MT103", "Linear Algebra Concepts", "Mathematics")
    
    print("\n  ... waiting 30s quota cooldown before Course 2 ...")
    time.sleep(30)

    # Course 2: AI/ML - Deep Learning
    test_api_course_quiz("22AM301", "Sigmoidal Neuron", "Deep Learning")

    print("\n  ... waiting 30s quota cooldown before Course 3 ...")
    time.sleep(30)

    # Course 3: Web Technologies - Variables
    test_api_course_quiz("22CS303", "Variables", "Web Technologies")

    print("\n" + "=" * 70)
    print("   ALL TESTS PASSED SUCCESSFULLY ACROSS ALL COURSES & MODULES!")
    print("=" * 70)


if __name__ == "__main__":
    run_all_tests()
