"""
Enhanced Question and Concept Novelty Evaluation for LearnAI.

Maintains question fingerprints, concept tags, and learning objectives across
attempts to prevent both phrasing repetition and underlying concept repetition
during quiz retakes.
"""
import re
import logging
from typing import List, Tuple, Dict, Any, Set, Optional

logger = logging.getLogger("learnai.novelty")

TEXT_DUPLICATE_THRESHOLD = 0.45     # Jaccard on word trigrams/bigrams (lower = stricter)
CONCEPT_SIMILARITY_THRESHOLD = 0.50 # Jaccard on normalized concept tokens (lower = stricter)
HISTORY_QUESTION_LIMIT = 50         # Max recent questions retained in memory
HISTORY_CONCEPT_LIMIT = 30          # Max recent concepts retained in memory
MAX_EXCLUSION_PROMPT_QUESTIONS = 10 # Questions shown in LLM exclusion list
MAX_EXCLUSION_PROMPT_CONCEPTS = 12  # Concepts shown in LLM exclusion list

COMMON_STOPWORDS = {
    "a", "an", "the", "in", "on", "of", "for", "to", "at", "by", "with", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "which", "what", "how", "why", "when", "where", "who",
    "that", "this", "these", "those", "and", "or", "but", "if", "not", "as",
    "can", "could", "should", "would", "will", "shall", "into", "through", "about"
}


# ─────────────────────────────────────────────────────────────────────────────
# Text and Concept Normalization
# ─────────────────────────────────────────────────────────────────────────────

def normalise_text(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fingerprint(question_text: str) -> str:
    """Return a canonical fingerprint for a question string."""
    return normalise_text(question_text)


def extract_concept_keywords(concept_text: str) -> Set[str]:
    """Extract significant content keywords from a concept tag."""
    norm = normalise_text(concept_text)
    words = set(norm.split())
    keywords = {w for w in words if w not in COMMON_STOPWORDS and len(w) > 2}
    return keywords if keywords else words


def _ngrams(text: str, n: int = 3) -> Set[str]:
    """Generate word n-grams from normalized text."""
    words = text.split()
    if len(words) < n:
        return {text} if text else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _jaccard(set_a: Set[str], set_b: Set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def calculate_question_similarity(q1: str, q2: str) -> float:
    """
    Computes text similarity using a blend of content keyword overlap,
    bigrams, and trigrams to catch both exact matches and paraphrased questions.
    Returns value between 0.0 and 1.0.
    """
    norm1 = normalise_text(q1)
    norm2 = normalise_text(q2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0

    kw1 = extract_concept_keywords(norm1)
    kw2 = extract_concept_keywords(norm2)
    j_kw = _jaccard(kw1, kw2)

    trigrams1 = _ngrams(norm1, 3)
    trigrams2 = _ngrams(norm2, 3)
    j_tri = _jaccard(trigrams1, trigrams2)

    bigrams1 = _ngrams(norm1, 2)
    bigrams2 = _ngrams(norm2, 2)
    j_bi = _jaccard(bigrams1, bigrams2)

    # Blend: strong keyword overlap (paraphrasing) or n-gram match (phrasing copy)
    return max(j_tri, j_bi * 0.9, j_kw * 0.85)


def calculate_concept_similarity(concept1: str, concept2: str) -> float:
    """
    Computes semantic similarity between two concept tags using a combination of
    Jaccard keyword similarity and overlap coefficient (useful when one concept is concisely named).
    """
    norm1 = normalise_text(concept1)
    norm2 = normalise_text(concept2)
    if norm1 == norm2 and norm1:
        return 1.0

    kw1 = extract_concept_keywords(concept1)
    kw2 = extract_concept_keywords(concept2)
    if not kw1 or not kw2:
        return 0.0

    jaccard = _jaccard(kw1, kw2)
    min_size = min(len(kw1), len(kw2))
    overlap_coeff = len(kw1 & kw2) / min_size if min_size else 0.0

    return max(jaccard, overlap_coeff)


# ─────────────────────────────────────────────────────────────────────────────
# Within-Quiz Duplicate Detection & Option Verification
# ─────────────────────────────────────────────────────────────────────────────

def are_options_duplicate(opts1: Any, opts2: Any) -> Tuple[bool, float]:
    """Checks if two questions share essentially the same 4 options, even if permuted."""
    if not isinstance(opts1, dict) or not isinstance(opts2, dict):
        return False, 0.0
    s1 = {normalise_text(str(v)) for v in opts1.values() if v and len(normalise_text(str(v))) > 0}
    s2 = {normalise_text(str(v)) for v in opts2.values() if v and len(normalise_text(str(v))) > 0}
    if not s1 or not s2:
        return False, 0.0
    overlap = len(s1 & s2) / max(len(s1), len(s2))
    return (overlap >= 0.75, overlap)


def is_question_near_duplicate(q1: Dict[str, Any], q2: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Determines if q2 is an exact or near-duplicate of q1 within the same quiz or across attempts.
    Checks:
      1. Exact wording match
      2. Artificial variation patterns: '(Application Variation)', 'Variation'
      3. High textual similarity (phrasing copy)
      4. Option set permutation with shared question topic
      5. Same correct answer text with moderate question overlap
    """
    t1 = str(q1.get("question", "")).strip()
    t2 = str(q2.get("question", "")).strip()
    if not t1 or not t2:
        return False, ""

    norm1 = normalise_text(t1)
    norm2 = normalise_text(t2)

    # 1. Exact match
    if norm1 == norm2:
        return True, f"Exact identical question wording: '{t2[:60]}...'"

    # 2. Artificial variations (e.g. "(Application Variation)")
    base1 = re.sub(r'\(?\b(?:application\s+)?variation\b\)?', '', norm1).strip()
    base2 = re.sub(r'\(?\b(?:application\s+)?variation\b\)?', '', norm2).strip()
    if base1 == base2 or (base1 and base2 and calculate_question_similarity(base1, base2) >= 0.70):
        return True, f"Artificial variation of existing question: '{t2[:60]}...'"

    # 3. High textual / phrasing similarity
    sim = calculate_question_similarity(t1, t2)
    if sim >= 0.50:
        return True, f"High phrasing similarity ({sim:.2f}) with question: '{t1[:60]}...'"

    # 4. Option set duplication (permuted options)
    opts_dup, opt_ratio = are_options_duplicate(q1.get("options"), q2.get("options"))
    if opts_dup and sim >= 0.28:
        return True, f"Identical answer option set ({opt_ratio:.0%} match) with question: '{t1[:60]}...'"

    # 5. Same correct answer text with moderate question overlap
    c1 = str(q1.get("correct_answer", "")).upper()
    c2 = str(q2.get("correct_answer", "")).upper()
    ans_val1 = normalise_text(str(q1.get("options", {}).get(c1, "")))
    ans_val2 = normalise_text(str(q2.get("options", {}).get(c2, "")))
    if ans_val1 and ans_val2 and ans_val1 == ans_val2 and len(ans_val1.split()) >= 2 and sim >= 0.38:
        return True, f"Tests identical target answer fact ('{ans_val1[:40]}') with similar stem ({sim:.2f})"

    return False, ""


def detect_within_quiz_duplicates(questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Scans all questions within a single quiz to find exact and near-duplicates.
    Returns unique questions, flagged duplicate indices, duplicate pairs, and reasons.
    """
    duplicate_indices = set()
    duplicate_pairs = []
    reasons = []

    for j in range(len(questions)):
        if j in duplicate_indices:
            continue
        for i in range(j):
            if i in duplicate_indices:
                continue
            is_dup, reason = is_question_near_duplicate(questions[i], questions[j])
            if is_dup:
                duplicate_indices.add(j)
                duplicate_pairs.append((i, j, reason))
                reasons.append(f"Q{j + 1} duplicates Q{i + 1}: {reason}")
                break

    unique_questions = [q for idx, q in enumerate(questions) if idx not in duplicate_indices]
    return {
        "has_duplicates": len(duplicate_indices) > 0,
        "duplicate_indices": sorted(list(duplicate_indices)),
        "duplicate_pairs": duplicate_pairs,
        "unique_questions": unique_questions,
        "duplicate_reasons": reasons
    }


GENERIC_DOMAIN_WORDS = {
    "linear", "concept", "concepts", "introduction", "basic", "basics",
    "definition", "definitions", "application", "applications", "method", "methods",
    "type", "types", "system", "systems", "example", "examples", "properties",
    "overview", "problem", "problems", "theory", "process", "rule", "rules",
    "function", "functions", "model", "models", "structure", "structures",
    "analysis", "technique", "techniques", "simple", "general", "standard",
    "different", "following", "given", "common", "important", "order", "value", "values"
}


def is_question_relevant_to_topic(
    question: Dict[str, Any],
    topic_name: str,
    concepts: Optional[List[str]] = None,
    syllabus_context: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Verifies whether a question is relevant to the selected topic and its syllabus scope.
    Prevents AI from generating questions on unrelated topics from the same course.
    """
    q_text = str(question.get("question", "")).strip()
    if not q_text:
        return False, "Empty question statement"

    # Extract domain vocabulary
    vocab_sources = [topic_name]
    if concepts:
        vocab_sources.extend(concepts)
    if syllabus_context:
        vocab_sources.append(syllabus_context)

    target_text = " ".join(vocab_sources)
    target_norm = normalise_text(target_text)
    target_keywords = extract_concept_keywords(target_norm)

    if len(target_keywords) < 3:
        words = set(target_norm.split())
        target_keywords = {w for w in words if w not in COMMON_STOPWORDS and len(w) >= 2}

    # Question keywords (text + concept + explanation + options)
    q_full = q_text + " " + str(question.get("concept", "")) + " " + str(question.get("explanation", ""))
    if isinstance(question.get("options"), dict):
        for opt_val in question.get("options", {}).values():
            q_full += " " + str(opt_val)

    q_norm = normalise_text(q_full)
    q_keywords = extract_concept_keywords(q_norm)

    # Overlap check
    shared = target_keywords & q_keywords
    distinctive = shared - GENERIC_DOMAIN_WORDS
    if len(distinctive) >= 1:
        return True, f"Distinctive relevant keywords found: {list(distinctive)[:4]}"
    if len(shared) >= 2:
        return True, f"Relevant keywords found: {list(shared)[:4]}"

    # Substring / stem match (e.g. 'matrix' in 'matrices', 'sigmoid' in 'sigmoidal')
    for kw in target_keywords:
        if kw not in GENERIC_DOMAIN_WORDS and len(kw) >= 4 and any(kw in qw or qw in kw for qw in q_keywords if len(qw) >= 4):
            return True, f"Stem match with keyword '{kw}'"

    return False, f"Question has no relevance to topic '{topic_name}' or its syllabus concepts"


def normalize_question_concept(
    q: Dict[str, Any],
    topic_name: str,
    concepts: Optional[List[str]] = None
) -> str:
    """
    Normalizes the concept field of a question to align with genuine syllabus concepts.
    Maps vague or missing concepts to the most appropriate syllabus concept.
    """
    raw_c = str(q.get("concept", "")).strip()
    PLACEHOLDERS = {"concept name", "concept", "general", "none", "n/a", "null", "undefined", ""}

    if concepts:
        clean_concepts = [c.strip() for c in concepts if c and c.strip()]
        if clean_concepts:
            # 1. Direct match with a syllabus concept
            for sc in clean_concepts:
                if normalise_text(sc) == normalise_text(raw_c):
                    return sc

            # 2. Similarity match with raw concept
            if raw_c.lower() not in PLACEHOLDERS:
                best_sc = None
                best_sim = 0.0
                for sc in clean_concepts:
                    sim = calculate_concept_similarity(raw_c, sc)
                    if sim > best_sim:
                        best_sim = sim
                        best_sc = sc
                if best_sim >= 0.40 and best_sc:
                    return best_sc

            # 3. Keyword and semantic match with question text
            q_text = str(q.get("question", ""))
            q_norm = normalise_text(q_text)
            q_kw = extract_concept_keywords(q_norm)

            best_sc = None
            max_score = 0.0
            for sc in clean_concepts:
                sc_norm = normalise_text(sc)
                sc_kw = extract_concept_keywords(sc_norm)
                score = 0.0
                # Direct phrase match in question text
                if sc_norm in q_norm:
                    score += 10.0
                # Distinctive keyword overlap
                distinctive_overlap = (sc_kw & q_kw) - GENERIC_DOMAIN_WORDS
                score += len(distinctive_overlap) * 3.0
                # Generic keyword overlap
                generic_overlap = (sc_kw & q_kw) & GENERIC_DOMAIN_WORDS
                score += len(generic_overlap) * 1.0
                # Stem matching
                for skw in sc_kw:
                    if skw not in GENERIC_DOMAIN_WORDS and len(skw) >= 4:
                        if any(skw in qk or qk in skw for qk in q_kw if len(qk) >= 4):
                            score += 1.5
                if score > max_score:
                    max_score = score
                    best_sc = sc
            if max_score > 0.0 and best_sc:
                return best_sc

            # If single concept in topic, use it
            if len(clean_concepts) == 1:
                return clean_concepts[0]

    # Fallback: if raw_c is meaningful and not a placeholder or course name, keep it
    if raw_c and raw_c.lower() not in PLACEHOLDERS and len(raw_c) > 2:
        return raw_c

    return topic_name


# ─────────────────────────────────────────────────────────────────────────────
# History Extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_fingerprints_from_history(topic_history: List[Dict[str, Any]]) -> List[str]:
    """Pull question texts from recent quiz_attempts and return fingerprints."""
    fps: List[str] = []
    for attempt in topic_history:
        quiz_data = attempt.get("quiz_data", {})
        questions = quiz_data.get("questions", []) if isinstance(quiz_data, dict) else []
        for q in questions:
            q_text = q.get("question", "") if isinstance(q, dict) else ""
            if q_text:
                fp = fingerprint(q_text)
                if fp not in fps:
                    fps.append(fp)
            if len(fps) >= HISTORY_QUESTION_LIMIT:
                return fps
    return fps


def extract_concepts_from_history(topic_history: List[Dict[str, Any]]) -> List[str]:
    """Pull concept tags from recent quiz_attempts, preserving uniqueness."""
    concepts: List[str] = []
    for attempt in topic_history:
        quiz_data = attempt.get("quiz_data", {})
        questions = quiz_data.get("questions", []) if isinstance(quiz_data, dict) else []
        for q in questions:
            c_text = q.get("concept", "") if isinstance(q, dict) else ""
            if c_text:
                c_clean = c_text.strip()
                if c_clean and c_clean not in concepts:
                    concepts.append(c_clean)
            if len(concepts) >= HISTORY_CONCEPT_LIMIT:
                return concepts
    return concepts


# ─────────────────────────────────────────────────────────────────────────────
# Novelty Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_quiz_novelty(
    questions: List[Dict[str, Any]],
    history_fingerprints: List[str],
    history_concepts: List[str]
) -> Dict[str, Any]:
    """
    Evaluates whether a generated quiz contains duplicates (text phrasing or concepts)
    compared to the student's previous attempts.

    Returns:
        dict:
            is_fully_novel: bool
            text_duplicate_indices: List[int]
            concept_duplicate_indices: List[int]
            all_flagged_indices: List[int]
            duplicate_reasons: List[str]
            novel_count: int
    """
    if not history_fingerprints and not history_concepts:
        return {
            "is_fully_novel": True,
            "text_duplicate_indices": [],
            "concept_duplicate_indices": [],
            "all_flagged_indices": [],
            "duplicate_reasons": [],
            "novel_count": len(questions)
        }

    text_dups = []
    concept_dups = []
    reasons = []

    for idx, q in enumerate(questions):
        q_text = q.get("question", "")
        q_concept = q.get("concept", "")
        q_fp = fingerprint(q_text)

        # 1. Text duplicate check
        is_text_dup = False
        for hist_fp in history_fingerprints:
            sim = calculate_question_similarity(q_fp, hist_fp)
            if sim >= TEXT_DUPLICATE_THRESHOLD:
                text_dups.append(idx)
                reasons.append(f"Q{idx + 1} phrasing similarity ({sim:.2f}) with previous question: '{hist_fp[:50]}...'")
                is_text_dup = True
                break

        # 2. Concept tracking (informational, not a duplicate rejection)
        if not is_text_dup and history_concepts:
            for hist_concept in history_concepts:
                c_sim = calculate_concept_similarity(q_concept, hist_concept)
                if c_sim >= CONCEPT_SIMILARITY_THRESHOLD:
                    concept_dups.append(idx)
                    break

    # Only actual phrasing / question duplicates are flagged for replacement
    all_flagged = sorted(list(set(text_dups)))
    is_fully_novel = len(all_flagged) == 0

    return {
        "is_fully_novel": is_fully_novel,
        "text_duplicate_indices": text_dups,
        "concept_duplicate_indices": concept_dups,
        "all_flagged_indices": all_flagged,
        "duplicate_reasons": reasons,
        "novel_count": len(questions) - len(all_flagged)
    }


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Building
# ─────────────────────────────────────────────────────────────────────────────

def build_exclusion_prompt_block(
    history_fingerprints: List[str],
    history_concepts: List[str],
    attempt_id: str,
    attempt_number: int = 1
) -> str:
    """
    Constructs an explicit anti-repetition block for the LLM prompt.
    Supplies both previous question phrasing and already-tested concepts.
    """
    if not history_fingerprints and not history_concepts:
        return (
            f"Quiz Attempt ID: {attempt_id} (First Attempt)\n"
            "Generate 10 fresh, high-quality multiple choice questions covering foundational and advanced aspects of this topic.\n"
        )

    parts = [f"Quiz Attempt ID: {attempt_id} (Retake Attempt #{attempt_number})\n"]
    parts.append("CRITICAL NOVELTY AND CONCEPT DIVERSITY REQUIREMENTS:")
    parts.append(
        "The student has already attempted a quiz on this topic. Repeating questions, identical answers, "
        "or testing the exact same sub-concepts will degrade the learning experience.\n"
    )

    if history_concepts:
        sample_concepts = history_concepts[:MAX_EXCLUSION_PROMPT_CONCEPTS]
        concept_list = ", ".join(f"'{c}'" for c in sample_concepts)
        parts.append(
            f"1. PREVIOUSLY COVERED CONCEPTS:\n"
            f"   [{concept_list}]\n"
            f"   Test syllabus concepts through FRESH angles, new numerical values, edge cases, "
            f"   practical scenarios, and applied problems rather than repeating previous questions.\n"
        )

    if history_fingerprints:
        sample_questions = history_fingerprints[:MAX_EXCLUSION_PROMPT_QUESTIONS]
        q_list = "\n".join(f"   - {fp}" for fp in sample_questions)
        parts.append(
            f"2. PREVIOUS QUESTION PHRASINGS (DO NOT REPEAT OR PARAPHRASE):\n"
            f"{q_list}\n"
        )

    parts.append(
        "3. RULES FOR FRESH QUESTIONS:\n"
        "   - You MUST generate and return all 10 questions in the 'questions' array (count must be exactly 10).\n"
        "   - Every question must test a distinct learning objective or scenario.\n"
        "   - Do not ask standard definition questions if already tested.\n"
        "   - Use applied problem statements (e.g. given specific input values, weights, or conditions, what is the output?).\n"
        "   - Ensure all 4 options are distinct, plausible, and only one is correct.\n"
    )

    return "\n".join(parts)
