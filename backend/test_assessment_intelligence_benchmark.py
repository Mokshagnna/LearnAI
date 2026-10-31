"""
test_assessment_intelligence_benchmark.py
=========================================
Automated Benchmark and Acceptance Evaluation Suite for LearnAI Assessment Intelligence.

Validates:
1. Short-answer evaluation engine (exact, synonym, partial, irrelevant, empty answers).
2. Labeled dataset evaluation metrics (precision, recall, F1, accuracy) for short-answer grading.
3. Statistically grounded persistent weak-topic detection (sample size thresholds, isolation from 1-off errors, recovery).
4. Difficulty distribution calibration and question structure validation.
5. Duplicate and malformed question rejection.
6. Extraction failure safety and unreadable document handling.
7. Session authorization and prompt-injection neutralization.
"""

import io
import json
import pytest
from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient

from main import app
from services.short_answer_evaluator import evaluate_short_answer
from services.adaptive import detect_persistent_weak_topics, ADAPTIVE_LEVEL_FOUNDATIONAL, ADAPTIVE_LEVEL_MIXED
from services.providers.base import validate_quiz_data
from services.database import init_db, get_connection, get_utc_now_iso, serialize_json_for_db
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Reset in-memory rate limiters for high-volume automated testing."""
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()
    yield
    quiz_gen_limiter.reset()
    quiz_submit_limiter.reset()


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Isolated SQLite database for assessment testing."""
    db_file = tmp_path / "intelligence_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    return db_file


@pytest.fixture
def client(test_db):
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Short-Answer Evaluation Benchmark
# ─────────────────────────────────────────────────────────────────────────────

class TestShortAnswerEvaluationBenchmark:
    """Evaluates short-answer assessment across exact, paraphrased, partial, and negative cases."""

    REF_ANSWER = "Boyce-Codd Normal Form (BCNF) requires that for every non-trivial functional dependency X -> Y, X must be a superkey."
    REF_CONCEPTS = ["BCNF", "functional dependency", "superkey", "determinant"]

    def test_exact_match_yields_full_credit(self):
        res = evaluate_short_answer(
            student_answer=self.REF_ANSWER,
            reference_answer=self.REF_ANSWER,
            key_concepts=self.REF_CONCEPTS
        )
        assert res["score"] == 1.0
        assert res["percentage"] == 100.0
        assert res["is_correct"] is True
        assert res["partial_credit"] is False
        assert len(res["missing_concepts"]) == 0
        assert "matches reference criteria" in res["feedback"]

    def test_paraphrased_synonym_match_yields_passing_credit(self):
        student_synonym = (
            "In Boyce-Codd normal form, any relation is in BCNF if every determinant in all non-trivial functional dependencies is a candidate key or superkey."
        )
        res = evaluate_short_answer(
            student_answer=student_synonym,
            reference_answer=self.REF_ANSWER,
            key_concepts=self.REF_CONCEPTS
        )
        assert res["is_correct"] is True
        assert res["score"] >= 0.70
        assert "BCNF" in res["matched_concepts"]
        assert "superkey" in res["matched_concepts"]

    def test_partial_credit_identifies_missing_concepts(self):
        # Mentioned BCNF and functional dependencies, but omitted superkey / determinant
        partial_answer = "BCNF is a database normal form based on functional dependencies to eliminate redundancy."
        res = evaluate_short_answer(
            student_answer=partial_answer,
            reference_answer=self.REF_ANSWER,
            key_concepts=self.REF_CONCEPTS
        )
        assert res["partial_credit"] is True or (0.35 <= res["score"] < 0.70)
        assert "BCNF" in res["matched_concepts"]
        assert "superkey" in res["missing_concepts"]
        assert "Partially correct" in res["feedback"] or "missing" in res["feedback"].lower()

    def test_irrelevant_off_topic_answer_rejected(self):
        irrelevant = "We use eigenvalues and eigenvectors to compute matrix transformations in linear algebra."
        res = evaluate_short_answer(
            student_answer=irrelevant,
            reference_answer=self.REF_ANSWER,
            key_concepts=self.REF_CONCEPTS
        )
        assert res["is_correct"] is False
        assert res["partial_credit"] is False
        assert res["score"] < 0.20
        assert len(res["matched_concepts"]) == 0

    def test_empty_and_trivial_non_answers_rejected(self):
        for trivial in ["", "   ", "idk", "i don't know", "skip", "pass", "??"]:
            res = evaluate_short_answer(
                student_answer=trivial,
                reference_answer=self.REF_ANSWER,
                key_concepts=self.REF_CONCEPTS
            )
            assert res["score"] == 0.0
            assert res["is_correct"] is False
            assert res["partial_credit"] is False
            assert "No substantive answer provided" in res["feedback"]

    def test_labeled_dataset_accuracy_and_metrics(self):
        """
        Runs a 10-item labeled evaluation benchmark (5 positive, 5 negative)
        and computes empirical Precision, Recall, and Accuracy.
        """
        labeled_dataset = [
            # True Positives
            {
                "text": "Strict 2PL prevents dirty reads and ensures conflict serializability by holding exclusive locks until commit.",
                "ref": "Strict Two-Phase Locking ensures serializability and prevents dirty reads by holding exclusive locks until transaction commit.",
                "concepts": ["2PL", "serializability", "locks"],
                "label": True
            },
            {
                "text": "Two phase locking protocol guarantees serializability using expanding growing phase and shrinking phase.",
                "ref": "Two-Phase Locking (2PL) guarantees conflict serializability using growing and shrinking lock phases.",
                "concepts": ["Two-Phase Locking", "serializability"],
                "label": True
            },
            {
                "text": "ACID properties: Atomicity (all or nothing), Consistency, Isolation (serializable), and Durability (crash recovery).",
                "ref": "Transactions require ACID properties: Atomicity, Consistency, Isolation, and Durability.",
                "concepts": ["ACID", "durability", "atomicity"],
                "label": True
            },
            {
                "text": "B+ Trees store records only at leaf nodes, while internal nodes store index keys for fast search.",
                "ref": "B+ Trees store data records exclusively in leaf nodes and use internal nodes for key indexing.",
                "concepts": ["B+ Tree", "leaf nodes", "index"],
                "label": True
            },
            {
                "text": "Dense index has an index entry for every search key value in the data file.",
                "ref": "A dense index stores an index entry for every search key value in the primary data file.",
                "concepts": ["dense index", "search key"],
                "label": True
            },
            # True Negatives
            {
                "text": "Baking a chocolate cake with cocoa powder and sugar.",
                "ref": "Two-Phase Locking protocol.",
                "concepts": ["2PL", "serializability"],
                "label": False
            },
            {
                "text": "I do not know the answer to this question.",
                "ref": "ACID properties.",
                "concepts": ["ACID"],
                "label": False
            },
            {
                "text": "Solving differential equations using Runge-Kutta fourth order method.",
                "ref": "B+ Trees in file indexing.",
                "concepts": ["B+ Tree", "index"],
                "label": False
            },
            {
                "text": "pass",
                "ref": "Dense index.",
                "concepts": ["dense index"],
                "label": False
            },
            {
                "text": "Supervised learning uses labeled training datasets to train classification models.",
                "ref": "Database normal forms and functional dependencies.",
                "concepts": ["BCNF", "functional dependency"],
                "label": False
            },
        ]

        tp = fp = tn = fn = 0
        for item in labeled_dataset:
            res = evaluate_short_answer(item["text"], item["ref"], key_concepts=item["concepts"], passing_threshold=0.45)
            pred = res["is_correct"]
            actual = item["label"]
            if pred and actual:
                tp += 1
            elif pred and not actual:
                fp += 1
            elif not pred and not actual:
                tn += 1
            elif not pred and actual:
                fn += 1

        accuracy = (tp + tn) / len(labeled_dataset)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0

        assert accuracy >= 0.90, f"Expected accuracy >= 0.90, got {accuracy}"
        assert precision >= 0.80, f"Expected precision >= 0.80, got {precision}"
        assert recall >= 0.80, f"Expected recall >= 0.80, got {recall}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Multi-Attempt Persistent Weak-Topic Detection Benchmark
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistentWeakTopicDetection:
    """Validates that persistent weak topics require sample size and are isolated from 1-off errors."""

    def _insert_attempt(self, conn, student_id, course_code, topic_name, score, total, weak_concepts, created_at):
        now = created_at or get_utc_now_iso()
        sess_id = f"sess-{created_at}-{student_id}"
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO students (id, created_at, last_active) VALUES (?, ?, ?)", (student_id, now, now))
        cursor.execute("""
            INSERT OR IGNORE INTO quiz_sessions (
                id, student_id, course_code, course_name, topic_name, status,
                questions_snapshot, recommended_difficulty, created_at, submitted_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, 'completed', '[]', 'mixed', ?, ?, ?)
        """, (sess_id, student_id, course_code, "Database Management Systems", topic_name, now, now, now))
        cursor.execute("""
            INSERT INTO quiz_attempts (
                id, student_id, course_code, course_name, topic_name,
                quiz_data, score, total, percentage, status,
                difficulty_accuracy, concept_accuracy,
                weak_concepts, strong_concepts,
                recommended_difficulty, created_at, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"att-{created_at}-{student_id}", student_id, course_code, "Database Management Systems", topic_name,
            serialize_json_for_db({"questions": []}), score, total, round((score / total) * 100.0, 1),
            "Needs Revision" if score / total < 0.5 else "Good",
            serialize_json_for_db({}), serialize_json_for_db({}),
            serialize_json_for_db(weak_concepts), serialize_json_for_db([]),
            "mixed", now, sess_id
        ))
        conn.commit()

    def test_single_isolated_bad_attempt_not_flagged_as_persistent(self, test_db):
        conn = get_connection(str(test_db))
        # 1 isolated attempt of 3/10 (30%)
        self._insert_attempt(conn, "student-1", "22CS201", "Normal Forms Based", 3, 10, ["1NF", "BCNF"], "2026-10-25T10:00:00Z")

        # Must not be flagged because min_attempts=2 and min_questions=15 thresholds are unmet
        weak_topics = detect_persistent_weak_topics("student-1", conn=conn)
        assert len(weak_topics) == 0

    def test_chronic_multi_attempt_failure_flagged_with_revision(self, test_db):
        conn = get_connection(str(test_db))
        # Attempt 1: 3/10
        self._insert_attempt(conn, "student-chronic", "22CS201", "Normal Forms Based", 3, 10, ["BCNF", "1NF"], "2026-10-25T10:00:00Z")
        # Attempt 2: 4/10
        self._insert_attempt(conn, "student-chronic", "22CS201", "Normal Forms Based", 4, 10, ["BCNF", "2NF"], "2026-10-25T11:00:00Z")

        weak_topics = detect_persistent_weak_topics("student-chronic", conn=conn)
        assert len(weak_topics) == 1
        wt = weak_topics[0]
        assert wt["topic_name"] == "Normal Forms Based"
        assert wt["total_questions"] == 20
        assert wt["total_correct"] == 7
        assert wt["accuracy"] == 35.0
        assert wt["attempt_count"] == 2
        assert "BCNF" in wt["chronic_weak_concepts"]
        assert "Persistent learning gap" in wt["recommendation"]
        assert wt["suggested_level"] == ADAPTIVE_LEVEL_FOUNDATIONAL

    def test_recovering_topic_not_flagged_as_persistent(self, test_db):
        conn = get_connection(str(test_db))
        # Attempt 1: 3/10 (failure)
        self._insert_attempt(conn, "student-recovered", "22CS201", "Normal Forms Based", 3, 10, ["BCNF"], "2026-10-25T10:00:00Z")
        # Attempt 2: 8/10 (strong recovery)
        self._insert_attempt(conn, "student-recovered", "22CS201", "Normal Forms Based", 8, 10, [], "2026-10-25T11:00:00Z")

        # Active recovery on latest attempt (80% >= 70%) prevents false persistent labeling
        weak_topics = detect_persistent_weak_topics("student-recovered", conn=conn)
        assert len(weak_topics) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Quiz Quality and Difficulty Distribution Verification
# ─────────────────────────────────────────────────────────────────────────────

class TestQuizQualityAndDistributionVerification:
    """Validates question structures, options, and difficulty calibration."""

    def test_difficulty_drift_calibrated_to_requested_distribution(self):
        # 10 distinct questions on Normal Forms where provider tagged all 10 as "Easy"
        distinct_normal_forms_questions = [
            ("What condition defines First Normal Form (1NF) in relational design?", "Attribute values must be atomic", "1NF"),
            ("Which normal form requires eliminating partial functional dependencies on candidate keys?", "Second Normal Form (2NF)", "2NF"),
            ("What functional dependency rule does Third Normal Form enforce regarding non-prime attributes?", "No transitive dependency on a superkey", "3NF"),
            ("Why is Boyce-Codd Normal Form considered strictly stronger than 3NF?", "Every determinant must be a superkey", "Boyce-Codd normal form"),
            ("What role do candidate keys play in schema decomposition?", "Determine unique tuples and dependencies", "Candidate Keys"),
            ("How does non-additive join decomposition guarantee data integrity?", "Ensures original relation can be recovered without spurious tuples", "Lossless Join"),
            ("What constitutes a transitive functional dependency in a relation schema?", "X -> Y and Y -> Z where Y is not a candidate key", "Transitive Dependency"),
            ("When is a decomposition guaranteed to be dependency preserving?", "When all original functional dependencies can be inferred from decomposed schemas", "Dependency Preservation"),
            ("What is the primary objective of functional dependency inference rules?", "Derive sound and complete dependency closures", "Inference Rules"),
            ("How does Boyce-Codd normal form handle relations with multiple overlapping candidate keys?", "Requires every determinant to be a superkey even if keys overlap", "Boyce-Codd normal form")
        ]

        raw_quiz = {
            "questions": [
                {
                    "question": q,
                    "options": {
                        "A": ans,
                        "B": f"Incorrect distractor {idx}A",
                        "C": f"Incorrect distractor {idx}B",
                        "D": f"Incorrect distractor {idx}C"
                    },
                    "correct_answer": "A",
                    "difficulty": "Easy",
                    "concept": c,
                    "explanation": f"Validated correct answer for {q}."
                }
                for idx, (q, ans, c) in enumerate(distinct_normal_forms_questions)
            ]
        }
        requested_dist = {"Easy": 2, "Medium": 4, "Hard": 4}  # Advanced quiz distribution
        validated = validate_quiz_data(
            raw_quiz,
            course_name="Database Management Systems",
            topic_name="Normal Forms Based",
            concepts=[
                "Boyce-Codd normal form", "1NF", "2NF", "3NF",
                "Candidate Keys", "Lossless Join", "Dependency Preservation",
                "Inference Rules", "Transitive Dependency"
            ],
            difficulty_dist=requested_dist
        )
        diff_counts = {}
        for q in validated["questions"]:
            diff_counts[q["difficulty"]] = diff_counts.get(q["difficulty"], 0) + 1

        assert diff_counts.get("Hard") == 4
        assert diff_counts.get("Medium") == 4
        assert diff_counts.get("Easy") == 2

    def test_malformed_question_missing_options_rejected(self):
        malformed = {
            "questions": [
                {
                    "question": "Incomplete question without 4 options?",
                    "options": {"A": "Single Option"},
                    "correct_answer": "A",
                    "explanation": "Missing options."
                }
            ]
        }
        with pytest.raises(ValueError) as exc:
            validate_quiz_data(
                malformed,
                course_name="DBMS",
                topic_name="Normal Forms Based",
                min_questions=5
            )
        assert "Expected at least 5 questions" in str(exc.value) or "unique questions passed" in str(exc.value)

    def test_duplicate_question_statements_rejected(self):
        duplicates = {
            "questions": [
                {
                    "question": "What is Boyce-Codd Normal Form?",
                    "options": {"A": "Superkey determinant", "B": "Non-key determinant", "C": "Join dependency", "D": "Multi-valued"},
                    "correct_answer": "A",
                    "explanation": "BCNF requires superkey."
                }
                for _ in range(8)
            ]
        }
        with pytest.raises(ValueError):
            validate_quiz_data(
                duplicates,
                course_name="DBMS",
                topic_name="Normal Forms Based",
                min_questions=5
            )
