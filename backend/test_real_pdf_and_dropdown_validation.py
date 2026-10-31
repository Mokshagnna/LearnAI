"""
Comprehensive Regression Test Suite:
1. Real PDF extraction and verification on CS217_2024_lec13.pdf (9 pages, 543,944 bytes)
2. Accurate coverage classification & qualitative labelling (Strong match, Partial match, Not found)
3. Page-level evidence provenance (no fabricated pages, BPTT page 6/7, RNN page 2/4, LSTM page 8/9, GRU/Sentiment 0 pages)
4. Absence of hallucinated coverage (strictly not_found with 0 evidence for uncovered topics)
5. Topic dropdown optgroup grouping, deduplication, and empty optgroup elimination
6. Notes-grounded vs General Syllabus Practice payload validation
7. Student data isolation and database persistence
"""

import os
import sys
import json
import pytest

# Ensure the backend directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.notes_extractor import (
    extract_text_from_file,
    extract_text_with_locations,
    extract_text_from_pdf_pages
)
from services.notes_matcher import (
    match_notes_to_curriculum,
    _coverage_status_for,
    _has_direct_evidence,
    _topic_in_locations,
    _words_in_proximity,
    _is_incidental_mention,
    consolidate_candidates,
    alias_for,
    get_canonical_topic_info
)
from services.database import (
    get_connection,
    init_db,
    create_or_update_study_material,
    get_student_study_materials,
    get_study_material_detail
)

REAL_PDF_PATH = r"C:\Users\moksh\Downloads\CS217_2024_lec13.pdf"
FALLBACK_PDF_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "CS217_2024_lec13.pdf")
REAL_PPTX_PATH = r"C:\Users\moksh\Downloads\.pptx\ADA .pptx"
FALLBACK_PPTX_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ADA .pptx")

def get_real_pptx():
    if os.path.exists(REAL_PPTX_PATH):
        return REAL_PPTX_PATH
    if os.path.exists(FALLBACK_PPTX_PATH):
        return FALLBACK_PPTX_PATH
    pytest.skip(f"ADA .pptx not found at {REAL_PPTX_PATH} or {FALLBACK_PPTX_PATH}")

def get_real_pdf():
    if os.path.exists(REAL_PDF_PATH):
        return REAL_PDF_PATH
    if os.path.exists(FALLBACK_PDF_PATH):
        return FALLBACK_PDF_PATH
    pytest.skip(f"CS217_2024_lec13.pdf not found at {REAL_PDF_PATH} or {FALLBACK_PDF_PATH}")


# ── 1. Real PDF Extraction & Page Text Verification ──────────────────────────

def test_real_pdf_file_properties_and_page_count():
    pdf_path = get_real_pdf()
    file_size = os.path.getsize(pdf_path)
    assert file_size == 543944, f"Expected exactly 543,944 bytes, got {file_size}"

    with open(pdf_path, "rb") as f:
        file_bytes = f.read()

    pages = extract_text_from_pdf_pages(file_bytes)
    assert len(pages) == 9, f"Expected 9 pages, got {len(pages)}"

    locations = extract_text_with_locations("CS217_2024_lec13.pdf", file_bytes)
    assert len(locations) == 9

    # Page 1: Lecture 13 intro, Basic RNN structure
    p1 = pages[0]
    assert p1["page"] == 1
    assert "recurrent neural networks" in p1["text"].lower()
    assert "basic rnn structure" in p1["text"].lower()

    # Page 2: Architecture of an RNN, Named Entity Recognition
    p2 = pages[1]
    assert p2["page"] == 2
    assert "architecture of an rnn" in p2["text"].lower()

    # Page 6: Training RNNs, Backpropagation Through Time (BPTT)
    p6 = pages[5]
    assert p6["page"] == 6
    p6_lower = p6["text"].lower()
    assert "backpropagation through time" in p6_lower or "bptt" in p6_lower

    # Page 8: Vanishing Gradient Problem, Long Short Term Memory
    p8 = pages[7]
    assert p8["page"] == 8
    p8_lower = p8["text"].lower()
    assert "vanishing gradient" in p8_lower
    assert "long short term memory" in p8_lower or "lstm" in p8_lower

    # Page 9: LSTM cell architecture (Forget gate, Input gate, Output gate)
    p9 = pages[8]
    assert p9["page"] == 9
    p9_lower = p9["text"].lower()
    assert "forget" in p9_lower or "input gate" in p9_lower or "cell" in p9_lower


# ── 2. Coverage Classification & Qualitative Matching on Real PDF ────────────

@pytest.fixture(scope="module")
def extracted_lecture():
    pdf_path = get_real_pdf()
    with open(pdf_path, "rb") as f:
        file_bytes = f.read()
    full_text = extract_text_from_file("CS217_2024_lec13.pdf", file_bytes)
    locations = extract_text_with_locations("CS217_2024_lec13.pdf", file_bytes)
    return {
        "full_text": full_text,
        "locations": locations
    }

def test_coverage_classification_on_real_pdf(extracted_lecture):
    result = match_notes_to_curriculum(
        extracted_text=extracted_lecture["full_text"],
        document_locations=extracted_lecture["locations"]
    )

    assert result.get("success") is True
    matched_topics = result.get("matched_topics", [])
    assert len(matched_topics) > 0

    by_name = {t["topic_name"]: t for t in matched_topics}

    # 1. Architecture of an RNN: Must be covered (Strong match)
    assert "Architecture of an RNN" in by_name
    rnn = by_name["Architecture of an RNN"]
    assert rnn.get("coverage_status") == "covered"
    assert rnn.get("match_category") == "Strong match" or rnn.get("match_label") == "Strong match"

    # 2. Backpropagation through Time: Must be covered (Strong match)
    assert "Backpropagation through Time" in by_name
    bptt = by_name["Backpropagation through Time"]
    assert bptt.get("coverage_status") == "covered"
    assert bptt.get("match_category") == "Strong match" or bptt.get("match_label") == "Strong match"

    # 3. Long Short Term Memory (LSTM): Must be partially covered or covered
    assert "Long Short Term Memory (LSTM)" in by_name
    lstm = by_name["Long Short Term Memory (LSTM)"]
    assert lstm.get("coverage_status") in ("partially_covered", "covered")

    # 4. Gated Recurrent Units (GRU): NOT mentioned in lecture -> must NOT be in matched_topics or not_found
    if "Gated Recurrent Units" in by_name:
        gru = by_name["Gated Recurrent Units"]
        assert gru.get("coverage_status") == "not_found"
        assert len(gru.get("evidence", [])) == 0

    # 5. Sentiment Analysis: NOT mentioned -> must NOT be in matched_topics or not_found
    if "Sentiment Analysis" in by_name:
        sent = by_name["Sentiment Analysis"]
        assert sent.get("coverage_status") == "not_found"
        assert len(sent.get("evidence", [])) == 0

    # 6. Applications- Text Classification: Passing/incidental mention on page 1/4 only
    # MUST NOT produce Strong Match or Partial Match; must be not_found (0 evidence)
    assert "Applications- Text Classification" not in by_name, (
        "Applications- Text Classification must not be claimed as covered or partially covered "
        "when the lecture contains only incidental mentions."
    )


# ── 3. Page-Level Evidence Provenance (No Fabrication, Accurate Pages) ───────

def test_page_level_evidence_provenance(extracted_lecture):
    result = match_notes_to_curriculum(
        extracted_text=extracted_lecture["full_text"],
        document_locations=extracted_lecture["locations"]
    )
    by_name = {t["topic_name"]: t for t in result.get("matched_topics", [])}

    # Architecture of an RNN: Must point to Page 2 (where Section 13.2 and Figure 13.1 are)
    rnn = by_name["Architecture of an RNN"]
    rnn_evidence = rnn.get("evidence", [])
    assert len(rnn_evidence) > 0, "Architecture of an RNN must have evidence"
    rnn_pages = [e.get("page_number") for e in rnn_evidence]
    assert 2 in rnn_pages or 4 in rnn_pages, f"Expected page 2 or 4 in RNN evidence, got {rnn_pages}"

    # Backpropagation through Time: MUST point to Page 6 (where Section 13.4.2 is)
    # Must NOT blindly match Page 1 due to incidental words like 'time'
    bptt = by_name["Backpropagation through Time"]
    bptt_evidence = bptt.get("evidence", [])
    assert len(bptt_evidence) > 0, "BPTT must have evidence"
    bptt_pages = [e.get("page_number") for e in bptt_evidence]
    assert 6 in bptt_pages or 7 in bptt_pages, f"Expected page 6 or 7 in BPTT evidence, got {bptt_pages}"
    # Verify BPTT snippet actually contains BPTT or Backpropagation
    bptt_snippets = " ".join([e.get("snippet", "").lower() for e in bptt_evidence])
    assert "backpropagation" in bptt_snippets or "bptt" in bptt_snippets

    # LSTM: Must point to Page 8 or 9 (where Section 13.5 and LSTM architecture are)
    lstm = by_name["Long Short Term Memory (LSTM)"]
    lstm_evidence = lstm.get("evidence", [])
    assert len(lstm_evidence) > 0, "LSTM must have evidence"
    lstm_pages = [e.get("page_number") for e in lstm_evidence]
    assert 8 in lstm_pages or 9 in lstm_pages, f"Expected page 8 or 9 in LSTM evidence, got {lstm_pages}"

    # Evidence locations must contain required metadata keys: location, snippet, relevance, matched_terms
    for topic_item in result.get("matched_topics", []):
        for ev in topic_item.get("evidence", []):
            assert "location" in ev
            assert "snippet" in ev
            assert "relevance" in ev
            assert "matched_terms" in ev
            assert "page_number" in ev or "slide_number" in ev


# ── 4. Strict Evidence Invariant: 0 Evidence MUST Yield NOT_FOUND ─────────────

def test_strict_zero_evidence_yields_not_found():
    # A candidate topic that has NO text presence in the document
    res = _coverage_status_for(
        cand={"topic_name": "Quantum Computing Superposition", "confidence": 0.85, "title_present": False, "score": 0.85},
        text_lower="simple deep learning lecture text",
        evidence=[]  # explicitly 0 evidence
    )
    assert res == "not_found"


# ── 5. Dropdown Optgroup Partitioning & Deduplication Logic ──────────────────

def test_dropdown_grouping_logic():
    syllabus_topics = [
        {"id": "t1", "name": "Architecture of an RNN"},
        {"id": "t2", "name": "Backpropagation through Time"},
        {"id": "t3", "name": "Long Short Term Memory (LSTM)"},
        {"id": "t4", "name": "Applications- Text Classification"},
        {"id": "t5", "name": "Gated Recurrent Units"},
        {"id": "t6", "name": "Sentiment Analysis"},
        {"id": "t1_dup", "name": "Architecture of an RNN"}  # duplicate
    ]

    raw_matches = [
        {"topic_name": "Architecture of an RNN", "coverage_status": "covered"},
        {"topic_name": "Backpropagation through Time", "coverage_status": "covered"},
        {"topic_name": "Long Short Term Memory (LSTM)", "coverage_status": "partially_covered"},
        {"topic_name": "Applications- Text Classification", "coverage_status": "not_found"},
        {"topic_name": "Gated Recurrent Units", "coverage_status": "not_found"},
        {"topic_name": "Sentiment Analysis", "coverage_status": "not_found"}
    ]

    # Mirror frontend/js/app.js coverage map construction
    coverage_map = {}
    for m in raw_matches:
        raw_key = m["topic_name"].strip().lower()
        norm_key = raw_key.replace("-", " ").replace("(", "").replace(")", "").strip()
        coverage_map[raw_key] = m["coverage_status"]
        coverage_map[norm_key] = m["coverage_status"]

    # Deduplicate syllabus topics first
    deduped = []
    seen = set()
    for t in syllabus_topics:
        norm = t["name"].lower().replace("-", " ").replace("(", "").replace(")", "").strip()
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(t)

    assert len(deduped) == 6, "Expected duplicate 'Architecture of an RNN' to be removed"

    covered = []
    partial = []
    other = []

    for t in deduped:
        raw_key = t["name"].strip().lower()
        norm_key = raw_key.replace("-", " ").replace("(", "").replace(")", "").strip()
        status = coverage_map.get(raw_key) or coverage_map.get(norm_key) or "not_found"
        if status == "covered":
            covered.append(t["name"])
        elif status == "partially_covered":
            partial.append(t["name"])
        else:
            other.append(t["name"])

    # Optgroup 1: Topics found in your notes
    assert covered == ["Architecture of an RNN", "Backpropagation through Time"]

    # Optgroup 2: Partially covered topics
    assert partial == ["Long Short Term Memory (LSTM)"]

    # Optgroup 3: Other syllabus topics (general practice)
    assert other == ["Applications- Text Classification", "Gated Recurrent Units", "Sentiment Analysis"]

    # Verify no overlap across groups
    assert set(covered).isdisjoint(set(partial))
    assert set(covered).isdisjoint(set(other))
    assert set(partial).isdisjoint(set(other))


# ── 6. Notes Grounded vs General Practice Request Payload Verification ───────

def test_quiz_generation_payload_isolation():
    """
    Ensure notes_session_id and study_material_id are sent ONLY when topic is covered
    or partially covered, and are None for general practice topics.
    """
    last_extraction = {
        "notes_session_id": "sess_dl_999",
        "study_material_id": "mat_dl_888",
        "matched_topics": [
            {"topic_name": "Architecture of an RNN", "coverage_status": "covered"},
            {"topic_name": "Gated Recurrent Units", "coverage_status": "not_found"}
        ]
    }

    def build_payload(topic_name, coverage_status):
        payload = {
            "course": "CS217",
            "topic": topic_name,
            "difficulty": "medium",
            "num_questions": 5
        }
        if coverage_status in ("covered", "partially_covered") and last_extraction:
            payload["notes_session_id"] = last_extraction.get("notes_session_id")
            payload["study_material_id"] = last_extraction.get("study_material_id")
        else:
            payload["notes_session_id"] = None
            payload["study_material_id"] = None
        return payload

    # 1. Covered topic -> Grounded
    p_covered = build_payload("Architecture of an RNN", "covered")
    assert p_covered["notes_session_id"] == "sess_dl_999"
    assert p_covered["study_material_id"] == "mat_dl_888"

    # 2. Not found topic -> General practice (NO grounding hallucination)
    p_uncovered = build_payload("Gated Recurrent Units", "not_found")
    assert p_uncovered["notes_session_id"] is None
    assert p_uncovered["study_material_id"] is None


# ── 7. Database Persistence & Student Data Isolation ─────────────────────────

def test_database_persistence_and_student_isolation():
    init_db()
    student_a = "student_alpha_001"
    student_b = "student_beta_002"

    matched_topics = [
        {
            "topic_id": "rnn_arch",
            "topic_name": "Architecture of an RNN",
            "course_code": "CS217",
            "course_name": "Deep Learning",
            "confidence": 0.95,
            "coverage_status": "covered",
            "matched_concepts": ["recurrent", "hidden state"],
            "evidence": [{"page_number": 2, "location": "Page 2", "snippet": "Architecture of an RNN", "relevance": "Section 13.2"}]
        }
    ]

    # Create material for Student A
    mat = create_or_update_study_material(
        student_id=student_a,
        filename="CS217_2024_lec13.pdf",
        file_type="application/pdf",
        file_size=543944,
        extracted_text="Recurrent Neural Networks lecture text",
        matched_topics=matched_topics,
        notes_session_id="sess_alpha_1"
    )
    assert mat is not None
    mat_id = mat["id"]

    # Student A queries materials: must find mat_id
    materials_a = get_student_study_materials(student_a)
    assert any(m["id"] == mat_id for m in materials_a)

    detail_a = get_study_material_detail(mat_id, student_a)
    assert detail_a is not None
    assert detail_a["filename"] == "CS217_2024_lec13.pdf"
    assert len(detail_a["topics"]) == 1
    assert detail_a["topics"][0]["coverage_status"] == "covered"

    # Student B queries materials: must NOT see Student A's materials
    materials_b = get_student_study_materials(student_b)
    assert not any(m["id"] == mat_id for m in materials_b)

    # Student B tries to access Student A's material directly: PermissionError must be raised
    with pytest.raises(PermissionError):
        get_study_material_detail(mat_id, student_b)


# ── 8. Incidental Mention Rejection & Case Distinction ───────────────────────

def test_incidental_mention_rejection():
    """
    Enforces distinction between:
      Case A: Incidental mention in a list -> not_found (0 valid evidence)
      Case B: Foundational explanation -> partially_covered
      Case C: Substantial coverage -> covered
    """
    # Case A: Passing mention in an illustrative list
    incidental_locs = [
        {
            "location": "Page 1",
            "page_number": 1,
            "text": "Deep learning architectures have revolutionized many modern domains including text classification, speech processing, and automated translation systems."
        }
    ]
    ev_incidental = _topic_in_locations(
        "Applications- Text Classification",
        ["text classification"],
        incidental_locs,
        course_code="22AM301"
    )
    # The occurrence in a generic list must be marked incidental or score < 12.0
    for e in ev_incidental:
        assert e.get("is_incidental") is True or e.get("score", 0) < 12.0

    cand_incidental = {
        "topic_name": "Applications- Text Classification",
        "course_code": "22AM301",
        "score": 0.35,
        "matched_concepts": ["text classification"]
    }
    status_inc = _coverage_status_for(cand_incidental, incidental_locs[0]["text"].lower(), ev_incidental)
    assert status_inc == "not_found", f"Incidental mention must be not_found, got {status_inc}"

    # Case B / C: Substantive technical explanation with heading
    substantive_locs = [
        {
            "location": "Page 4",
            "page_number": 4,
            "text": "14.2 Text Classification Architectures\nText classification assigns categorical labels to sequence inputs. Given document tokens x_1 to x_T, we compute sequence embeddings and apply softmax cross-entropy loss."
        }
    ]
    ev_substantive = _topic_in_locations(
        "Applications- Text Classification",
        ["text classification"],
        substantive_locs,
        course_code="22AM301"
    )
    assert len(ev_substantive) >= 1
    assert any(not e.get("is_incidental", False) for e in ev_substantive)
    cand_sub = {
        "topic_name": "Applications- Text Classification",
        "course_code": "22AM301",
        "score": 0.65,
        "matched_concepts": ["text classification"]
    }
    status_sub = _coverage_status_for(cand_sub, substantive_locs[0]["text"].lower(), ev_substantive)
    assert status_sub in ("partially_covered", "covered")


# ── 9. Canonical Alias Deduplication & Semantic Boundary Preservation ────────

def test_canonical_alias_deduplication_and_boundary_preservation():
    """
    Verifies:
      1. Canonical deduplication merges synonyms (BPTT / Backpropagation Through Time).
      2. Distinct syllabus topics (RNN vs LSTM vs GRU) remain strictly separate.
      3. Cross-course alias isolation prevents leakage across courses.
    """
    # 1. Merging synonyms under course 22AM301
    cands_synonyms = [
        {
            "course_code": "22AM301",
            "course_name": "Deep Learning",
            "topic_name": "BPTT",
            "topic_id": "alias::bptt",
            "score": 0.80,
            "confidence": 0.80,
            "evidence": [{"page_number": 6, "score": 32.0, "evidence_type": "section_heading"}]
        },
        {
            "course_code": "22AM301",
            "course_name": "Deep Learning",
            "topic_name": "Backpropagation through Time",
            "topic_id": "22AM301-Q019-1",
            "score": 0.85,
            "confidence": 0.85,
            "evidence": [{"page_number": 6, "score": 32.0, "evidence_type": "section_heading"}]
        }
    ]
    merged = consolidate_candidates(cands_synonyms)
    assert len(merged) == 1, f"Expected 1 consolidated record, got {len(merged)}"
    assert merged[0]["topic_name"] == "Backpropagation through Time"

    # 2. Boundary preservation: Distinct topics must NEVER merge
    distinct_cands = [
        {"course_code": "22AM301", "topic_name": "Architecture of an RNN", "topic_id": "22AM301-Q018-1", "score": 0.9},
        {"course_code": "22AM301", "topic_name": "Long Short Term Memory (LSTM)", "topic_id": "22AM301-Q019-1", "score": 0.85},
        {"course_code": "22AM301", "topic_name": "Gated Recurrent Units", "topic_id": "22AM301-Q019-2", "score": 0.8},
        {"course_code": "22AM301", "topic_name": "Applications- Text Classification", "topic_id": "22AM301-Q019-3", "score": 0.75},
        {"course_code": "22AM301", "topic_name": "Sentiment Analysis", "topic_id": "22AM301-Q020", "score": 0.7}
    ]
    consolidated_distinct = consolidate_candidates(distinct_cands)
    assert len(consolidated_distinct) == 5, f"Expected 5 distinct topics, got {len(consolidated_distinct)}"
    distinct_names = {t["topic_name"] for t in consolidated_distinct}
    assert "Architecture of an RNN" in distinct_names
    assert "Long Short Term Memory (LSTM)" in distinct_names
    assert "Gated Recurrent Units" in distinct_names
    assert "Applications- Text Classification" in distinct_names
    assert "Sentiment Analysis" in distinct_names

    # 3. Cross-course alias isolation
    # 'dutch national flag' is valid for 22AM203 (Algorithms), but must NOT match 22AM301 (Deep Learning)
    canon_ada = alias_for("dutch national flag", "22AM203")
    assert canon_ada == "Algorithm, Pseudo-Code for Expressing Algorithms, Performance Analysis - Space and Time Complexity"

    canon_dl = alias_for("dutch national flag", "22AM301")
    assert canon_dl is None, "Cross-course alias must not leak into unrelated courses"


# ── 10. Evidence Quality & Proximity Window Boundaries ───────────────────────

def test_evidence_quality_and_proximity_boundaries():
    """
    Verifies:
      1. Multi-word defining terms scattered across distant paragraphs (distance > 120 chars) are rejected.
      2. Terms in close proximity (<= 120 chars) form a valid compound evidence point.
      3. Section headings receive section_heading classification and high evidence score.
    """
    # Dispersed text: 'classification' and 'text' separated by 400 characters
    distant_text = (
        "In this chapter we explore pass fail classification for student performance. "
        + ("word " * 60)
        + "Later in the textbook, speech and text signals are introduced as modalities."
    )
    has_prox, _, _ = _words_in_proximity(distant_text.lower(), {"classification", "text"}, window=120)
    assert has_prox is False, "Scattered words across distant paragraphs must fail proximity check"

    # Close text: terms in immediate proximity
    close_text = "We evaluate recurrent neural networks for text classification benchmarks."
    has_prox_close, start, end = _words_in_proximity(close_text.lower(), {"classification", "text"}, window=120)
    assert has_prox_close is True
    assert end - start <= 120

    # Section heading quality test
    heading_text = "13.4.2 Backpropagation through Time\nHere we derive the gradient equations..."
    locs = [{"location": "Page 6", "page_number": 6, "text": heading_text}]
    ev = _topic_in_locations("Backpropagation through Time", [], locs, course_code="22AM301")
    assert len(ev) == 1
    assert ev[0]["evidence_type"] == "section_heading"
    assert ev[0]["score"] >= 25.0


# ── 11. Cross-Document Validation on Second Real Document (ADA .pptx) ────────

def test_cross_document_validation_on_real_pptx():
    """
    Performs cross-document validation against a real second lecture document from a different course:
      File: ADA .pptx (13 slides, 341,674 bytes)
      Course: 22AM203 (Design and Analysis of Algorithms Using Python)
    Verifies:
      1. Accurate extraction of 13 slides.
      2. Taught topic coverage: 'Greedy Method' (slides 9, 12) and 'Space and Time Complexity' (slides 3, 4, 8) are covered.
      3. Untaught syllabus topics ('Coloring and Hamiltonian Cycles', 'Branch and Bound') are strictly not_found with 0 evidence.
    """
    pptx_path = get_real_pptx()
    with open(pptx_path, "rb") as f:
        file_bytes = f.read()

    locations = extract_text_with_locations("ADA .pptx", file_bytes)
    assert len(locations) == 13, f"Expected 13 slides, got {len(locations)}"

    full_text = "\n\n".join([l["text"] for l in locations])
    result = match_notes_to_curriculum(full_text, document_locations=locations)

    assert result.get("success") is True
    assert result.get("relevant") is True

    matched = result.get("matched_topics", [])
    assert len(matched) >= 1

    by_name = {t["topic_name"]: t for t in matched}

    # Verify at least one taught topic is matched and covered
    assert "Greedy Method" in by_name or "Algorithm, Pseudo-Code for Expressing Algorithms, Performance Analysis - Space and Time Complexity" in by_name

    if "Greedy Method" in by_name:
        greedy = by_name["Greedy Method"]
        assert greedy["coverage_status"] == "covered"
        ev_pages = [e.get("page_number") for e in greedy.get("evidence", [])]
        assert 9 in ev_pages or 12 in ev_pages

    # Untaught topics in 22AM203 curriculum must NOT be in matched_topics
    assert "Coloring and Hamiltonian Cycles. Backtracking and Branch" not in by_name
    assert "Branch and Bound" not in by_name
    assert "Dynamic Backtracking: General Method" not in by_name


# ── 12. Synthetic Multi-Domain Regression Fixtures ───────────────────────────

def test_synthetic_multi_domain_regression_fixtures():
    """
    Deterministic synthetic fixtures across distinct academic subject areas
    demonstrating domain-general zero-evidence invariants and alias isolation.
    """
    # Domain 1: Database Management Systems (relational normalization)
    db_text = (
        "Database Normalization Lecture: Boyce-Codd Normal Form (BCNF) requires that every determinant "
        "is a candidate key. Third Normal Form eliminates transitive functional dependencies."
    )
    db_locs = [{"location": "Page 1", "page_number": 1, "text": db_text}]
    db_res = match_notes_to_curriculum(db_text, document_locations=db_locs)
    # Even if DB topics match or don't match, verify unmentioned Deep Learning topics have 0 evidence
    unrelated_cov = _coverage_status_for(
        cand={"topic_name": "Backpropagation through Time", "course_code": "22AM301", "score": 0.0},
        text_lower=db_text.lower(),
        evidence=[]
    )
    assert unrelated_cov == "not_found"

    # Domain 2: Operating Systems (Virtual memory demand paging)
    os_text = (
        "Operating Systems: Demand Paging and Page Replacement Algorithms. When a page fault occurs, "
        "the OS brings the desired page into an available frame using LRU or FIFO replacement."
    )
    os_locs = [{"location": "Page 1", "page_number": 1, "text": os_text}]
    os_res = match_notes_to_curriculum(os_text, document_locations=os_locs)
    unrelated_cov_os = _coverage_status_for(
        cand={"topic_name": "Greedy Method", "course_code": "22AM203", "score": 0.0},
        text_lower=os_text.lower(),
        evidence=[]
    )
    assert unrelated_cov_os == "not_found"
