"""
Unit and regression tests for Module 2: Novelty Evaluation Architecture Remediation.

Verifies:
1. Same topic, different concepts are accepted as novel.
2. Same concept, genuinely different problem-solving tasks are accepted as novel.
3. Same underlying question with superficial wording changes is detected as duplicate.
4. Same numerical problem with changed numerical values is detected as duplicate.
5. Numerical problems with different mathematical target operations are accepted as novel.
6. Questions with overlapping terminology but different learning objectives are accepted as novel.
7. Syllabus concept tags are NOT independently sufficient evidence that two questions are duplicates.
8. Retake quiz evaluation on the same syllabus topic yields 10/10 novel without redundant replenishment calls.
9. Exact duplicate detection (Level 1) is strictly preserved.
10. Cognitive operation and reasoning skill differentiation.
"""

import pytest
from services.novelty import (
    evaluate_quiz_novelty,
    is_question_near_duplicate,
    extract_cognitive_operation,
    CognitiveOperation,
    are_numerical_problems_identical_structure,
    get_numerical_template,
    extract_numerical_parameters
)


class TestCognitiveOperationAndNumericalTemplate:
    """Verifies reasoning skill classification and numerical problem structure comparison."""

    def test_cognitive_operation_classification(self):
        q_calc = "Calculate the determinant of the 2x2 matrix [[2, 1], [4, 3]]."
        q_def = "What is the primary definition of an artificial neural network?"
        q_prop = "What is the necessary condition for a square matrix A to be invertible?"
        q_comp = "What is the primary difference between L1 regularization and L2 weight decay?"
        q_app = "When applied to an image corrupted with salt-and-pepper noise, why is a median filter preferred?"
        q_proc = "How does backpropagation compute the gradients of the loss with respect to hidden weights?"

        assert extract_cognitive_operation(q_calc) == CognitiveOperation.CALCULATION
        assert extract_cognitive_operation(q_def) == CognitiveOperation.DEFINITION_RECALL
        assert extract_cognitive_operation(q_prop) == CognitiveOperation.THEORETICAL_PROPERTY
        assert extract_cognitive_operation(q_comp) == CognitiveOperation.COMPARISON_CONTRAST
        assert extract_cognitive_operation(q_app) == CognitiveOperation.APPLICATION_SCENARIO
        assert extract_cognitive_operation(q_proc) == CognitiveOperation.PROCESS_MECHANISM

    def test_numerical_template_identical_structure_detected(self):
        q1 = "Calculate the determinant of the 2x2 matrix [[2, 1], [4, 3]]."
        q2 = "Calculate the determinant of the 2x2 matrix [[5, 2], [3, 1]]."

        is_dup, reason = are_numerical_problems_identical_structure(q1, q2)
        assert is_dup is True
        assert "varied numerical values" in reason.lower() or "identical" in reason.lower()

    def test_numerical_template_different_mathematical_operations_not_duplicate(self):
        q1 = "Calculate the determinant of the 2x2 matrix [[2, 1], [4, 3]]."
        q2 = "Calculate the trace of the 2x2 matrix [[2, 1], [4, 3]]."

        # Determinant vs Trace: Different mathematical quantities
        is_dup, _ = is_question_near_duplicate(
            {"question": q1, "concept": "Determinant"},
            {"question": q2, "concept": "Trace"},
            topic_name="Matrices"
        )
        assert is_dup is False


class TestNoveltyDistinctions:
    """Verifies all 5 distinction requirements from the Module 2 specifications."""

    def test_same_topic_different_concepts_accepted(self):
        """Case 1: Same topic, different concepts must be accepted as novel."""
        q1 = {
            "question": "How does a 3x3 median filter reduce impulse noise while preserving sharp boundaries?",
            "concept": "Median Filtering"
        }
        q2 = {
            "question": "What transformation is applied to the CDF in histogram equalization to enhance contrast?",
            "concept": "Histogram Processing"
        }
        is_dup, _ = is_question_near_duplicate(q1, q2, topic_name="Image Enhancement")
        assert is_dup is False

    def test_same_concept_different_problem_solving_tasks_accepted(self):
        """Case 2: Same concept, genuinely different problem-solving tasks must be accepted as novel."""
        q_theory = {
            "question": "What is the necessary condition for a square matrix A to be invertible?",
            "concept": "Matrix Inversion"
        }
        q_calc = {
            "question": "Calculate the determinant of the 2x2 matrix [[2, 1], [4, 3]] to verify if it has an inverse.",
            "concept": "Matrix Inversion"
        }
        is_dup, _ = is_question_near_duplicate(q_theory, q_calc, topic_name="Matrices and Determinants")
        assert is_dup is False, "Theoretical property and calculation on same concept must not be flagged as duplicates"

    def test_superficial_wording_changes_detected_as_duplicate(self):
        """Case 3: Same underlying question with superficial wording changes must be detected as duplicate."""
        q_orig = {
            "question": "What is the primary function of the McCulloch-Pitts neuron?",
            "concept": "Purpose of MP Neuron"
        }
        q_reworded = {
            "question": "What is the main purpose of the McCulloch-Pitts neuron?",
            "concept": "Purpose of MP Neuron"
        }
        is_dup, reason = is_question_near_duplicate(q_orig, q_reworded, topic_name="McCulloch-Pitts Neuron")
        assert is_dup is True
        assert "paraphrasing" in reason.lower() or "near-duplicate" in reason.lower() or "objective" in reason.lower()

    def test_same_numerical_problem_with_changed_values_detected_as_duplicate(self):
        """Case 4: Same numerical problem with changed numerical values must be detected as duplicate."""
        q1 = {
            "question": "Find the determinant of the 2x2 matrix [[2, 1], [4, 3]].",
            "concept": "Determinant Calculation"
        }
        q2 = {
            "question": "Find the determinant of the 2x2 matrix [[5, 2], [3, 1]].",
            "concept": "Determinant Calculation"
        }
        is_dup, reason = is_question_near_duplicate(q1, q2, topic_name="Matrices")
        assert is_dup is True
        assert "numerical" in reason.lower() or "template" in reason.lower()

    def test_overlapping_terminology_different_learning_objectives_accepted(self):
        """Case 5: Questions with overlapping terminology but different learning objectives must be accepted as novel."""
        q_eigen = {
            "question": "Which condition guarantees that a square matrix A has linearly independent eigenvectors?",
            "concept": "Diagonalizability"
        }
        q_ortho = {
            "question": "If matrix A represents an orthogonal transformation, what is the value of det(A)?",
            "concept": "Orthogonal Matrices"
        }
        is_dup, _ = is_question_near_duplicate(q_eigen, q_ortho, topic_name="Matrices")
        assert is_dup is False, "Distinct mathematical theorems with shared matrix vocabulary must be novel"


class TestSyllabusConceptIndependence:
    """Verifies Requirement 6: Syllabus concept tags are NOT independently sufficient evidence of duplication."""

    def test_syllabus_concept_tags_alone_do_not_flag_duplicates(self):
        # Student attempted Question 1 in Attempt 1
        history_fps = [
            "What is the mathematical condition for a matrix to be orthogonal?"
        ]
        history_concepts = ["Matrix Properties"]
        history_questions = [
            {
                "question": "What is the mathematical condition for a matrix to be orthogonal?",
                "concept": "Matrix Properties"
            }
        ]

        # New question in Retake Attempt 2 has the SAME syllabus concept tag "Matrix Properties",
        # but tests a completely distinct property (Symmetric matrices / real eigenvalues).
        retake_question = {
            "question": "Which statement is true regarding the eigenvalues of any real symmetric matrix?",
            "options": {
                "A": "All eigenvalues are strictly real",
                "B": "All eigenvalues must be complex conjugates",
                "C": "All eigenvalues are zero",
                "D": "Eigenvalues cannot be determined"
            },
            "correct_answer": "A",
            "concept": "Matrix Properties"
        }

        res = evaluate_quiz_novelty(
            questions=[retake_question],
            history_fingerprints=history_fps,
            history_concepts=history_concepts,
            history_questions=history_questions,
            topic_name="Matrices"
        )

        assert res["novel_count"] == 1
        assert res["is_fully_novel"] is True
        assert len(res["all_flagged_indices"]) == 0

    def test_exact_duplicate_in_retake_is_strictly_rejected(self):
        """Verifies Level 1 exact duplicate rejection is strictly preserved."""
        history_fps = ["What is the primary function of the McCulloch-Pitts neuron?"]
        history_concepts = ["MP Neuron Purpose"]

        candidate = {
            "question": "What is the primary function of the McCulloch-Pitts neuron?",
            "concept": "MP Neuron Purpose"
        }

        res = evaluate_quiz_novelty(
            questions=[candidate],
            history_fingerprints=history_fps,
            history_concepts=history_concepts,
            history_questions=[candidate],
            topic_name="McCulloch-Pitts Neuron"
        )
        assert res["is_fully_novel"] is False
        assert res["novel_count"] == 0
        assert 0 in res["text_duplicate_indices"]


class TestCompleteRetakeQuizEvaluation:
    """Simulates realistic 10-question retake quiz against complete 10-question prior attempt."""

    def test_realistic_10_question_retake_yields_full_novelty(self):
        """
        When 10 fresh questions are generated for a retake covering the authorized syllabus concepts,
        all 10 are recognized as novel, eliminating redundant regeneration loops.
        """
        topic = "Image Enhancement"
        authorized_concepts = ["Spatial Filtering", "Histogram Processing", "Frequency Domain Enhancement"]

        # Attempt 1 (History): 10 questions testing these concepts
        history_questions = [
            {"question": f"Attempt 1 historical question on {authorized_concepts[i % 3]} testing theory facet {i}?", "concept": authorized_concepts[i % 3]}
            for i in range(10)
        ]
        history_fps = [q["question"] for q in history_questions]
        history_concepts = list(set(authorized_concepts))

        # Attempt 2 (Retake Candidates): 10 brand-new questions on the SAME authorized concepts
        retake_candidates = [
            {
                "question": f"Attempt 2 fresh applied problem on {authorized_concepts[j % 3]} with specific neighborhood calculation {j+100}?",
                "options": {"A": "Choice A", "B": "Choice B", "C": "Choice C", "D": "Choice D"},
                "correct_answer": "A",
                "concept": authorized_concepts[j % 3]
            }
            for j in range(10)
        ]

        res = evaluate_quiz_novelty(
            questions=retake_candidates,
            history_fingerprints=history_fps,
            history_concepts=history_concepts,
            history_questions=history_questions,
            topic_name=topic
        )

        assert res["novel_count"] == 10
        assert res["is_fully_novel"] is True
        assert len(res["all_flagged_indices"]) == 0
        assert len(res["duplicate_reasons"]) == 0
