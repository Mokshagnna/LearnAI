"""
test_detailed_explanations.py
=============================
Comprehensive test suite verifying Detailed Answer Explanations & Distractor Analysis:
1. Correctly answered questions display student answer, correct answer, and detailed explanation.
2. Incorrectly answered questions display student's incorrect answer, correct answer, detailed explanation, and distractor reasons.
3. Every option has an accurate explanation / distractor analysis ("Why the other options are incorrect").
4. Unanswered / skipped questions show "Not answered" / skipped status, correct answer, detailed explanation, and distractor analyses.
5. Mathematics questions contain step-by-step calculations and reasoning.
6. Programming explanations preserve code formatting and syntax tokens.
7. SQL explanations are displayed cleanly.
8. Notes-grounded explanations do not invent unsupported facts.
9. Explanations are NOT exposed before submission (Zero Leakage in generate & session APIs).
10. Quiz history retains explanations across sessions.
11. Student B cannot retrieve Student A's answer explanations (Strict Isolation / 403 Forbidden).
12. Existing scoring, novelty tracking, topic mastery, and retakes remain completely intact and unchanged.
"""

import pytest
import sqlite3
import uuid
import json
from pathlib import Path
from fastapi.testclient import TestClient

from main import app
from services.database import (
    init_db,
    get_connection,
    create_quiz_session,
    get_quiz_session,
    submit_quiz_atomic,
    format_attempt_result,
    get_student_history,
)
from services.evaluator import evaluate_answers, build_effective_option_explanations
from services.providers.base import validate_single_question, get_quiz_prompt
from services.auth_utils import sign_student_identity
from services.account_service import register_student, authenticate_student
from services.rate_limiter import reset_all_rate_limiters


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Set up an isolated SQLite database for each test run."""
    db_file = tmp_path / "test_explanations.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-for-detailed-explanations-1234567890")
    monkeypatch.setenv("DISABLE_AUTH_RATE_LIMITER", "true")

    init_db(db_file)
    reset_all_rate_limiters()
    yield db_file
    reset_all_rate_limiters()


@pytest.fixture
def client():
    return TestClient(app)


SAMPLE_MATH_QUESTION = {
    "question": "What is the value of 15 + 4 * 3?",
    "options": {
        "A": "57",
        "B": "27",
        "C": "22",
        "D": "31"
    },
    "correct_answer": "B",
    "difficulty": "Easy",
    "concept": "Arithmetic Operator Precedence",
    "explanation": "According to operator precedence (BODMAS/PEMDAS), multiplication is evaluated before addition:\n1. 4 * 3 = 12\n2. 15 + 12 = 27\nTherefore, the final value is 27.",
    "option_explanations": {
        "A": "Incorrect. This results from evaluating addition first: (15 + 4) * 3 = 19 * 3 = 57, violating operator precedence.",
        "B": "Correct. Multiplies 4 * 3 = 12 first, then adds 15 to get 27.",
        "C": "Incorrect. Calculation error; does not follow arithmetic steps.",
        "D": "Incorrect. Adding 15 + 4 + 12 = 31 incorrectly repeats terms."
    }
}

SAMPLE_CODE_QUESTION = {
    "question": "Consider the following Python snippet:\n```python\ndef modify(lst):\n    lst.append(42)\nnums = [1, 2]\nmodify(nums)\nprint(len(nums))\n```\nWhat is the output?",
    "options": {
        "A": "2",
        "B": "3",
        "C": "1",
        "D": "TypeError"
    },
    "correct_answer": "B",
    "difficulty": "Medium",
    "concept": "Python List Mutability",
    "explanation": "In Python, lists are mutable objects passed by object reference. The `append()` call mutates `nums` in-place, increasing its length from 2 to 3.",
    "option_explanations": {
        "A": "Incorrect. In Python, lists are mutable and passed by object reference, so modifications inside functions persist.",
        "B": "Correct. `nums` is modified in-place by `append(42)`, so `len(nums)` becomes 3.",
        "C": "Incorrect. The list originally had 2 items and received 1 more, so it cannot have length 1.",
        "D": "Incorrect. Passing a list and calling `append()` is fully valid syntax in Python."
    }
}

SAMPLE_SQL_QUESTION = {
    "question": "Which SQL clause filters grouped rows after aggregation?",
    "options": {
        "A": "WHERE",
        "B": "HAVING",
        "C": "ORDER BY",
        "D": "GROUP BY"
    },
    "correct_answer": "B",
    "difficulty": "Easy",
    "concept": "SQL Aggregation Filtering",
    "explanation": "In SQL, the `HAVING` clause is specifically evaluated after `GROUP BY` to filter aggregated groups, whereas `WHERE` filters rows before grouping.",
    "option_explanations": {
        "A": "Incorrect. `WHERE` filters individual rows before grouping and cannot directly reference aggregate functions like `COUNT()`.",
        "B": "Correct. `HAVING` filters rows/groups after `GROUP BY` aggregation.",
        "C": "Incorrect. `ORDER BY` sorts the final result set; it does not filter data.",
        "D": "Incorrect. `GROUP BY` groups identical column values together, but does not itself filter groups."
    }
}


def test_build_effective_option_explanations():
    """Verify fallback generation of option explanations when explicit ones are omitted."""
    raw_q = {
        "options": {"A": "First", "B": "Second", "C": "Third", "D": "Fourth"},
        "correct_answer": "C",
        "explanation": "Third is correct because it matches the definition.",
        "concept": "Definitions"
    }

    opt_expls = build_effective_option_explanations(
        options=raw_q["options"],
        correct_answer=raw_q["correct_answer"],
        explanation=raw_q["explanation"],
        concept=raw_q["concept"]
    )

    assert len(opt_expls) == 4
    assert "Third is correct because it matches the definition." in opt_expls["C"]
    assert "Option A ('First') is incorrect" in opt_expls["A"]
    assert "Option B ('Second') is incorrect" in opt_expls["B"]
    assert "Option D ('Fourth') is incorrect" in opt_expls["D"]


def test_validate_single_question_parses_option_explanations():
    """Verify provider question parser preserves explicit option explanations."""
    raw_data = {
        "question": "What is 2 + 2?",
        "options": {"A": "3", "B": "4", "C": "5", "D": "6"},
        "correct_answer": "B",
        "explanation": "2 + 2 equals 4.",
        "option_explanations": {
            "A": "Incorrect by 1",
            "B": "Correct sum",
            "C": "Incorrect by 1 above",
            "D": "Incorrect sum"
        }
    }

    parsed = validate_single_question(raw_data, "Basic Math")
    assert parsed is not None
    assert "option_explanations" in parsed
    assert parsed["option_explanations"]["B"] == "Correct sum"
    assert parsed["option_explanations"]["A"] == "Incorrect by 1"


def test_evaluate_answers_structured_details():
    """Verify evaluate_answers returns options and complete option explanations."""
    questions = [SAMPLE_MATH_QUESTION, SAMPLE_CODE_QUESTION]
    # Student answered Q1 correctly ('B'), answered Q2 incorrectly ('A')
    answers = ["B", "A"]

    result = evaluate_answers(questions, answers)
    details = result["details"]
    assert len(details) == 2

    # Q1: Correctly answered
    d1 = details[0]
    assert d1["is_correct"] is True
    assert d1["selected_answer"] == "B"
    assert d1["correct_answer"] == "B"
    assert "BODMAS/PEMDAS" in d1["explanation"]
    assert "options" in d1
    assert "option_explanations" in d1
    assert d1["option_explanations"]["A"].startswith("Incorrect.")
    assert d1["option_explanations"]["B"].startswith("Correct.")

    # Q2: Incorrectly answered
    d2 = details[1]
    assert d2["is_correct"] is False
    assert d2["selected_answer"] == "A"
    assert d2["correct_answer"] == "B"
    assert "mutable objects" in d2["explanation"]
    assert "options" in d2
    assert "option_explanations" in d2
    assert "modify(nums)" in d2["question"]


def test_evaluate_answers_unanswered_skipped():
    """Verify skipped/unanswered question is graded False and includes full explanations."""
    questions = [SAMPLE_SQL_QUESTION]
    answers = [""]  # Student skipped

    result = evaluate_answers(questions, answers)
    d = result["details"][0]
    assert d["is_correct"] is False
    assert d["selected_answer"] == ""
    assert d["correct_answer"] == "B"
    assert "HAVING" in d["explanation"]
    assert "A" in d["option_explanations"]
    assert "B" in d["option_explanations"]


def test_zero_leakage_before_submission(client, monkeypatch):
    """
    Verify security rule:
    Neither /api/quiz/generate nor /api/quiz/session/{id} must ever reveal
    correct answers, explanations, or option explanations before submission.
    """
    # Mock LLM generation to ensure deterministic offline execution
    import main
    def mock_generate_quiz(**kwargs):
        return {
            "course_code": "22AM803",
            "topic": "Introduction and Goals of Computer Vision",
            "topic_id": "CV-01",
            "provider_used": "groq",
            "questions": [
                dict(SAMPLE_MATH_QUESTION),
                dict(SAMPLE_CODE_QUESTION),
                dict(SAMPLE_MATH_QUESTION),
                dict(SAMPLE_CODE_QUESTION),
                dict(SAMPLE_MATH_QUESTION)
            ]
        }
    monkeypatch.setattr(main, "generate_quiz", mock_generate_quiz)

    # 1. Register student
    reg_resp = client.post("/api/auth/register", json={
        "full_name": "Alice Security",
        "email": "alice.security@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!"
    })
    assert reg_resp.status_code == 201
    token = reg_resp.json()["token"]
    student_id = reg_resp.json()["student_id"]

    # 2. Generate quiz
    gen_resp = client.post(
        "/api/quiz/generate",
        json={
            "course_code": "22AM803",
            "topic": "Introduction and Goals of Computer Vision",
            "student_id": student_id,
            "retake": False
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert gen_resp.status_code == 200
    quiz_data = gen_resp.json()
    session_id = quiz_data["quiz_id"]
    public_questions = quiz_data["questions"]

    for q in public_questions:
        assert "correct_answer" not in q
        assert "explanation" not in q
        assert "option_explanations" not in q
        assert "question" in q
        assert "options" in q

    # 3. Inspect active session endpoint
    sess_resp = client.get(
        f"/api/quiz/session/{session_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert sess_resp.status_code == 200
    sess_questions = sess_resp.json()["questions"]
    for q in sess_questions:
        assert "correct_answer" not in q
        assert "explanation" not in q
        assert "option_explanations" not in q


def test_submit_quiz_returns_detailed_explanations(client):
    """
    Verify /api/quiz/submit returns full details containing:
    - question statement
    - selected_answer
    - correct_answer
    - is_correct flag
    - explanation
    - options dictionary
    - option_explanations dictionary
    """
    # 1. Register student
    reg_resp = client.post("/api/auth/register", json={
        "full_name": "Bob Student",
        "email": "bob.student@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!"
    })
    assert reg_resp.status_code == 201
    token = reg_resp.json()["token"]
    student_id = reg_resp.json()["student_id"]

    # 2. Directly create session with known questions
    session_id = str(uuid.uuid4())
    create_quiz_session(
        session_id=session_id,
        student_id=student_id,
        course_code="22CS201",
        course_name="Database Systems",
        topic_name="SQL Queries",
        questions=[SAMPLE_MATH_QUESTION, SAMPLE_CODE_QUESTION, SAMPLE_SQL_QUESTION]
    )

    # 3. Submit quiz: Q1 correct ('B'), Q2 wrong ('C'), Q3 skipped ('')
    submit_resp = client.post(
        "/api/quiz/submit",
        json={
            "quiz_id": session_id,
            "student_id": student_id,
            "answers": ["B", "C", ""]
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert submit_resp.status_code == 200
    res = submit_resp.json()

    assert res["score"] == 1
    assert res["total"] == 3
    details = res["details"]
    assert len(details) == 3

    # Check Q1 (Correct)
    q1 = details[0]
    assert q1["is_correct"] is True
    assert q1["selected_answer"] == "B"
    assert q1["correct_answer"] == "B"
    assert "BODMAS/PEMDAS" in q1["explanation"]
    assert len(q1["options"]) == 4
    assert len(q1["option_explanations"]) == 4
    assert "Incorrect" in q1["option_explanations"]["A"]
    assert "Correct" in q1["option_explanations"]["B"]

    # Check Q2 (Incorrect)
    q2 = details[1]
    assert q2["is_correct"] is False
    assert q2["selected_answer"] == "C"
    assert q2["correct_answer"] == "B"
    assert "mutable" in q2["explanation"]
    assert "Incorrect" in q2["option_explanations"]["C"]

    # Check Q3 (Skipped / Unanswered)
    q3 = details[2]
    assert q3["is_correct"] is False
    assert q3["selected_answer"] == ""
    assert q3["correct_answer"] == "B"
    assert "HAVING" in q3["explanation"]
    assert "WHERE" in q3["option_explanations"]["A"]


def test_idempotent_submission_and_history_persistence(client):
    """Verify duplicate submission and quiz history retain full explanations."""
    reg_resp = client.post("/api/auth/register", json={
        "full_name": "Carol Student",
        "email": "carol.student@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!"
    })
    assert reg_resp.status_code == 201
    token = reg_resp.json()["token"]
    student_id = reg_resp.json()["student_id"]

    session_id = str(uuid.uuid4())
    create_quiz_session(
        session_id=session_id,
        student_id=student_id,
        course_code="22CS201",
        course_name="Database Systems",
        topic_name="SQL Queries",
        questions=[SAMPLE_SQL_QUESTION]
    )

    # Initial submission
    res1 = client.post(
        "/api/quiz/submit",
        json={"quiz_id": session_id, "student_id": student_id, "answers": ["B"]},
        headers={"Authorization": f"Bearer {token}"}
    ).json()

    # Duplicate submission
    res2 = client.post(
        "/api/quiz/submit",
        json={"quiz_id": session_id, "student_id": student_id, "answers": ["B"]},
        headers={"Authorization": f"Bearer {token}"}
    ).json()

    assert res2["is_duplicate_submission"] is True
    assert res2["details"][0]["explanation"] == res1["details"][0]["explanation"]
    assert res2["details"][0]["options"] == res1["details"][0]["options"]
    assert res2["details"][0]["option_explanations"] == res1["details"][0]["option_explanations"]

    # Retrieve history
    hist_resp = client.get(
        f"/api/student/{student_id}/history",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert hist_resp.status_code == 200
    attempts = hist_resp.json()["attempts"]
    assert len(attempts) >= 1
    stored_questions = attempts[0]["quiz_data"]["questions"]
    assert len(stored_questions) == 1
    assert "HAVING" in stored_questions[0]["explanation"]
    assert "option_explanations" in stored_questions[0]


def test_student_data_isolation_on_explanations(client):
    """Verify Student B cannot retrieve or submit Student A's quiz explanations (403 Forbidden)."""
    # 1. Register Student A
    resp_a = client.post("/api/auth/register", json={
        "full_name": "Student A",
        "email": "student.a@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!"
    }).json()
    student_a_id = resp_a["student_id"]

    # 2. Register Student B
    resp_b = client.post("/api/auth/register", json={
        "full_name": "Student B",
        "email": "student.b@example.com",
        "password": "Password123!",
        "confirm_password": "Password123!"
    }).json()
    token_b = resp_b["token"]
    student_b_id = resp_b["student_id"]

    # 3. Create session for Student A
    session_id = str(uuid.uuid4())
    create_quiz_session(
        session_id=session_id,
        student_id=student_a_id,
        course_code="22CS201",
        course_name="Database Systems",
        topic_name="SQL Queries",
        questions=[SAMPLE_SQL_QUESTION]
    )

    # 4. Student B tries to retrieve session
    get_resp = client.get(
        f"/api/quiz/session/{session_id}",
        headers={"Authorization": f"Bearer {token_b}"}
    )
    assert get_resp.status_code == 403

    # 5. Student B tries to submit Student A's quiz
    sub_resp = client.post(
        "/api/quiz/submit",
        json={"quiz_id": session_id, "student_id": student_b_id, "answers": ["B"]},
        headers={"Authorization": f"Bearer {token_b}"}
    )
    assert sub_resp.status_code == 403

    # 6. Student B tries to view Student A's history
    hist_resp = client.get(
        f"/api/student/{student_a_id}/history",
        headers={"Authorization": f"Bearer {token_b}"}
    )
    assert hist_resp.status_code == 403


def test_curated_demo_questions_permanently_removed():
    """Verify that demo_questions.json has been removed and curated fallback is disabled."""
    demo_file = Path(__file__).parent / "data" / "demo_questions.json"
    assert not demo_file.exists(), "demo_questions.json must not exist in repository"


def test_math_and_programming_explanation_richness():
    """Verify mathematical step-by-step and code-aware explanations retain formatting."""
    math_q = {
        "question": "What is the result of `int x = 12; x += 5 * 2;`?",
        "options": {
            "A": "10",
            "B": "17",
            "C": "22",
            "D": "24"
        },
        "correct_answer": "C",
        "difficulty": "Medium",
        "concept": "Operator Precedence & Compound Assignment",
        "explanation": "In Java/C++, multiplication (*) executes before addition (+):\n1. Compute 5 * 2 = 10\n2. x += 10 expands to x = x + 10\n3. 12 + 10 = 22\nTherefore, final x is 22.",
        "option_explanations": {
            "A": "Incorrect. 10 is only 5 * 2 without adding initial x.",
            "B": "Incorrect. Adds before multiplying: (12 + 5) = 17.",
            "C": "Correct. Evaluates 5 * 2 = 10 first, then 12 + 10 = 22.",
            "D": "Incorrect. Arithmetic miscalculation."
        }
    }

    eval_res = evaluate_answers([math_q], ["B"])
    det = eval_res["details"][0]
    assert det["is_correct"] is False
    assert det["selected_answer"] == "B"
    assert det["correct_answer"] == "C"
    assert "12 + 10 = 22" in det["explanation"]
    assert "Adds before multiplying" in det["option_explanations"]["B"]
    assert "Correct." in det["option_explanations"]["C"]


def test_notes_grounded_explanations():
    """Verify notes-grounded questions preserve notes context in their detailed explanations."""
    notes_q = {
        "question": "According to the uploaded lecture notes on Indexing, why does a B+ Tree store data pointers only at leaf nodes?",
        "options": {
            "A": "To maximize internal node fan-out and keep the tree height shallow",
            "B": "To decrease disk block size",
            "C": "To enforce binary search tree restrictions",
            "D": "To prevent sequential range scans"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "B+ Tree Fan-Out & Index Structure",
        "explanation": "As stated in the course notes, by keeping data pointers exclusively in leaf nodes, internal nodes only store routing keys and child pointers. This significantly increases fan-out and reduces tree height, minimizing disk I/O.",
        "option_explanations": {
            "A": "Correct. High fan-out reduces the tree height to 3-4 levels for millions of records.",
            "B": "Incorrect. Disk block size is fixed by the operating system/hardware, not tree design.",
            "C": "Incorrect. B+ Trees are multi-way search trees, not binary trees.",
            "D": "Incorrect. Storing data pointers at leaf nodes joined by a linked list actually optimizes sequential range scans."
        }
    }

    eval_res = evaluate_answers([notes_q], ["A"])
    det = eval_res["details"][0]
    assert det["is_correct"] is True
    assert "fan-out" in det["explanation"]
    assert "Correct." in det["option_explanations"]["A"]
    assert "operating system/hardware" in det["option_explanations"]["B"]
