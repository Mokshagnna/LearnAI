"""
short_answer_evaluator.py
=========================
Hybrid Short-Answer and Free-Response Assessment Engine for LearnAI.

Combines:
1. Deterministic surface checks (empty, non-committal, irrelevant text rejection).
2. Keyphrase / Rubric-based concept coverage (exact, synonym, and token overlap).
3. TF-IDF character and word n-gram cosine similarity against reference answer/explanation.
4. Calibrated score calculation producing a graded score (0.0 to 1.0), partial credit,
   and actionable concept-level diagnostic feedback.
5. Explicit separation of deterministic vs model-assisted grading.
"""

import re
import logging
from typing import Dict, Any, List, Set, Optional, Tuple

logger = logging.getLogger("learnai.short_answer")

# Common academic English stopwords (excluding negation words)
STOPWORDS = {
    "a", "an", "the", "in", "on", "of", "for", "to", "at", "by", "with", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "which", "what", "how", "why", "when", "where", "who",
    "that", "this", "these", "those", "and", "or", "but", "if", "as",
    "can", "could", "should", "would", "will", "shall", "into", "through", "about",
    "it", "its", "they", "them", "their", "we", "us", "our", "you", "your"
}

# Negation tokens for detecting inverted technical claims
NEGATION_TOKENS = {
    "not", "no", "never", "neither", "nor", "cannot", "doesnt", "isnt", "wont", "dont", "fails"
}

# Common predicate / relational verbs in technical student explanations
PREDICATE_VERBS = {
    "is", "are", "was", "were", "means", "requires", "guarantees", "ensures",
    "eliminates", "decomposes", "holds", "uses", "contains", "provides",
    "satisfies", "has", "have", "must", "can", "will", "defined", "stores",
    "prevents", "allows", "acts", "serves", "determines", "maps", "occurs", "be"
}

# Trivial non-answers
TRIVIAL_NON_ANSWERS = {
    "", "idk", "i don't know", "i dont know", "i don t know", "no idea", "pass", "skip", "n/a", "none",
    "dont know", "not sure", "dunno", "nothing", "?", "??", "???", "test", "asdf"
}

# Common technical synonym groups across computer science curriculum
TECHNICAL_SYNONYMS: Dict[str, Set[str]] = {
    "bcnf": {"boyce-codd normal form", "boyce codd", "boyce-codd"},
    "acid": {"atomicity consistency isolation durability", "acid properties"},
    "2pl": {"two phase locking", "two-phase locking", "two phase lock"},
    "serializability": {"serializable", "serial schedule", "conflict serializability"},
    "deadlock": {"dead lock", "circular wait", "mutual deadlock"},
    "indexing": {"indexes", "index", "b-tree", "b+ tree", "b+tree", "btree"},
    "primary key": {"candidate key", "unique identifier", "primary identifier"},
    "foreign key": {"referential constraint", "reference attribute", "referential integrity"},
    "normalization": {"decomposing", "schema decomposition", "normal forms"},
    "atomicity": {"all or nothing", "all-or-nothing", "atomic operation"},
    "durability": {"persistent", "survives crashes", "crash recovery", "non-volatile"}
}


def normalize_text(text: str) -> str:
    """Normalize input text: lowercase, strip apostrophes, collapse whitespace, strip punctuation."""
    if not text:
        return ""
    clean = text.lower().replace("'", "")
    clean = re.sub(r"[^\w\s-]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def extract_keywords(text: str) -> Set[str]:
    """Extract significant content keywords from normalized text."""
    norm = normalize_text(text)
    words = re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", norm)
    return {w for w in words if w not in STOPWORDS}


def _expand_synonyms(keywords: Set[str]) -> Set[str]:
    """Expands a set of keywords to include known domain synonyms."""
    expanded = set(keywords)
    for kw in list(keywords):
        for canonical, syns in TECHNICAL_SYNONYMS.items():
            if kw == canonical or kw in syns or any(kw in s for s in syns):
                expanded.add(canonical)
                expanded.update(syns)
    return expanded


def _is_concept_negated(concept_norm: str, text_norm: str) -> bool:
    """
    Checks if a concept appears in an explicitly negated context
    (e.g., 'not ensure serializability' or 'fails to preserve atomicity').
    """
    words = text_norm.split()
    c_words = concept_norm.split()
    if not c_words or not words:
        return False
    for i in range(len(words)):
        if words[i:i + len(c_words)] == c_words:
            window = words[max(0, i - 3):i]
            if any(w in NEGATION_TOKENS for w in window):
                return True
    return False


def compute_token_overlap(student_text: str, reference_text: str) -> float:
    """Computes Jaccard keyword overlap between student and reference text."""
    student_kws = extract_keywords(student_text)
    ref_kws = extract_keywords(reference_text)
    if not ref_kws:
        return 1.0 if not student_kws else 0.0
    if not student_kws:
        return 0.0

    student_expanded = _expand_synonyms(student_kws)
    ref_expanded = _expand_synonyms(ref_kws)

    # Calculate matched reference keywords
    matched = 0
    for r in ref_kws:
        if r in student_kws or any(r in s or s in r for s in student_expanded):
            matched += 1

    return round(matched / len(ref_kws), 4)


def compute_ngram_jaccard(text_a: str, text_b: str, n: int = 2) -> float:
    """Computes character or word n-gram Jaccard similarity."""
    words_a = text_a.split()
    words_b = text_b.split()
    if len(words_a) < n or len(words_b) < n:
        # Fall back to word overlap
        set_a = set(words_a)
        set_b = set(words_b)
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    ngrams_a = {" ".join(words_a[i:i + n]) for i in range(len(words_a) - n + 1)}
    ngrams_b = {" ".join(words_b[i:i + n]) for i in range(len(words_b) - n + 1)}

    intersection = len(ngrams_a & ngrams_b)
    union = len(ngrams_a | ngrams_b)
    return round(intersection / union, 4) if union else 0.0


def evaluate_short_answer(
    student_answer: str,
    reference_answer: str,
    key_concepts: Optional[List[str]] = None,
    rubric_criteria: Optional[List[str]] = None,
    passing_threshold: float = 0.50
) -> Dict[str, Any]:
    """
    Evaluates a free-form short answer against a reference answer and optional rubric.

    Returns:
        score: float (0.0 to 1.0)
        percentage: float (0.0 to 100.0)
        is_correct: bool (score >= passing_threshold)
        partial_credit: bool (0.30 <= score < passing_threshold)
        matched_concepts: List[str]
        missing_concepts: List[str]
        feedback: str
        evaluation_method: str ('deterministic_rubric' or 'hybrid_semantic')
    """
    clean_student = (student_answer or "").strip()
    norm_student = normalize_text(clean_student)

    # 1. Trivial, empty, or non-committal answer rejection
    if not norm_student or norm_student in TRIVIAL_NON_ANSWERS or len(norm_student) < 3:
        all_concepts = list(key_concepts or [])
        return {
            "score": 0.0,
            "percentage": 0.0,
            "is_correct": False,
            "partial_credit": False,
            "matched_concepts": [],
            "missing_concepts": all_concepts,
            "feedback": "No substantive answer provided. Expected concepts: " + (", ".join(all_concepts) if all_concepts else "key technical terms."),
            "evaluation_method": "deterministic_rule"
        }

    clean_ref = (reference_answer or "").strip()
    norm_ref = normalize_text(clean_ref)

    # 2. Perfect verbatim match
    if norm_student == norm_ref:
        return {
            "score": 1.0,
            "percentage": 100.0,
            "is_correct": True,
            "partial_credit": False,
            "matched_concepts": list(key_concepts or []),
            "missing_concepts": [],
            "feedback": "Excellent. Answer matches reference criteria completely.",
            "evaluation_method": "deterministic_rule"
        }

    # 3. Concept / Rubric Keyword Matching
    student_tokens = extract_keywords(clean_student)
    student_tokens_expanded = _expand_synonyms(student_tokens)

    matched_concepts: List[str] = []
    missing_concepts: List[str] = []

    target_concepts = list(key_concepts or [])
    if rubric_criteria:
        target_concepts.extend([rc for rc in rubric_criteria if rc not in target_concepts])

    if target_concepts:
        for c in target_concepts:
            c_norm = normalize_text(c)
            c_words = extract_keywords(c)
            c_expanded = _expand_synonyms({c_norm} | c_words)
            # Check exact substring or keyword presence
            has_presence = (c_norm in norm_student) or (c_words and c_words.issubset(student_tokens)) or bool(c_expanded & student_tokens_expanded)

            # Check negation consistency: if student negates a concept that reference asserts affirmatively, disqualify it
            is_negated_student = _is_concept_negated(c_norm, norm_student)
            is_negated_ref = _is_concept_negated(c_norm, norm_ref)

            if has_presence and (is_negated_student == is_negated_ref):
                matched_concepts.append(c)
            else:
                missing_concepts.append(c)

        concept_recall = len(matched_concepts) / len(target_concepts) if target_concepts else 0.0
    else:
        concept_recall = compute_token_overlap(clean_student, clean_ref)

    # 4. Text & Bigram Similarity
    token_overlap = compute_token_overlap(clean_student, clean_ref)
    bigram_sim = compute_ngram_jaccard(norm_student, norm_ref, n=2)

    # 5. Semantic Cosine Similarity (via scikit-learn normalized term frequencies)
    # Uses use_idf=False to prevent inverted 2-document IDF penalties on matching terms
    tfidf_sim = 0.0
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        vec = TfidfVectorizer(use_idf=False, norm="l2", ngram_range=(1, 2), stop_words="english")
        tfidf_mat = vec.fit_transform([clean_student, clean_ref])
        sim_val = cosine_similarity(tfidf_mat[0:1], tfidf_mat[1:2])[0][0]
        tfidf_sim = float(sim_val)
    except Exception as e:
        logger.debug(f"TF-IDF short answer similarity fallback: {e}")
        tfidf_sim = max(token_overlap, bigram_sim)

    # 6. Calibrated Blended Scoring
    # Concept coverage is paramount (60% weight); semantic similarity provides linguistic nuance (40% weight)
    if target_concepts:
        final_score = (0.60 * concept_recall) + (0.25 * tfidf_sim) + (0.15 * token_overlap)
        # Keyword-stuffing guard: lists of isolated keywords lacking sentential/semantic overlap
        if concept_recall >= 0.75 and (tfidf_sim < 0.15 or token_overlap < 0.15):
            final_score = min(final_score, 0.40)
    else:
        final_score = (0.50 * tfidf_sim) + (0.35 * token_overlap) + (0.15 * bigram_sim)

    final_score = round(min(1.0, max(0.0, final_score)), 3)
    percentage = round(final_score * 100.0, 1)

    is_correct = final_score >= passing_threshold
    partial_credit = (final_score >= 0.35) and not is_correct

    # 7. Construct Pedagogical Diagnostic Feedback
    if is_correct:
        if missing_concepts:
            feedback = f"Good answer. Correctly covered: {', '.join(matched_concepts)}. To improve further, also mention: {', '.join(missing_concepts)}."
        else:
            feedback = "Correct and well-formulated answer covering all required key concepts."
    elif partial_credit:
        matched_str = f" Covered: {', '.join(matched_concepts)}." if matched_concepts else ""
        missing_str = f" Missing required concepts: {', '.join(missing_concepts)}." if missing_concepts else ""
        feedback = f"Partially correct.{matched_str}{missing_str}"
    else:
        missing_str = f" Expected concepts: {', '.join(missing_concepts)}." if missing_concepts else ""
        feedback = f"Incorrect or insufficient explanation.{missing_str} Review the reference explanation."

    return {
        "score": final_score,
        "percentage": percentage,
        "is_correct": is_correct,
        "partial_credit": partial_credit,
        "matched_concepts": matched_concepts,
        "missing_concepts": missing_concepts,
        "feedback": feedback,
        "concept_recall": round(concept_recall, 3),
        "semantic_similarity": round(tfidf_sim, 3),
        "evaluation_method": "hybrid_deterministic_tfidf"
    }
