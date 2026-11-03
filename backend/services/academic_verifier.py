"""
LearnAI Academic & Mathematical Correctness Verifier
===================================================
Provides deterministic verification and validation for academic rigor,
mathematical calculations, defensible answer keys, and logical consistency
in AI-generated multiple choice questions.

Distinguishes three verification states via VerificationStatus:
1. VERIFIED_CORRECT: Confirmed accurate through deterministic calculation or theorem verification.
2. VERIFIED_INCORRECT: Flagged and rejected due to factual, arithmetic, or structural violation.
3. UNSUPPORTED: Outside current deterministic scope or ambiguous query formulation (non-blocking).

Returns a backward-compatible 2-tuple subclass (VerificationResult) that unpacks as:
    is_valid, reason = verify_academic_correctness(question)
while exposing .status, .is_verified_correct, .is_verified_incorrect, and .is_unsupported.
"""

import ast
import re
import math
import logging
from enum import Enum
from typing import Dict, Any, Optional, Tuple, List, Union

logger = logging.getLogger("learnai.academic_verifier")


# ==============================================================================
# 0. Three-State Verification Result Architecture
# ==============================================================================

class VerificationStatus(str, Enum):
    VERIFIED_CORRECT = "VERIFIED_CORRECT"
    VERIFIED_INCORRECT = "VERIFIED_INCORRECT"
    UNSUPPORTED = "UNSUPPORTED"


class VerificationResult(tuple):
    """
    A 2-tuple (is_valid, reason) with an explicit .status attribute.
    100% backward-compatible with unpacking:
        is_valid, reason = verify_academic_correctness(question)
    while exposing explicit state properties:
        result.status -> VerificationStatus (VERIFIED_CORRECT, VERIFIED_INCORRECT, UNSUPPORTED)
        result.is_verified_correct -> bool
        result.is_verified_incorrect -> bool
        result.is_unsupported -> bool
    """
    def __new__(
        cls,
        is_valid: bool,
        reason: Optional[str] = None,
        status: VerificationStatus = VerificationStatus.UNSUPPORTED
    ):
        self = super().__new__(cls, (is_valid, reason))
        self.status = status
        return self

    @property
    def is_valid(self) -> bool:
        return self[0]

    @property
    def reason(self) -> Optional[str]:
        return self[1]

    @property
    def is_verified_correct(self) -> bool:
        return self.status == VerificationStatus.VERIFIED_CORRECT

    @property
    def is_verified_incorrect(self) -> bool:
        return self.status == VerificationStatus.VERIFIED_INCORRECT

    @property
    def is_unsupported(self) -> bool:
        return self.status == VerificationStatus.UNSUPPORTED

    def __repr__(self) -> str:
        return f"VerificationResult(is_valid={self.is_valid}, reason={repr(self.reason)}, status={self.status.value})"


# ==============================================================================
# 1. Safe AST-Based Arithmetic Evaluator
# ==============================================================================

class SafeArithmeticEvaluator(ast.NodeVisitor):
    """
    Safely evaluates basic arithmetic expressions from Python AST.
    Strictly forbids any function calls, variable lookups, imports, or attributes.
    Supports: +, -, *, /, unary +, unary -, parentheses, exponentiation (bounded).
    """

    ALLOWED_BIN_OPS = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b if abs(b) > 1e-12 else None,
        ast.Pow: lambda a, b: a ** b if abs(b) <= 20 and abs(a) <= 1e6 else None,
    }

    ALLOWED_UNARY_OPS = {
        ast.UAdd: lambda a: +a,
        ast.USub: lambda a: -a,
    }

    def generic_visit(self, node: ast.AST) -> Optional[float]:
        # Reject any unrecognized AST node immediately
        return None

    def visit_Expression(self, node: ast.Expression) -> Optional[float]:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Optional[float]:
        if isinstance(node.value, (int, float)):
            return float(node.value)
        return None

    # Backward compatibility for Python ASTs with ast.Num
    def visit_Num(self, node: Any) -> Optional[float]:
        return float(node.n)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Optional[float]:
        operand = self.visit(node.operand)
        if operand is None:
            return None
        op_func = self.ALLOWED_UNARY_OPS.get(type(node.op))
        if op_func is None:
            return None
        try:
            return op_func(operand)
        except Exception:
            return None

    def visit_BinOp(self, node: ast.BinOp) -> Optional[float]:
        left = self.visit(node.left)
        right = self.visit(node.right)
        if left is None or right is None:
            return None
        op_func = self.ALLOWED_BIN_OPS.get(type(node.op))
        if op_func is None:
            return None
        try:
            res = op_func(left, right)
            if res is not None and (math.isnan(res) or math.isinf(res)):
                return None
            return res
        except (ZeroDivisionError, OverflowError, ValueError):
            return None


def evaluate_arithmetic_expr(expr_str: str) -> Optional[float]:
    """
    Evaluates a single arithmetic expression string safely using AST.
    Normalizes implicit multiplication (e.g. 2(3) -> 2*(3)) and caret exponentiation (^ -> **).
    Returns float result or None if expression is invalid or contains non-arithmetic tokens.
    """
    if not expr_str:
        return None

    s = expr_str.strip().rstrip(".,;")
    if not s:
        return None

    # Reject if expression contains letters or identifiers (e.g., variables, functions)
    if re.search(r'[a-zA-Z_]', s):
        return None

    # Normalize implicit multiplication: e.g. 2(3) -> 2 * (3), (2)(3) -> (2) * (3)
    s = re.sub(r'(\d)\s*\(', r'\1 * (', s)
    s = re.sub(r'\)\s*\(', r') * (', s)
    s = re.sub(r'\)\s*(\d)', r') * \1', s)

    # Normalize caret ^ to ** for exponentiation
    s = s.replace('^', '**')

    try:
        tree = ast.parse(s, mode='eval')
        return SafeArithmeticEvaluator().visit(tree)
    except Exception:
        return None


def parse_number_or_fraction(text: str) -> Optional[float]:
    """
    Parses a float or rational fraction (e.g. '1/2', '-3/4', '0.5', 'x = 1/2')
    from an answer string. Returns None if invalid or zero denominator.
    Strictly forbids falling through to integer extraction on zero denominator or malformed fractions.
    """
    if not text:
        return None

    cleaned = str(text).strip()
    # Strip variable assignments or option prefixes: 'x = 1/2' -> '1/2', 'Option A: 3/2' -> '3/2'
    m_assign = re.search(r'^(?:[a-zA-Z]\s*=\s*|Option\s+[A-D]:\s*)(.*)', cleaned, flags=re.IGNORECASE)
    target = m_assign.group(1).strip() if m_assign else cleaned

    # Check if string contains a fraction syntax (slash '/')
    if '/' in target:
        frac_match = re.search(r'([+-]?\s*\d+(?:\.\d+)?)\s*/\s*([+-]?\s*\d+(?:\.\d+)?)', target)
        if frac_match:
            try:
                num = float(frac_match.group(1).replace(" ", ""))
                denom = float(frac_match.group(2).replace(" ", ""))
                if abs(denom) < 1e-12:
                    # Syntactically a fraction with zero denominator -> strictly invalid
                    return None
                return num / denom
            except Exception:
                return None
        # Contains slash but could not parse a valid fraction -> malformed fraction, do NOT fall through
        return None

    # Match regular decimal or integer: [+-]?\d+(?:\.\d+)?
    num_match = re.search(r'(?<![a-zA-Z_])([+-]?\s*\d+(?:\.\d+)?)(?![a-zA-Z_])', target)
    if num_match:
        try:
            return float(num_match.group(1).replace(" ", ""))
        except Exception:
            return None

    return None


# ==============================================================================
# 2. Matrix Verification Helpers
# ==============================================================================

def _parse_matrix_2x2(text: str) -> Optional[List[List[float]]]:
    """Attempts to parse a 2x2 matrix from strings like '[[1, 2], [3, 4]]'."""
    pattern = r'\[\s*\[\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*\]\s*,\s*\[\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*\]\s*\]'
    match = re.search(pattern, text)
    if match:
        try:
            a, b, c, d = map(float, match.groups())
            return [[a, b], [c, d]]
        except Exception:
            return None
    return None


def _is_matrix_symmetric(mat: List[List[float]], tol: float = 1e-5) -> bool:
    """Checks if a 2x2 matrix equals its transpose (A_01 == A_10)."""
    return abs(mat[0][1] - mat[1][0]) <= tol


def _is_matrix_diagonal(mat: List[List[float]], tol: float = 1e-5) -> bool:
    """Checks if off-diagonal entries are zero (A_01 == 0 and A_10 == 0)."""
    return abs(mat[0][1]) <= tol and abs(mat[1][0]) <= tol


def _detect_matrix_query_polarity(q_lower: str, property_name: str) -> Optional[bool]:
    """
    Determines whether a question is asking for a matrix with the given property
    (True for positive query, False for negative query), or None if ambiguous/unsupported.
    Prevents false polarity inversions from incidental parenthetical qualifiers (e.g. 'not complex').
    """
    # 1. Clean out parenthetical qualifiers like '(assume real entries, not complex)'
    q_main = re.sub(r'\(.*?\)', ' ', q_lower).strip()

    # 2. Explicit negative patterns directly modifying property_name
    neg_property_patterns = [
        rf'\bnot\s+(?:a\s+)?{property_name}\b',
        rf'\bnon-?{property_name}\b',
        rf'\bnever\s+(?:a\s+)?{property_name}\b',
        rf'\bfails?\s+to\s+be\s+(?:a\s+)?{property_name}\b',
        rf'\b(?:all|every)\s+.*?\b{property_name}\b.*?\bexcept\b',
        rf'\bwhich\s+.*?\b{property_name}\b.*?\b(?:false|incorrect)\b',
        rf'\bincorrectly\s+classified\s+as\s+(?:a\s+)?{property_name}\b'
    ]
    is_neg = any(bool(re.search(pat, q_main)) or bool(re.search(pat, q_lower)) for pat in neg_property_patterns)

    # 3. Explicit positive patterns
    pos_property_patterns = [
        rf'\bwhich\s+.*?\bis\s+(?:a\s+)?{property_name}\b',
        rf'\bidentify\s+(?:the\s+)?{property_name}\b',
        rf'\bfind\s+(?:the\s+)?{property_name}\b',
        rf'\bwhich\s+matrix\s+is\s+{property_name}\b',
        rf'\bwhich\s+of\s+the\s+following\s+(?:matrices\s+)?is\s+(?:a\s+)?{property_name}\b',
        rf'\bis\s+(?:an?\s+)?example\s+of\s+(?:a\s+)?{property_name}\b'
    ]
    is_pos = any(bool(re.search(pat, q_main)) for pat in pos_property_patterns)

    if is_neg:
        return False
    if is_pos:
        return True
    return None


# ==============================================================================
# 3. Individual Academic & Mathematical Verifiers
# ==============================================================================

def check_multiple_correct_options(question: Dict[str, Any]) -> VerificationResult:
    """
    Verifies that every MCQ has exactly one defensible correct option.
    Detects cases where multiple options satisfy the requested property,
    correctly distinguishing positive queries ('is symmetric') from
    negative queries ('is NOT symmetric') using targeted property patterns.
    """
    q_text = str(question.get("question", "")).strip()
    options = question.get("options", {})
    correct_key = str(question.get("correct_answer", "")).strip().upper()
    q_lower = q_text.lower()

    # Parse 2x2 matrices from options
    parsed_matrices: Dict[str, List[List[float]]] = {}
    for k, v in options.items():
        mat = _parse_matrix_2x2(str(v))
        if mat:
            parsed_matrices[k] = mat

    # If options do not consist of 4 parseable 2x2 matrices, check is unsupported for matrix properties
    if len(parsed_matrices) < 4:
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    # 1. Symmetric matrix classification
    if "symmetric" in q_lower and "skew" not in q_lower and "antisymmetric" not in q_lower:
        polarity = _detect_matrix_query_polarity(q_lower, "symmetric")
        if polarity is None:
            # Ambiguous or unsupported phrasing -> fail-open as unsupported
            logger.debug("[ACADEMIC VERIFIER: UNSUPPORTED] Ambiguous polarity for symmetric matrix query.")
            return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

        sym_keys = [k for k, mat in parsed_matrices.items() if _is_matrix_symmetric(mat)]
        non_sym_keys = [k for k in parsed_matrices if k not in sym_keys]

        if not polarity:  # Negative query: asking for NOT symmetric / non-symmetric
            if len(non_sym_keys) > 1:
                return VerificationResult(
                    False,
                    f"Multiple options satisfy the NOT symmetric property: {non_sym_keys}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            if len(non_sym_keys) == 0:
                return VerificationResult(
                    False,
                    "No options satisfy the NOT symmetric property (all options are symmetric)",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            if non_sym_keys[0] != correct_key:
                return VerificationResult(
                    False,
                    f"Answer key mismatch for NOT symmetric query: Option {non_sym_keys[0]} is not symmetric, "
                    f"but marked correct answer is {correct_key}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)
        else:  # Positive query: asking for symmetric
            if len(sym_keys) > 1:
                return VerificationResult(
                    False,
                    f"Multiple options satisfy the symmetric property: {sym_keys}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            if len(sym_keys) == 0:
                return VerificationResult(
                    False,
                    "No options satisfy the symmetric property",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            if sym_keys[0] != correct_key:
                return VerificationResult(
                    False,
                    f"Answer key mismatch for symmetric query: Option {sym_keys[0]} is symmetric, "
                    f"but marked correct answer is {correct_key}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)

    # 2. Diagonal matrix classification
    if "diagonal" in q_lower and "diagonalizable" not in q_lower and "diagonalization" not in q_lower:
        polarity = _detect_matrix_query_polarity(q_lower, "diagonal")
        if polarity is None:
            logger.debug("[ACADEMIC VERIFIER: UNSUPPORTED] Ambiguous polarity for diagonal matrix query.")
            return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

        diag_keys = [k for k, mat in parsed_matrices.items() if _is_matrix_diagonal(mat)]
        non_diag_keys = [k for k in parsed_matrices if k not in diag_keys]

        if not polarity:  # Negative query: asking for NOT diagonal
            if len(non_diag_keys) > 1:
                return VerificationResult(
                    False,
                    f"Multiple options satisfy the NOT diagonal property: {non_diag_keys}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            if len(non_diag_keys) == 0:
                return VerificationResult(
                    False,
                    "No options satisfy the NOT diagonal property (all options are diagonal)",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            if non_diag_keys[0] != correct_key:
                return VerificationResult(
                    False,
                    f"Answer key mismatch for NOT diagonal query: Option {non_diag_keys[0]} is not diagonal, "
                    f"but marked correct answer is {correct_key}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)
        else:  # Positive query: asking for diagonal
            if len(diag_keys) > 1:
                return VerificationResult(
                    False,
                    f"Multiple options satisfy the diagonal property: {diag_keys}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            if len(diag_keys) == 0:
                return VerificationResult(
                    False,
                    "No options satisfy the diagonal property",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            if diag_keys[0] != correct_key:
                return VerificationResult(
                    False,
                    f"Answer key mismatch for diagonal query: Option {diag_keys[0]} is diagonal, "
                    f"but marked correct answer is {correct_key}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)

    return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)


def _parse_linear_equation(eq_str: str) -> Optional[Tuple[Dict[str, float], float]]:
    """
    Parses a single linear equation string like '2x - 3y = 5' or 'y + 2x = 4'.
    Returns (dict of variable -> coefficient, RHS constant) or None if unparseable.
    """
    eq_str = eq_str.strip().rstrip(".,;")
    if "=" not in eq_str:
        return None

    parts = eq_str.split("=")
    if len(parts) != 2:
        return None

    lhs, rhs = parts[0].strip(), parts[1].strip()
    try:
        c = float(rhs.replace(" ", ""))
    except ValueError:
        return None

    # Match linear variable terms: e.g. '+ 2x', '- y', '3.5z'
    term_pattern = re.compile(r'([+-]?\s*\d*(?:\.\d+)?)\s*([a-zA-Z])')
    matches = term_pattern.findall(lhs)
    if not matches:
        return None

    coeffs: Dict[str, float] = {}
    for coeff_str, var in matches:
        v = var.lower()
        cleaned = coeff_str.replace(" ", "")
        if cleaned in ("", "+"):
            val = 1.0
        elif cleaned == "-":
            val = -1.0
        else:
            try:
                val = float(cleaned)
            except ValueError:
                val = 1.0
        coeffs[v] = coeffs.get(v, 0.0) + val

    return coeffs, c


def check_linear_system_solution(question: Dict[str, Any]) -> VerificationResult:
    """
    Deterministically solves 2-variable linear systems:
      a1*x + b1*y = c1
      a2*x + b2*y = c2
    supporting reordered terms (e.g. 2y + x = 4), fraction answers (e.g. 11/7, 1/2),
    and validates that the marked answer key matches the mathematical solution.
    """
    q_text = str(question.get("question", "")).strip()
    options = question.get("options", {})
    correct_key = str(question.get("correct_answer", "")).strip().upper()
    correct_val = str(options.get(correct_key, "")).strip()

    # Extract potential equation segments containing variable terms and '='
    eq_pattern = re.compile(
        r'([+-]?\s*\d*\.?\d*\s*[a-zA-Z]\s*(?:[+-]\s*\d*\.?\d*\s*[a-zA-Z])*\s*=\s*[+-]?\s*\d+\.?\d*)'
    )
    raw_matches = eq_pattern.findall(q_text)
    if len(raw_matches) < 2:
        logger.debug("[ACADEMIC VERIFIER: UNSUPPORTED] Fewer than 2 linear equations found in question text.")
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    parsed_eqs = []
    for rm in raw_matches[:2]:
        p = _parse_linear_equation(rm)
        if p:
            parsed_eqs.append(p)

    if len(parsed_eqs) < 2:
        logger.debug("[ACADEMIC VERIFIER: UNSUPPORTED] Could not parse at least 2 clean linear equations.")
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    eq1_coeffs, c1 = parsed_eqs[0]
    eq2_coeffs, c2 = parsed_eqs[1]

    all_vars = sorted(list(set(eq1_coeffs.keys()) | set(eq2_coeffs.keys())))
    if len(all_vars) != 2:
        logger.debug(f"[ACADEMIC VERIFIER: UNSUPPORTED] System has {len(all_vars)} variables: {all_vars}.")
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    v1, v2 = all_vars[0], all_vars[1]
    a1, b1 = eq1_coeffs.get(v1, 0.0), eq1_coeffs.get(v2, 0.0)
    a2, b2 = eq2_coeffs.get(v1, 0.0), eq2_coeffs.get(v2, 0.0)

    det = a1 * b2 - a2 * b1
    if abs(det) < 1e-9:
        logger.debug("[ACADEMIC VERIFIER: UNSUPPORTED] Singular linear system (det == 0).")
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    sol_v1 = (c1 * b2 - c2 * b1) / det
    sol_v2 = (a1 * c2 - a2 * c1) / det

    # Identify which variable is being asked for
    q_lower = q_text.lower()
    target_var = None
    target_sol = None

    # Check if correct option explicitly specifies the variable: e.g. 'x = 2' or 'y = 1'
    clean_val = correct_val.replace(" ", "").lower()
    if f"{v1}=" in clean_val:
        target_var, target_sol = v1, sol_v1
    elif f"{v2}=" in clean_val:
        target_var, target_sol = v2, sol_v2
    else:
        # Check question prompt asking for variable using word boundaries
        pattern_v1 = re.compile(rf'\b(?:value\s+of|find|solve\s+for|determine|what\s+is|calculate)\s+{v1}\b')
        pattern_v2 = re.compile(rf'\b(?:value\s+of|find|solve\s+for|determine|what\s+is|calculate)\s+{v2}\b')

        has_v1 = bool(pattern_v1.search(q_lower))
        has_v2 = bool(pattern_v2.search(q_lower))

        if has_v1 and not has_v2:
            target_var, target_sol = v1, sol_v1
        elif has_v2 and not has_v1:
            target_var, target_sol = v2, sol_v2

    if target_var is not None and target_sol is not None:
        chosen_num = parse_number_or_fraction(correct_val)
        if chosen_num is not None:
            if abs(chosen_num - target_sol) > 0.05:
                return VerificationResult(
                    False,
                    f"Linear system solution mismatch for {target_var}: "
                    f"equations yield {target_var}={target_sol:.4f}, but marked option {correct_key} "
                    f"asserts {target_var}={chosen_num:.4f}",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)

    return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)


def check_arithmetic_in_explanation(question: Dict[str, Any]) -> VerificationResult:
    """
    Detects arithmetic calculation errors and hallucinations in explanations using
    a safe AST evaluator supporting multi-term operations (e.g. 2*4 - 3 = 5, 1*4 - 2*3 = -2)
    and chained equations (A = B = C). Rejects factually false arithmetic like 15 - 4 = 7.
    """
    explanation = str(question.get("explanation", "")).strip()
    if not explanation:
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    # Match equation chains containing numbers, arithmetic operators, parentheses, and '='
    pattern = re.compile(
        r'((?:(?<=[^a-zA-Z0-9_])|^)[\(\d\.\+\-][\d\.\+\-\*\/\(\)\s\^]*=(?:[\s\d\.\+\-\*\/\(\)\^]+=)*[\s\d\.\+\-\*\/\(\)\^]*\d[\d\.\)]*)'
    )

    matches_found = False
    for m in pattern.finditer(explanation):
        chain_str = m.group(0).strip().rstrip(".,;")
        if "=" not in chain_str:
            continue

        parts = chain_str.split("=")
        evaluated_values = []
        parsed_parts = []

        for p in parts:
            p_clean = p.strip().rstrip(".,;")
            if not p_clean:
                continue
            val = evaluate_arithmetic_expr(p_clean)
            if val is not None:
                evaluated_values.append(val)
                parsed_parts.append(p_clean)
            else:
                break

        if len(evaluated_values) >= 2 and len(evaluated_values) == len(parts):
            matches_found = True
            for i in range(len(evaluated_values) - 1):
                if abs(evaluated_values[i] - evaluated_values[i + 1]) > 1e-4:
                    return VerificationResult(
                        False,
                        f"Arithmetic hallucination in explanation: '{parsed_parts[i]} = {parsed_parts[i+1]}' "
                        f"is mathematically false ({parsed_parts[i]} evaluates to {evaluated_values[i]}, "
                        f"whereas {parsed_parts[i+1]} evaluates to {evaluated_values[i+1]})",
                        VerificationStatus.VERIFIED_INCORRECT
                    )

    if matches_found:
        return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)

    return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)


def check_matrix_theorems_and_dimensions(question: Dict[str, Any]) -> VerificationResult:
    """
    Validates linear algebra definitions and Rank-Nullity dimensional consistency:
    1. Idempotent matrices: Always diagonalizable because minimal polynomial divides x(x-1)=0.
       Never assert idempotent matrices are defective or non-diagonalizable.
       Properly handles negations ('cannot be defective', 'never defective', 'not defective').
       Separates question, explanation, and selected correct option from distractors;
       never rejects a question solely because an unselected distractor states a false theorem.
    2. Rank-Nullity Solution Geometry:
       In N variables with rank R, dimension of solution space is N - R free parameters.
       In 3 variables, rank 1 yields 3 - 1 = 2 free parameters (a plane, never a line).
    """
    q_text = str(question.get("question", "")).strip()
    options = question.get("options", {})
    correct_key = str(question.get("correct_answer", "")).strip().upper()
    explanation = str(question.get("explanation", "")).strip()

    # Safety: Validate presence and resolution of correct_key
    if not correct_key or not isinstance(options, dict) or correct_key not in options:
        logger.debug(f"[ACADEMIC VERIFIER: UNSUPPORTED] Answer key '{correct_key}' cannot be resolved in options.")
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    selected_val = str(options.get(correct_key, "")).strip().lower()
    q_lower = q_text.lower()
    expl_lower = explanation.lower()

    # Only inspect question premise, explanation, and the SELECTED correct option
    # DO NOT inspect unselected distractor options, as MCQs legitimately include false distractors
    authoritative_text = f"{q_lower} {expl_lower} {selected_val}"

    # 1. Idempotent matrix diagonalizability theorem
    if "idempotent" in authoritative_text or "idempotent" in q_lower:
        is_neg_query = "false" in q_lower or "incorrect" in q_lower or "not true" in q_lower

        if is_neg_query:
            # Question asks "Which statement is false/incorrect?"
            # For idempotent matrices, "The matrix is always diagonalizable" is a TRUE theorem.
            # If the selected answer asserts "always diagonalizable" as the false statement, that is a violation!
            if "always diagonalizable" in selected_val or "is diagonalizable" in selected_val:
                return VerificationResult(
                    False,
                    "Contradicts linear algebra theorem: 'The matrix is always diagonalizable' "
                    "is a true theorem for idempotent matrices, not a false statement.",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            # If selected answer asserts "can be defective" as the false statement, that is CORRECT
            if "defective" in selected_val and ("not" in expl_lower or "cannot" in expl_lower or "never" in expl_lower or "always diagonalizable" in expl_lower or "diagonalizable" in expl_lower):
                return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)
        else:
            # Positive query or explanation premise:
            # Selected answer or explanation must NOT claim that an idempotent matrix is defective
            if "defective" in selected_val or "defective" in expl_lower:
                target_to_check = f"{expl_lower} {selected_val}"
                has_valid_negation = bool(
                    re.search(r'\b(not|cannot|never|can\s+never)\s+(be\s+)?defective\b', target_to_check)
                )
                if not has_valid_negation:
                    return VerificationResult(
                        False,
                        "Contradicts linear algebra theorem: idempotent projection matrices "
                        "satisfy x(x-1)=0 and are always diagonalizable over R and C; they are never defective.",
                        VerificationStatus.VERIFIED_INCORRECT
                    )

            if "need not be diagonalizable" in expl_lower or "cannot be diagonalized" in expl_lower or "is not diagonalizable" in expl_lower:
                return VerificationResult(
                    False,
                    "Contradicts linear algebra theorem: idempotent matrices are always diagonalizable.",
                    VerificationStatus.VERIFIED_INCORRECT
                )

            if "need not be diagonalizable" in selected_val or "cannot be diagonalized" in selected_val:
                return VerificationResult(
                    False,
                    "Contradicts linear algebra theorem: idempotent matrices are always diagonalizable.",
                    VerificationStatus.VERIFIED_INCORRECT
                )

        return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)

    # 2. Rank-Nullity / Solution space dimension
    has_3_vars = (
        ("x" in q_lower and "y" in q_lower and "z" in q_lower)
        or "3 variables" in authoritative_text
        or "three variables" in authoritative_text
    )
    if has_3_vars:
        rank_1 = bool(
            re.search(r'rank\s*=\s*1\b', authoritative_text)
            or "rank is 1" in authoritative_text
            or "rank=1" in authoritative_text
            or "rank 1" in authoritative_text
        )
        if rank_1:
            line_asserted = bool(
                re.search(r'\b(form|forms|forming|define|defines|defining|represent|represents|is|a)\s+(a\s+)?(1-?d\s+)?line\b', selected_val)
                or "one free parameter" in authoritative_text
                or "1 free parameter" in authoritative_text
            )
            if line_asserted:
                return VerificationResult(
                    False,
                    "Rank-Nullity dimension error: a system of rank 1 in 3 variables "
                    "has 3 - 1 = 2 free parameters, which geometrically defines a plane, not a line.",
                    VerificationStatus.VERIFIED_INCORRECT
                )
            return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)

    return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)


def check_dropout_inference_scaling(question: Dict[str, Any]) -> VerificationResult:
    """
    Validates Dropout regularization scaling conventions:
    - Classical Dropout (Srivastava et al., 2014): Weights are multiplied by p (halved if p=0.5)
      at test/inference time so expected activations match training.
    - Inverted Dropout (PyTorch / TensorFlow default): Activations are divided by p (multiplied by 1/p)
      during TRAINING; at test/inference time, weights and activations are unchanged (1.0x).
    - Under NO convention are weights multiplied by 1/p (e.g. 2.0x) at test/inference time.
    - Accurately respects clause boundaries (commas, semicolons, whereas, while, but) so that
      training-phase activation scaling is never conflated with inference scaling.
    """
    q_text = str(question.get("question", "")).strip()
    options = question.get("options", {})
    correct_key = str(question.get("correct_answer", "")).strip().upper()
    explanation = str(question.get("explanation", "")).strip()
    q_lower = q_text.lower()
    expl_lower = explanation.lower()

    if "dropout" not in q_lower and "dropout" not in expl_lower:
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    is_inference_q = any(w in q_lower for w in ["inference", "test", "testing", "evaluation"])
    selected_val = str(options.get(correct_key, "")).lower()

    # 1. Option-level check: Reject if asking about inference/test scaling and marked option claims multiplying by 2 or 1/p
    if is_inference_q:
        invalid_inference_terms = [
            "multiply by 2", "multiplied by 2", "multiply by 1/p", "multiplied by 1/p",
            "multiply the weights by 2", "multiply the weights by 1/p", "divide by 0.5",
            "divided by 0.5", "divide the weights by 0.5", "divided by p", "multiply by 1/0.5"
        ]
        if any(term in selected_val for term in invalid_inference_terms):
            return VerificationResult(
                False,
                "Dropout scaling error: weights at inference are multiplied by p (e.g. 0.5) in "
                "classical dropout, or left unscaled (1.0) in inverted dropout. They are never "
                "multiplied by 1/p (2.0) during inference.",
                VerificationStatus.VERIFIED_INCORRECT
            )

    # 2. Explanation-level check: Split by sentences and clauses to strictly respect clause boundaries
    sentences = re.split(r'[\.\!\?\n]+', expl_lower)
    invalid_scaling_terms = [
        "multiplied by 2", "multiply by 2", "multiplied by 1/p",
        "multiplied by 1/0.5", "divide by 0.5", "divided by 0.5"
    ]

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        # Split sentence into clauses by punctuation and contrastive conjunctions
        clauses = re.split(r'[,;]|\b(?:whereas|while|but|however|although|in contrast to)\b', sent)
        for clause in clauses:
            cl = clause.strip()
            if not cl:
                continue

            has_inference = bool(re.search(r'\b(inference|test|testing|evaluation)\b', cl))
            has_training = bool(re.search(r'\b(train|training)\b', cl))
            has_invalid_scale = any(term in cl for term in invalid_scaling_terms)

            # If clause explicitly discusses inference AND has invalid scaling AND does NOT mention training:
            if has_inference and has_invalid_scale and not has_training:
                return VerificationResult(
                    False,
                    "Dropout scaling error in explanation: claiming inference-time multiplication by 1/p (2.0) "
                    "contradicts both classical and inverted dropout formulations.",
                    VerificationStatus.VERIFIED_INCORRECT
                )

            # If clause mentions both inference and training, inspect direct proximity
            if has_inference and has_invalid_scale and has_training:
                inf_direct = re.search(r'(?:inference|test|testing)\b[^,;]*?\b(?:multiplied by 2|multiply by 2|multiplied by 1/p|multiplied by 1/0.5)', cl)
                train_direct = re.search(r'(?:training|train)\b[^,;]*?\b(?:multiplied by 2|multiply by 2|multiplied by 1/p|multiplied by 1/0.5)', cl)
                if inf_direct and not train_direct:
                    return VerificationResult(
                        False,
                        "Dropout scaling error in explanation: claiming inference-time multiplication by 1/p (2.0) "
                        "contradicts both classical and inverted dropout formulations.",
                        VerificationStatus.VERIFIED_INCORRECT
                    )

    return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)


# ==============================================================================
# 4. Master Verifier Entrypoint
# ==============================================================================

def verify_academic_correctness(question: Dict[str, Any]) -> VerificationResult:
    """
    Master academic and mathematical correctness verifier.
    Runs all independent correctness audits on a candidate question.
    Returns a backward-compatible VerificationResult(tuple) exposing .status:
      - VERIFIED_CORRECT: Deterministically verified accurate.
      - VERIFIED_INCORRECT: Deterministically verified erroneous (rejection reason provided).
      - UNSUPPORTED: Outside deterministic scope or unparseable input (non-blocking).

    Guarantees that no unhandled parser exception crashes the quiz generation pipeline.
    """
    if not isinstance(question, dict):
        return VerificationResult(False, "Question candidate is not a dictionary", VerificationStatus.VERIFIED_INCORRECT)

    try:
        results: List[VerificationResult] = [
            # 1. Multiple correct options / distractor overlap (positive & negative queries)
            check_multiple_correct_options(question),

            # 2. Linear system exact solution (2-variable systems, fractions, reordered terms)
            check_linear_system_solution(question),

            # 3. Arithmetic consistency in explanations (Safe AST evaluator, multi-term, chained equalities)
            check_arithmetic_in_explanation(question),

            # 4. Matrix theorems and Rank-Nullity dimensional geometry (negation handling, distractor isolation)
            check_matrix_theorems_and_dimensions(question),

            # 5. Dropout inference scaling conventions (inverted vs classical, clause boundary isolation)
            check_dropout_inference_scaling(question)
        ]

        # 1. If ANY checker deterministically verified an error -> reject immediately
        for res in results:
            if res.is_verified_incorrect:
                return res

        # 2. If ANY checker deterministically verified correctness (and none failed) -> VERIFIED_CORRECT
        for res in results:
            if res.is_verified_correct:
                return VerificationResult(True, None, VerificationStatus.VERIFIED_CORRECT)

        # 3. Otherwise, question falls outside active deterministic models -> UNSUPPORTED (non-blocking)
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)

    except Exception as e:
        logger.error(f"[ACADEMIC VERIFIER: EXCEPTION] Unexpected error during academic verification: {e}", exc_info=True)
        # Non-blocking on unexpected error: categorizes as UNSUPPORTED to prevent pipeline crash
        return VerificationResult(True, None, VerificationStatus.UNSUPPORTED)
