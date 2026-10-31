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


# ═════════════════════════════════════════════════════════════════════════════
# Topic normalization, aliasing, deduplication and coverage classification
# ═════════════════════════════════════════════════════════════════════════════

# Broad parent / generic concepts that should NOT be repeated as separate topic
# cards or shown as supporting concepts under every specific topic. They remain
# valid curriculum entries but are treated as supporting context, not standalone
# extracted cards, unless the document contains a meaningful independent section.
GENERIC_PARENT_CONCEPTS = {
    "neural network", "neural networks", "neural net", "neural nets",
    "recurrent neural", "recurrent neural network", "recurrent neural networks",
    "machine learning", "deep learning", "artificial intelligence",
    "convolutional neural network", "convolutional neural networks",
    "shallow neural networks and deep neural networks",
    "shallow neural networks", "deep neural networks",
}

# Structured Canonical Topic Definitions with Scoped Course Context
# Used to resolve synonyms to stable canonical syllabus topics while strictly
# preventing cross-course alias collisions or inappropriate generic merges.
CANONICAL_TOPIC_ALIASES: Dict[str, Dict[str, Any]] = {
    # Deep Learning (22AM301 / CS217)
    "backpropagation through time": {
        "canonical_name": "Backpropagation Through Time",
        "canonical_id": "22AM301-Q018",
        "course_codes": ["22AM301", "CS217"],
        "aliases": [
            "bptt",
            "backpropagation through time",
            "back-propagation through time",
            "back propagation through time",
            "backpropagation through time (bptt)",
            "bptt algorithm"
        ]
    },
    "long short-term memory (lstm)": {
        "canonical_name": "Long Short-Term Memory",
        "canonical_id": "22AM301-Q019-1",
        "course_codes": ["22AM301", "CS217"],
        "aliases": [
            "lstm",
            "long short term memory",
            "long short-term memory",
            "long short term memory (lstm)",
            "long short-term memory (lstm)",
            "long short term memory networks",
            "long short-term memory networks",
            "lstm networks",
            "lstm network",
            "lstm cell"
        ]
    },
    "gated recurrent units": {
        "canonical_name": "Gated Recurrent Units",
        "canonical_id": "22AM301-Q019-2",
        "course_codes": ["22AM301", "CS217"],
        "aliases": [
            "gru",
            "gated recurrent unit",
            "gated recurrent units",
            "gated recurrent units (gru)",
            "gru networks",
            "gru cell"
        ]
    },
    "architecture of an rnn": {
        "canonical_name": "Architecture of an RNN",
        "canonical_id": "22AM301-Q016",
        "course_codes": ["22AM301", "CS217"],
        "aliases": [
            "architecture of an rnn",
            "rnn architecture",
            "basic rnn structure",
            "recurrent neural network architecture",
            "structure of an rnn"
        ]
    },
    "applications text classification": {
        "canonical_name": "Applications- Text Classification",
        "canonical_id": "22AM301-Q019-3",
        "course_codes": ["22AM301", "CS217"],
        "aliases": [
            "applications- text classification",
            "applications text classification",
            "text classification"
        ]
    },
    "sentiment analysis": {
        "canonical_name": "Sentiment Analysis",
        "canonical_id": "22AM301-Q020",
        "course_codes": ["22AM301", "CS217"],
        "aliases": [
            "sentiment analysis"
        ]
    },
    "stochastic gradient descent": {
        "canonical_name": "Stochastic Gradient Descent Method",
        "canonical_id": "22AM301-Q007",
        "course_codes": ["22AM301", "CS217"],
        "aliases": [
            "sgd",
            "stochastic gradient descent",
            "stochastic gradient descent method"
        ]
    },
    # Design & Analysis of Algorithms (22AM203)
    "dutch national flag problem": {
        "canonical_name": "Algorithm, Pseudo-Code for Expressing Algorithms, Performance Analysis - Space and Time Complexity",
        "canonical_id": "22AM203-Q001",
        "course_codes": ["22AM203"],
        "aliases": [
            "dutch national flag",
            "dutch national flag problem",
            "dnf problem",
            "three-way partitioning",
            "3-way partitioning"
        ]
    },
    "greedy method": {
        "canonical_name": "Greedy Method",
        "canonical_id": "22AM203-Q004-2",
        "course_codes": ["22AM203"],
        "aliases": [
            "greedy method",
            "greedy approach",
            "greedy algorithm",
            "fractional knapsack",
            "fractional knapsack problem"
        ]
    }
}

ALIAS_TO_CANONICAL: Dict[str, Dict[str, Any]] = {}
for _ckey, _cdata in CANONICAL_TOPIC_ALIASES.items():
    _all_names = list(_cdata.get("aliases", [])) + [_ckey]
    if _cdata.get("canonical_name"):
        _all_names.append(_cdata["canonical_name"])
    for _alias in _all_names:
        _norm = re.sub(r"[\-_\:\(\)\[\]\.,;]+", " ", _alias.lower()).strip()
        _norm = re.sub(r"\s+", " ", _norm)
        ALIAS_TO_CANONICAL[_norm] = _cdata

TOPIC_ALIASES: Dict[str, str] = {
    _norm: _cdata["canonical_name"] for _norm, _cdata in ALIAS_TO_CANONICAL.items()
}


def normalize_topic_name(name: str) -> str:
    """
    Normalize a topic name for comparison:
      - lower case, strip, collapse whitespace
      - remove punctuation / hyphens / underscores
      - collapse internal whitespace
    Returns a comparison key (not a display title).
    """
    if not name:
        return ""
    lower = name.lower().strip()
    lower = re.sub(r"[\-_\:\(\)\[\]\.,;]+", " ", lower)
    lower = re.sub(r"\s+", " ", lower).strip()
    return lower


def alias_for(name: str, course_code: Optional[str] = None) -> Optional[str]:
    """
    Return the canonical syllabus title for a known alias/variant, or None.
    If course_code is provided, verifies the alias is valid for that course to
    prevent accidental cross-course alias leakage.
    """
    norm = normalize_topic_name(name)
    cdata = ALIAS_TO_CANONICAL.get(norm)
    if not cdata:
        return None
    if course_code:
        c_upper = course_code.upper().strip()
        allowed = [c.upper() for c in cdata.get("course_codes", [])]
        if allowed and c_upper not in allowed:
            return None
    return cdata.get("canonical_name")


def get_canonical_topic_info(name: str, course_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Returns full canonical topic metadata for a known alias/variant.
    """
    norm = normalize_topic_name(name)
    cdata = ALIAS_TO_CANONICAL.get(norm)
    if not cdata:
        return None
    if course_code:
        c_upper = course_code.upper().strip()
        allowed = [c.upper() for c in cdata.get("course_codes", [])]
        if allowed and c_upper not in allowed:
            return None
    return cdata


def find_syllabus_topic(course_code: str, topic_name: str,
                        curriculum: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Locate the canonical syllabus topic record for a candidate topic.

    Resolution order:
      1. Exact alias match (TOPIC_ALIASES) -> curriculum title lookup.
      2. Normalized exact title match within the course.
      3. Normalized contains-match within the course (conservative).

    Returns the syllabus topic dict (with id/name/concepts) or None. Never
    invents a topic id.
    """
    if curriculum is None:
        try:
            curriculum = curriculum_service.get_curriculum()
        except Exception:
            return None

    courses = curriculum.get("courses", [])
    target = normalize_topic_name(topic_name)
    if not target:
        return None

    def _is_meaningful_overlap(s_norm: str, t_norm: str) -> bool:
        """True only when the two normalized names refer to the same topic."""
        if s_norm == t_norm:
            return True
        # Contains match only when the shorter name is a substantial portion of
        # the longer one, to avoid matching generic words (e.g. "Introduction"
        # inside "Introduction to Transaction").
        if len(s_norm) >= 8 and len(t_norm) >= 8:
            shorter, longer = (s_norm, t_norm) if len(s_norm) <= len(t_norm) else (t_norm, s_norm)
            if shorter in longer and (len(shorter) / len(longer)) >= 0.75:
                return True
        return False

    # 1. Alias resolution -> then locate that canonical title in the course.
    canonical = alias_for(topic_name)
    search_names = [canonical] if canonical else []
    search_names.append(topic_name)

    for course in courses:
        if course.get("code") != course_code:
            continue
        for topic in course.get("topics", []):
            t_name = topic.get("name", "")
            t_norm = normalize_topic_name(t_name)
            for sname in search_names:
                s_norm = normalize_topic_name(sname)
                if s_norm and _is_meaningful_overlap(s_norm, t_norm):
                    return topic
    return None


def _words_in_proximity(text: str, words: Set[str], window: int = 120) -> Tuple[bool, int, int]:
    """
    Determines whether all defining words appear within a character proximity
    window (<= window characters), ensuring that isolated words scattered across
    distant paragraphs are not falsely counted as a compound concept.
    """
    w_list = [w.lower() for w in words if len(w) >= 3]
    if not w_list:
        return False, 0, 0
    positions = []
    for w in w_list:
        matches = [m.start() for m in re.finditer(r"\b" + re.escape(w) + r"\b", text)]
        if not matches:
            return False, 0, 0
        positions.append(matches)

    if len(positions) == 1:
        return True, positions[0][0], positions[0][0] + len(w_list[0])

    if len(positions) == 2:
        for p1 in positions[0]:
            for p2 in positions[1]:
                if abs(p1 - p2) <= window:
                    start = min(p1, p2)
                    end = max(p1 + len(w_list[0]), p2 + len(w_list[1]))
                    return True, start, end
        return False, 0, 0

    # For >= 3 words
    all_points = []
    for i, plist in enumerate(positions):
        for p in plist:
            all_points.append((p, i, len(w_list[i])))
    all_points.sort(key=lambda x: x[0])

    for i in range(len(all_points)):
        start_p = all_points[i][0]
        words_found = {all_points[i][1]}
        end_p = start_p + all_points[i][2]
        for j in range(i + 1, len(all_points)):
            if all_points[j][0] - start_p > window:
                break
            words_found.add(all_points[j][1])
            end_p = max(end_p, all_points[j][0] + all_points[j][2])
            if len(words_found) == len(positions):
                return True, start_p, end_p
    return False, 0, 0


def _is_incidental_mention(text_lower: str, term_start: int, term_end: int) -> bool:
    """
    Detects whether a topic mention is merely an incidental reference in a list
    or passing illustrative example (Case A) without genuine explanatory depth.
    """
    pre_window = text_lower[max(0, term_start - 140):term_start]
    post_window = text_lower[term_end:min(len(text_lower), term_end + 140)]
    combined = pre_window + " " + post_window

    list_patterns = [
        r"applications?\s+(?:like|such\s+as|include|including)\b",
        r"(?:domains|fields|areas|tasks)\s+(?:like|such\s+as|include|including)\b",
        r"examples?\s+(?:like|such\s+as|include|including)\b",
        r"tasks?\s+(?:like|such\s+as)\b",
        r"such\s+as\b",
        r"for\s+example\b",
        r"\bincluding\b",
        r"e\.g\.",
        r"(?:autofill|spelling\s+corrections?|speech|grammar)\s*,\s*(?:and|or)?",
    ]
    is_list_context = any(re.search(pat, pre_window) for pat in list_patterns)

    # Explanatory markers (if present, the passage genuinely teaches the concept)
    explanatory_markers = [
        r"\b(?:defined\s+as|definition|formulation|algorithm|architecture|formula|equation)\b",
        r"\b(?:consists\s+of|works\s+by|composed\s+of|calculated\s+by|derived\s+by|solution\s+to)\b",
        r"[=+\-*/∂∑σ\\]",
        r"\b(?:layer|gate|cell|weights?|biases?|gradients?|loss|recurrence|unrolled)\b"
    ]
    has_explanation = any(re.search(pat, combined) for pat in explanatory_markers)

    return is_list_context and not has_explanation


def _topic_in_locations(topic_name: str, concepts: List[str],
                        locations: List[Dict[str, Any]],
                        course_code: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Find document locations (page/slide/section) that genuinely explain the topic.
    Ranks locations by relevance and evidence depth, filtering out incidental
    keyword overlap on common/generic words and ungrounded passing mentions.
    """
    if not locations:
        return []

    norm_topic = normalize_topic_name(topic_name)
    base_title = re.sub(r"\([^\)]*\)", "", (topic_name or "").lower()).strip()

    topic_phrases = [norm_topic, base_title]
    cdata = get_canonical_topic_info(topic_name, course_code) or get_canonical_topic_info(base_title, course_code)
    if cdata:
        for al in cdata.get("aliases", []):
            topic_phrases.append(normalize_topic_name(al))
    for alias_key, canon_val in TOPIC_ALIASES.items():
        if normalize_topic_name(canon_val) in (norm_topic, normalize_topic_name(base_title)):
            topic_phrases.append(alias_key.lower().strip())
    topic_phrases = [p for p in set(topic_phrases) if len(p) >= 3]

    title_words = set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", base_title.lower()))
    generic_words = {
        "neural", "network", "networks", "deep", "learning", "machine", "model",
        "models", "algorithm", "algorithms", "system", "systems", "basic", "basics",
        "introduction", "applications", "and", "for", "with", "the", "from", "that",
        "this", "are", "was", "were", "been", "have", "has", "can", "will", "time", "through"
    }
    defining_words = title_words - generic_words - DOCUMENT_NOISE_WORDS
    if not defining_words:
        defining_words = title_words

    clean_concepts = []
    for c in (concepts or []):
        for sub_c in re.split(r"[,;:\n]+", str(c)):
            sub_c_clean = sub_c.strip().lower()
            if sub_c_clean and sub_c_clean not in GENERIC_PARENT_CONCEPTS and len(sub_c_clean) >= 3:
                clean_concepts.append(sub_c_clean)

    scored_locs = []
    for loc in locations:
        text = loc.get("text", "") or ""
        text_lower = re.sub(r"\s+", " ", (text or "").lower())
        if not text_lower:
            continue

        score = 0.0
        matched_terms = []
        best_term_start = 0
        best_term_end = 0
        evidence_type = "conceptual_explanation"
        is_incidental = False

        # 1. Exact phrase / canonical alias match
        exact_match_found = False
        for phrase in topic_phrases:
            idx = text_lower.find(phrase)
            if idx != -1:
                # Word boundary check for short acronyms
                if len(phrase) <= 5:
                    if not bool(re.search(r"\b" + re.escape(phrase) + r"\b", text_lower)):
                        continue
                exact_match_found = True
                score += 16.0
                matched_terms.append(phrase)
                best_term_start = idx
                best_term_end = idx + len(phrase)

                # Check section heading
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                for line in lines:
                    l_lower = line.lower()
                    if phrase in l_lower:
                        is_candidate_heading = (
                            (len(line) < 70 and not line.strip().endswith((".", ";", "?", "!")) and not re.search(r"\b(we|i|you|they|briefly|mentioned|studied|moved\s+on)\b", l_lower))
                            or bool(re.match(r"^(?:chapter|unit|module|section|part|\d+[\.\d]*|[a-zA-Z]\))\s*", l_lower))
                        )
                        if is_candidate_heading:
                            score += 16.0
                            evidence_type = "section_heading"
                            break

                # Check mathematical derivations
                if any(sym in text_lower for sym in ["∂", "∑", "softmax", "dw", "db", "w(t)", "s(t)", "c(t)", "h(t)", "loss", "gradient"]):
                    score += 12.0
                    if evidence_type != "section_heading":
                        evidence_type = "mathematical_derivation"

                if _is_incidental_mention(text_lower, best_term_start, best_term_end):
                    if evidence_type != "section_heading":
                        is_incidental = True
                        evidence_type = "incidental_mention"
                        score = 4.0

                # Check architectural explanation / mechanisms
                if any(term in text_lower for term in ["architecture", "figure", "forget gate", "input gate", "output gate", "cell state", "hidden state", "unrolled"]):
                    score += 10.0
                    if evidence_type not in ("section_heading", "mathematical_derivation"):
                        evidence_type = "architectural_explanation"

                # Check if merely an incidental list mention
                if _is_incidental_mention(text_lower, best_term_start, best_term_end):
                    if evidence_type not in ("section_heading", "mathematical_derivation", "architectural_explanation"):
                        is_incidental = True
                        evidence_type = "incidental_mention"
                        score = 4.0
                break

        # 2. If NO exact phrase match, check proximity for defining words
        if not exact_match_found:
            if len(defining_words) >= 2:
                has_prox, p_start, p_end = _words_in_proximity(text_lower, defining_words, window=120)
                if has_prox:
                    matched_terms.extend(list(defining_words))
                    score += 10.0 + len(defining_words) * 3.0
                    best_term_start = p_start
                    best_term_end = p_end
                    if _is_incidental_mention(text_lower, p_start, p_end):
                        is_incidental = True
                        evidence_type = "incidental_mention"
                        score = 4.0
            elif len(defining_words) == 1:
                single_word = list(defining_words)[0]
                idx = text_lower.find(single_word)
                if idx != -1 and bool(re.search(r"\b" + re.escape(single_word) + r"\b", text_lower)):
                    matched_terms.append(single_word)
                    score += 14.0
                    best_term_start = idx
                    best_term_end = idx + len(single_word)
                    if _is_incidental_mention(text_lower, idx, idx + len(single_word)):
                        is_incidental = True
                        evidence_type = "incidental_mention"
                        score = 4.0

        # 3. Clean concepts
        for c in clean_concepts:
            if c in text_lower or (len(c) > 4 and c.rstrip('s') in text_lower):
                score += 12.0
                matched_terms.append(c)

        # Discard incidental mentions or weak scores (< 12.0)
        if is_incidental or score < 12.0 or not matched_terms:
            continue

        raw_idx = text.lower().find(matched_terms[0]) if matched_terms else 0
        if raw_idx == -1: raw_idx = 0
        start = max(0, raw_idx - 80)
        end = min(len(text), raw_idx + len(matched_terms[0]) + 160)
        snippet = text[start:end].strip().replace("\n", " ")

        page_num = loc.get("page_number") or loc.get("slide_number")
        slide_num = loc.get("slide_number") or (page_num if loc.get("location_type") == "slide" else None)
        if page_num is None:
            m = re.search(r"\d+", loc.get("location", ""))
            page_num = int(m.group()) if m else None
        if slide_num is None and loc.get("location_type") == "slide":
            slide_num = page_num

        relevance_desc = f"{evidence_type.replace('_', ' ').title()}: Matched '{matched_terms[0]}' with {len(matched_terms)} technical indicators"
        scored_locs.append({
            "location": loc.get("location", ""),
            "location_type": loc.get("location_type", "section"),
            "page_number": page_num,
            "slide_number": slide_num,
            "score": score,
            "snippet": snippet[:400],
            "matched_terms": list(dict.fromkeys(matched_terms))[:6],
            "evidence_type": evidence_type,
            "relevance": relevance_desc,
            "is_incidental": is_incidental
        })

    scored_locs.sort(key=lambda x: x["score"], reverse=True)
    top_locs = scored_locs[:4]

    def _extract_page_num(l_str):
        m = re.search(r"\d+", l_str or "")
        return int(m.group()) if m else 0

    top_locs.sort(key=lambda x: _extract_page_num(x.get("location")))
    return top_locs
def filter_supporting_concepts(topic_name: str, concepts: List[str],
                               max_visible: int = 4) -> List[str]:
    """
    Deduplicate and rank supporting concepts for display.

    - Removes exact/near-duplicate aliases (normalized).
    - Removes generic parent concepts that add no specific information.
    - Removes concepts that merely repeat the topic name.
    - Prefers specific technical concepts over broad labels.
    - Caps visible concepts at max_visible (default 4).
    """
    if not concepts:
        return []
    topic_norm = normalize_topic_name(topic_name)

    seen: Set[str] = set()
    filtered: List[str] = []
    for c in concepts:
        c_clean = str(c).strip()
        if not c_clean:
            continue
        c_norm = normalize_topic_name(c_clean)
        if not c_norm:
            continue
        # Skip generic parent concepts.
        if c_norm in GENERIC_PARENT_CONCEPTS:
            continue
        # Skip concepts that just repeat the topic name.
        if c_norm == topic_norm:
            continue
        # Skip near-duplicate aliases already kept.
        if c_norm in seen:
            continue
        seen.add(c_norm)
        filtered.append(c_clean)

    # Prefer specific (longer, multi-word) technical concepts first.
    filtered.sort(key=lambda c: (len(normalize_topic_name(c)), len(c)), reverse=True)
    return filtered[:max_visible]


def classify_coverage(score: float, title_evidence: bool, concept_evidence: int,
                      content_depth: float, keyword_only: bool) -> str:
    """
    Classify syllabus coverage for a matched topic.

    Rules:
      - covered (Strong Match): direct topic evidence, meaningful explanatory depth,
        substantial explanation or mathematical/architectural formulations.
      - partially_covered (Partial Match): genuine relevant foundational explanation exists,
        some important aspects covered, but insufficient for full syllabus scope.
      - not_found (Not Found): no meaningful explanatory evidence, incidental keyword mentions only,
        or topic absent from document.
      - needs_review (Needs Review): extraction quality is ambiguous.
    """
    if keyword_only or not title_evidence:
        return "not_found"

    # Strong match requires direct title/alias presence, high confidence, and substantive depth
    if title_evidence and score >= 0.55 and content_depth >= 0.20:
        return "covered"

    # Partial match requires genuine foundational explanation
    if title_evidence and score >= 0.35 and content_depth >= 0.10:
        return "partially_covered"

    return "not_found"
def consolidate_candidates(candidates: List[Dict[str, Any]],
                           curriculum: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Consolidate semantically duplicate candidate topics using canonical topic identity.

    Preferred identity order:
      1. Stable syllabus topic ID, when available.
      2. Explicit canonical topic ID or alias mapping.
      3. Carefully normalized topic name as a fallback.

    Merge rules:
      - Multiple aliases resolving to the same canonical topic produce ONE displayed topic.
      - Course-scoped: aliases do not merge across unrelated courses.
      - Distinct syllabus topics (RNN vs LSTM; LSTM vs GRU; Text Classification vs
        Sentiment Analysis; Forward Pass vs BPTT) NEVER merge.
      - Winning record preserves canonical topic ID, canonical display name, module,
        unit, and merges all unique evidence and concepts without duplicate pages.
    """
    if curriculum is None:
        try:
            curriculum = curriculum_service.get_curriculum()
        except Exception:
            curriculum = {"courses": []}

    groups: Dict[str, List[Dict[str, Any]]] = {}

    for cand in candidates:
        course_code = cand.get("course_code", "")
        topic_name = cand.get("topic_name", "")
        topic_id = cand.get("topic_id") or ""

        # 1. Syllabus topic lookup within the course
        syll_topic = find_syllabus_topic(course_code, topic_name, curriculum)

        # 2. Canonical alias lookup within the course
        cdata = get_canonical_topic_info(topic_name, course_code)

        if syll_topic and syll_topic.get("id"):
            canon_id = syll_topic["id"]
            canon_name = syll_topic.get("name") or topic_name
            canon_course = syll_topic.get("course_code") or course_code
            module_info = syll_topic.get("module")
            unit_info = syll_topic.get("unit")
            parent_topic = syll_topic.get("parent_topic") or syll_topic.get("parent_section")
        elif cdata:
            canon_id = cdata.get("canonical_id") or f"alias::{course_code}::{normalize_topic_name(cdata['canonical_name'])}"
            canon_name = cdata.get("canonical_name") or topic_name
            canon_course = course_code
            module_info = cand.get("module")
            unit_info = cand.get("unit")
            parent_topic = cand.get("parent_topic")
        elif topic_id:
            canon_id = topic_id
            canon_name = topic_name
            canon_course = course_code
            module_info = cand.get("module")
            unit_info = cand.get("unit")
            parent_topic = cand.get("parent_topic")
        else:
            canon_id = f"norm::{course_code}::{normalize_topic_name(topic_name)}"
            canon_name = topic_name
            canon_course = course_code
            module_info = cand.get("module")
            unit_info = cand.get("unit")
            parent_topic = cand.get("parent_topic")

        # Group key is strictly course-scoped so different courses never merge!
        group_key = f"{canon_course.lower()}::{canon_id.lower()}"
        entry = dict(cand)
        entry["_canon_name"] = canon_name
        entry["_canon_id"] = canon_id
        entry["_module"] = module_info
        entry["_unit"] = unit_info
        entry["_parent_topic"] = parent_topic
        entry["_alias_origin"] = topic_name
        groups.setdefault(group_key, []).append(entry)

    consolidated: List[Dict[str, Any]] = []
    status_rank = {"covered": 3, "partially_covered": 2, "needs_review": 1, "not_found": 0}

    for group_key, members in groups.items():
        members.sort(key=lambda m: float(m.get("confidence", 0) or 0), reverse=True)
        winner = members[0]
        winner_name = winner.get("_canon_name") or winner.get("topic_name")
        winner_id = winner.get("_canon_id") or winner.get("topic_id")
        winner_course = winner.get("course_code")

        # Merge evidence locations without duplicating page references or excerpts
        all_evidence: List[Dict[str, Any]] = []
        seen_loc_keys: Set[str] = set()
        best_score = 0.0

        for m in members:
            sc = float(m.get("score", 0) or 0)
            if sc > best_score:
                best_score = sc
            for ev in (m.get("evidence") or []):
                p_num = ev.get("page_number")
                s_num = ev.get("slide_number")
                l_type = ev.get("location_type", "section")
                if p_num is not None:
                    loc_key = f"{l_type}::pg_{p_num}"
                elif s_num is not None:
                    loc_key = f"{l_type}::sl_{s_num}"
                else:
                    loc_key = f"{l_type}::{ev.get('location', '')}"

                if loc_key not in seen_loc_keys:
                    seen_loc_keys.add(loc_key)
                    all_evidence.append(ev)

        # Merge concepts
        all_concepts: List[str] = []
        seen_concepts: Set[str] = set()
        for m in members:
            for c in (m.get("matched_concepts") or []):
                c_norm = normalize_topic_name(str(c))
                if c_norm and c_norm not in seen_concepts:
                    seen_concepts.add(c_norm)
                    all_concepts.append(str(c).strip())

        # Collect original aliases
        all_aliases: Set[str] = set()
        for m in members:
            if m.get("_alias_origin"):
                all_aliases.add(m["_alias_origin"])
            for a in (m.get("topic_aliases") or []):
                all_aliases.add(a)

        # Highest coverage status
        best_status = max((m.get("coverage_status", "not_found") for m in members), key=lambda s: status_rank.get(s, 0))

        winner["topic_name"] = winner_name
        winner["topic_id"] = winner_id
        winner["course_code"] = winner_course
        if winner.get("_module") is not None:
            winner["module"] = winner.get("_module")
        if winner.get("_unit") is not None:
            winner["unit"] = winner.get("_unit")
        if winner.get("_parent_topic"):
            winner["parent_topic"] = winner.get("_parent_topic")
        winner["score"] = round(best_score, 4)
        winner["confidence"] = round(min(1.0, best_score), 2)
        winner["evidence"] = all_evidence[:6]
        winner["matched_concepts"] = filter_supporting_concepts(winner_name, all_concepts)
        winner["topic_aliases"] = sorted(list(all_aliases))
        winner["coverage_status"] = best_status
        winner["match_category"] = _coverage_category(best_status)
        winner["_deduped_from"] = len(members)

        for k in ("_canon_name", "_canon_id", "_syllabus_topic", "_module", "_unit", "_parent_topic", "_alias_origin"):
            winner.pop(k, None)
        consolidated.append(winner)

    consolidated.sort(key=lambda x: (x.get("has_course", False), float(x.get("score", 0) or 0)), reverse=True)
    return consolidated
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

            # Build enriched topic document text for vectorization.
            # syllabus_context is retained because it provides course-specific
            # vocabulary that supports paraphrase matching and course routing.
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


def _coverage_status_for(cand: Dict[str, Any], text_lower: str,
                         evidence: List[Dict[str, Any]]) -> str:
    """
    Determine coverage status for a single candidate topic.
    Enforces the zero-evidence invariant and distinguishes:
      Case A: Incidental mention -> not_found
      Case B: Foundational explanation -> partially_covered
      Case C: Substantial coverage -> covered
    """
    # 1. Inviolable zero-evidence invariant
    if not evidence:
        return "not_found"

    # 2. Case A: If all evidence locations are incidental mentions or weak (< 12.0)
    valid_explanatory = [
        e for e in evidence
        if not e.get("is_incidental", False) and float(e.get("score", 0)) >= 12.0
    ]
    if not valid_explanatory:
        return "not_found"

    topic_name = cand.get("topic_name", "")
    t_name_lower = (topic_name or "").lower().strip()
    base_title = re.sub(r"\([^\)]*\)", "", t_name_lower).strip()

    title_in_text = (len(t_name_lower) >= 4 and t_name_lower in text_lower) or (len(base_title) >= 4 and base_title in text_lower)
    alias_in_text = False
    norm_target = normalize_topic_name(topic_name)
    norm_base = normalize_topic_name(base_title)
    course_code = cand.get("course_code")

    cdata = get_canonical_topic_info(topic_name, course_code) or get_canonical_topic_info(base_title, course_code)
    if cdata:
        for al in cdata.get("aliases", []):
            al_lower = al.lower().strip()
            if len(al_lower) >= 3 and (al_lower in text_lower or bool(re.search(r"\b" + re.escape(al_lower) + r"\b", text_lower))):
                alias_in_text = True
                break

    for alias_key, canon_val in TOPIC_ALIASES.items():
        norm_canon = normalize_topic_name(canon_val)
        if norm_canon in (norm_target, norm_base) or alias_key in (norm_target, norm_base):
            if len(alias_key) >= 3 and (
                alias_key in text_lower or
                bool(re.search(r"\b" + re.escape(alias_key) + r"\b", text_lower))
            ):
                alias_in_text = True
                break

    title_present = title_in_text or alias_in_text

    # Proximity check for defining words
    title_words = set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", base_title.lower()))
    GENERIC_TOPIC_WORDS = {
        "introduction", "overview", "basics", "basic", "techniques", "methods",
        "concepts", "theory", "principles", "application", "applications", "using",
        "types", "desirable", "characterizing", "based"
    }
    defining = title_words - DOCUMENT_NOISE_WORDS - GENERIC_PARENT_CONCEPTS - GENERIC_TOPIC_WORDS
    if not defining:
        defining = title_words - DOCUMENT_NOISE_WORDS - GENERIC_PARENT_CONCEPTS

    if len(defining) >= 2 and not title_in_text and not alias_in_text:
        has_prox, _, _ = _words_in_proximity(text_lower, defining, window=120)
        if has_prox:
            title_present = True
        else:
            title_present = False
    elif len(defining) == 1 and not title_in_text and not alias_in_text:
        if any(w in text_lower for w in defining):
            title_present = True
        else:
            title_present = False

    concept_count = len(cand.get("matched_concepts") or [])
    score = float(cand.get("score", 0) or 0)
    content_depth = min(1.0, len(valid_explanatory) / 3.0)

    # 3. Case C: Substantial coverage (Strong Match)
    has_substantive = any(
        e.get("evidence_type") in ("section_heading", "mathematical_derivation", "architectural_explanation") and float(e.get("score", 0)) >= 25.0
        for e in valid_explanatory
    )
    if title_present and (has_substantive or (len(valid_explanatory) >= 2 and score >= 0.40)):
        return "covered"

    # 4. Case B: Foundational explanation (Partial Match)
    if (title_present or concept_count >= 1) and len(valid_explanatory) >= 1 and (score >= 0.28 or has_substantive):
        return "partially_covered"

    return "not_found"
def _coverage_category(status: str) -> str:
    """User-facing qualitative match category for a coverage status."""
    return {
        "covered": "Strong match",
        "partially_covered": "Partial match",
        "not_found": "Not found",
        "needs_review": "Needs review",
    }.get(status, "Needs review")


def _augmented_text_words(text_lower: str, text_tokens: set) -> set:
    """Text tokens plus hyphen-split parts, so 'short-term' also matches 'short'/'term'."""
    words = set(text_tokens)
    for w in list(text_tokens):
        if "-" in w:
            for p in w.split("-"):
                if len(p) >= 3:
                    words.add(p)
    return words


def _has_direct_evidence(topic_name: str, syllabus_concepts: List[str],
                         text_lower: str, text_tokens: set) -> bool:
    """
    True when the document directly discusses the topic (its title/alias or a
    syllabus concept is substantially present in phrase or proximity), as opposed to
    matching only through shared unit vocabulary in syllabus_context.
    """
    t_name_lower = (topic_name or "").lower().strip()
    if not t_name_lower:
        return False

    if len(t_name_lower) >= 4 and t_name_lower in text_lower:
        return True

    base_title = re.sub(r"\([^\)]*\)", "", t_name_lower).strip()
    if len(base_title) >= 4 and base_title in text_lower:
        return True

    cdata = get_canonical_topic_info(topic_name) or get_canonical_topic_info(base_title)
    if cdata:
        for al in cdata.get("aliases", []):
            al_lower = al.lower().strip()
            if len(al_lower) >= 3 and (al_lower in text_lower or bool(re.search(r"\b" + re.escape(al_lower) + r"\b", text_lower))):
                return True

    norm_target = normalize_topic_name(topic_name)
    norm_base = normalize_topic_name(base_title)
    for alias_key, canon_val in TOPIC_ALIASES.items():
        norm_canon = normalize_topic_name(canon_val)
        if norm_canon in (norm_target, norm_base) or alias_key in (norm_target, norm_base):
            if len(alias_key) >= 3 and (
                alias_key in text_lower or
                bool(re.search(r"\b" + re.escape(alias_key) + r"\b", text_lower))
            ):
                return True

    # Multi-word defining words MUST appear in proximity (<= 120 chars)
    GENERIC_TOPIC_WORDS = {
        "introduction", "overview", "basics", "basic", "techniques", "methods",
        "concepts", "theory", "principles", "application", "applications", "using",
        "types", "desirable", "characterizing", "based"
    }
    base_words = [
        w for w in re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", base_title)
        if w not in DOCUMENT_NOISE_WORDS and w not in GENERIC_PARENT_CONCEPTS and w not in GENERIC_TOPIC_WORDS
    ]
    if not base_words:
        base_words = [
            w for w in re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", base_title)
            if w not in DOCUMENT_NOISE_WORDS and w not in GENERIC_PARENT_CONCEPTS
        ]

    present_defining = {w for w in base_words if w in text_lower or (len(w) > 4 and w.rstrip('s') in text_lower)}
    if len(present_defining) >= 2:
        has_prox, _, _ = _words_in_proximity(text_lower, present_defining, window=120)
        if has_prox:
            return True
    elif len(present_defining) == 1:
        w = list(present_defining)[0]
        aug_words = _augmented_text_words(text_lower, text_tokens)
        if w in aug_words or len(w) >= 8 or bool(re.search(r"\b" + re.escape(w) + r"\b", text_lower)):
            return True

    for c in (syllabus_concepts or []):
        c_str = str(c).strip().lower()
        if not c_str or c_str in GENERIC_PARENT_CONCEPTS or len(c_str) < 4:
            continue
        if c_str in text_lower:
            return True

    return False
def match_notes_to_curriculum(extracted_text: str, student_id: Optional[str] = None,
                              document_locations: Optional[List[Dict[str, Any]]] = None
                              ) -> Dict[str, Any]:
    """
    Matches extracted notes text against curriculum courses and selectable topics.
    Combines statistical TF-IDF cosine similarity with lexical topic/concept matching
    and course-context priority.

    Enhancements over the legacy heuristic:
      - Builds page/slide-level evidence from document_locations when provided.
      - Consolidates semantic duplicates (aliases + same syllabus topic id) so
        synonyms such as BPTT/Backpropagation Through Time appear once.
      - Classifies each topic's coverage (covered / partially_covered /
        not_found / needs_review) instead of exposing a raw similarity score
        as a coverage percentage.
      - Removes the artificial six-topic cap; the result count is driven by the
        document's real content.

    Backward-compatible fields (confidence, score, tfidf_similarity,
    matched_concepts, relevant, partial) are preserved. New fields:
    coverage_status, match_category, evidence, internal_score, score_type.
    """
    clean_text = (extracted_text or "").strip()
    if not clean_text or len(clean_text) < 15:
        return {
            "success": True,
            "relevant": False,
            "partial": False,
            "matched_topics": [],
            "message": "Extracted text is too short or empty to identify academic syllabus topics.",
            "notes_session_id": None,
            "notes_preview": ""
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
        c_overlap = (sum(1 for w in c_words if w in text_lower or (len(w) > 4 and w.rstrip('s') in text_lower)) / len(c_words)) if c_words else 0.0

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

            # 1. Lexical Topic Name Match (with canonical aliases)
            alias_matched = False
            base_tname = re.sub(r"\([^\)]*\)", "", t_name_lower).strip()
            cdata = get_canonical_topic_info(topic_name, course_code) or get_canonical_topic_info(base_tname, course_code)
            if cdata:
                for al in cdata.get("aliases", []):
                    al_clean = al.lower().strip()
                    if len(al_clean) >= 3 and (al_clean in text_lower or bool(re.search(r"\b" + re.escape(al_clean) + r"\b", text_lower))):
                        lex_score += 0.50
                        alias_matched = True
                        break

            if not alias_matched:
                if len(t_name_lower) >= 4 and t_name_lower in text_lower:
                    lex_score += 0.50
                else:
                    t_words = [w for w in t_name_lower.split() if len(w) > 3]
                    if t_words:
                        overlap = sum(1 for w in t_words if w in text_lower or (len(w) > 4 and w.rstrip('s') in text_lower))
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

            if score >= 0.40 or (alias_matched and score >= 0.25):
                confidence = round(min(1.0, score), 2)
                candidates.append({
                    "course_code": course_code,
                    "course_name": course_name,
                    "topic_name": topic_name,
                    "topic_id": topic_id,
                    "unit": unit,
                    "matched_concepts": unique_concepts,
                    "syllabus_concepts": list(concepts),
                    "confidence": confidence,
                    "has_course": has_full_course,
                    "score": score,
                    "tfidf_similarity": round(tfidf_sim, 4)
                })

    # Sort candidates by:
    # 1. Explicit course mention
    # 2. Score descending
    candidates.sort(key=lambda x: (x["has_course"], x["score"]), reverse=True)

    # Sibling suppression: within a course + unit, a topic matched only through
    # the shared syllabus_context (no direct title/concept evidence) is dropped
    # when a sibling in the same unit has direct evidence. This prevents claiming
    # sibling topics (e.g. GRU / Text Classification / Sentiment Analysis) merely
    # because the notes cover LSTM. Paraphrase-only matches (no direct evidence
    # anywhere in the unit) are preserved.
    from collections import defaultdict
    by_unit = defaultdict(list)
    for c in candidates:
        by_unit[(c.get("course_code"), c.get("unit"))].append(c)
    kept: List[Dict[str, Any]] = []
    for (cc, unit), group in by_unit.items():
        direct = [c for c in group if _has_direct_evidence(
            c.get("topic_name", ""), c.get("syllabus_concepts") or [],
            text_lower, text_tokens)]
        if direct:
            kept.extend(direct)
        else:
            kept.extend(group)
    candidates = kept

    # If document_locations is not provided (e.g. raw text notes), create virtual locations from paragraphs
    effective_locations = document_locations
    if not effective_locations:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean_text) if p.strip()]
        if not paragraphs:
            paragraphs = [clean_text]
        effective_locations = [
            {
                "location": f"Section {i + 1}",
                "location_type": "section",
                "page_number": i + 1,
                "text": p
            }
            for i, p in enumerate(paragraphs)
        ]

    # Normalize locations so page_number is populated from slide_number if missing
    norm_doc_locations = []
    for i, loc in enumerate(effective_locations):
        nl = dict(loc)
        p_num = nl.get("page_number") or nl.get("slide_number")
        if p_num is None:
            m = re.search(r"\d+", nl.get("location", ""))
            p_num = int(m.group()) if m else (i + 1)
        nl["page_number"] = p_num
        if nl.get("location_type") == "slide" or "slide_number" in nl:
            nl["slide_number"] = p_num
        norm_doc_locations.append(nl)

    # Build evidence + coverage status for every candidate before consolidation.
    for cand in candidates:
        evidence = _topic_in_locations(
            cand.get("topic_name", ""),
            cand.get("matched_concepts") or [],
            norm_doc_locations,
            course_code=cand.get("course_code")
        )
        cand["evidence"] = evidence
        cand["coverage_status"] = _coverage_status_for(cand, text_lower, evidence)
        cand["match_category"] = _coverage_category(cand["coverage_status"])
        cand["internal_score"] = round(float(cand.get("score", 0) or 0), 4)
        cand["score_type"] = "hybrid_tfidf_lexical_course"  # NOT a coverage percentage

    # Consolidate semantic duplicates (aliases + same syllabus topic id).
    consolidated = consolidate_candidates(candidates, curriculum=curriculum)

    # Keep only topics with actual supporting evidence for the "found in notes" set.
    matched_topics = [
        t for t in consolidated
        if t.get("coverage_status") in ("covered", "partially_covered", "needs_review")
    ]

    if not matched_topics:
        return {
            "success": True,
            "relevant": False,
            "partial": False,
            "matched_topics": [],
            "message": "The uploaded document does not cover supported LearnAI curriculum topics. LearnAI supports curriculum topics from our engineering syllabus.",
            "notes_session_id": None,
            "notes_preview": clean_text[:400] + ("..." if len(clean_text) > 400 else "")
        }

    session_id = store_notes_session(clean_text, matched_topics, student_id=student_id)

    any_partial = (
        any(t.get("coverage_status") == "partially_covered" for t in matched_topics)
        or (len(clean_text) > 400 and (len(matched_topics[0].get("matched_concepts", [])) <= 2 or len(matched_topics) > 1))
    )
    match_count = len(matched_topics)
    primary_topic = matched_topics[0]["topic_name"]
    primary_course = matched_topics[0]["course_name"]

    if match_count == 1:
        message = f"Matched syllabus topic: '{primary_topic}' ({primary_course})."
    else:
        message = f"Found {match_count} matched syllabus topics in your notes, led by '{primary_topic}'."

    return {
        "success": True,
        "relevant": True,
        "partial": any_partial,
        "matched_topics": matched_topics,
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
