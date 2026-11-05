import os
import json
import re
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from services.output_verifier import clean_option_string, verify_and_calibrate_question_options
from services.academic_verifier import verify_academic_correctness

logger = logging.getLogger("learnai.providers.base")


class ProviderError(Exception):
    """Base error for LLM providers."""
    def __init__(self, message: str, category: str = "PROVIDER_ERROR", retryable: bool = False):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class ProviderUnavailableError(ProviderError):
    """Raised when provider returns temporary service errors (503, 429, 408, 500, timeouts)."""
    def __init__(self, message: str, category: str = "SERVICE_UNAVAILABLE"):
        super().__init__(message, category=category, retryable=True)


class ProviderConfigError(ProviderError):
    """Raised when provider has missing/invalid credentials or configuration."""
    def __init__(self, message: str, category: str = "AUTH_CONFIG_ERROR"):
        super().__init__(message, category=category, retryable=False)


def get_quiz_prompt(
    course_code: str,
    course_name: str,
    topic_name: str,
    syllabus_context: str,
    difficulty_dist: Optional[Dict[str, int]] = None,
    concepts: Optional[List[str]] = None,
    disallowed_topics: Optional[List[str]] = None,
    num_questions: Optional[int] = None
) -> str:
    """Common standardized prompt for all AI providers with strict topic grounding and anti-duplication directives."""
    if not difficulty_dist:
        total = num_questions if (num_questions and num_questions > 0) else 10
        from services.adaptive import get_difficulty_distribution
        difficulty_dist = get_difficulty_distribution("mixed", total_questions=total)
    else:
        total = sum(difficulty_dist.values())
        if total <= 0:
            total = num_questions if (num_questions and num_questions > 0) else 10

    e_cnt = difficulty_dist.get("Easy", 0)
    m_cnt = difficulty_dist.get("Medium", 0)
    h_cnt = difficulty_dist.get("Hard", 0)

    diff_lines = []
    if e_cnt > 0:
        diff_lines.append(f"- Questions 1 to {e_cnt} must be Easy")
    if m_cnt > 0:
        diff_lines.append(f"- Questions {e_cnt + 1} to {e_cnt + m_cnt} must be Medium")
    if h_cnt > 0:
        diff_lines.append(f"- Questions {total - h_cnt + 1} to {total} must be Hard")
    diff_req = "\n".join(diff_lines)

    concept_instructions = ""
    if concepts and len(concepts) > 0:
        clean_concepts = [c.strip() for c in concepts if c and c.strip()]
        if clean_concepts:
            formatted_concepts = "\n".join(f"  * {c}" for c in clean_concepts)
            concept_instructions = f"""
Syllabus Concepts to Cover for '{topic_name}':
{formatted_concepts}

- You MUST distribute the {total} questions across these syllabus concepts for balanced syllabus coverage.
- Do NOT test only one single concept repeatedly while neglecting the others.
- Ensure each question explicitly sets its "concept" field to the relevant syllabus concept name from this list.
"""

    negative_constraint_block = ""
    if disallowed_topics:
        clean_disallowed = [t.strip() for t in disallowed_topics if t and t.strip()]
        if clean_disallowed:
            negative_constraint_block = (
                f"- NEGATIVE CONSTRAINT (STRICTLY FORBIDDEN): Absolutely DO NOT generate questions on these "
                f"competing or adjacent topics, even if they appear in notes or syllabus reference:\n"
                + "\n".join(f"  * {d}" for d in clean_disallowed)
                + "\n"
            )

    return f"""You are an expert academic AI quiz generator for university students (B.Tech CSE AI&ML curriculum).

Course: {course_code} - {course_name}
Selected Topic: {topic_name}
{concept_instructions}
Syllabus Reference Context:
{syllabus_context}

CRITICAL RULES FOR RELEVANCE AND TOPIC ISOLATION:
1. STRICT TOPIC ISOLATION (MANDATORY & ABSOLUTE):
   - ALL {total} questions MUST strictly and directly assess the Selected Topic: "{topic_name}".
   - Under NO circumstances should you include questions from other topics, modules, or units of {course_name}.
   {negative_constraint_block}   - Do NOT bring in unrelated concepts from other parts of the course syllabus just to fill {total} questions.
   - If study notes are provided in the reference context, use them ONLY for '{topic_name}'. You MUST completely IGNORE any passages in the notes discussing other topics.
   - Ground all questions solely in the selected topic and its syllabus concepts.

2. ZERO DUPLICATE QUESTIONS (MANDATORY):
   - Every question must test a DISTINCT learning objective, property, application, scenario, or calculation.
   - Do NOT ask the same question multiple times with minor rewording.
   - Do NOT repeat questions with permuted options or different answer letter positions.
   - Do NOT generate artificial variations (e.g., "(Application Variation)" of earlier questions).
   - Ensure high question diversity across the topic's learning objectives.

3. STRUCTURE & FORMAT REQUIREMENTS:
   - Return EXACTLY {total} multiple-choice questions in the "questions" array.
   {diff_req}
   - Every question MUST contain exactly 4 distinct, non-empty options with keys "A", "B", "C", and "D".
   - ZERO DUPLICATE OPTIONS: All 4 options must be mutually exclusive and semantically distinct. Never generate identical or duplicate options.
   - FOR CODE OUTPUT & FORMATTED I/O QUESTIONS (e.g. C/C++ printf/scanf, %4d, %04d, \\t, \\n, Python print, string formatting):
     * Preserve exact spaces, indentation, and alignment in the options.
     * Ensure the correct option matches the precise compiler/runtime output (e.g. for printf("%4d%4d", 12, 5); field width 4 produces two leading spaces for 12 and three spaces for 5: "  12   5").
     * Distractor options must test plausible common mistakes (e.g. unformatted single spaces, wrong field widths, no spaces, or left-alignment) and MUST NOT look identical to each other or to the correct option.
   - All option values must be complete, meaningful, and non-empty. Never leave any option blank or with placeholder text.
   - "correct_answer" MUST be the exact uppercase letter corresponding to the correct option: "A", "B", "C", or "D".
   - In the "concept" field, specify the exact syllabus concept or learning objective assessed.
   - In "explanation", provide a clear, concise step-by-step educational reason (1-2 sentences) demonstrating why the correct answer is correct.

4. ACADEMIC & MATHEMATICAL RIGOR (MANDATORY):
   - EXACTLY ONE DEFENSIBLE ANSWER: Exactly one option must satisfy the question criteria. When asking to identify an entity or property (e.g., symmetric matrix, diagonal matrix, linear equation, valid loss), NO distractor may inadvertently satisfy that definition (e.g. if asking for a symmetric matrix, no distractor can be symmetric, noting that diagonal scalar matrices like [[5,0],[0,5]] are symmetric).
   - CALCULATION & ARITHMETIC RIGOR: Every arithmetic step in questions and explanations must be 100% mathematically correct. Verify all basic calculations (e.g. never claim 15 - 4 = 7; never claim 7y = 7 if 15 - 4 = 11).
   - LINEAR ALGEBRA THEOREMS: Adhere strictly to proven theorems. Idempotent matrices satisfy x(x-1)=0 and are ALWAYS diagonalizable; never state or imply they are defective or non-diagonalizable.
   - RANK-NULLITY & GEOMETRIC ACCURACY: In systems with n variables and rank r, the dimension of the solution space is n - r free parameters. In 3 variables (x, y, z), a system with rank 1 yields 3 - 1 = 2 free parameters, which geometrically defines a plane, not a line.
   - DROPOUT CONVENTIONS: When discussing Dropout scaling, distinguish classical dropout (weights multiplied by p at test time) from inverted dropout (activations scaled by 1/p during training; weights unscaled at test time). Never state that weights are multiplied by 1/p at test time.
   - Return ONLY valid JSON matching this exact structure:

{{
  "subject": "{course_name}",
  "topic": "{topic_name}",
  "questions": [
    {{
      "question": "Clear, specific question statement directly testing {topic_name}",
      "options": {{
        "A": "Option A text",
        "B": "Option B text",
        "C": "Option C text",
        "D": "Option D text"
      }},
      "correct_answer": "A",
      "difficulty": "Easy",
      "concept": "Specific syllabus concept assessed",
      "explanation": "Concise step-by-step reason why A is correct."
    }}
  ]
}}
"""


def get_analysis_prompt(subject: str, topic: str, weak_concepts: List[str], score: int, total: int) -> str:
    """Common standardized feedback prompt for all AI providers."""
    weak_text = ", ".join(weak_concepts) if weak_concepts else "None"
    return f"""You are an AI tutor for a B.Tech CSE-AI&ML student.

Course: {subject}
Topic: {topic}
Score: {score}/{total}
Weak Concepts: {weak_text}

Give concise personalized revision feedback.

Return only valid JSON using this structure:

{{
  "summary": "Short performance analysis",
  "revision": "What the student should revise",
  "recommendation": "What the student should practice next"
}}
"""


def calculate_max_output_tokens(model_name: str, question_count: int = 10) -> int:
    """
    Calculates dynamic output token budget respecting model-specific completion limits.
    Configurable via BASE_QUESTION_TOKEN_BUDGET (default 210) and QUESTION_TOKEN_BUFFER (default 300).
    Never exceeds verified model max completion limits.
    """
    m_lower = (model_name or "").lower()

    # Model-specific verified completion limits
    if "gpt-oss-20b" in m_lower:
        model_limit = 4096
    elif "@cf/" in m_lower or "cloudflare" in m_lower:
        model_limit = 3000
    elif any(x in m_lower for x in ["llama-3.1-8b", "llama-3.3-70b", "llama-3", "qwen"]):
        model_limit = 8192
    elif "gemini" in m_lower:
        model_limit = 8192
    else:
        model_limit = 4096

    base_budget = int(os.getenv("BASE_QUESTION_TOKEN_BUDGET", "210"))
    buffer = int(os.getenv("QUESTION_TOKEN_BUFFER", "300"))
    explicit_max = os.getenv("MAX_GEN_TOKENS")
    if explicit_max:
        try:
            return min(model_limit, int(explicit_max))
        except ValueError:
            pass

    needed = base_budget * max(1, question_count) + buffer
    return min(model_limit, max(1800, needed))


def extract_json_from_text(text: str) -> Any:
    """Strips markdown code fences and parses raw JSON string safely with automatic repair for minor formatting quirks."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()

    if not text:
        raise ValueError("Model output is empty or whitespace only.")

    # 1. Fast direct parse
    try:
        return json.loads(text, strict=False)
    except Exception:
        pass

    # 2. Extract outermost JSON object from first '{' to last '}'
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            return json.loads(candidate, strict=False)
        except Exception:
            # Clean trailing commas before closing braces/brackets
            cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
            try:
                return json.loads(cleaned, strict=False)
            except Exception:
                pass

    # 3. Attempt structural repair for truncated question arrays or unclosed brackets
    if first_brace != -1:
        fragment = text[first_brace:]
        # Find the last completed question object in the array
        matches = [m.start() for m in re.finditer(r'\}', fragment)]
        if matches:
            for end_pos in reversed(matches):
                trimmed = fragment[:end_pos + 1].rstrip(", \n\r\t")
                candidate_fixed = trimmed + "\n  ]\n}"
                try:
                    res = json.loads(candidate_fixed, strict=False)
                    if isinstance(res, dict) and isinstance(res.get("questions"), list) and len(res["questions"]) > 0:
                        return res
                except Exception:
                    continue

        cleaned = re.sub(r",\s*([\]}])", r"\1", fragment)
        if cleaned.count('"') % 2 != 0:
            cleaned += '"'
        open_curlies = cleaned.count("{") - cleaned.count("}")
        open_brackets = cleaned.count("[") - cleaned.count("]")
        if open_curlies > 0 or open_brackets > 0:
            repaired = cleaned.rstrip(", \n\r\t")
            repaired += ("]" * max(0, open_brackets)) + ("}" * max(0, open_curlies))
            try:
                return json.loads(repaired, strict=False)
            except Exception:
                pass

    first_bracket = text.find("[")
    if first_brace == -1 and first_bracket == -1:
        snippet = text[:120].replace("\n", " ").strip()
        raise ValueError(f"Model output contains no JSON object or array: '{snippet}'")

    # 4. Final attempt fallback
    return json.loads(text, strict=False)


def normalize_correct_answer(raw_ans: Any, options: Dict[str, str]) -> Optional[str]:
    """
    Normalizes raw correct_answer from various LLM response formats:
      - Direct letter: 'A', 'b', 'C', 'D'
      - Labeled letter: 'Option A', 'Choice (B)', 'C)', '[D]', 'A.'
      - Numeric index: 1 -> A, 2 -> B, 3 -> C, 4 -> D (or 0 -> A)
      - Full option text matching one of the options
    Returns normalized 'A', 'B', 'C', or 'D', or None if it cannot be mapped.
    """
    if raw_ans is None:
        return None

    if isinstance(raw_ans, dict):
        raw_ans = (
            raw_ans.get("letter")
            or raw_ans.get("answer")
            or raw_ans.get("key")
            or raw_ans.get("option")
            or raw_ans.get("value")
            or ""
        )

    ans_str = str(raw_ans).strip()
    if not ans_str:
        return None

    clean_upper = ans_str.upper()
    if clean_upper in {"A", "B", "C", "D"}:
        return clean_upper

    # Check for prefix patterns like "Option A", "Choice B", "(C)", "D.", "A)"
    m = re.match(r'^(?:OPTION|CHOICE|ANSWER)?\s*[\(\[]?\s*([A-D])\s*[\)\]\.:]?', clean_upper)
    if m:
        letter = m.group(1)
        if letter in {"A", "B", "C", "D"}:
            return letter

    # Numeric indices: 1 -> A, 2 -> B, 3 -> C, 4 -> D
    if ans_str in {"1", "2", "3", "4"}:
        return {"1": "A", "2": "B", "3": "C", "4": "D"}[ans_str]
    if isinstance(raw_ans, int) and 1 <= raw_ans <= 4:
        return {1: "A", 2: "B", 3: "C", 4: "D"}[raw_ans]
    if ans_str == "0" or (isinstance(raw_ans, int) and raw_ans == 0):
        return "A"

    # Full option text match
    for opt_key, opt_val in options.items():
        if not opt_val:
            continue
        if ans_str == opt_val or ans_str.lower() == opt_val.lower():
            return opt_key

    # Leading letter match if followed by separator (e.g. "B: <?php")
    m2 = re.match(r'^([A-D])\s*[:\-\)]\s*(.+)', clean_upper)
    if m2 and m2.group(1) in {"A", "B", "C", "D"}:
        return m2.group(1)

    return None


def extract_normalized_options(raw_options: Any) -> Optional[Dict[str, str]]:
    """
    Extracts and validates exactly 4 non-empty options for keys A, B, C, D.
    Rejects questions with missing, incomplete, or placeholder options.
    Never invents missing options.
    Preserves meaningful leading, trailing, and multiple horizontal spaces.
    """
    if not raw_options:
        return None

    options: Dict[str, str] = {}
    PLACEHOLDERS = {"null", "none", "undefined", "n/a", "na", "nil", "{}", "[]", "placeholder", "-", "..."}

    # Case 1: Dictionary
    if isinstance(raw_options, dict):
        for k, v in raw_options.items():
            if v is None:
                continue
            k_clean = str(k).strip().upper()
            cleaned_val = clean_option_string(v)
            if k_clean in {"A", "B", "C", "D"}:
                options[k_clean] = cleaned_val
            elif any(k_clean.startswith(prefix) for prefix in ("OPTION", "CHOICE", "ANS", "OPT")):
                sub_k = re.sub(r'^(?:OPTION|CHOICE|ANS|OPT)[_\s\-\.:]*', '', k_clean).strip()
                if sub_k in {"A", "B", "C", "D"}:
                    options[sub_k] = cleaned_val
                elif sub_k in {"1", "2", "3", "4"}:
                    sub_letter = {"1": "A", "2": "B", "3": "C", "4": "D"}[sub_k]
                    options[sub_letter] = cleaned_val
            elif k_clean in {"1", "2", "3", "4"}:
                sub_letter = {"1": "A", "2": "B", "3": "C", "4": "D"}[k_clean]
                options[sub_letter] = cleaned_val

    # Case 2: List of strings or list of dicts
    elif isinstance(raw_options, list) and len(raw_options) >= 4:
        for idx, item in enumerate(raw_options[:4]):
            letter = ["A", "B", "C", "D"][idx]
            if isinstance(item, dict):
                text = (
                    item.get("text")
                    or item.get("value")
                    or item.get("content")
                    or item.get("val")
                    or item.get("option")
                    or ""
                )
                options[letter] = clean_option_string(text)
            elif isinstance(item, str):
                cleaned = item.strip("\r\n")
                m = re.match(r'^[A-D]\s*[\)\.:\-]\s*(.+)', cleaned, flags=re.IGNORECASE)
                if m:
                    cleaned = m.group(1)
                options[letter] = clean_option_string(cleaned)
            elif item is not None:
                options[letter] = clean_option_string(item)

    # Verify exactly 4 options: A, B, C, D
    if set(options.keys()) != {"A", "B", "C", "D"}:
        return None

    # Verify all 4 options have meaningful, non-empty text
    for k in ["A", "B", "C", "D"]:
        val = options[k]
        if not val or not val.strip() or val.strip().lower() in PLACEHOLDERS:
            return None

    return options


def is_contradictory_explanation(letter: str, correct_ans: str, explanation_text: str) -> bool:
    """
    Detect whether an option explanation contradicts the known truth value of the option:
    - If letter != correct_ans (distractor), detects claims asserting it is the correct answer.
    - If letter == correct_ans, detects claims asserting it is incorrect.
    """
    text = explanation_text.lower()
    if letter != correct_ans:
        positive_cues = [
            "is correct", "is the correct", "is right", "is the right",
            "correct answer", "correct option", "correct choice", "correctly describes",
            "is the proper", "is accurate"
        ]
        negation_cues = [
            "not correct", "not the correct", "incorrect", "wrong", "not right",
            "not the right", "not accurate", "not the proper", "fails", "does not"
        ]
        has_positive = any(cue in text for cue in positive_cues)
        has_negation = any(cue in text for cue in negation_cues)
        if has_positive and not has_negation:
            return True
    else:
        negative_cues = [
            "is incorrect", "is not correct", "is wrong", "is not right",
            "incorrect choice", "incorrect option", "incorrect answer"
        ]
        if any(cue in text for cue in negative_cues):
            return True
    return False


def validate_single_question(raw_q: Any, topic_name: str = "General", return_details: bool = False) -> Any:
    """
    Validates a single multiple-choice question strictly:
      - Non-empty question text
      - Exactly four usable options (A, B, C, D) with meaningful text
      - All 4 options are semantically distinct (no duplicates)
      - Correct answer maps to one of the options
      - Calibrates and verifies programming output accuracy (printf, scanf, Python print)
    Returns cleaned and validated question dict, or None if malformed.
    If return_details is True, returns Tuple[Optional[Dict], Optional[str], Optional[str]]
    """
    if not isinstance(raw_q, dict):
        if return_details:
            return None, "Question candidate is not a JSON object", "SCHEMA"
        return None

    q_text = str(
        raw_q.get("question")
        or raw_q.get("question_text")
        or raw_q.get("text")
        or raw_q.get("prompt")
        or raw_q.get("q")
        or ""
    ).strip()

    if not q_text or q_text.lower() in {"null", "none", "n/a", "question", "question text"}:
        if return_details:
            return None, "Question text is missing or placeholder", "SCHEMA"
        return None

    raw_options = raw_q.get("options") or raw_q.get("choices") or raw_q.get("answers")
    options = extract_normalized_options(raw_options)
    if not options:
        if return_details:
            return None, "Options are missing, fewer than 4 choices, or malformed", "OPTIONS"
        return None

    raw_ans = (
        raw_q.get("correct_answer")
        or raw_q.get("answer")
        or raw_q.get("correct")
        or raw_q.get("correctAnswer")
    )
    correct_ans = normalize_correct_answer(raw_ans, options)
    if not correct_ans:
        if return_details:
            return None, "Correct answer letter is missing or does not map to options", "OPTIONS"
        return None

    concept = str(raw_q.get("concept") or raw_q.get("topic") or topic_name).strip() or topic_name
    explanation = str(
        raw_q.get("explanation")
        or raw_q.get("reason")
        or f"Option {correct_ans} is the correct answer based on {topic_name} principles."
    ).strip()

    raw_opt_expl = (
        raw_q.get("option_explanations")
        or raw_q.get("options_explanations")
        or raw_q.get("distractor_explanations")
        or raw_q.get("distractors")
    )
    option_explanations: Dict[str, str] = {}
    if isinstance(raw_opt_expl, dict):
        for k, v in raw_opt_expl.items():
            k_clean = str(k).strip().upper()
            if k_clean in {"A", "B", "C", "D"} and v:
                option_explanations[k_clean] = str(v).strip()
    elif isinstance(raw_opt_expl, list) and len(raw_opt_expl) >= 4:
        for idx, item in enumerate(raw_opt_expl[:4]):
            k_clean = ["A", "B", "C", "D"][idx]
            if item:
                option_explanations[k_clean] = str(item).strip()

    raw_diff = str(raw_q.get("difficulty") or "").strip().capitalize()
    difficulty = raw_diff if raw_diff in {"Easy", "Medium", "Hard"} else None

    # Guarantee complete and pedagogically sound option explanations for all 4 choices: A, B, C, D
    complete_opt_expl: Dict[str, str] = {}
    for letter in ["A", "B", "C", "D"]:
        existing = option_explanations.get(letter, "").strip()
        opt_val = options.get(letter, f"Option {letter}")

        # Detect and flag contradictory explanations
        if existing and is_contradictory_explanation(letter, correct_ans, existing):
            logger.warning(
                f"[PEDAGOGICAL CORRECTION] Contradictory explanation detected for option {letter} "
                f"(correct={correct_ans}): '{existing}'. Replacing with pedagogically sound explanation."
            )
            existing = ""

        # Detect unhelpful tautologies (e.g. "Option B is incorrect.")
        if existing:
            is_tautological = existing.lower() in {
                f"option {letter.lower()} is incorrect.",
                f"option {letter.lower()} is incorrect",
                f"option {letter.lower()} is wrong.",
                f"option {letter.lower()} is wrong",
                "incorrect.", "incorrect", "wrong.", "wrong"
            }
            if is_tautological:
                existing = ""

        if existing:
            complete_opt_expl[letter] = existing
        elif letter == correct_ans:
            complete_opt_expl[letter] = explanation or f"Option {letter} is the correct answer: it accurately reflects the principles of {concept}."
        else:
            complete_opt_expl[letter] = (
                f"Option {letter} ('{opt_val}') is incorrect. In the study of {concept}, "
                f"this option represents an alternative or common misconception that does not satisfy "
                f"the problem criteria. Refer to Option {correct_ans} for the valid approach."
            )

    candidate = {
        "question": q_text,
        "options": options,
        "correct_answer": correct_ans,
        "concept": concept,
        "explanation": explanation,
        "option_explanations": complete_opt_expl
    }
    if difficulty:
        candidate["difficulty"] = difficulty

    # Verify 4-option distinctness, whitespace-sensitive output, and printf/print accuracy
    is_valid, calibrated, reason = verify_and_calibrate_question_options(candidate)
    if not is_valid:
        logger.warning(f"[OPTION VALIDATION] Question rejected: {reason}")
        if return_details:
            return None, f"Option calibration failed: {reason}", "OPTIONS"
        return None

    # Verify academic and mathematical correctness (theorems, single correct answer, arithmetic, dimensions)
    try:
        is_acad_valid, acad_reason = verify_academic_correctness(calibrated)
        if not is_acad_valid:
            logger.warning(f"[ACADEMIC VALIDATION] Question rejected: {acad_reason}")
            if return_details:
                return None, f"Academic verifier rejected question: {acad_reason}", "ACADEMIC"
            return None
    except Exception as e:
        logger.error(f"[ACADEMIC VALIDATION] Verifier failed with exception: {e}. Preserving candidate.")

    if return_details:
        return calibrated, None, None
    return calibrated


def validate_quiz_data(
    data: Any,
    course_name: str,
    topic_name: str,
    concepts: Optional[List[str]] = None,
    syllabus_context: Optional[str] = None,
    min_questions: int = 1,
    difficulty_dist: Optional[Dict[str, int]] = None,
    course_code: str = "",
    competing_topics: Optional[List[Any]] = None,
    expected_count: Optional[int] = None
) -> Dict[str, Any]:
    """
    Validates that the generated data strictly fulfills quiz requirements:
      - Validates non-empty question statements and 4 non-empty options (A, B, C, D)
      - Verifies that correct answer maps to one of the 4 options
      - Verifies strict topic isolation and relevance against competing syllabus/document topics
      - Detects and removes exact and near-duplicate questions within the quiz
      - Normalizes concept tags to genuine syllabus concepts only AFTER topic verification
      - NEVER creates fake or '(Application Variation)' duplicate questions
    """
    from services.novelty import (
        detect_within_quiz_duplicates,
        is_question_relevant_to_topic,
        normalize_question_concept
    )

    if isinstance(data, list):
        questions = data
        data = {"questions": questions}
    elif isinstance(data, dict):
        questions = data.get("questions")
        if not questions and isinstance(data.get("quiz"), dict):
            questions = data["quiz"].get("questions")
        if not questions:
            for alt_key in ["quiz", "quiz_questions", "items"]:
                if isinstance(data.get(alt_key), list):
                    questions = data[alt_key]
                    break
    else:
        raise ValueError("Root response must be a JSON object or array.")

    if not isinstance(questions, list) or len(questions) < min_questions:
        count = len(questions) if isinstance(questions, list) else 0
        raise ValueError(f"Expected at least {min_questions} questions, got {count}.")

    valid_questions: List[Dict[str, Any]] = []
    rejection_reasons: List[Dict[str, Any]] = []

    for raw_q in questions:
        validated_q, fail_reason, fail_cat = validate_single_question(raw_q, topic_name=topic_name, return_details=True)
        if not validated_q:
            rejection_reasons.append({"question": raw_q, "reason": fail_reason, "category": fail_cat})
            continue

        # 1. Check topic relevance and isolation BEFORE normalizing concept tag
        # (Prevents off-topic questions from masking their identity via premature concept substitution)
        is_relevant, rel_reason = is_question_relevant_to_topic(
            validated_q,
            topic_name,
            concepts=concepts,
            syllabus_context=syllabus_context,
            course_code=course_code,
            competing_topics=competing_topics
        )
        if not is_relevant:
            logger.warning(f"[TOPIC ISOLATION FILTER] Skipped off-topic question: {rel_reason} ('{validated_q['question'][:50]}...')")
            rejection_reasons.append({"question": validated_q, "reason": rel_reason, "category": "TOPIC_ISOLATION"})
            continue

        # 2. Normalize concept tag to genuine syllabus concept only for validated questions
        validated_q["concept"] = normalize_question_concept(validated_q, topic_name, concepts)

        valid_questions.append(validated_q)

    # 3. Detect and remove within-quiz duplicates (exact and near-duplicates)
    dedup_res = detect_within_quiz_duplicates(valid_questions)
    unique_questions = dedup_res["unique_questions"]

    if dedup_res["has_duplicates"]:
        for reason in dedup_res["duplicate_reasons"]:
            logger.warning(f"[DEDUPLICATION FILTER] Dropped duplicate question: {reason}")

    if len(unique_questions) < min_questions:
        raise ValueError(f"Only {len(unique_questions)} unique questions passed strict relevance, duplicate, and option validation.")

    # Apply and calibrate difficulty distribution
    if not difficulty_dist:
        difficulty_dist = {"Easy": 4, "Medium": 4, "Hard": 2}
    expected_difficulties: List[str] = []
    for diff, count in difficulty_dist.items():
        expected_difficulties.extend([diff] * count)

    for index, q in enumerate(unique_questions):
        raw_diff = str(q.get("difficulty") or "").strip().capitalize()
        if raw_diff not in {"Easy", "Medium", "Hard"}:
            if index < len(expected_difficulties):
                raw_diff = expected_difficulties[index]
            else:
                raw_diff = "Medium"
        q["difficulty"] = raw_diff

    # If all questions have collapsed to a single difficulty but a multi-level distribution was requested,
    # align them to the requested distribution to prevent difficulty drift
    present_diffs = {q["difficulty"] for q in unique_questions}
    if len(present_diffs) == 1 and len(expected_difficulties) >= len(unique_questions) and len(difficulty_dist) > 1:
        for index, q in enumerate(unique_questions):
            q["difficulty"] = expected_difficulties[index]

    max_slice = expected_count if (expected_count and expected_count > 0) else (sum(difficulty_dist.values()) if difficulty_dist else 10)
    for d_reason in dedup_res.get("duplicate_reasons", []):
        rejection_reasons.append({"question": None, "reason": d_reason, "category": "DUPLICATE_INTERNAL"})

    return {
        "subject": course_name,
        "topic": topic_name,
        "questions": unique_questions[:max_slice],
        "unique_count": min(len(unique_questions), max_slice),
        "has_duplicates": dedup_res["has_duplicates"],
        "rejection_reasons": rejection_reasons
    }



class BaseAIProvider(ABC):
    """Abstract interface that all quiz AI providers must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the provider (e.g. 'gemini', 'groq')."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Returns True if the provider has an API key configured."""
        pass

    @abstractmethod
    def generate_quiz_raw(
        self,
        course_code: str,
        course_name: str,
        topic_name: str,
        syllabus_context: str,
        timeout_seconds: float,
        custom_prompt: Optional[str] = None,
        **kwargs
    ) -> str:
        """Generates raw quiz JSON text from the provider within the specified timeout."""
        pass

    @abstractmethod
    def generate_analysis_raw(
        self,
        subject: str,
        topic: str,
        weak_concepts: List[str],
        score: int,
        total: int,
        timeout_seconds: float
    ) -> str:
        """Generates raw feedback JSON text from the provider within the specified timeout."""
        pass
