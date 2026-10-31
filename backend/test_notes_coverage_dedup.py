"""
LearnAI — Regression tests for Notes Coverage, Deduplication, and Matching Accuracy.

These tests are deterministic and do NOT require a live LLM API. They exercise the
extraction-location helpers, the normalisation / aliasing / dedup layer, the
evidence-based coverage classifier, the topic isolation validator, and the
student data isolation guards.
"""

import json
import os
import sys

import pytest

# Ensure the backend package is importable regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.notes_extractor import extract_text_with_locations, ExtractionError
from services.notes_matcher import (
    match_notes_to_curriculum,
    normalize_topic_name,
    alias_for,
    find_syllabus_topic,
    consolidate_candidates,
    filter_supporting_concepts,
    classify_coverage,
    _has_direct_evidence,
    _augmented_text_words,
)
from services.novelty import is_question_relevant_to_topic
from services.database import (
    get_connection,
    init_db,
    create_or_update_study_material,
    get_study_material_detail,
)
import services.database as database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RNN_NOTES = (
    "Lecture 13: Recurrent Neural Networks. "
    "Architecture of an RNN: an RNN cell takes input and previous hidden state "
    "and shares weights across time steps. Unfolding of an RNN across time. "
    "Forward pass: h_t = tanh(W_xh x_t + W_hh h_{t-1} + b_h). "
    "Backpropagation Through Time (BPTT) unfolds the network and applies the "
    "chain rule so gradients flow backward through all time steps. "
    "The vanishing gradient problem makes long-range dependencies hard to learn. "
    "Long Short-Term Memory (LSTM) networks use input, forget and output gates "
    "to control information flow through the cell state."
)

LOCATIONS = [
    {"location": "Page 1", "location_type": "page",
     "text": "Lecture 13: Recurrent Neural Networks. Basic RNN structure."},
    {"location": "Page 2", "location_type": "page",
     "text": "Architecture of an RNN. shared weights across time steps. Unfolding of an RNN."},
    {"location": "Page 6", "location_type": "page",
     "text": "Backpropagation Through Time (BPTT). chain rule. gradients flow backward."},
    {"location": "Page 9", "location_type": "page",
     "text": "Long Short-Term Memory (LSTM). input, forget and output gates. Cell state."},
]


def _topic_names(res):
    return [t["topic_name"] for t in res.get("matched_topics", [])]


# ---------------------------------------------------------------------------
# A. Topic extraction / location hygiene
# ---------------------------------------------------------------------------

class TestExtractionLocations:
    def test_locations_preserve_page_numbers(self):
        locations = extract_text_with_locations("notes.txt", b"Page one content.\n\nPage two content.")
        assert len(locations) == 1
        assert locations[0]["location_type"] == "section"
        assert "Page one content" in locations[0]["text"]

    def test_empty_document_raises_instead_of_silently_returning_topics(self):
        with pytest.raises(ExtractionError):
            extract_text_with_locations("empty.txt", b"")


class TestMatchingBasics:
    def test_extracts_rnn_topics_from_lecture(self):
        res = match_notes_to_curriculum(RNN_NOTES, document_locations=LOCATIONS)
        assert res["relevant"] is True
        names = _topic_names(res)
        assert any("Architecture of an RNN" in n for n in names), names
        assert any("Backpropagation through Time" in n for n in names), names

    def test_does_not_invent_unsupported_topics(self):
        # GRU / Text Classification / Sentiment Analysis are NOT in the lecture.
        res = match_notes_to_curriculum(RNN_NOTES, document_locations=LOCATIONS)
        names = _topic_names(res)
        for forbidden in ["Gated Recurrent Units", "Text Classification", "Sentiment Analysis"]:
            assert not any(forbidden.lower() in n.lower() for n in names), (forbidden, names)

    def test_keyword_only_mention_is_not_classified_as_covered(self):
        # A bare passing mention must not be treated as complete topic coverage.
        res = match_notes_to_curriculum(
            "We briefly mentioned Gated Recurrent Units but moved on.", document_locations=[]
        )
        for t in res.get("matched_topics", []):
            assert t.get("coverage_status") != "covered", (t["topic_name"], t.get("coverage_status"))

    def test_generic_term_does_not_create_card(self):
        res = match_notes_to_curriculum(
            "This lecture is about neural networks generally and training them.",
            document_locations=[]
        )
        # Should not surface a generic "Neural Networks" card as a standalone topic.
        names = _topic_names(res)
        assert not any(n.lower() == "neural networks" for n in names), names


# ---------------------------------------------------------------------------
# B. Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def _cand(self, name, topic_id="", score=0.8, concepts=None, course="22AM301"):
        return {
            "course_code": course,
            "course_name": "Deep Learning",
            "topic_name": name,
            "topic_id": topic_id,
            "score": score,
            "confidence": score,
            "matched_concepts": concepts or [],
            "has_course": True,
            "evidence": [{"location": "Page 1", "matched_terms": [name]}],
        }

    def test_bptt_and_backpropagation_merge(self):
        cands = [
            self._cand("BPTT", topic_id=""),
            self._cand("Backpropagation through Time", topic_id="22AM301-Q018", score=0.85),
        ]
        out = consolidate_candidates(cands)
        assert len(out) == 1, out
        assert out[0]["topic_name"] == "Backpropagation through Time"
        assert out[0]["topic_id"] == "22AM301-Q018"
        assert out[0]["_deduped_from"] == 2

    def test_lstm_and_long_short_term_memory_merge(self):
        cands = [
            self._cand("LSTM", topic_id="", score=0.7),
            self._cand("Long Short-Term Memory", topic_id="22AM301-Q019-1", score=0.75),
        ]
        out = consolidate_candidates(cands)
        assert len(out) == 1, out
        assert out[0]["topic_name"] == "Long Short Term Memory (LSTM)"
        assert out[0]["_deduped_from"] == 2

    def test_rnn_lstm_gru_stay_separate(self):
        cands = [
            self._cand("Architecture of an RNN", topic_id="22AM301-Q016"),
            self._cand("LSTM Architecture", topic_id="22AM301-Q019-1"),
            self._cand("GRU Architecture", topic_id="22AM301-Q019-2"),
        ]
        out = consolidate_candidates(cands)
        assert len(out) == 3, [t["topic_name"] for t in out]

    def test_text_classification_vs_sentiment_analysis_stay_separate(self):
        cands = [
            self._cand("Applications- Text Classification", topic_id="22AM301-Q019-3"),
            self._cand("Sentiment Analysis", topic_id="22AM301-Q020"),
        ]
        out = consolidate_candidates(cands)
        assert len(out) == 2, [t["topic_name"] for t in out]

    def test_generic_concept_tags_are_filtered(self):
        kept = filter_supporting_concepts(
            "Architecture of an RNN",
            ["Neural Networks", "Recurrent Neural Networks", "Hidden states",
             "Recurrent connections", "Hidden states", "RNN cell structure"],
            max_visible=4,
        )
        assert "Hidden states" in kept
        assert "Recurrent connections" in kept
        assert "RNN cell structure" in kept
        # Generic parents removed.
        assert not any("Neural Network" in c for c in kept)
        # Capped.
        assert len(kept) <= 4

    def test_capitalization_alias_normalized(self):
        assert alias_for("bptt") == "Backpropagation Through Time"
        assert alias_for("BPTT") == "Backpropagation Through Time"
        assert alias_for("lstm") == "Long Short-Term Memory"
        assert normalize_topic_name("  Backpropagation  Through  Time  ") == "backpropagation through time"


# ---------------------------------------------------------------------------
# C. Matching accuracy / coverage classification
# ---------------------------------------------------------------------------

class TestCoverageClassification:
    def test_strongly_supported_topic_is_covered(self):
        # Title present + multiple concepts + page evidence.
        status = classify_coverage(0.75, True, 3, 0.8, False)
        assert status == "covered"

    def test_partially_supported_is_not_covered(self):
        status = classify_coverage(0.5, True, 1, 0.2, False)
        assert status == "partially_covered"

    def test_absent_topic_is_not_found(self):
        status = classify_coverage(0.1, False, 0, 0.0, True)
        assert status == "not_found"

    def test_keyword_only_never_covered(self):
        status = classify_coverage(0.42, False, 0, 0.0, True)
        assert status == "not_found"

    def test_rnn_lecture_classifications(self):
        res = match_notes_to_curriculum(RNN_NOTES, document_locations=LOCATIONS)
        by_name = {t["topic_name"]: t for t in res["matched_topics"]}
        arch = by_name.get("Architecture of an RNN")
        assert arch is not None, _topic_names(res)
        assert arch["coverage_status"] in ("covered", "partially_covered"), arch
        bptt = by_name.get("Backpropagation through Time")
        assert bptt is not None, _topic_names(res)
        assert bptt["coverage_status"] in ("covered", "partially_covered"), bptt
        # Evidence must point to real pages, never fabricated.
        for t in res["matched_topics"]:
            for e in t.get("evidence") or []:
                assert e["location"] in {"Page 1", "Page 2", "Page 6", "Page 9"}, e

    def test_match_category_not_a_coverage_percentage(self):
        res = match_notes_to_curriculum(RNN_NOTES, document_locations=LOCATIONS)
        for t in res["matched_topics"]:
            assert t.get("score_type") == "hybrid_tfidf_lexical_course"
            assert "match_category" in t
            assert "%" not in str(t.get("match_category", ""))


# ---------------------------------------------------------------------------
# D. Quiz topic isolation
# ---------------------------------------------------------------------------

class TestQuizIsolation:
    def test_question_is_relevant_to_its_topic(self):
        q = {
            "question": "What does the forget gate do in an LSTM cell?",
            "concept": "forget gate",
            "explanation": "It controls how much of the previous cell state is retained.",
            "options": {"A": "Erases state", "B": "Retains state", "C": "Adds input", "D": "Outputs state"},
            "correct_answer": "B",
        }
        ok, reason = is_question_relevant_to_topic(
            q, "Long Short Term Memory (LSTM)",
            concepts=["forget gate", "input gate", "output gate", "cell state"],
            course_code="22AM301",
        )
        assert ok is True, reason

    def test_question_is_rejected_for_competing_topic(self):
        # A question about LSTM (a competing/sibling topic) must not be accepted
        # for a GRU topic.
        q = {
            "question": "What does the forget gate do in an LSTM cell?",
            "concept": "forget gate",
            "explanation": "LSTM forget gate retains or discards cell state.",
            "options": {"A": "X", "B": "Y", "C": "Z", "D": "W"},
            "correct_answer": "A",
        }
        ok, reason = is_question_relevant_to_topic(
            q, "Gated Recurrent Units",
            concepts=["reset gate", "update gate"],
            course_code="22AM301",
            competing_topics=[
                {
                    "name": "Long Short Term Memory (LSTM)",
                    "concepts": ["forget gate", "input gate", "output gate", "cell state"],
                }
            ],
        )
        assert ok is False, reason


# ---------------------------------------------------------------------------
# E. Student data isolation
# ---------------------------------------------------------------------------

class TestStudentIsolation:
    def _fresh_db(self, tmp_path):
        db = tmp_path / "test.db"
        conn = get_connection(str(db))
        init_db(str(db))
        return conn

    def test_student_a_cannot_access_student_b_material(self, tmp_path):
        conn = self._fresh_db(tmp_path)
        try:
            a_id = database.get_or_create_student("student-a", conn=conn)
            b_id = database.get_or_create_student("student-b", conn=conn)
            mat = create_or_update_study_material(
                student_id=a_id, filename="A.pdf", file_type="PDF", file_size=10,
                extracted_text="Architecture of an RNN.",
                matched_topics=[
                    {"course_code": "22AM301", "course_name": "Deep Learning",
                     "topic_name": "Architecture of an RNN", "topic_id": "22AM301-Q016",
                     "confidence": 0.9, "matched_concepts": ["hidden state"],
                     "coverage_status": "covered", "evidence": [], "source_location": "Page 2"}
                ],
                conn=conn,
            )
            # Owner can read.
            detail = get_study_material_detail(mat["id"], a_id, conn=conn)
            assert detail is not None
            assert len(detail["topics"]) == 1
            assert detail["topics"][0]["coverage_status"] == "covered"
            # Non-owner is denied.
            with pytest.raises(PermissionError):
                get_study_material_detail(mat["id"], b_id, conn=conn)
        finally:
            conn.close()

    def test_cross_student_grounding_text_denied(self, tmp_path):
        # Same (file) connection, different student ids -> PermissionError.
        conn = self._fresh_db(tmp_path)
        try:
            a_id = database.get_or_create_student("student-a", conn=conn)
            b_id = database.get_or_create_student("student-b", conn=conn)
            mat = create_or_update_study_material(
                student_id=a_id, filename="A.pdf", file_type="PDF", file_size=1,
                extracted_text="x", matched_topics=[], conn=conn,
            )
            with pytest.raises(PermissionError):
                database.get_study_material_grounding_text(mat["id"], student_id=b_id, conn=conn)
        finally:
            conn.close()
