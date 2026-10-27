"""
Notes Matcher Service for LearnAI
Aligns extracted student study notes with the university curriculum syllabus.
- Evaluates topic titles, concept keywords, and syllabus context against curriculum.json.
- Employs hybrid matching: TF-IDF cosine similarity across curriculum corpus combined
  with lexical topic/concept matching and course-context priority.
- Extracts statistical candidate keyphrases from uploaded notes using TF-IDF n-grams.
- Manages temporary in-memory notes sessions for quiz generation grounding.

NOTE ON NLP CAPABILITIES & LIMITATIONS:
The topic matching in this service uses TF-IDF (Term Frequency-Inverse Document Frequency)
vectorization and cosine similarity from scikit-learn. TF-IDF is a statistical n-gram
term-weighting technique that scores topics based on shared domain vocabulary distributions
and keyword co-occurrence across syllabus text. It does NOT perform deep neural semantic
embeddings, syntactic dependency parsing, or automated synonym expansion.
"""

import time
import uuid
import re
import logging
from typing import Dict, List, Any, Optional, Tuple, Set
from services.curriculum_service import curriculum_service

logger = logging.getLogger("learnai.notes_matcher")

# Verification and safe conditional import of scikit-learn
try:
    from sklearn.feature_extraction.text import TfidfVectorizer, ENGLISH_STOP_WORDS
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn is not available; falling back to heuristic matching.")

_NOTES_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
SESSION_TTL_SECONDS = 3600  # 1 hour TTL

# Lazily initialized TF-IDF index across all curriculum courses and topics
_CURRICULUM_TFIDF_INDEX: Optional[Dict[str, Any]] = None

DOCUMENT_NOISE_WORDS = {
    "lecture", "today", "covered", "discussed", "introduced", "finally",
    "chapter", "slide", "slides", "notes", "summary", "example", "examples",
    "overview", "page", "pages", "unit", "module", "definition", "definitions",
    "concept", "concepts", "topic", "topics", "study", "student", "class"
}

COMMON_ACRONYMS = {
    "sql", "nosql", "acid", "dbms", "rdbms", "ddl", "dml", "dcl", "tcl",
    "1nf", "2nf", "3nf", "bcnf", "4nf", "5nf", "2pl", "api", "rest", "json",
    "xml", "http", "tcp", "udp", "ip", "ram", "cpu", "gpu", "gui", "cli",
    "uml", "er", "eer", "ai", "ml", "nlp", "cnn", "rnn", "lstm", "gan", "svm",
    "knn", "pca", "dag", "bfs", "dfs", "cfg", "cnf", "gnf", "dfa", "nfa", "pda"
}

if SKLEARN_AVAILABLE:
    STOP_WORDS: Set[str] = set(ENGLISH_STOP_WORDS).union(DOCUMENT_NOISE_WORDS)
else:
    STOP_WORDS = DOCUMENT_NOISE_WORDS


def _clean_session_cache():
    """Removes expired notes sessions from memory."""
    now = time.time()
    expired = [sid for sid, data in _NOTES_SESSION_CACHE.items() if now - data.get("created_at", 0) > SESSION_TTL_SECONDS]
    for sid in expired:
        _NOTES_SESSION_CACHE.pop(sid, None)


def store_notes_session(text: str, matched_topics: List[Dict[str, Any]], student_id: Optional[str] = None) -> str:
    """Stores extracted notes text in session cache and returns session ID."""
    _clean_session_cache()
    session_id = str(uuid.uuid4())
    _NOTES_SESSION_CACHE[session_id] = {
        "text": text,
        "matched_topics": matched_topics,
        "student_id": student_id,
        "created_at": time.time()
    }
    return session_id


def get_notes_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves notes session by ID if valid and not expired."""
    _clean_session_cache()
    return _NOTES_SESSION_CACHE.get(session_id)


def _tokenize(text: str) -> set:
    """Extracts alphanumeric words of length >= 3."""
    return set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", text.lower()))


def format_concept_title(phrase: str) -> str:
    """Formats candidate keyphrase preserving acronym capitalization."""
    words = phrase.split()
    formatted = []
    for w in words:
        if w.lower() in COMMON_ACRONYMS:
            formatted.append(w.upper())
        elif len(w) <= 2 and not w.isalpha():
            formatted.append(w.upper())
        else:
            formatted.append(w.capitalize())
    return " ".join(formatted)


def extract_document_keyphrases(text: str, top_n: int = 15) -> List[Tuple[str, float]]:
    """
    Extracts statistical candidate keyphrases from uploaded text using
    TF-IDF-weighted unigrams, bigrams, and trigrams.
    Filters common stopwords, presentation noise, and subphrase redundancies.
    """
    clean = (text or "").strip()
    if len(clean) < 15 or not SKLEARN_AVAILABLE:
        return []

    # Split into clean clause segments (sentences, bullet points, list items)
    segments = [s.strip() for s in re.split(r'[\n.?!;,:]+', clean) if len(s.strip()) >= 3]
    if not segments:
        segments = [clean]

    max_df_val = 0.95 if len(segments) >= 5 else 1.0

    try:
        vec = TfidfVectorizer(
            ngram_range=(1, 3),
            stop_words=list(STOP_WORDS),
            token_pattern=r'(?u)\b[a-zA-Z0-9_\-\+]{2,}\b',
            max_df=max_df_val,
            min_df=1
        )
        tfidf = vec.fit_transform(segments)
    except ValueError:
        return []

    feature_names = vec.get_feature_names_out()
    scores = tfidf.sum(axis=0).A1
    ranked_indices = scores.argsort()[::-1]

    raw_keyphrases: List[Tuple[str, float]] = []
    seen: Set[str] = set()

    for idx in ranked_indices:
        phrase = feature_names[idx].strip()
        words = phrase.split()
        if not words:
            continue
        # Reject phrases starting or ending with stopwords
        if words[0] in STOP_WORDS or words[-1] in STOP_WORDS:
            continue
        # Reject phrases that are pure digits
        if all(w.isdigit() for w in words):
            continue
        norm_key = phrase.lower()
        if norm_key in seen:
            continue
        seen.add(norm_key)

        title = format_concept_title(phrase)
        raw_keyphrases.append((title, round(float(scores[idx]), 3)))

    # Deduplicate redundant single-word phrases if a longer compound phrase contains them
    compound_phrases = [p.lower() for p, _ in raw_keyphrases if len(p.split()) > 1]
    filtered_keyphrases: List[Tuple[str, float]] = []
    for p, sc in raw_keyphrases:
        p_lower = p.lower()
        if len(p.split()) == 1 and any(p_lower in cp.split() for cp in compound_phrases):
            continue
        filtered_keyphrases.append((p, sc))
        if len(filtered_keyphrases) >= top_n:
            break

    return filtered_keyphrases


def _get_curriculum_tfidf_index() -> Optional[Dict[str, Any]]:
    """
    Lazily builds and caches a TF-IDF index across all curriculum courses and topics.
    Returns None if scikit-learn is unavailable.
    """
    global _CURRICULUM_TFIDF_INDEX
    if _CURRICULUM_TFIDF_INDEX is not None:
        return _CURRICULUM_TFIDF_INDEX

    if not SKLEARN_AVAILABLE:
        return None

    curriculum = curriculum_service.get_curriculum()
    courses = curriculum.get("courses", [])

    corpus: List[str] = []
    topic_metadata: List[Dict[str, Any]] = []

    for crs in courses:
        course_code = crs.get("code", "")
        course_name = crs.get("name", "")
        for t in crs.get("topics", []):
            topic_name = t.get("name", "")
            topic_id = t.get("id", "")
            unit = t.get("unit")
            concepts = t.get("concepts", [])
            syllabus_context = t.get("syllabus_context", "")

            # Build enriched topic document text for vectorization
            concepts_text = " ".join(concepts)
            topic_doc = f"{course_code} {course_name} {topic_name} {concepts_text} {syllabus_context}"
            corpus.append(topic_doc)
            topic_metadata.append({
                "course_code": course_code,
                "course_name": course_name,
                "topic_name": topic_name,
                "topic_id": topic_id,
                "unit": unit,
                "concepts": concepts,
                "syllabus_context": syllabus_context
            })

    if not corpus:
        return None

    try:
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", max_features=15000)
        tfidf_matrix = vectorizer.fit_transform(corpus)
        _CURRICULUM_TFIDF_INDEX = {
            "vectorizer": vectorizer,
            "tfidf_matrix": tfidf_matrix,
            "metadata": topic_metadata
        }
        logger.info(f"Initialized curriculum TF-IDF index: {len(corpus)} topics indexed.")
        return _CURRICULUM_TFIDF_INDEX
    except Exception as e:
        logger.error(f"Failed to build curriculum TF-IDF index: {e}")
        return None


def match_notes_to_curriculum(extracted_text: str, student_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Matches extracted notes text against curriculum courses and selectable topics.
    Combines statistical TF-IDF cosine similarity with lexical topic/concept matching
    and course-context priority.
    Returns structured results including matched topics, relevance flag, partial flag, and session ID.
    """
    clean_text = (extracted_text or "").strip()
    if not clean_text or len(clean_text) < 15:
        return {
            "success": True,
            "relevant": False,
            "partial": False,
            "matched_topics": [],
            "message": "Extracted text is too short or empty to identify academic syllabus topics.",
            "notes_session_id": None
        }

    text_lower = clean_text.lower()
    text_tokens = _tokenize(clean_text)

    # Extract dynamic keyphrase candidates from the document
    doc_keyphrases = extract_document_keyphrases(clean_text, top_n=20)
    doc_keyphrase_names = [kp for kp, _ in doc_keyphrases]
    doc_keyphrase_lower = [kp.lower() for kp in doc_keyphrase_names]

    tfidf_index = _get_curriculum_tfidf_index()
    sims = None
    if tfidf_index:
        try:
            q_vec = tfidf_index["vectorizer"].transform([clean_text])
            sims = cosine_similarity(q_vec, tfidf_index["tfidf_matrix"])[0]
        except Exception as e:
            logger.warning(f"Error computing notes TF-IDF similarity: {e}")
            sims = None

    curriculum = curriculum_service.get_curriculum()
    courses = curriculum.get("courses", [])

    candidates: List[Dict[str, Any]] = []
    topic_counter = 0

    for course in courses:
        course_code = course.get("code", "")
        course_name = course.get("name", "")
        c_code_lower = course_code.lower()
        c_name_lower = course_name.lower()

        # Full course code or name mention in notes
        has_full_course = (c_code_lower in text_lower) or (c_name_lower in text_lower)

        # Partial course word overlap (e.g. 'database' in 'database management systems')
        c_words = [w for w in c_name_lower.split() if len(w) > 3 and w not in ("and", "the", "for", "with", "intro")]
        c_overlap = (sum(1 for w in c_words if w in text_lower) / len(c_words)) if c_words else 0.0

        for topic in course.get("topics", []):
            topic_name = topic.get("name", "")
            t_name_lower = topic_name.lower()
            topic_id = topic.get("id") or ""
            unit = topic.get("unit")
            concepts = topic.get("concepts", [])
            syllabus_ctx = (topic.get("syllabus_context") or "").lower()

            tfidf_sim = float(sims[topic_counter]) if (sims is not None and topic_counter < len(sims)) else 0.0
            topic_counter += 1

            lex_score = 0.0
            matched_concepts: List[str] = []

            # 1. Lexical Topic Name Match
            if len(t_name_lower) >= 4 and t_name_lower in text_lower:
                lex_score += 0.50
            else:
                t_words = [w for w in t_name_lower.split() if len(w) > 3]
                if t_words:
                    overlap = sum(1 for w in t_words if w in text_lower)
                    if overlap == len(t_words):
                        lex_score += 0.40
                    elif overlap > 0:
                        lex_score += 0.20 * (overlap / len(t_words))

            # 2. Syllabus Concepts Overlap (against note text and dynamic keyphrases)
            for c in concepts:
                c_clean = c.strip().lower()
                if len(c_clean) >= 3:
                    if c_clean in text_lower:
                        lex_score += 0.25
                        matched_concepts.append(c)
                    elif any(c_clean == kp or c_clean in kp for kp in doc_keyphrase_lower):
                        lex_score += 0.20
                        matched_concepts.append(c)
                    else:
                        c_toks = _tokenize(c_clean)
                        if c_toks and len(c_toks.intersection(text_tokens)) == len(c_toks):
                            lex_score += 0.20
                            matched_concepts.append(c)

            # 3. Incorporate high-ranking document keyphrases aligned with this topic's context
            for kp, kp_lower in zip(doc_keyphrase_names, doc_keyphrase_lower):
                if len(kp_lower) >= 3 and (kp_lower in syllabus_ctx or kp_lower in t_name_lower):
                    if kp not in matched_concepts and len(matched_concepts) < 8:
                        matched_concepts.append(kp)

            unique_concepts = list(dict.fromkeys(matched_concepts))

            # 4. Hybrid Scoring Blend
            if sims is not None:
                # Normalize cosine similarity (0.40 is considered an exceptionally strong match)
                sim_norm = min(1.0, tfidf_sim / 0.40)
                course_score = 1.0 if has_full_course else (0.50 * c_overlap if c_overlap > 0 else 0.0)

                # Gatekeeper: if TF-IDF similarity is essentially zero (<0.05), reject accidental word collisions
                if tfidf_sim < 0.05 and not has_full_course and lex_score < 0.50:
                    score = 0.0
                else:
                    score = (0.50 * sim_norm) + (0.25 * min(1.0, lex_score)) + (0.25 * course_score)
            else:
                # Fallback to pure heuristic scoring if scikit-learn is unavailable
                score = lex_score
                if has_full_course and score > 0.15:
                    score += 0.40

            if score >= 0.40:
                confidence = round(min(1.0, score), 2)
                candidates.append({
                    "course_code": course_code,
                    "course_name": course_name,
                    "topic_name": topic_name,
                    "topic_id": topic_id,
                    "unit": unit,
                    "matched_concepts": unique_concepts,
                    "confidence": confidence,
                    "has_course": has_full_course,
                    "score": score,
                    "tfidf_similarity": round(tfidf_sim, 4)
                })

    # Sort candidates by:
    # 1. Explicit course mention
    # 2. Score descending
    candidates.sort(key=lambda x: (x["has_course"], x["score"]), reverse=True)

    # Deduplicate candidates by (course_code, topic_name)
    seen_keys = set()
    deduped_candidates = []
    for cand in candidates:
        key = (cand["course_code"], cand["topic_name"])
        if key not in seen_keys:
            seen_keys.add(key)
            deduped_candidates.append(cand)

    top_matches = deduped_candidates[:6]

    if not top_matches:
        return {
            "success": True,
            "relevant": False,
            "partial": False,
            "matched_topics": [],
            "message": "The uploaded document does not cover supported LearnAI curriculum topics. LearnAI supports curriculum topics from our engineering syllabus.",
            "notes_session_id": None
        }

    is_partial = False
    if len(clean_text) > 400 and (len(top_matches[0]["matched_concepts"]) <= 2 or len(top_matches) > 1):
        is_partial = True

    session_id = store_notes_session(clean_text, top_matches, student_id=student_id)

    match_count = len(top_matches)
    primary_topic = top_matches[0]["topic_name"]
    primary_course = top_matches[0]["course_name"]

    if match_count == 1:
        message = f"Matched syllabus topic: '{primary_topic}' ({primary_course})."
    else:
        message = f"Found {match_count} matched syllabus topics in your notes, led by '{primary_topic}'."

    return {
        "success": True,
        "relevant": True,
        "partial": is_partial,
        "matched_topics": top_matches,
        "message": message,
        "notes_session_id": session_id,
        "notes_preview": clean_text[:400] + ("..." if len(clean_text) > 400 else "")
    }


def select_relevant_notes_context(
    notes_text: str,
    topic_name: str,
    course_code: str = "",
    competing_topics: Optional[List[Any]] = None,
    matched_concepts: Optional[List[str]] = None,
    max_chars: int = 3000
) -> str:
    """
    Selects the most topic-relevant windows of text from uploaded notes
    using paragraph segmentation, TF-IDF ranking, and competing topic suppression.
    Guarantees:
      - Strictly excludes passages/slides that belong to competing syllabus topics.
      - Returns empty string if uploaded text has negligible relevance to the target topic.
      - Preserves document reading order for selected topic-isolated windows.
    """
    safe_text = (notes_text or "").replace("</student_notes_untrusted>", "").replace("<student_notes_untrusted>", "").strip()
    if not safe_text:
        return ""

    # 1. Segment text into coherent paragraph blocks
    raw_paras = [p.strip() for p in re.split(r'\n{2,}|\n(?=(?:Slide\s+\d+|Module|Unit|Chapter|Topic)\b)', safe_text) if p.strip()]
    if not raw_paras:
        raw_paras = [safe_text]

    # Break oversized paragraphs (>1200 chars) into sentence or clause chunks
    chunks: List[str] = []
    for p in raw_paras:
        if len(p) <= 1200:
            chunks.append(p)
        else:
            sentences = re.split(r'(?<=[.?!])\s+', p)
            if len(sentences) == 1:
                # No sentence punctuation; split by whitespace
                words = p.split()
                curr: List[str] = []
                curr_len = 0
                for w in words:
                    if curr_len + len(w) > 1000 and curr:
                        chunks.append(" ".join(curr))
                        curr = [w]
                        curr_len = len(w)
                    else:
                        curr.append(w)
                        curr_len += len(w) + 1
                if curr:
                    chunks.append(" ".join(curr))
            else:
                curr = []
                curr_len = 0
                for s in sentences:
                    if curr_len + len(s) > 1000 and curr:
                        chunks.append(" ".join(curr))
                        curr = [s]
                        curr_len = len(s)
                    else:
                        curr.append(s)
                        curr_len += len(s) + 1
                if curr:
                    chunks.append(" ".join(curr))

    # 2. Form chunk windows (evaluates each coherent paragraph or slide independently)
    windows: List[Dict[str, Any]] = [
        {"index": idx, "text": chunk} for idx, chunk in enumerate(chunks)
    ]

    if not windows:
        return ""

    # 3. Setup Target and Competing Vocabulary
    topic_tokens = set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", topic_name.lower()))
    if matched_concepts:
        for mc in matched_concepts:
            topic_tokens.update(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", str(mc).lower()))

    tfidf_index = _get_curriculum_tfidf_index()
    target_t_lower = topic_name.strip().lower()
    target_c_lower = course_code.strip().lower()
    topic_idx = None

    competing_tokens_map: Dict[str, Set[str]] = {}
    if competing_topics:
        for ct in competing_topics:
            c_name = ct if isinstance(ct, str) else (ct.get("name") or ct.get("topic_name") or "")
            c_name = str(c_name).strip()
            if c_name and c_name.lower() != target_t_lower:
                c_toks = set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", c_name.lower()))
                if isinstance(ct, dict):
                    for mc in ct.get("matched_concepts", []) or ct.get("concepts", []):
                        c_toks.update(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", str(mc).lower()))
                competing_tokens_map[c_name] = c_toks

    # Find curriculum topic vector for target topic if available (prioritizing exact topic name match)
    if tfidf_index and "metadata" in tfidf_index:
        for idx, m in enumerate(tfidf_index["metadata"]):
            m_t_lower = m["topic_name"].lower()
            m_c_lower = m["course_code"].lower()
            if m_t_lower == target_t_lower:
                if not target_c_lower or m_c_lower == target_c_lower:
                    topic_idx = idx
                    for c in m.get("concepts", []):
                        topic_tokens.update(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", c.lower()))
                    break
        if topic_idx is None:
            for idx, m in enumerate(tfidf_index["metadata"]):
                m_t_lower = m["topic_name"].lower()
                m_c_lower = m["course_code"].lower()
                if target_t_lower in m_t_lower or (len(m_t_lower) > 12 and m_t_lower in target_t_lower):
                    if not target_c_lower or m_c_lower == target_c_lower:
                        topic_idx = idx
                        for c in m.get("concepts", []):
                            topic_tokens.update(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", c.lower()))
                        break

    # Clean competing tokens to be exclusive (remove tokens shared with target topic)
    comp_exclusive_map: Dict[str, Set[str]] = {}
    for c_name, c_toks in competing_tokens_map.items():
        excl = c_toks - topic_tokens
        if excl:
            comp_exclusive_map[c_name] = excl

    # 4. Score windows against target topic using TF-IDF and lexical overlap
    scored_windows: List[Tuple[float, Dict[str, Any]]] = []
    topic_query = f"{course_code} {topic_name}".strip()

    sims = None
    if tfidf_index and SKLEARN_AVAILABLE:
        try:
            vec = tfidf_index["vectorizer"]
            w_vecs = vec.transform([w["text"] for w in windows])

            if topic_idx is not None:
                q_vec = tfidf_index["tfidf_matrix"][topic_idx]
            else:
                q_vec = vec.transform([topic_query])

            sims = cosine_similarity(q_vec, w_vecs)[0]
        except Exception as e:
            logger.warning(f"Error ranking notes windows via TF-IDF: {e}")
            sims = None

    for i, w in enumerate(windows):
        tfidf_score = float(sims[i]) if (sims is not None and i < len(sims)) else 0.0
        w_lower = w["text"].lower()
        w_tokens = set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", w_lower))

        lex_overlap = len(topic_tokens & w_tokens)
        target_score = tfidf_score + (0.05 * lex_overlap)

        # Competing topics suppression
        max_comp_overlap = 0
        for c_name, excl_tokens in comp_exclusive_map.items():
            c_overlap = len(excl_tokens & w_tokens)
            if c_overlap > max_comp_overlap:
                max_comp_overlap = c_overlap

        # If window is strongly dominated by a competing topic and has low/no target overlap, exclude it
        if max_comp_overlap >= 2 and max_comp_overlap > lex_overlap:
            continue

        # If window has zero target overlap and very low TF-IDF, exclude it
        if lex_overlap == 0 and target_score < 0.04:
            continue

        scored_windows.append((target_score, w))

    if not scored_windows:
        # Check if single short snippet directly discussing topic
        if len(safe_text) < 300 and len(topic_tokens & set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", safe_text.lower()))) > 0:
            return safe_text
        return ""

    # Sort windows by score descending
    scored_windows.sort(key=lambda x: x[0], reverse=True)

    # Filter out irrelevant windows (relative threshold against highest-scoring window)
    max_score = scored_windows[0][0] if scored_windows else 0.0
    if max_score > 0.10:
        candidate_windows = [item for item in scored_windows if item[0] >= 0.40 * max_score]
    elif max_score > 0.03:
        candidate_windows = [item for item in scored_windows if item[0] >= 0.02]
    else:
        candidate_windows = scored_windows

    # 5. Greedily select top windows fitting into max_chars
    selected_windows: List[Dict[str, Any]] = []
    accumulated_chars = 0

    for sc, w in candidate_windows:
        remaining = max_chars - accumulated_chars
        if remaining <= 100:
            break
        w_len = len(w["text"])
        if w_len <= remaining:
            selected_windows.append(w)
            accumulated_chars += w_len
        else:
            w_copy = dict(w)
            w_copy["text"] = w_copy["text"][:remaining]
            selected_windows.append(w_copy)
            accumulated_chars += len(w_copy["text"])
            break

    if not selected_windows:
        return ""

    # 6. Re-sort selected windows in original document sequence
    selected_windows.sort(key=lambda w: w["index"])
    assembled_excerpt = "\n\n[...]\n\n".join(w["text"] for w in selected_windows)
    return assembled_excerpt[:max_chars].strip()
