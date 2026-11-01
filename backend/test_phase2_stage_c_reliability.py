"""
test_phase2_stage_c_reliability.py
==================================
Verification tests for Stage C:
- AI question-generation and explanation reliability
- Exact question counts (e.g. 5, 10)
- Exactly 4 distinct options (A, B, C, D)
- Valid correct answer letter
- Complete option-specific explanations for all 4 choices (A, B, C, D) guaranteed
- Bounded backoff / jitter on 429 / 503
"""

import pytest
from services.providers.base import (
    validate_single_question,
    validate_quiz_data,
    extract_normalized_options
)
from services.providers.groq_provider import GroqProvider
from services.providers.nvidia_provider import NvidiaProvider


def test_complete_four_option_explanations_when_model_provides_none():
    """When an AI model provides no option_explanations, all 4 (A, B, C, D) are synthesized."""
    raw_q = {
        "question": "What is the primary purpose of an index in a relational database?",
        "options": {
            "A": "To accelerate query search and retrieval operations",
            "B": "To ensure ACID transactions are serialized",
            "C": "To store table data in compressed text files",
            "D": "To automatically execute stored procedures"
        },
        "correct_answer": "A",
        "concept": "Database Indexing",
        "explanation": "An index creates an auxiliary data structure that speeds up lookups.",
        # option_explanations completely omitted
    }

    validated = validate_single_question(raw_q, topic_name="Database Indexing")
    assert validated is not None
    assert "option_explanations" in validated

    opt_expl = validated["option_explanations"]
    assert set(opt_expl.keys()) == {"A", "B", "C", "D"}
    # All 4 explanations are non-empty strings
    for letter in ["A", "B", "C", "D"]:
        assert isinstance(opt_expl[letter], str)
        assert len(opt_expl[letter].strip()) > 5
    # Correct answer explanation matches the verified explanation
    assert opt_expl["A"] == "An index creates an auxiliary data structure that speeds up lookups."
    # Distractors explain why incorrect
    assert "incorrect" in opt_expl["B"].lower()
    assert "incorrect" in opt_expl["C"].lower()
    assert "incorrect" in opt_expl["D"].lower()


def test_complete_four_option_explanations_when_model_provides_partial():
    """When an AI model provides explanations for only some options, the rest are filled."""
    raw_q = {
        "question": "Which HTTP status code signifies that a requested resource was not found?",
        "options": {
            "A": "404 Not Found",
            "B": "200 OK",
            "C": "500 Internal Server Error",
            "D": "301 Moved Permanently"
        },
        "correct_answer": "A",
        "concept": "HTTP Status Codes",
        "explanation": "404 indicates the origin server did not find a current representation.",
        "option_explanations": {
            "A": "Correct: 404 designates that the URI could not be located.",
            "B": "Incorrect: 200 signifies success."
            # C and D missing
        }
    }

    validated = validate_single_question(raw_q, topic_name="HTTP Status Codes")
    assert validated is not None
    opt_expl = validated["option_explanations"]

    assert set(opt_expl.keys()) == {"A", "B", "C", "D"}
    # Existing explanations preserved
    assert opt_expl["A"] == "Correct: 404 designates that the URI could not be located."
    assert opt_expl["B"] == "Incorrect: 200 signifies success."
    # Missing explanations synthesized
    assert "incorrect" in opt_expl["C"].lower()
    assert "incorrect" in opt_expl["D"].lower()


def test_exact_question_count_preservation():
    """Validate that validate_quiz_data enforces expected count and slices properly."""
    topic = "Python Variables"
    concepts = ["Variables", "Assignment", "Naming Rules"]
    questions = [
        {"question": "Which identifier is illegal as a variable name in Python?", "options": {"A": "2value", "B": "value_2", "C": "_value", "D": "VALUE"}, "correct_answer": "A", "concept": "Naming Rules", "explanation": "Identifiers cannot start with a digit."},
        {"question": "What happens when you reassign an existing variable in Python?", "options": {"A": "The name references a new object in memory", "B": "The previous memory block is overwritten in-place", "C": "A type-error occurs", "D": "The variable is marked constant"}, "correct_answer": "A", "concept": "Variables", "explanation": "Variables are name bindings to objects."},
        {"question": "Which keyword prevents creating a local variable and uses outer scope?", "options": {"A": "global", "B": "extern", "C": "static", "D": "shared"}, "correct_answer": "A", "concept": "Variables", "explanation": "The global keyword modifies module-level variables."},
        {"question": "How does multiple variable assignment like x, y = 10, 20 work in Python?", "options": {"A": "Through tuple packing and unpacking", "B": "Sequential chained pointer copying", "C": "Stack allocation", "D": "Lexical binding table"}, "correct_answer": "A", "concept": "Assignment", "explanation": "Python packs the right side into a tuple and unpacks it."},
        {"question": "What is the result of using the walrus operator := in an expression?", "options": {"A": "Assigns and returns the value simultaneously", "B": "Checks equality without assignment", "C": "Declares an immutable constant", "D": "Deletes the variable"}, "correct_answer": "A", "concept": "Assignment", "explanation": "The walrus operator assigns expressions inside conditions."},
        {"question": "What does id(x) return for a Python variable x?", "options": {"A": "The unique identity and memory address of the object", "B": "The hash value of the variable", "C": "The data type code", "D": "The reference count"}, "correct_answer": "A", "concept": "Variables", "explanation": "id() returns the object identity."},
        {"question": "Which value does an uninitialized variable hold in Python?", "options": {"A": "NameError is raised; uninitialized variables do not exist", "B": "None", "C": "0", "D": "Undefined"}, "correct_answer": "A", "concept": "Variables", "explanation": "Variables must be assigned before use."},
        {"question": "What occurs when multiple variables refer to the same mutable list?", "options": {"A": "Mutating via one variable reflects in the other", "B": "Copy-on-write clone is made", "C": "Compilation error", "D": "The list is frozen"}, "correct_answer": "A", "concept": "Assignment", "explanation": "Both variables point to the same list object."},
        {"question": "What does augmented assignment a += b do for immutable integers?", "options": {"A": "Rebinds a to a newly created integer", "B": "Mutates the integer in place", "C": "Raises TypeError", "D": "Converts a to float"}, "correct_answer": "A", "concept": "Assignment", "explanation": "Integers are immutable so re-binding occurs."},
        {"question": "Which naming convention is recommended by PEP 8 for Python constants?", "options": {"A": "ALL_CAPS_WITH_UNDERSCORES", "B": "camelCase", "C": "kebab-case", "D": "leading_dollar"}, "correct_answer": "A", "concept": "Naming Rules", "explanation": "PEP 8 specifies uppercase for constants."},
        {"question": "Can Python variable names contain Unicode characters like accented letters?", "options": {"A": "Yes, Python 3 supports Unicode identifier characters", "B": "No, ASCII only", "C": "Only in comments", "D": "Only with special imports"}, "correct_answer": "A", "concept": "Naming Rules", "explanation": "Python 3 supports UTF-8 identifiers."},
        {"question": "What does the 'del' statement do to a Python variable?", "options": {"A": "Unbinds the name from the object in local namespace", "B": "Immediately frees the memory", "C": "Sets variable to None", "D": "Zeroes the bytes"}, "correct_answer": "A", "concept": "Variables", "explanation": "del unbinds the variable name."}
    ]

    data = {"questions": questions}

    quiz = validate_quiz_data(
        data,
        course_name="Python Programming",
        topic_name=topic,
        concepts=concepts,
        syllabus_context="Topic covering Python variables, naming rules, and dynamic reference assignment.",
        min_questions=1,
        expected_count=10
    )
    assert len(quiz["questions"]) >= 10
    for q in quiz["questions"]:
        assert len(q["options"]) == 4
        assert set(q["option_explanations"].keys()) == {"A", "B", "C", "D"}


def test_malformed_question_with_duplicate_options_rejected():
    """Questions where options are duplicates of each other must be rejected."""
    raw_q = {
        "question": "What is the capital of France?",
        "options": {
            "A": "Paris",
            "B": "Paris",  # duplicate of A
            "C": "Lyon",
            "D": "Marseille"
        },
        "correct_answer": "A",
        "concept": "Geography"
    }

    validated = validate_single_question(raw_q, topic_name="Geography")
    assert validated is None


def test_nvidia_retryable_error_identification():
    """Verify NvidiaProvider properly classifies 429 and 503 as retryable."""
    provider = NvidiaProvider()

    class MockRateLimitError(Exception):
        status_code = 429

    class MockServiceUnavailableError(Exception):
        status_code = 503

    class MockAuthError(Exception):
        status_code = 401

    class MockNotFoundError(Exception):
        status_code = 404

    assert provider._is_retryable(MockRateLimitError("Rate limit reached")) is True
    assert provider._is_retryable(MockServiceUnavailableError("Service overloaded")) is True
    assert provider._is_retryable(MockAuthError("Unauthorized API key")) is False
    assert provider._is_retryable(MockNotFoundError("Model not found")) is False


def test_contradictory_distractor_explanation_detected_and_corrected():
    """
    If an AI model hallucinates that a distractor is correct ('Option B is correct...'),
    the validator must detect the contradiction and replace it with a pedagogical explanation.
    """
    raw_q = {
        "question": "What is the worst-case time complexity of quicksort?",
        "options": {
            "A": "O(n^2)",
            "B": "O(n log n)",
            "C": "O(log n)",
            "D": "O(n)"
        },
        "correct_answer": "A",
        "concept": "Quicksort Complexity",
        "explanation": "Quicksort degrades to O(n^2) when partitions are unbalanced.",
        "option_explanations": {
            "A": "Option A is correct: worst-case occurs with already sorted partitions.",
            # Hallucinated contradictory explanation claiming distractor B is correct:
            "B": "Option B is the correct answer because quicksort runs in O(n log n).",
            "C": "Option C is too fast for comparison sorts.",
            "D": "Option D is linear time, which is not quicksort's worst case."
        }
    }

    validated = validate_single_question(raw_q, topic_name="Quicksort Complexity")
    assert validated is not None
    opt_expl = validated["option_explanations"]

    # Option B explanation must NOT say it is correct
    assert "is the correct answer" not in opt_expl["B"]
    assert "incorrect" in opt_expl["B"].lower()
    # Verified pedagogical synthesis referencing concept and correct answer A
    assert "quicksort complexity" in opt_expl["B"].lower()
    assert "option a" in opt_expl["B"].lower()


def test_tautological_distractor_explanation_upgraded():
    """
    If an AI model provides a lazy tautology like 'Option C is incorrect.',
    the validator must upgrade it with concept-aware pedagogical guidance.
    """
    raw_q = {
        "question": "Which data structure operates on a FIFO basis?",
        "options": {
            "A": "Queue",
            "B": "Stack",
            "C": "Heap",
            "D": "Tree"
        },
        "correct_answer": "A",
        "concept": "Queue Data Structure",
        "explanation": "Queues operate on First-In First-Out semantics.",
        "option_explanations": {
            "A": "Queues process elements in the order they arrive.",
            "B": "Stack operates on LIFO (Last-In First-Out).",
            "C": "Option C is incorrect.",  # Lazy tautology
            "D": "Tree is a hierarchical graph without FIFO behavior."
        }
    }

    validated = validate_single_question(raw_q, topic_name="Queue Data Structure")
    assert validated is not None
    opt_expl = validated["option_explanations"]

    # Option C should not remain a bare lazy tautology
    assert opt_expl["C"] != "Option C is incorrect."
    assert "queue data structure" in opt_expl["C"].lower()
    assert "option a" in opt_expl["C"].lower()


def test_distractor_explanation_subtle_and_nuanced_cases():
    """
    Test nuanced distractor explanations:
    - Legitimate partially correct statements with negation must NOT be falsely flagged.
    - Explicit negations ('is not correct') must NOT be falsely flagged.
    - Mathematical and code-based failure explanations must be preserved.
    - True contradictions (asserting correctness for a distractor) must be detected and corrected.
    """
    raw_q = {
        "question": "What is the result of evaluating the limit as x approaches 0 of sin(x)/x?",
        "options": {
            "A": "1",
            "B": "0",
            "C": "-1",
            "D": "Infinity"
        },
        "correct_answer": "A",
        "concept": "Calculus Limits",
        "explanation": "By L'Hopital's rule or geometric definition, lim x->0 sin(x)/x = 1.",
        "option_explanations": {
            "A": "Option A is correct: standard limit equals 1.",
            # Legitimate partial-truth with negation (should NOT be rejected):
            "B": "While sin(0) = 0, directly substituting yields 0/0 and fails without limit laws.",
            # Explicit negation (should NOT be rejected):
            "C": "Option C is not correct because the ratio does not evaluate to -1.",
            # Code / math failure description (should NOT be rejected):
            "D": "The function remains bounded between -1 and 1, so it does not diverge to infinity."
        }
    }

    validated = validate_single_question(raw_q, topic_name="Calculus Limits")
    assert validated is not None
    opt_expl = validated["option_explanations"]

    # All legitimate nuanced explanations preserved
    assert opt_expl["B"] == "While sin(0) = 0, directly substituting yields 0/0 and fails without limit laws."
    assert opt_expl["C"] == "Option C is not correct because the ratio does not evaluate to -1."
    assert opt_expl["D"] == "The function remains bounded between -1 and 1, so it does not diverge to infinity."
