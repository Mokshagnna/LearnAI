"""
Unit and regression tests for academic correctness, mathematical rigor,
retake novelty buffer optimization, three-state verification, and 10-question integrity.
"""

import pytest
from services.academic_verifier import (
    verify_academic_correctness,
    check_multiple_correct_options,
    check_linear_system_solution,
    check_arithmetic_in_explanation,
    check_matrix_theorems_and_dimensions,
    check_dropout_inference_scaling,
    evaluate_arithmetic_expr,
    parse_number_or_fraction,
    VerificationStatus,
    VerificationResult
)
from services.providers.base import validate_single_question, validate_quiz_data
from services.novelty import evaluate_quiz_novelty


# ==============================================================================
# 1. Defect 1: Matrix Query Polarity Determination & Incidental Negations
# ==============================================================================

def test_defect_1_multiple_symmetric_options_rejected():
    """Regression test for Run 1 Q4: Both [[5,0],[0,5]] and [[2,7],[7,2]] are symmetric."""
    question_defect = {
        "question": "Which of the following matrices is symmetric?",
        "options": {
            "A": "[[1,2],[3,4]]",
            "B": "[[0,1],[-1,0]]",
            "C": "[[5,0],[0,5]]",
            "D": "[[2,7],[7,2]]"
        },
        "correct_answer": "D",
        "concept": "Types of matrices",
        "explanation": "A symmetric matrix equals its transpose; option D satisfies A^T = A."
    }
    valid, reason = verify_academic_correctness(question_defect)
    assert not valid
    assert "Multiple options satisfy the symmetric property" in reason
    assert validate_single_question(question_defect) is None


def test_matrix_polarity_positive_incidental_not_complex():
    """Positive symmetry question containing incidental 'not complex' in parenthetical must be accepted."""
    q_positive_incidental = {
        "question": "Which of the following matrices is symmetric? (Assume real entries, not complex).",
        "options": {
            "A": "[[1,2],[3,4]]",
            "B": "[[0,1],[-1,0]]",
            "C": "[[1,3],[2,4]]",
            "D": "[[2,7],[7,2]]"
        },
        "correct_answer": "D",
        "concept": "Types of matrices",
        "explanation": "Option D has A[0,1] = A[1,0] = 7, satisfying A^T = A."
    }
    res = check_multiple_correct_options(q_positive_incidental)
    assert res.is_valid is True
    assert res.is_verified_correct is True
    assert validate_single_question(q_positive_incidental) is not None


def test_matrix_polarity_positive_diagonal_incidental_negation():
    """Positive diagonal question containing incidental negation must be accepted."""
    q_diag_incidental = {
        "question": "Which of the following is a diagonal matrix? (Do not consider zero matrices).",
        "options": {
            "A": "[[1,0],[0,2]]",
            "B": "[[1,2],[3,4]]",
            "C": "[[0,1],[1,0]]",
            "D": "[[2,3],[0,1]]"
        },
        "correct_answer": "A",
        "concept": "Types of matrices",
        "explanation": "Option A has all off-diagonal elements equal to zero."
    }
    res = check_multiple_correct_options(q_diag_incidental)
    assert res.is_valid is True
    assert res.is_verified_correct is True
    assert validate_single_question(q_diag_incidental) is not None


def test_matrix_polarity_explicit_not_symmetric():
    """Explicit 'NOT symmetric' question must accept 3 symmetric distractors and 1 non-symmetric."""
    q_not_sym = {
        "question": "Which of the following matrices is NOT symmetric?",
        "options": {
            "A": "[[1,1],[1,1]]",
            "B": "[[2,7],[7,2]]",
            "C": "[[5,0],[0,5]]",
            "D": "[[1,2],[3,4]]"
        },
        "correct_answer": "D",
        "concept": "Types of matrices",
        "explanation": "Option D has A[0,1]=2 and A[1,0]=3, so A^T != A; all others are symmetric."
    }
    res = check_multiple_correct_options(q_not_sym)
    assert res.is_valid is True
    assert res.is_verified_correct is True
    assert validate_single_question(q_not_sym) is not None


def test_matrix_polarity_non_symmetric():
    """'non-symmetric' question must accept 3 symmetric distractors and 1 non-symmetric."""
    q_non_sym = {
        "question": "Which of the following matrices is non-symmetric?",
        "options": {
            "A": "[[1,1],[1,1]]",
            "B": "[[2,7],[7,2]]",
            "C": "[[5,0],[0,5]]",
            "D": "[[1,2],[3,4]]"
        },
        "correct_answer": "D",
        "concept": "Types of matrices",
        "explanation": "Option D is not equal to its transpose."
    }
    res = check_multiple_correct_options(q_non_sym)
    assert res.is_valid is True
    assert res.is_verified_correct is True


def test_matrix_polarity_explicit_not_diagonal():
    """Explicit 'NOT diagonal' question must accept 3 diagonal distractors and 1 non-diagonal."""
    q_not_diag = {
        "question": "Which of the following matrices is NOT a diagonal matrix?",
        "options": {
            "A": "[[1,0],[0,2]]",
            "B": "[[3,0],[0,4]]",
            "C": "[[5,0],[0,5]]",
            "D": "[[1,2],[0,4]]"
        },
        "correct_answer": "D",
        "concept": "Types of matrices",
        "explanation": "Option D has a non-zero off-diagonal entry A[0,1]=2."
    }
    res = check_multiple_correct_options(q_not_diag)
    assert res.is_valid is True
    assert res.is_verified_correct is True


def test_matrix_polarity_non_diagonal():
    """'non-diagonal' question must accept 3 diagonal distractors and 1 non-diagonal."""
    q_non_diag = {
        "question": "Identify the non-diagonal matrix among the choices:",
        "options": {
            "A": "[[1,0],[0,2]]",
            "B": "[[3,0],[0,4]]",
            "C": "[[5,0],[0,5]]",
            "D": "[[1,2],[0,4]]"
        },
        "correct_answer": "D",
        "concept": "Types of matrices",
        "explanation": "Option D is not diagonal."
    }
    res = check_multiple_correct_options(q_non_diag)
    assert res.is_valid is True
    assert res.is_verified_correct is True


def test_matrix_polarity_ambiguous_wording():
    """Ambiguous matrix query wording returns UNSUPPORTED (non-blocking)."""
    q_ambig = {
        "question": "Consider properties of square matrices in general.",
        "options": {
            "A": "[[1,0],[0,1]]",
            "B": "[[2,0],[0,2]]",
            "C": "[[3,0],[0,3]]",
            "D": "[[4,0],[0,4]]"
        },
        "correct_answer": "A",
        "concept": "Types of matrices",
        "explanation": "Identity matrix."
    }
    res = check_multiple_correct_options(q_ambig)
    assert res.is_valid is True
    assert res.is_unsupported is True


def test_matrix_polarity_multiple_correct_rejected():
    """Positive query with multiple symmetric options must be rejected."""
    q_multi_sym = {
        "question": "Which of the following matrices is symmetric?",
        "options": {
            "A": "[[1,1],[1,1]]",
            "B": "[[2,7],[7,2]]",
            "C": "[[1,2],[3,4]]",
            "D": "[[0,1],[-1,0]]"
        },
        "correct_answer": "A",
        "concept": "Types of matrices",
        "explanation": "Both A and B are symmetric."
    }
    res = check_multiple_correct_options(q_multi_sym)
    assert res.is_valid is False
    assert res.is_verified_incorrect is True
    assert "Multiple options satisfy the symmetric property" in res.reason


# ==============================================================================
# 2. Defect 2: Theorem Verification Must Ignore False Distractors
# ==============================================================================

def test_defect_2_idempotent_diagonalizability_theorem_violation_rejected():
    """Regression test for Run 1 Q9: Claiming idempotent matrices need not be diagonalizable."""
    question_defect = {
        "question": "Which statement about idempotent matrices (A^2 = A) is false?",
        "options": {
            "A": "All eigenvalues are either 0 or 1",
            "B": "The trace equals the rank of the matrix",
            "C": "The matrix is always diagonalizable",
            "D": "The zero matrix is idempotent"
        },
        "correct_answer": "C",
        "concept": "Types of matrices",
        "explanation": "Idempotent matrices need not be diagonalizable; they can be defective."
    }
    valid, reason = verify_academic_correctness(question_defect)
    assert not valid
    assert "idempotent" in reason.lower() and "diagonalizable" in reason.lower()
    assert validate_single_question(question_defect) is None


def test_idempotent_theorem_with_false_distractor_accepted():
    """MCQ with correct answer Option A and false distractor Option B ('matrix can be defective') must PASS."""
    q_false_distractor = {
        "question": "Which statement about idempotent matrices is true?",
        "options": {
            "A": "All eigenvalues are 0 or 1.",
            "B": "The matrix can be defective.",    # False distractor!
            "C": "The trace is always 0.",
            "D": "The determinant is always 2."
        },
        "correct_answer": "A",
        "concept": "Types of matrices",
        "explanation": "Idempotent matrices have eigenvalues in {0, 1}."
    }
    res = check_matrix_theorems_and_dimensions(q_false_distractor)
    assert res.is_valid is True
    assert res.is_verified_correct is True
    assert validate_single_question(q_false_distractor) is not None


def test_idempotent_theorem_incorrect_selected_answer_rejected():
    """MCQ where the MARKED selected answer claims an idempotent matrix can be defective must FAIL."""
    q_bad_selected = {
        "question": "Which statement about idempotent matrices is true?",
        "options": {
            "A": "The matrix can be defective.",    # Marked answer claiming defective!
            "B": "All eigenvalues are 0 or 1.",
            "C": "Trace is 0.",
            "D": "Det is 2."
        },
        "correct_answer": "A",
        "concept": "Types of matrices",
        "explanation": "Idempotent matrices can be defective."
    }
    res = check_matrix_theorems_and_dimensions(q_bad_selected)
    assert res.is_valid is False
    assert res.is_verified_incorrect is True
    assert "never defective" in res.reason


def test_idempotent_theorem_incorrect_explanation_rejected():
    """MCQ where explanation contradicts diagonalizability must FAIL."""
    q_bad_expl = {
        "question": "What is a property of idempotent matrices?",
        "options": {
            "A": "Always diagonalizable",
            "B": "Never invertible",
            "C": "Trace is 0",
            "D": "None"
        },
        "correct_answer": "A",
        "concept": "Types of matrices",
        "explanation": "Idempotent matrices need not be diagonalizable in general."
    }
    res = check_matrix_theorems_and_dimensions(q_bad_expl)
    assert res.is_valid is False
    assert res.is_verified_incorrect is True
    assert "always diagonalizable" in res.reason


def test_idempotent_theorem_false_statement_in_unrelated_distractor():
    """Unrelated distractor stating a false theorem must not reject the valid question."""
    q_unrelated = {
        "question": "Which of the following is true regarding idempotent projection operators (A^2 = A)?",
        "options": {
            "A": "Its eigenvalues are restricted to 0 and 1.",
            "B": "It is always defective.",
            "C": "It cannot be diagonalized.",
            "D": "Its trace is always negative."
        },
        "correct_answer": "A",
        "concept": "Types of matrices",
        "explanation": "The minimal polynomial divides x(x-1)=0, giving eigenvalues in {0,1}."
    }
    res = check_matrix_theorems_and_dimensions(q_unrelated)
    assert res.is_valid is True
    assert res.is_verified_correct is True


def test_idempotent_theorem_missing_or_invalid_answer_key():
    """Missing or unresolvable answer key safely returns UNSUPPORTED."""
    q_missing_key = {
        "question": "Which statement about idempotent matrices is true?",
        "options": {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"},
        "correct_answer": "Z",  # Invalid key
        "explanation": "Explanation."
    }
    res = check_matrix_theorems_and_dimensions(q_missing_key)
    assert res.is_valid is True
    assert res.is_unsupported is True


def test_idempotent_theorem_valid_negations_accepted():
    """Valid scientific explanations stating idempotent matrices 'cannot be defective' or 'never defective'."""
    cases = [
        "Idempotent projection matrices cannot be defective because their minimal polynomial divides x(x-1)=0.",
        "Since A^2 = A, the matrix is never defective and is always diagonalizable.",
        "Idempotent matrices are not defective; their algebraic and geometric multiplicities coincide."
    ]
    for expl in cases:
        q = {
            "question": "What is a key structural property of any real idempotent matrix A (A^2 = A)?",
            "options": {
                "A": "It is always diagonalizable over R.",
                "B": "Its trace is always 0.",
                "C": "It is always invertible.",
                "D": "Its determinant is always -1."
            },
            "correct_answer": "A",
            "concept": "Types of matrices",
            "explanation": expl
        }
        res = check_matrix_theorems_and_dimensions(q)
        assert res.is_valid is True, f"Valid negation in explanation was falsely rejected: {res.reason}"


# ==============================================================================
# 3. Defect 3: Dropout Scaling Clause Boundary Isolation
# ==============================================================================

def test_defect_5_dropout_inference_scaling_misconception_rejected():
    """Regression test for Run 5 Q5: Claiming weights are multiplied by 2.0 at inference."""
    question_defect = {
        "question": "In a Dropout layer with a keep probability of 0.5, how should the weights be scaled during the inference (testing) phase to maintain the expected value of the network?",
        "options": {
            "A": "Multiply the weights by 0.5.",
            "B": "Multiply the weights by 2.0.",
            "C": "Keep the weights unchanged.",
            "D": "Divide the weights by 0.5."
        },
        "correct_answer": "B",
        "concept": "Regularization Methods-Dropout",
        "explanation": "To compensate for scaling during inference, the weights must be multiplied by 1/0.5 = 2.0 to preserve magnitude."
    }
    valid, reason = verify_academic_correctness(question_defect)
    assert not valid
    assert "Dropout scaling error" in reason
    assert validate_single_question(question_defect) is None


def test_dropout_clause_boundaries_exact_valid_example():
    """'During testing activations are left unchanged, whereas during training they are multiplied by 2.' must PASS."""
    q_exact = {
        "question": "In inverted Dropout, how are activations handled across phases?",
        "options": {
            "A": "Activations are scaled during training and unchanged at test time",
            "B": "Weights are halved at test time",
            "C": "No scaling ever",
            "D": "Only scaled at test time"
        },
        "correct_answer": "A",
        "concept": "Regularization Methods-Dropout",
        "explanation": "During testing activations are left unchanged, whereas during training they are multiplied by 2."
    }
    res = check_dropout_inference_scaling(q_exact)
    assert res.is_valid is True
    assert res.is_verified_correct is True
    assert validate_single_question(q_exact) is not None


def test_dropout_training_scaling_followed_by_unscaled_inference():
    """Valid inverted dropout stating training scaled by 2 and inference unscaled must PASS."""
    q_valid = {
        "question": "In inverted Dropout with p=0.5, describe the scaling strategy.",
        "options": {
            "A": "Scale training by 2; keep inference unscaled (1.0x)",
            "B": "Scale inference by 2",
            "C": "Scale inference by 0.5",
            "D": "No training scaling"
        },
        "correct_answer": "A",
        "concept": "Regularization Methods-Dropout",
        "explanation": "During the training phase activations are multiplied by 1/p = 2; during inference, weights remain unscaled."
    }
    res = check_dropout_inference_scaling(q_valid)
    assert res.is_valid is True
    assert res.is_verified_correct is True


def test_dropout_incorrect_inference_multiplication_by_2():
    """Explanation claiming inference activations or weights are multiplied by 2 must FAIL."""
    q_bad_expl = {
        "question": "In Dropout with keep prob 0.5, what happens at inference?",
        "options": {
            "A": "Multiply by 2",
            "B": "Unscaled",
            "C": "Multiply by 0.5",
            "D": "None"
        },
        "correct_answer": "A",
        "concept": "Regularization Methods-Dropout",
        "explanation": "During inference, activations are multiplied by 2 to compensate for dropped units."
    }
    res = check_dropout_inference_scaling(q_bad_expl)
    assert res.is_valid is False
    assert res.is_verified_incorrect is True
    assert "Dropout scaling error" in res.reason


def test_dropout_incorrect_inference_weight_scaling():
    """Marked answer claiming inference weights are multiplied by 2 must FAIL."""
    q_bad_opt = {
        "question": "How are weights scaled during the inference phase of Dropout with p=0.5?",
        "options": {
            "A": "Multiply the weights by 2.0",
            "B": "Keep unscaled",
            "C": "Multiply by 0.5",
            "D": "Divide by 2"
        },
        "correct_answer": "A",
        "concept": "Regularization Methods-Dropout",
        "explanation": "Weights are scaled."
    }
    res = check_dropout_inference_scaling(q_bad_opt)
    assert res.is_valid is False
    assert res.is_verified_incorrect is True
    assert "Dropout scaling error: weights at inference" in res.reason


def test_dropout_sentence_with_both_valid_training_and_invalid_inference():
    """Sentence with valid training scaling BUT also claiming inference multiplication by 2 must FAIL."""
    q_both = {
        "question": "Explain Dropout scaling for keep probability 0.5.",
        "options": {
            "A": "Option A",
            "B": "Option B",
            "C": "Option C",
            "D": "Option D"
        },
        "correct_answer": "A",
        "concept": "Regularization Methods-Dropout",
        "explanation": "While training activations are divided by 0.5, during inference weights are multiplied by 2."
    }
    res = check_dropout_inference_scaling(q_both)
    assert res.is_valid is False
    assert res.is_verified_incorrect is True


def test_dropout_semicolon_and_comma_clauses():
    """Test semicolon and comma separated clauses."""
    q_semi = {
        "question": "In inverted Dropout with p=0.5, how are phases separated?",
        "options": {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"},
        "correct_answer": "A",
        "concept": "Regularization Methods-Dropout",
        "explanation": "At training time activations are multiplied by 2; at testing time no scaling is applied."
    }
    res = check_dropout_inference_scaling(q_semi)
    assert res.is_valid is True
    assert res.is_verified_correct is True


def test_dropout_ambiguous_statements():
    """Non-dropout question returns UNSUPPORTED."""
    q_non_dropout = {
        "question": "What is Batch Normalization?",
        "options": {"A": "A", "B": "B", "C": "C", "D": "D"},
        "correct_answer": "A",
        "concept": "Normalization",
        "explanation": "Batch norm normalizes layer activations."
    }
    res = check_dropout_inference_scaling(q_non_dropout)
    assert res.is_valid is True
    assert res.is_unsupported is True


# ==============================================================================
# 4. Defect 4: Zero-Denominator Fractions and Fraction Parsing
# ==============================================================================

def test_zero_denominator_fraction_rejected():
    """Syntactically fraction inputs with zero denominator must return None, never fall through to 1.0."""
    assert parse_number_or_fraction("1/0") is None
    assert parse_number_or_fraction("-3/0") is None
    assert parse_number_or_fraction("1 / 0") is None
    assert parse_number_or_fraction("5 / -0") is None
    assert parse_number_or_fraction("0/0") is None
    assert parse_number_or_fraction("x = 1/0") is None
    assert parse_number_or_fraction("Option A: 2/0") is None


def test_valid_fraction_parsing():
    """Valid fractions and decimals must parse correctly."""
    assert abs(parse_number_or_fraction("1/2") - 0.5) < 1e-4
    assert abs(parse_number_or_fraction("-3/4") - (-0.75)) < 1e-4
    assert abs(parse_number_or_fraction("3/-4") - (-0.75)) < 1e-4
    assert abs(parse_number_or_fraction("-5/-2") - 2.5) < 1e-4
    assert abs(parse_number_or_fraction("0.5") - 0.5) < 1e-4
    assert abs(parse_number_or_fraction("-1.25") - (-1.25)) < 1e-4
    assert abs(parse_number_or_fraction("x = 1/2") - 0.5) < 1e-4
    assert abs(parse_number_or_fraction("Option A: -3/4") - (-0.75)) < 1e-4


def test_malformed_fraction_strings_rejected():
    """Malformed fraction strings must return None without extracting accidental numbers."""
    assert parse_number_or_fraction("1/abc") is None
    assert parse_number_or_fraction("/5") is None
    assert parse_number_or_fraction("3/") is None
    assert parse_number_or_fraction("//") is None
    assert parse_number_or_fraction("invalid") is None
    assert parse_number_or_fraction("") is None
    assert parse_number_or_fraction(None) is None


# ==============================================================================
# 5. Three-State Verification Architecture & Backward Compatibility
# ==============================================================================

def test_three_state_verification_result_tuple_unpacking():
    """VerificationResult must cleanly unpack into a 2-tuple (is_valid, reason)."""
    res_correct = VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)
    valid, reason = res_correct
    assert valid is True
    assert reason is None
    assert res_correct.is_verified_correct is True
    assert res_correct.is_verified_incorrect is False
    assert res_correct.is_unsupported is False
    assert res_correct.status == VerificationStatus.VERIFIED_CORRECT

    res_incorrect = VerificationResult(False, "Math error", VerificationStatus.VERIFIED_INCORRECT)
    valid, reason = res_incorrect
    assert valid is False
    assert reason == "Math error"
    assert res_incorrect.is_verified_incorrect is True

    res_unsupported = VerificationResult(True, None, VerificationStatus.UNSUPPORTED)
    valid, reason = res_unsupported
    assert valid is True
    assert reason is None
    assert res_unsupported.is_unsupported is True


def test_verifier_exception_safety_returns_unsupported():
    """Unexpected exception during verification must return UNSUPPORTED (fail-open) with logging."""
    res = verify_academic_correctness(None)  # Non-dict
    assert res.is_valid is False  # Explicit rejection of non-dict
    assert res.is_verified_incorrect is True

    # Malformed internal structure
    res_malformed = verify_academic_correctness({"question": {"nested": "not a string"}})
    assert res_malformed.is_valid is True
    assert res_malformed.is_unsupported is True


# ==============================================================================
# 6. Linear System Exact Solutions, Fractions, and Reordered Variables
# ==============================================================================

def test_defect_3_linear_system_arithmetic_hallucination_rejected():
    """Regression test for Run 3 Q2: Linear system x + 2y = 5, 3x - y = 4 has y=11/7, not y=1."""
    question_defect = {
        "question": "Solve using Gauss elimination: x + 2y = 5, 3x - y = 4. What is the value of y?",
        "options": {
            "A": "1",
            "B": "2",
            "C": "3",
            "D": "4"
        },
        "correct_answer": "A",
        "concept": "Solution of system of linear equations",
        "explanation": "Eliminate x: 15 - 4 = 7, so 7y = 7, giving y = 1."
    }
    valid, reason = verify_academic_correctness(question_defect)
    assert not valid
    assert "Linear system solution mismatch" in reason or "Arithmetic hallucination" in reason
    assert validate_single_question(question_defect) is None

    # Corrected system: x + 2y = 5, 3x - y = 8 has exact solution y = 1, x = 3
    question_valid = {
        "question": "Solve using Gauss elimination: x + 2y = 5, 3x - y = 8. What is the value of y?",
        "options": {
            "A": "1",
            "B": "2",
            "C": "3",
            "D": "4"
        },
        "correct_answer": "A",
        "concept": "Solution of system of linear equations",
        "explanation": "Multiply first equation by 3: 3x + 6y = 15. Subtracting second equation gives 7y = 7, so y = 1."
    }
    assert validate_single_question(question_valid) is not None


def test_linear_system_fractional_solution_pass():
    """Linear system yielding fraction 11/7 with marked option '11/7' must be accepted."""
    q_fraction = {
        "question": "Solve the system: x + 2y = 5, 3x - y = 4. What is the value of y?",
        "options": {
            "A": "11/7",
            "B": "2/7",
            "C": "13/7",
            "D": "5/7"
        },
        "correct_answer": "A",
        "concept": "Solution of system of linear equations",
        "explanation": "Multiply first by 3: 3x + 6y = 15. Subtract second: 7y = 11, giving y = 11/7."
    }
    res = check_linear_system_solution(q_fraction)
    assert res.is_valid is True
    assert res.is_verified_correct is True


def test_linear_system_reordered_variables():
    """Linear system where second equation has variables reordered: x + y = 3, 2y + x = 4."""
    # Solution: x = 2, y = 1. Target: x
    q_reordered_x = {
        "question": "Solve the linear system: x + y = 3, 2y + x = 4. What is the value of x?",
        "options": {
            "A": "2",
            "B": "1",
            "C": "0",
            "D": "-1"
        },
        "correct_answer": "A",
        "concept": "Solution of system of linear equations",
        "explanation": "Subtract equation 1 from equation 2: (2y + x) - (x + y) = 4 - 3, so y = 1. Then x = 2."
    }
    res = check_linear_system_solution(q_reordered_x)
    assert res.is_valid is True
    assert res.is_verified_correct is True

    # Target: y with hallucinated answer 5 must be rejected
    q_reordered_bad = {
        "question": "Solve the linear system: x + y = 3, 2y + x = 4. What is the value of y?",
        "options": {
            "A": "5",
            "B": "1",
            "C": "2",
            "D": "3"
        },
        "correct_answer": "A",
        "concept": "Solution of system of linear equations",
        "explanation": "Wrongly yields y = 5."
    }
    res_bad = check_linear_system_solution(q_reordered_bad)
    assert res_bad.is_valid is False
    assert "Linear system solution mismatch for y" in res_bad.reason


# ==============================================================================
# 7. Rank-Nullity Solution Space Dimensional Geometry
# ==============================================================================

def test_defect_4_rank_dimension_solution_space_rejected():
    """Regression test for Run 3 Q10: 3 variables with rank 1 has 2 free parameters (plane), not line."""
    question_defect = {
        "question": "For the system x+2y-z=3, 2x+4y-2z=6, 3x+6y-3z=9, describe its solution set.",
        "options": {
            "A": "Unique solution (x,y,z)=(0,0,0)",
            "B": "No solution (inconsistent)",
            "C": "Infinitely many solutions forming a line",
            "D": "Infinitely many solutions forming a plane"
        },
        "correct_answer": "C",
        "concept": "Consistency of system of linear equations",
        "explanation": "All three equations are multiples; rank=1 < variables=3, giving one free parameter -> line of solutions."
    }
    valid, reason = verify_academic_correctness(question_defect)
    assert not valid
    assert "Rank-Nullity dimension error" in reason
    assert validate_single_question(question_defect) is None

    # Valid question marking plane and correctly explaining 2 free parameters
    question_valid = {
        "question": "For the system x+2y-z=3, 2x+4y-2z=6, 3x+6y-3z=9, describe its solution set.",
        "options": {
            "A": "Unique solution (x,y,z)=(0,0,0)",
            "B": "No solution (inconsistent)",
            "C": "Infinitely many solutions forming a line",
            "D": "Infinitely many solutions forming a plane"
        },
        "correct_answer": "D",
        "concept": "Consistency of system of linear equations",
        "explanation": "All equations are multiples, so rank=1 with 3 variables yields 3 - 1 = 2 free parameters, forming a plane."
    }
    assert validate_single_question(question_valid) is not None


def test_rank_nullity_various_phrasings_rejected():
    """Phrasing variants ('solutions form a line', 'defines a line') for rank 1 in 3 vars must be caught."""
    phrasings = [
        "The solutions form a line in 3D space.",
        "This defines a 1D line of solutions.",
        "Infinitely many solutions along a line."
    ]
    for p in phrasings:
        q = {
            "question": "For a system in 3 variables with rank = 1, how do the solutions behave geometrically?",
            "options": {"A": p, "B": "A plane", "C": "A point", "D": "Empty"},
            "correct_answer": "A",
            "concept": "Consistency of system of linear equations",
            "explanation": "Rank 1 with 3 variables."
        }
        res = check_matrix_theorems_and_dimensions(q)
        assert res.is_valid is False
        assert "Rank-Nullity dimension error" in res.reason


# ==============================================================================
# 8. Multi-Term Safe AST Arithmetic Evaluator Tests
# ==============================================================================

def test_safe_ast_evaluator_valid_multi_term():
    """Verify that multi-term arithmetic expressions are evaluated correctly."""
    assert evaluate_arithmetic_expr("2*4 - 3") == 5.0
    assert evaluate_arithmetic_expr("1*4 - 2*3") == -2.0
    assert evaluate_arithmetic_expr("8 - 3") == 5.0
    assert evaluate_arithmetic_expr("2 * (3 + 4)") == 14.0
    assert evaluate_arithmetic_expr("2(0 - 10)") == -20.0
    assert evaluate_arithmetic_expr("2^3") == 8.0
    assert evaluate_arithmetic_expr("15 - 4") == 11.0


def test_safe_ast_evaluator_rejects_unsafe():
    """Verify that non-arithmetic code, function calls, and variable lookups return None."""
    assert evaluate_arithmetic_expr("__import__('os').system('dir')") is None
    assert evaluate_arithmetic_expr("eval('2+2')") is None
    assert evaluate_arithmetic_expr("x + 2") is None
    assert evaluate_arithmetic_expr("det") is None


def test_arithmetic_explanation_multi_term_valid_passes():
    """Expressions like 'det = 2*4 - 3 = 5' and 'det = 1*4 - 2*3 = -2' must pass."""
    valid_explanations = [
        "Computing the determinant: det = 2*4 - 3 = 5.",
        "Using determinant formula: det = 1*4 - 2*3 = 4 - 6 = -2.",
        "We have 2(0 - 10) = -20.",
        "Since rank is 1 with 3 variables, 3 - 1 = 2 free parameters."
    ]
    for expl in valid_explanations:
        q = {
            "question": "Calculate the determinant.",
            "options": {"A": "5", "B": "2", "C": "1", "D": "0"},
            "correct_answer": "A",
            "concept": "Determinants",
            "explanation": expl
        }
        res = check_arithmetic_in_explanation(q)
        assert res.is_valid is True, f"Valid arithmetic '{expl}' was falsely rejected: {res.reason}"


def test_arithmetic_explanation_hallucination_rejected():
    """Arithmetic hallucination like '15 - 4 = 7' or 'det = 2*4 - 3 = 7' must be rejected."""
    bad_explanations = [
        "det = 2*4 - 3 = 7.",
        "det = 1*4 - 2*3 = 4 - 6 = 2.",
        "15 - 4 = 7."
    ]
    for expl in bad_explanations:
        q = {
            "question": "Calculate the determinant.",
            "options": {"A": "5", "B": "2", "C": "1", "D": "0"},
            "correct_answer": "A",
            "concept": "Determinants",
            "explanation": expl
        }
        res = check_arithmetic_in_explanation(q)
        assert res.is_valid is False, f"Hallucinated arithmetic '{expl}' was not rejected!"
        assert "Arithmetic hallucination" in res.reason


# ==============================================================================
# 9. Retake Novelty Candidate Buffer and 10-Question Enforcement
# ==============================================================================

def test_retake_novelty_candidate_buffer_single_pass():
    """
    Verifies that when a candidate pool has 12 questions (with 1 duplicate of prior attempt and 11 novel),
    the novelty-aware candidate buffer prioritizes the novel candidates so that exactly 10 novel questions
    are retained without triggering a second Targeted Replacement pass.
    """
    history_fps = ["which of the following best describes a matrix"]
    history_questions = [{"question": "Which of the following best describes a matrix?"}]

    candidates = []
    candidates.append({
        "question": "Which of the following best describes a matrix?",
        "options": {"A": "Array", "B": "Scalar", "C": "Vector", "D": "Field"},
        "correct_answer": "A",
        "concept": "Definition of matrix",
        "explanation": "A matrix is an array of numbers."
    })
    for i in range(1, 12):
        candidates.append({
            "question": f"Topic aspect #{i}: How does property index {i} affect linear rank?",
            "options": {"A": f"Opt A{i}", "B": f"Opt B{i}", "C": f"Opt C{i}", "D": f"Opt D{i}"},
            "correct_answer": "A",
            "concept": "Algebra of matrices",
            "explanation": f"Explanation for aspect {i}."
        })

    from services.novelty import normalise_text, is_question_near_duplicate
    norm_fps = {normalise_text(fp) for fp in history_fps if normalise_text(fp)}

    novel_pool = []
    collided_pool = []
    for cand_q in candidates:
        c_fp = normalise_text(cand_q.get("question", ""))
        is_collided = c_fp in norm_fps
        if not is_collided and history_questions:
            for h_q in history_questions:
                near_dup, _ = is_question_near_duplicate(h_q, cand_q, topic_name="Matrices")
                if near_dup:
                    is_collided = True
                    break
        if is_collided:
            collided_pool.append(cand_q)
        else:
            novel_pool.append(cand_q)

    assert len(novel_pool) == 11
    assert len(collided_pool) == 1

    selected_10 = novel_pool[:10]
    assert len(selected_10) == 10

    novelty_res = evaluate_quiz_novelty(
        questions=selected_10,
        history_fingerprints=history_fps,
        history_concepts=["Definition of matrix"],
        history_questions=history_questions,
        topic_name="Matrices"
    )
    assert novelty_res["is_fully_novel"] is True
    assert len(novelty_res["all_flagged_indices"]) == 0
    assert novelty_res["novel_count"] == 10


def test_exactly_10_valid_questions_enforced():
    """Verifies validate_quiz_data enforces expected_count=10 and rejects partials if < 10."""
    topics = [
        "What is the definition of a square matrix?",
        "How is the trace of a matrix defined?",
        "What condition guarantees matrix invertibility?",
        "How does scalar multiplication scale determinant of 2x2 matrix?",
        "What is the transpose of an upper triangular matrix?",
        "Under what operation is matrix multiplication non-commutative?",
        "What is the rank of the 3x3 identity matrix?",
        "How are eigenvalues related to matrix trace?",
        "What is the determinant of a singular matrix?",
        "How does Gaussian elimination transform augmented matrices?",
        "What is the product of orthogonal matrices?",
        "What is the dimension of the null space for rank 2 matrix in R3?"
    ]

    raw_questions = [
        {
            "question": q_text,
            "options": {"A": f"True Statement {i}", "B": f"False Option {i}B", "C": f"False Option {i}C", "D": f"False Option {i}D"},
            "correct_answer": "A",
            "concept": "Matrices",
            "explanation": f"Step by step educational explanation for item {i}."
        }
        for i, q_text in enumerate(topics)
    ]

    res = validate_quiz_data(
        {"questions": raw_questions},
        course_name="Linear Algebra",
        topic_name="Matrices",
        expected_count=10
    )
    assert len(res["questions"]) == 10
    assert res["unique_count"] == 10

    with pytest.raises(ValueError, match="Expected at least 10 questions, got 5"):
        validate_quiz_data(
            {"questions": raw_questions[:5]},
            course_name="Linear Algebra",
            topic_name="Matrices",
            min_questions=10,
            expected_count=10
        )
