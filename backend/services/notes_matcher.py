"""
Notes Matcher Service for LearnAI
Aligns extracted student study notes with the university curriculum syllabus.
- Evaluates topic titles, concept keywords, and syllabus context against curriculum.json.
- Prioritizes courses explicitly mentioned in the notes.
- Calculates relevance confidence scores and ranks topics.
- Distinguishes between fully matched, partially relevant, and irrelevant documents.
- Manages temporary in-memory notes sessions for quiz generation grounding.
"""

import time
import uuid
import re
import logging
from typing import Dict, List, Any, Optional
from services.curriculum_service import curriculum_service

logger = logging.getLogger("learnai.notes_matcher")

_NOTES_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
SESSION_TTL_SECONDS = 3600  # 1 hour TTL


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


def match_notes_to_curriculum(extracted_text: str, student_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Matches extracted notes text against curriculum courses and selectable topics.
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

    curriculum = curriculum_service.get_curriculum()
    courses = curriculum.get("courses", [])

    candidates: List[Dict[str, Any]] = []

    for course in courses:
        course_code = course.get("code", "")
        course_name = course.get("name", "")
        c_code_lower = course_code.lower()
        c_name_lower = course_name.lower()
        has_course_mention = (c_code_lower in text_lower) or (c_name_lower in text_lower)

        for topic in course.get("topics", []):
            topic_name = topic.get("name", "")
            t_name_lower = topic_name.lower()
            topic_id = topic.get("id") or ""
            unit = topic.get("unit")
            concepts = topic.get("concepts", [])

            score = 0.0
            matched_concepts = []

            # 1. Exact topic name match or strong word match
            if len(t_name_lower) >= 4 and t_name_lower in text_lower:
                score += 0.60
            else:
                t_words = [w for w in t_name_lower.split() if len(w) > 3]
                if t_words:
                    overlap = sum(1 for w in t_words if w in text_lower)
                    if overlap == len(t_words):
                        score += 0.50
                    elif overlap > 0:
                        score += 0.25 * (overlap / len(t_words))

            # 2. Concept keyword matches (avoiding overly generic single common words)
            for c in concepts:
                c_clean = c.strip().lower()
                if len(c_clean) >= 4:
                    if c_clean in text_lower:
                        score += 0.25
                        matched_concepts.append(c)
                    else:
                        c_toks = _tokenize(c_clean)
                        if c_toks and len(c_toks.intersection(text_tokens)) == len(c_toks):
                            score += 0.20
                            matched_concepts.append(c)

            # 3. Course context priority boost
            if has_course_mention and score > 0.15:
                score += 0.40

            unique_concepts = list(dict.fromkeys(matched_concepts))

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
                    "has_course": has_course_mention,
                    "score": score
                })

    # Sort candidates by:
    # 1. Whether course is explicitly mentioned
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
