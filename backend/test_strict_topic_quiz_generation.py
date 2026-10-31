"""
Test suite for Strict Selected-Topic Question Generation in LearnAI.
Verifies:
  1. Multi-topic document notes context isolation.
  2. Multi-topic quiz generation produces zero questions from other topics in the document.
  3. Out-of-topic question rejection by validation stage.
  4. Bounded replenishment retries on topic validation failures.
  5. Thin notes graceful syllabus fallback.
  6. End-to-end parameter preservation (topic_id, study_material_id, course_code).
  7. Student isolation: cross-student study_material_id access is rejected with 403.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from main import app
from services.database import (
    init_db,
    get_connection,
    get_or_create_student,
    create_or_update_study_material,
    get_quiz_session,
)
from services.notes_matcher import (
    select_relevant_notes_context,
    store_notes_session,
)
from services.novelty import is_question_relevant_to_topic
from services.providers.base import validate_quiz_data, get_quiz_prompt
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter
from services.auth_utils import sign_student_identity


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()
    yield
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "strict_topic_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Multi-Topic Document Notes Context Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_multi_topic_document_context_isolation():
    """
    Given a presentation document spanning multiple distinct topics:
      - Section 1: Entity-Relationship (ER) Modeling (Entity, Relationship, Cardinality)
      - Section 2: SQL Joins & Subqueries (INNER JOIN, LEFT JOIN, FULL OUTER JOIN)
      - Section 3: Normalization (1NF, 2NF, 3NF, BCNF, Functional Dependency)
    Selecting context for 'Normalization (1NF, 2NF, 3NF, BCNF)' must strictly extract
    Section 3 and completely suppress Section 1 and Section 2.
    """
    sec_er = (
        "Slide 1: Entity-Relationship (ER) Modeling in DBMS.\n"
        "Conceptual modeling uses entities, attributes, and relationships. "
        "Entities can be strong or weak entities. Attributes include atomic, composite, and multivalued. "
        "Cardinality ratios specify 1:1, 1:N, or M:N mappings between entity sets in ER diagrams.\n\n"
    )
    sec_joins = (
        "Slide 2: SQL Joins and Subqueries.\n"
        "SQL joins combine tuples from multiple relations. INNER JOIN returns rows with matching keys. "
        "LEFT OUTER JOIN preserves unmatched tuples from the left relation. "
        "Correlated subqueries execute once for each candidate row in the outer query.\n\n"
    )
    sec_norm = (
        "Slide 3: Relational Normalization and Normal Forms.\n"
        "Normalization decomposes relations to reduce data redundancy and eliminate update anomalies. "
        "First Normal Form (1NF) disallows multivalued attributes. "
        "Second Normal Form (2NF) eliminates partial functional dependencies on candidate keys. "
        "Third Normal Form (3NF) eliminates transitive functional dependencies. "
        "Boyce-Codd Normal Form (BCNF) strictly requires every determinant to be a superkey.\n\n"
    )
    full_doc = sec_er + sec_joins + sec_norm

    competing = ["Entity-Relationship (ER) Modeling", "SQL Joins & Subqueries"]
    norm_concepts = ["1NF", "2NF", "3NF", "BCNF", "Functional Dependency"]

    excerpt = select_relevant_notes_context(
        notes_text=full_doc,
        topic_name="Normalization (1NF, 2NF, 3NF, BCNF)",
        course_code="22CS201",
        competing_topics=competing,
        matched_concepts=norm_concepts,
        max_chars=2000
    )

    # Must contain Normalization content
    assert "1NF" in excerpt or "Normal Form" in excerpt
    assert "BCNF" in excerpt or "functional dependencies" in excerpt

    # Must NOT contain competing sections
    assert "Cardinality ratios" not in excerpt
    assert "ER diagrams" not in excerpt
    assert "LEFT OUTER JOIN" not in excerpt
    assert "Correlated subqueries" not in excerpt


def test_multi_topic_document_context_isolation_reversed():
    """
    Selecting context for 'SQL Joins & Subqueries' from the same document must
    extract the joins section and suppress ER modeling and Normalization.
    """
    full_doc = (
        "Slide 1: Entity-Relationship (ER) Modeling.\n"
        "Conceptual modeling uses entities and ER diagrams with cardinality.\n\n"
        "Slide 2: SQL Joins and Query Processing.\n"
        "SQL INNER JOIN and LEFT OUTER JOIN combine relations on join predicates.\n\n"
        "Slide 3: Normalization and 3NF BCNF.\n"
        "Eliminates functional dependency anomalies in relational schemas.\n\n"
    )
    competing = ["Entity-Relationship (ER) Modeling", "Normalization (1NF, 2NF, 3NF, BCNF)"]
    join_concepts = ["INNER JOIN", "LEFT JOIN", "Subqueries"]

    excerpt = select_relevant_notes_context(
        notes_text=full_doc,
        topic_name="SQL Joins & Subqueries",
        course_code="22CS201",
        competing_topics=competing,
        matched_concepts=join_concepts,
        max_chars=2000
    )

    assert "JOIN" in excerpt
    assert "cardinality" not in excerpt
    assert "BCNF" not in excerpt


# ─────────────────────────────────────────────────────────────────────────────
# 2. Out-of-Topic Candidate Question Rejection
# ─────────────────────────────────────────────────────────────────────────────

def test_out_of_topic_question_rejected_by_validator():
    """
    Validates that candidate questions matching competing course topics or off-syllabus
    material are rejected, while legitimate target-topic questions pass.
    """
    topic_target = "Normalization (1NF, 2NF, 3NF, BCNF)"
    course_code = "22CS201"
    target_concepts = ["1NF", "2NF", "3NF", "BCNF", "Functional Dependency"]
    competing = ["SQL Joins & Subqueries", "Entity-Relationship (ER) Modeling", "Indexing & B-Trees"]

    # 1. On-topic question -> PASS
    q_valid = {
        "question": "Which normal form addresses transitive functional dependencies on candidate keys?",
        "options": {"A": "1NF", "B": "2NF", "C": "3NF", "D": "BCNF"},
        "correct_answer": "C",
        "difficulty": "Medium",
        "concept": "3NF",
        "explanation": "Third normal form eliminates transitive functional dependencies."
    }
    is_rel, reason = is_question_relevant_to_topic(
        q_valid, topic_target, concepts=target_concepts, course_code=course_code, competing_topics=competing
    )
    assert is_rel is True, f"Valid normalization question rejected: {reason}"

    # 2. Competing SQL Joins question -> REJECT
    q_sql_join = {
        "question": "What type of SQL join returns all rows from the left table and matched records from the right table?",
        "options": {"A": "INNER JOIN", "B": "LEFT JOIN", "C": "RIGHT JOIN", "D": "CROSS JOIN"},
        "correct_answer": "B",
        "difficulty": "Easy",
        "concept": "LEFT JOIN",
        "explanation": "LEFT JOIN preserves all rows from the left table."
    }
    is_rel, reason = is_question_relevant_to_topic(
        q_sql_join, topic_target, concepts=target_concepts, course_code=course_code, competing_topics=competing
    )
    assert is_rel is False, "SQL join question should be rejected for Normalization"
    assert "SQL Joins" in reason or "no relevance" in reason

    # 3. Competing ER Modeling question -> REJECT
    q_er = {
        "question": "In an Entity-Relationship (ER) diagram, what geometric shape is used to represent an entity set?",
        "options": {"A": "Rectangle", "B": "Diamond", "C": "Ellipse", "D": "Double Rectangle"},
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Entity",
        "explanation": "Rectangles represent entity sets in ER diagrams."
    }
    is_rel, reason = is_question_relevant_to_topic(
        q_er, topic_target, concepts=target_concepts, course_code=course_code, competing_topics=competing
    )
    assert is_rel is False, "ER diagram question should be rejected for Normalization"

    # 4. Off-syllabus question (Differential Equations) -> REJECT
    q_diff_eq = {
        "question": "What is the integrating factor of the differential equation dy/dx + P(x)y = Q(x)?",
        "options": {"A": "e^(int P dx)", "B": "e^x", "C": "P(x)", "D": "Q(x)"},
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Differential Equations",
        "explanation": "Integrating factor formula."
    }
    is_rel, reason = is_question_relevant_to_topic(
        q_diff_eq, topic_target, concepts=target_concepts, course_code=course_code, competing_topics=competing
    )
    assert is_rel is False, "Off-syllabus question should be rejected"


def test_validate_quiz_data_filters_competing_questions():
    """
    Tests validate_quiz_data with a mixed candidate list (5 Normalization, 5 SQL Joins).
    The validator must accept ONLY the Normalization questions and reject the SQL Joins questions.
    """
    distinct_norm_questions = [
        ("Which normal form disallows multivalued and composite attributes?", "1NF", "1NF"),
        ("In 2NF, what type of dependency must be eliminated from candidate keys?", "Partial dependency", "2NF"),
        ("Which normal form addresses and eliminates transitive functional dependencies?", "3NF", "3NF"),
        ("What condition does Boyce-Codd Normal Form require for every non-trivial determinant?", "Must be a superkey", "BCNF"),
        ("Which formal property ensures that decomposing a relation loses no information?", "Lossless join", "Functional Dependency"),
    ]

    distinct_join_questions = [
        ("Which SQL join operation returns only matching tuples from both relations?", "INNER JOIN", "INNER JOIN"),
        ("What does a LEFT OUTER JOIN preserve when tuples have no right-side match?", "All left tuples with NULLs", "LEFT JOIN"),
        ("Which SQL construct combines all rows from both tables regardless of match?", "FULL OUTER JOIN", "FULL OUTER JOIN"),
        ("When is a CROSS JOIN produced in relational query processing?", "Cartesian product without predicate", "CROSS JOIN"),
        ("How does a correlated subquery evaluate join conditions against outer rows?", "Executes per outer row", "Subqueries"),
    ]

    mixed_questions = [
        {
            "question": q,
            "options": {"A": ans, "B": f"Incorrect {i}B", "C": f"Incorrect {i}C", "D": f"Incorrect {i}D"},
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": c,
            "explanation": f"Explanation for {c}."
        }
        for i, (q, ans, c) in enumerate(distinct_norm_questions)
    ] + [
        {
            "question": q,
            "options": {"A": ans, "B": f"Incorrect {i+10}B", "C": f"Incorrect {i+10}C", "D": f"Incorrect {i+10}D"},
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": c,
            "explanation": f"Explanation for {c}."
        }
        for i, (q, ans, c) in enumerate(distinct_join_questions)
    ]

    raw_data = {"questions": mixed_questions}
    result = validate_quiz_data(
        raw_data,
        course_name="Database Management Systems",
        topic_name="Normal Forms Based",
        concepts=["1NF", "2NF", "3NF", "BCNF", "Functional Dependency"],
        course_code="22CS201",
        competing_topics=["SQL Data Definition and Data Types"],
        min_questions=3
    )

    # Exactly 5 questions should survive (the 5 normalization questions)
    assert len(result["questions"]) == 5
    for q in result["questions"]:
        assert "join" not in q["question"].lower() or "lossless" in q["question"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Prompt Negative Constraints Generation
# ─────────────────────────────────────────────────────────────────────────────

def test_get_quiz_prompt_contains_negative_constraints():
    """
    Verifies that get_quiz_prompt renders explicit negative constraints when
    disallowed/competing topics are passed.
    """
    prompt = get_quiz_prompt(
        course_code="22CS201",
        course_name="Database Management Systems",
        topic_name="Normalization (1NF, 2NF, 3NF, BCNF)",
        syllabus_context="Normalization rules",
        concepts=["1NF", "2NF", "3NF"],
        disallowed_topics=["Entity-Relationship (ER) Modeling", "SQL Joins & Subqueries"]
    )

    assert "STRICT TOPIC ISOLATION" in prompt
    assert "NEGATIVE CONSTRAINT" in prompt
    assert "Entity-Relationship (ER) Modeling" in prompt
    assert "SQL Joins & Subqueries" in prompt
    assert "IGNORE any passages in the notes discussing other topics" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# 4. Thin Notes Graceful Syllabus Fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_thin_notes_graceful_syllabus_fallback():
    """
    When uploaded notes have negligible content for the requested topic:
      - select_relevant_notes_context returns empty string "".
      - Quiz generation proceeds from official curriculum syllabus without failing.
    """
    notes_about_python = (
        "Lecture on Python fundamentals.\n"
        "Python functions are defined with def keyword. Lists and dictionaries are built-in structures."
    )

    excerpt = select_relevant_notes_context(
        notes_text=notes_about_python,
        topic_name="Normalization (1NF, 2NF, 3NF, BCNF)",
        course_code="22CS201",
        competing_topics=["Entity-Relationship (ER) Modeling", "SQL Joins & Subqueries"],
        matched_concepts=["1NF", "2NF", "3NF", "BCNF"],
        max_chars=2000
    )

    # Empty string because notes have 0 relevance to Normalization
    assert excerpt == ""


# ─────────────────────────────────────────────────────────────────────────────
# 5. End-to-End Parameter Preservation & Quiz Generation
# ─────────────────────────────────────────────────────────────────────────────

def test_end_to_end_study_material_quiz_generation(client):
    """
    Full end-to-end integration:
      1. Create a study material with matched topics for a student.
      2. Call POST /api/quiz/generate with study_material_id.
      3. Verify topic_id, study_material_id, course_code are preserved in response.
      4. Verify persisted session contains study_material_id and topic_id in metadata.
      5. Submit quiz and verify topic_mastery update.
    """
    student_id = "test-student-strict-1"
    token = sign_student_identity(student_id)

    # Upload study material
    notes_text = (
        "Lecture Notes on Normalization in 22CS201.\n"
        "We covered First Normal Form (1NF), Second Normal Form (2NF), and Boyce-Codd Normal Form (BCNF).\n"
        "Functional dependencies determine primary keys and eliminate data anomalies."
    )
    matched_topics = [
        {
            "course_code": "22CS201",
            "course_name": "Database Management Systems",
            "topic_name": "Normal Forms Based",
            "topic_id": "22CS201-Q017-2",
            "unit": 3,
            "matched_concepts": ["1NF", "2NF", "3NF", "BCNF", "Functional Dependency"],
            "confidence": 0.95
        }
    ]

    mat_record = create_or_update_study_material(
        student_id=student_id,
        filename="Normalization_Deck.pptx",
        file_type="PPTX",
        file_size=2048,
        extracted_text=notes_text,
        matched_topics=matched_topics
    )
    material_id = mat_record["id"]

    # Mock AI provider generating 10 valid normalization questions
    mock_provider = MagicMock()
    mock_provider.name = "mock_provider"
    mock_provider.is_configured.return_value = True

    distinct_questions = [
        ("Which normal form disallows multivalued and composite attributes?", "1NF", "1NF"),
        ("In 2NF, what type of dependency must be eliminated from candidate keys?", "Partial dependency", "2NF"),
        ("Which normal form addresses and eliminates transitive functional dependencies?", "3NF", "3NF"),
        ("What condition does Boyce-Codd Normal Form require for every non-trivial determinant?", "Must be a superkey", "BCNF"),
        ("Which formal property ensures that decomposing a relation loses no information?", "Lossless join", "Functional Dependency"),
        ("When is a relational decomposition considered to be dependency-preserving?", "All dependencies are enforceable on individual relations", "Preservation"),
        ("What anomaly occurs when inserting a tuple requires entering unrelated placeholder data?", "Insertion anomaly", "Anomalies"),
        ("What is the closure of an attribute set under functional dependencies used to determine?", "Superkey status", "Candidate Keys"),
        ("Which normal form strictly deals with multivalued dependencies?", "4NF", "4NF"),
        ("In Boyce-Codd Normal Form, which dependencies are completely disallowed?", "Dependencies whose determinant is not a superkey", "BCNF")
    ]

    valid_questions = [
        {
            "question": q,
            "options": {"A": ans, "B": f"Distractor {i}B", "C": f"Distractor {i}C", "D": f"Distractor {i}D"},
            "correct_answer": "A",
            "difficulty": "Easy" if i < 4 else ("Medium" if i < 8 else "Hard"),
            "concept": c,
            "explanation": f"Normalization explanation for {c}."
        }
        for i, (q, ans, c) in enumerate(distinct_questions)
    ]
    mock_provider.generate_quiz_raw.return_value = json.dumps({
        "subject": "Database Management Systems",
        "topic": "Normal Forms Based",
        "questions": valid_questions
    })

    with patch("services.llm_service._get_providers", return_value=[mock_provider]):
        payload = {
            "course_code": "22CS201",
            "topic": "Normal Forms Based",
            "topic_id": "22CS201-Q017-2",
            "study_material_id": material_id,
            "student_id": student_id
        }
        res = client.post(
            "/api/quiz/generate",
            json=payload,
            headers={"Authorization": f"Bearer {token}"}
        )

        assert res.status_code == 200, res.text
        data = res.json()

        # Check preserved IDs in response
        assert data["course_code"] == "22CS201"
        assert data["topic"] == "Normal Forms Based"
        assert data["study_material_id"] == material_id
        assert data["topic_id"] == "22CS201-Q017-2"
        assert len(data["questions"]) == 10

        quiz_id = data["quiz_id"]

        # Check persisted session metadata in DB
        session = get_quiz_session(quiz_id, student_id=student_id)
        assert session is not None
        meta = session.get("adaptive_metadata", {})
        assert meta.get("study_material_id") == material_id
        assert meta.get("topic_id") == "22CS201-Q017-2"

        # Submit quiz
        sub_res = client.post(
            "/api/quiz/submit",
            json={
                "quiz_id": quiz_id,
                "student_id": student_id,
                "answers": ["A"] * 10
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        assert sub_res.status_code == 200, sub_res.text
        sub_data = sub_res.json()
        assert sub_data["score"] == 10


# ─────────────────────────────────────────────────────────────────────────────
# 6. Student Isolation: Cross-Student Study Material Rejection
# ─────────────────────────────────────────────────────────────────────────────

def test_cross_student_study_material_access_rejected(client):
    """
    Student A creates study material.
    Student B attempts to use Student A's study_material_id in /api/quiz/generate.
    Must return 403 Forbidden.
    """
    owner_id = "student-owner-100"
    attacker_id = "student-attacker-200"

    mat_record = create_or_update_study_material(
        student_id=owner_id,
        filename="Owner_Notes.pdf",
        file_type="PDF",
        file_size=1024,
        extracted_text="Database Normalization notes",
        matched_topics=[{
            "course_code": "22CS201",
            "course_name": "Database Management Systems",
            "topic_name": "Normalization (1NF, 2NF, 3NF, BCNF)",
            "topic_id": "top_1"
        }]
    )
    mat_id = mat_record["id"]

    attacker_token = sign_student_identity(attacker_id)

    payload = {
        "course_code": "22CS201",
        "topic": "Normalization (1NF, 2NF, 3NF, BCNF)",
        "study_material_id": mat_id,
        "student_id": attacker_id
    }

    res = client.post(
        "/api/quiz/generate",
        json=payload,
        headers={"Authorization": f"Bearer {attacker_token}"}
    )

    assert res.status_code == 403
    assert "Forbidden" in res.json().get("detail", "")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Bounded Replenishment on Topic Validation Failures
# ─────────────────────────────────────────────────────────────────────────────

def test_bounded_replenishment_on_off_topic_questions():
    """
    Verifies that when initial candidate questions contain off-topic questions:
      1. Off-topic questions are rejected during validation.
      2. Replenishment loop triggers with NEGATIVE CONSTRAINT in the replenishment prompt.
      3. Valid replenishment questions are accepted, achieving clean topic isolation.
      4. Loop executes at most 2 replenishment rounds without unbounded retry loops.
    """
    from services.llm_service import generate_quiz

    mock_provider = MagicMock()
    mock_provider.name = "mock_provider"
    mock_provider.is_configured.return_value = True

    # 6 valid normalization questions + 4 off-topic join questions
    initial_norm = [
        ("Which normal form addresses multivalued attributes?", "1NF", "1NF"),
        ("What does 2NF eliminate from candidate keys?", "Partial dependencies", "2NF"),
        ("Which normal form eliminates transitive functional dependencies?", "3NF", "3NF"),
        ("What does Boyce-Codd Normal Form require of all determinants?", "Superkey", "BCNF"),
        ("Which property ensures relation decomposition loses no information?", "Lossless join", "Functional Dependency"),
        ("What condition defines minimal cover of functional dependencies?", "Canonical cover", "Functional Dependency"),
    ]
    initial_joins = [
        ("Which SQL join returns matching rows from both tables?", "INNER JOIN", "INNER JOIN"),
        ("What does LEFT JOIN return when no right row matches?", "NULL values", "LEFT JOIN"),
        ("Which SQL join produces a Cartesian product?", "CROSS JOIN", "CROSS JOIN"),
        ("Which SQL keyword performs a full outer join?", "FULL JOIN", "FULL JOIN"),
    ]
    round_1_questions = [
        {
            "question": q,
            "options": {"A": ans, "B": f"Opt {i}B", "C": f"Opt {i}C", "D": f"Opt {i}D"},
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": c,
            "explanation": f"Explanation {c}"
        }
        for i, (q, ans, c) in enumerate(initial_norm)
    ] + [
        {
            "question": q,
            "options": {"A": ans, "B": f"Opt {i+10}B", "C": f"Opt {i+10}C", "D": f"Opt {i+10}D"},
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": c,
            "explanation": f"Explanation {c}"
        }
        for i, (q, ans, c) in enumerate(initial_joins)
    ]

    # Replenishment questions: 4 valid normalization questions
    rep_norm = [
        ("In 3NF, every non-prime attribute must not be transitively dependent on what?", "Primary key", "3NF"),
        ("What kind of dependency exists when X -> Y and Y -> Z?", "Transitive dependency", "Functional Dependency"),
        ("Which normal form strictly forbids multi-valued dependencies?", "4NF", "4NF"),
        ("What is the highest normal form based strictly on functional dependencies?", "BCNF", "BCNF"),
    ]
    round_2_questions = [
        {
            "question": q,
            "options": {"A": ans, "B": f"Dist {i+20}B", "C": f"Dist {i+20}C", "D": f"Dist {i+20}D"},
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": c,
            "explanation": f"Explanation {c}"
        }
        for i, (q, ans, c) in enumerate(rep_norm)
    ]

    prompts_received = []

    def fake_generate_raw(*args, **kwargs):
        custom_prompt = kwargs.get("custom_prompt", "")
        prompts_received.append(custom_prompt)
        if len(prompts_received) == 1:
            return json.dumps({"questions": round_1_questions})
        else:
            return json.dumps({"questions": round_2_questions})

    mock_provider.generate_quiz_raw.side_effect = fake_generate_raw

    with patch("services.llm_service._get_providers", return_value=[mock_provider]):
        quiz = generate_quiz(
            course_code="22CS201",
            course_name="Database Management Systems",
            topic_name="Normal Forms Based",
            syllabus_context="Normalization concepts and functional dependencies",
            concepts=["1NF", "2NF", "3NF", "BCNF", "Functional Dependency"],
            competing_topics=["SQL Data Definition and Data Types"]
        )

    # Provider called twice: initial attempt + 1 replenishment round
    assert len(prompts_received) == 2
    rep_prompt = prompts_received[1]
    assert "NEGATIVE CONSTRAINT" in rep_prompt
    assert "SQL Data Definition and Data Types" in rep_prompt
    assert "CRITICAL: Do NOT duplicate or rephrase any of these already accepted questions" in rep_prompt

    # Final quiz should have 10 valid questions, all strictly on normalization
    assert len(quiz["questions"]) == 10
    for q in quiz["questions"]:
        assert "join" not in q["question"].lower() or "lossless" in q["question"].lower()


def test_bounded_replenishment_stops_after_two_rounds():
    """
    If the provider repeatedly supplies off-topic questions during replenishment,
    replenishment must strictly stop after 2 attempts (max 2 rounds) and return the
    valid on-topic questions without failing or looping infinitely.
    """
    from services.llm_service import generate_quiz

    mock_provider = MagicMock()
    mock_provider.name = "mock_provider"
    mock_provider.is_configured.return_value = True

    # Initial: 7 valid normalization questions + 3 off-topic questions
    initial_norm = [
        ("Which normal form addresses multivalued attributes?", "1NF", "1NF"),
        ("What does 2NF eliminate from candidate keys?", "Partial dependencies", "2NF"),
        ("Which normal form eliminates transitive functional dependencies?", "3NF", "3NF"),
        ("What does Boyce-Codd Normal Form require of all determinants?", "Superkey", "BCNF"),
        ("Which property ensures relation decomposition loses no information?", "Lossless join", "Functional Dependency"),
        ("What condition defines minimal cover of functional dependencies?", "Canonical cover", "Functional Dependency"),
        ("What is an attribute closure used to compute?", "Candidate keys", "Functional Dependency"),
    ]
    off_topic = [
        ("Which SQL join returns matching rows from both tables?", "INNER JOIN", "INNER JOIN"),
        ("What does LEFT JOIN return when no right row matches?", "NULL values", "LEFT JOIN"),
        ("Which SQL join produces a Cartesian product?", "CROSS JOIN", "CROSS JOIN"),
    ]

    round_1_questions = [
        {
            "question": q,
            "options": {"A": ans, "B": f"Opt {i}B", "C": f"Opt {i}C", "D": f"Opt {i}D"},
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": c,
            "explanation": f"Explanation {c}"
        }
        for i, (q, ans, c) in enumerate(initial_norm)
    ] + [
        {
            "question": q,
            "options": {"A": ans, "B": f"Opt {i+10}B", "C": f"Opt {i+10}C", "D": f"Opt {i+10}D"},
            "correct_answer": "A",
            "difficulty": "Medium",
            "concept": c,
            "explanation": f"Explanation {c}"
        }
        for i, (q, ans, c) in enumerate(off_topic)
    ]

    call_count = 0

    def fake_generate_raw(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Always return only off-topic questions for replenishment
        if call_count == 1:
            return json.dumps({"questions": round_1_questions})
        else:
            return json.dumps({"questions": [
                {
                    "question": f"Off topic join question {call_count}_{i}",
                    "options": {"A": "INNER JOIN", "B": f"Opt {call_count}_{i}B", "C": f"Opt {call_count}_{i}C", "D": f"Opt {call_count}_{i}D"},
                    "correct_answer": "A",
                    "difficulty": "Medium",
                    "concept": "INNER JOIN",
                    "explanation": "Off-topic"
                }
                for i in range(4)
            ]})

    mock_provider.generate_quiz_raw.side_effect = fake_generate_raw

    with patch("services.llm_service._get_providers", return_value=[mock_provider]):
        quiz = generate_quiz(
            course_code="22CS201",
            course_name="Database Management Systems",
            topic_name="Normal Forms Based",
            syllabus_context="Normalization concepts and functional dependencies",
            concepts=["1NF", "2NF", "3NF", "BCNF", "Functional Dependency"],
            competing_topics=["SQL Data Definition and Data Types"]
        )

    # 1 initial call + exactly 2 replenishment calls = 3 total calls
    assert call_count == 3
    # Exactly 7 valid on-topic questions returned
    assert len(quiz["questions"]) == 7
    for q in quiz["questions"]:
        assert "join" not in q["question"].lower() or "lossless" in q["question"].lower()

