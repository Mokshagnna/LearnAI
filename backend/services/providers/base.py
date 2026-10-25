import json
import re
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List

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
    concepts: Optional[List[str]] = None
) -> str:
    """Common standardized prompt for all AI providers with strict topic grounding and anti-duplication directives."""
    if not difficulty_dist:
        difficulty_dist = {"Easy": 4, "Medium": 4, "Hard": 2}

    e_cnt = difficulty_dist.get("Easy", 4)
    m_cnt = difficulty_dist.get("Medium", 4)
    h_cnt = difficulty_dist.get("Hard", 2)

    diff_lines = []
    if e_cnt > 0:
        diff_lines.append(f"- Questions 1 to {e_cnt} must be Easy")
    if m_cnt > 0:
        diff_lines.append(f"- Questions {e_cnt + 1} to {e_cnt + m_cnt} must be Medium")
    if h_cnt > 0:
        diff_lines.append(f"- Questions {10 - h_cnt + 1} to 10 must be Hard")
    diff_req = "\n".join(diff_lines)

    concept_instructions = ""
    if concepts and len(concepts) > 0:
        clean_concepts = [c.strip() for c in concepts if c and c.strip()]
        if clean_concepts:
            formatted_concepts = "\n".join(f"  * {c}" for c in clean_concepts)
            concept_instructions = f"""
Syllabus Concepts to Cover for '{topic_name}':
{formatted_concepts}

- You MUST distribute the 10 questions across these syllabus concepts for balanced syllabus coverage.
- Do NOT test only one single concept repeatedly while neglecting the others.
- Ensure each question explicitly sets its "concept" field to the relevant syllabus concept name from this list.
"""

    return f"""You are an expert academic AI quiz generator for university students (B.Tech CSE AI&ML curriculum).

Course: {course_code} - {course_name}
Selected Topic: {topic_name}
{concept_instructions}
Syllabus Reference Context:
{syllabus_context}

CRITICAL RULES FOR RELEVANCE AND INTEGRITY:
1. STRICT TOPIC RELEVANCE (MANDATORY):
   - ALL 10 questions MUST strictly and directly assess the Selected Topic: "{topic_name}".
   - Under NO circumstances should you include questions from other topics, modules, or units of {course_name}.
   - Do NOT bring in unrelated concepts from other parts of the course syllabus just to fill 10 questions.
   - Ground all questions solely in the selected topic and its syllabus concepts.

2. ZERO DUPLICATE QUESTIONS (MANDATORY):
   - Every question must test a DISTINCT learning objective, property, application, scenario, or calculation.
   - Do NOT ask the same question multiple times with minor rewording.
   - Do NOT repeat questions with permuted options or different answer letter positions.
   - Do NOT generate artificial variations (e.g., "(Application Variation)" of earlier questions).
   - Ensure high question diversity across the topic's learning objectives.

3. STRUCTURE & FORMAT REQUIREMENTS:
   - Return EXACTLY 10 multiple-choice questions in the "questions" array.
   {diff_req}
   - Every question MUST contain exactly 4 distinct, non-empty options with keys "A", "B", "C", and "D".
   - All option values must be complete, meaningful, and non-empty. Never leave any option blank or with placeholder text.
   - "correct_answer" MUST be the exact uppercase letter corresponding to the correct option: "A", "B", "C", or "D".
   - In the "concept" field, specify the exact syllabus concept or learning objective assessed.
   - Include a concise 1-sentence explanation for the correct answer.
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
      "explanation": "Concise 1-sentence explanation"
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


def extract_json_from_text(text: str) -> Any:
    """Strips markdown code fences and parses raw JSON string safely with automatic repair for minor formatting quirks."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()

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
            if k_clean in {"A", "B", "C", "D"}:
                options[k_clean] = str(v).strip()
            elif any(k_clean.startswith(prefix) for prefix in ("OPTION", "CHOICE", "ANS", "OPT")):
                sub_k = re.sub(r'^(?:OPTION|CHOICE|ANS|OPT)[_\s\-\.:]*', '', k_clean).strip()
                if sub_k in {"A", "B", "C", "D"}:
                    options[sub_k] = str(v).strip()
                elif sub_k in {"1", "2", "3", "4"}:
                    sub_letter = {"1": "A", "2": "B", "3": "C", "4": "D"}[sub_k]
                    options[sub_letter] = str(v).strip()
            elif k_clean in {"1", "2", "3", "4"}:
                sub_letter = {"1": "A", "2": "B", "3": "C", "4": "D"}[k_clean]
                options[sub_letter] = str(v).strip()

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
                options[letter] = str(text).strip()
            elif isinstance(item, str):
                cleaned = item.strip()
                m = re.match(r'^[A-D]\s*[\)\.:\-]\s*(.+)', cleaned, flags=re.IGNORECASE)
                if m:
                    cleaned = m.group(1).strip()
                options[letter] = cleaned
            elif item is not None:
                options[letter] = str(item).strip()

    # Verify exactly 4 options: A, B, C, D
    if set(options.keys()) != {"A", "B", "C", "D"}:
        return None

    # Verify all 4 options have meaningful, non-empty text
    for k in ["A", "B", "C", "D"]:
        val = options[k]
        if not val or val.lower() in PLACEHOLDERS:
            return None

    return options


def validate_single_question(raw_q: Any, topic_name: str = "General") -> Optional[Dict[str, Any]]:
    """
    Validates a single multiple-choice question strictly:
      - Non-empty question text
      - Exactly four usable options (A, B, C, D) with meaningful text
      - Correct answer maps to one of the options
    Returns cleaned question dict, or None if malformed.
    """
    if not isinstance(raw_q, dict):
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
        return None

    raw_options = raw_q.get("options") or raw_q.get("choices") or raw_q.get("answers")
    options = extract_normalized_options(raw_options)
    if not options:
        return None

    raw_ans = (
        raw_q.get("correct_answer")
        or raw_q.get("answer")
        or raw_q.get("correct")
        or raw_q.get("correctAnswer")
    )
    correct_ans = normalize_correct_answer(raw_ans, options)
    if not correct_ans:
        return None

    concept = str(raw_q.get("concept") or raw_q.get("topic") or topic_name).strip() or topic_name
    explanation = str(
        raw_q.get("explanation")
        or raw_q.get("reason")
        or f"Option {correct_ans} is the correct answer based on {topic_name} principles."
    ).strip()

    raw_diff = str(raw_q.get("difficulty") or "").strip().capitalize()
    difficulty = raw_diff if raw_diff in {"Easy", "Medium", "Hard"} else None

    res = {
        "question": q_text,
        "options": options,
        "correct_answer": correct_ans,
        "concept": concept,
        "explanation": explanation
    }
    if difficulty:
        res["difficulty"] = difficulty
    return res


def validate_quiz_data(
    data: Any,
    course_name: str,
    topic_name: str,
    concepts: Optional[List[str]] = None,
    syllabus_context: Optional[str] = None,
    min_questions: int = 5,
    difficulty_dist: Optional[Dict[str, int]] = None
) -> Dict[str, Any]:
    """
    Validates that the generated data strictly fulfills quiz requirements:
      - Validates non-empty question statements and 4 non-empty options (A, B, C, D)
      - Verifies that correct answer maps to one of the 4 options
      - Verifies question relevance to selected topic and syllabus context
      - Detects and removes exact and near-duplicate questions within the quiz
      - Normalizes concept tags to genuine syllabus concepts
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

    for raw_q in questions:
        validated_q = validate_single_question(raw_q, topic_name=topic_name)
        if not validated_q:
            continue

        # 1. Normalize concept tag to genuine syllabus concept
        validated_q["concept"] = normalize_question_concept(validated_q, topic_name, concepts)

        # 2. Check topic relevance
        is_relevant, rel_reason = is_question_relevant_to_topic(
            validated_q, topic_name, concepts=concepts, syllabus_context=syllabus_context
        )
        if not is_relevant:
            logger.warning(f"[RELEVANCE FILTER] Skipped off-topic question: {rel_reason} ('{validated_q['question'][:50]}...')")
            continue

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

    return {
        "subject": course_name,
        "topic": topic_name,
        "questions": unique_questions[:10] if len(unique_questions) >= 10 else unique_questions,
        "unique_count": min(len(unique_questions), 10),
        "has_duplicates": dedup_res["has_duplicates"]
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
