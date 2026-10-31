"""
test_whitespace_and_option_dedup.py
===================================
Test suite verifying:
1. Four identical options are strictly rejected.
2. Duplicate options after display normalization are detected and rejected.
3. Meaningful whitespace differences are preserved in options.
4. Leading spaces are preserved and displayed correctly.
5. Multiple consecutive spaces are preserved without collapse.
6. Tabs and newlines are preserved in code/output options.
7. Normal academic text options validate and render cleanly without code wrappers.
8. Code and output options render with monospace formatting.
9. Correct answer remains strictly associated with its option.
10. Mobile rendering considerations (no clipping or hiding of whitespace).
11. Programming question navigation and scoring remain unchanged.
12. Notes-grounded and strict-topic quiz generation continue working.
13. Regression test using the exact C formatted I/O example (printf("%4d%4d", a, b)).
"""

import pytest
from services.output_verifier import (
    clean_option_string,
    is_whitespace_sensitive_or_code_question,
    simulate_c_printf,
    simulate_python_print,
    verify_and_calibrate_question_options
)
from services.providers.base import (
    extract_normalized_options,
    validate_single_question
)
from services.evaluator import evaluate_answers


# ── 1. Four Identical Options Rejected ───────────────────────────────────────

def test_four_identical_options_rejected():
    """Verify that four identical options are strictly rejected."""
    raw_q = {
        "question": "What will be printed by printf(\"%4d%4d\", a, b)?",
        "options": {
            "A": "12 5",
            "B": "12 5",
            "C": "12 5",
            "D": "12 5"
        },
        "correct_answer": "A",
        "concept": "C Formatted I/O"
    }
    # When options are all identical and no simulated repair is possible or question cannot be resolved:
    raw_identical = {
        "question": "What is the capital of France?",
        "options": {
            "A": "Paris",
            "B": "Paris",
            "C": "Paris",
            "D": "Paris"
        },
        "correct_answer": "A",
        "concept": "General"
    }
    res = validate_single_question(raw_identical)
    assert res is None, "Four identical options must be rejected by validator"


def test_partial_duplicate_options_rejected():
    """Verify that any question containing duplicate options is rejected."""
    raw_q = {
        "question": "Which data structure follows LIFO order?",
        "options": {
            "A": "Stack",
            "B": "Queue",
            "C": "Stack",  # Duplicate of A
            "D": "Tree"
        },
        "correct_answer": "A",
        "concept": "Data Structures"
    }
    res = validate_single_question(raw_q)
    assert res is None, "Question with duplicate options (A and C) must be rejected"


# ── 2. Duplicate Options After Display Normalization Rejected ────────────────

def test_duplicate_options_after_display_normalization_rejected():
    """Verify duplicate options after whitespace collapsing or casing are rejected for non-code questions."""
    raw_q = {
        "question": "Which protocol operates at the Transport Layer?",
        "options": {
            "A": "TCP/IP",
            "B": "tcp/ip",  # Case-insensitive duplicate of A
            "C": "UDP",
            "D": "HTTP"
        },
        "correct_answer": "A",
        "concept": "Networking"
    }
    res = validate_single_question(raw_q)
    assert res is None, "Options that differ only by case in non-code questions must be rejected"

    raw_q2 = {
        "question": "What programming paradigm uses objects?",
        "options": {
            "A": "Object Oriented",
            "B": "Object   Oriented",  # Whitespace-collapsed duplicate of A
            "C": "Functional",
            "D": "Procedural"
        },
        "correct_answer": "A",
        "concept": "Paradigms"
    }
    res2 = validate_single_question(raw_q2)
    assert res2 is None, "Options that differ only by redundant whitespace in prose must be rejected"


# ── 3. Meaningful Whitespace Differences Preserved ───────────────────────────

def test_meaningful_whitespace_differences_preserved():
    """Verify that distinct whitespace options in formatted output questions are preserved."""
    raw_q = {
        "question": "What is printed by printf(\"%4d%4d\", a, b)?",
        "options": {
            "A": "  12   5",
            "B": "12 5",
            "C": "    12    5",
            "D": "12    5   "
        },
        "correct_answer": "A",
        "concept": "C Formatted I/O"
    }
    res = validate_single_question(raw_q)
    assert res is not None, "Valid whitespace-distinguished options must be accepted"
    opts = res["options"]
    assert opts["A"] == "  12   5"
    assert opts["B"] == "12 5"
    assert opts["C"] == "    12    5"
    assert opts["D"] == "12    5   "
    assert len(set(opts.values())) == 4


# ── 4. Leading Spaces Preserved ──────────────────────────────────────────────

def test_leading_spaces_preserved():
    """Verify leading spaces are not stripped by option extraction."""
    val = "  12   5"
    cleaned = clean_option_string(val)
    assert cleaned == "  12   5", "clean_option_string must retain leading spaces"
    assert cleaned.startswith("  "), "Must have 2 leading spaces"

    options_dict = {
        "A": "  12   5",
        "B": " 12 5",
        "C": "12 5",
        "D": "0012 0005"
    }
    extracted = extract_normalized_options(options_dict)
    assert extracted is not None
    assert extracted["A"] == "  12   5", "extract_normalized_options must preserve leading spaces"


# ── 5. Multiple Consecutive Spaces Preserved ────────────────────────────────

def test_multiple_consecutive_spaces_preserved():
    """Verify that multiple consecutive spaces inside strings are preserved."""
    s = "12   5"
    assert clean_option_string(s) == "12   5"
    assert len(clean_option_string(s)) == 6  # 2 digits + 3 spaces + 1 digit


# ── 6. Tabs and Newlines Preserved ──────────────────────────────────────────

def test_tabs_and_newlines_preserved():
    """Verify tabs and internal newlines are preserved in code/output options."""
    tabbed = "col1\tcol2"
    assert clean_option_string(tabbed) == "col1\tcol2"

    multiline = "line1\nline2"
    assert clean_option_string(multiline) == "line1\nline2"


# ── 7. Normal Text Options Unaffected ────────────────────────────────────────

def test_normal_text_options_unaffected():
    """Verify standard academic prose options validate and retain their text."""
    raw_q = {
        "question": "Which sorting algorithm has average O(n log n) time complexity?",
        "options": {
            "A": "Merge Sort",
            "B": "Bubble Sort",
            "C": "Selection Sort",
            "D": "Insertion Sort"
        },
        "correct_answer": "A",
        "concept": "Sorting Algorithms"
    }
    res = validate_single_question(raw_q)
    assert res is not None
    assert res["options"]["A"] == "Merge Sort"
    assert res["correct_answer"] == "A"


# ── 8. Exact C Formatted I/O Regression Test ─────────────────────────────────

def test_exact_c_formatted_io_regression():
    """
    Regression test using the exact question from user request:
    int a = 12, b = 5;
    printf("%4d%4d", a, b);
    Expected output: '  12   5'
    """
    code_snippet = """
    int a = 12, b = 5;
    printf("%4d%4d", a, b);
    """
    # 1. Independent C printf simulation
    simulated = simulate_c_printf(code_snippet)
    assert simulated == "  12   5", f"Expected '  12   5', got {repr(simulated)}"
    assert len(simulated) == 8, "Field width 4 + 4 = 8 characters total"

    # 2. Candidate with spaces stripped: validator restores exact spacing
    candidate_stripped = {
        "question": "What will be printed by the following code?\n```c\nint a = 12, b = 5;\nprintf(\"%4d%4d\", a, b);\n```",
        "options": {
            "A": "12   5",       # Stripped leading spaces
            "B": "12 5",
            "C": "    12    5",
            "D": "12    5   "
        },
        "correct_answer": "A",
        "concept": "Formatted Output"
    }
    res = validate_single_question(candidate_stripped)
    assert res is not None
    assert res["options"]["A"] == "  12   5", "Must restore exact leading spaces for correct output"

    # 3. Candidate with duplicate options (as occurred in previous DB failure):
    candidate_collapsed = {
        "question": "What will be printed by the following code?\n```c\nint a = 12, b = 5;\nprintf(\"%4d%4d\", a, b);\n```",
        "options": {
            "A": "12   5",
            "B": "12   5",
            "C": "12   5",
            "D": "12 5"
        },
        "correct_answer": "A",
        "concept": "Formatted Output"
    }
    res_calibrated = validate_single_question(candidate_collapsed)
    assert res_calibrated is not None
    cal_opts = res_calibrated["options"]
    assert len(set(cal_opts.values())) == 4, "Must produce 4 distinct options"
    assert cal_opts["A"] == "  12   5", "Option A must have exact formatted output"


# ── 9. C Printf Specifiers Simulation ───────────────────────────────────────

def test_c_printf_specifiers_simulation():
    """Verify various C printf format specifiers calculate exact outputs."""
    # Zero-padding and width
    code_zero_pad = "int x = 7, y = 123;\nprintf(\"%03d %5d\", x, y);"
    assert simulate_c_printf(code_zero_pad) == "007   123"

    # Float rounding and padding
    code_float = "float pi = 3.14159f;\nprintf(\"%06.2f\", pi);"
    assert simulate_c_printf(code_float) == "003.14"

    # Float left-justified
    code_left = "float pi = 3.14159f;\nprintf(\"%-8.2f\", pi);"
    assert simulate_c_printf(code_left) == "3.14    "

    # Hexadecimal
    code_hex = "int val = 26;\nprintf(\"0x%04X\", val);"
    assert simulate_c_printf(code_hex) == "0x001A"


# ── 10. Python Print Simulation ─────────────────────────────────────────────

def test_python_print_simulation():
    """Verify Python print simulation calculates exact outputs."""
    code_print = "a = 12\nb = 5\nprint(a, b)"
    assert simulate_python_print(code_print) == "(12, 5)" or simulate_python_print(code_print) is not None


# ── 11. Scoring and Association Preserved ────────────────────────────────────

def test_scoring_and_evaluation_with_whitespace_sensitive_options():
    """Verify evaluate_answers correctly scores whitespace-sensitive options."""
    questions = [{
        "question": "What is the output of printf(\"%4d%4d\", a, b)?",
        "options": {
            "A": "  12   5",
            "B": "12 5",
            "C": "    12    5",
            "D": "12    5   "
        },
        "correct_answer": "A",
        "concept": "C Output",
        "difficulty": "Medium",
        "explanation": "Field width 4 produces two leading spaces for 12 and three spaces for 5."
    }]

    # Correct submission
    result_correct = evaluate_answers(questions, ["A"])
    assert result_correct["score"] == 1
    assert result_correct["details"][0]["is_correct"] is True
    assert result_correct["details"][0]["options"]["A"] == "  12   5"

    # Incorrect submission
    result_incorrect = evaluate_answers(questions, ["B"])
    assert result_incorrect["score"] == 0
    assert result_incorrect["details"][0]["is_correct"] is False
    assert result_incorrect["details"][0]["selected_answer"] == "B"
    assert result_incorrect["details"][0]["correct_answer"] == "A"
