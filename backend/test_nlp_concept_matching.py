"""
LearnAI — Acceptance and Regression Test Suite for Step 1 & Step 2:
Document Extraction Hygiene and NLP Concept Matching.

Validates:
1. Document extraction hygiene:
   - Unicode ligature normalization (e.g. 'ﬁ' -> 'fi', 'ﬂ' -> 'fl', 'ﬀ' -> 'ff').
   - Line-break hyphenation repair without breaking genuine compound technical terms.
   - Preservation of paragraph boundaries and size limits.
2. Runtime candidate keyphrase extraction:
   - Extraction of TF-IDF weighted unigrams, bigrams, and trigrams.
   - Stopword and presentation noise filtering.
   - Technical term and acronym preservation (1NF, 2NF, BCNF, SQL, ACID, 2PL, DBMS).
3. Hybrid curriculum topic matching (TF-IDF Cosine Similarity + Lexical + Course Context):
   - Exact curriculum title matching.
   - Paraphrased notes where wording differs from syllabus topic titles (e.g. DBMS Normalization, Concurrency).
   - Technical concepts ranked ahead of generic words.
   - Unrelated content (chocolate cake recipe, gardening tips) correctly rejected with relevant=False.
   - Notes from another course (e.g. Linear Algebra) not falsely matched to DBMS.
   - Empty, short (<15 chars), and noisy OCR strings handled safely.
   - Response contract schema preservation and session persistence.
"""

import pytest
from services.notes_extractor import (
    clean_extracted_text,
    repair_line_break_hyphenation,
    extract_text_from_txt,
    MAX_EXTRACTED_CHARS
)
from services.notes_matcher import (
    match_notes_to_curriculum,
    extract_document_keyphrases,
    store_notes_session,
    get_notes_session,
    format_concept_title,
    SKLEARN_AVAILABLE
)


class TestStep1ExtractionHygiene:
    """Validates ligature normalization, hyphenation repair, and sanitization."""

    def test_unicode_ligature_normalization(self):
        raw_text = "The speciﬁc conﬁguration afﬂicted the ﬂow and efﬁcient classiﬁcation."
        cleaned = clean_extracted_text(raw_text)
        assert "specific" in cleaned
        assert "configuration" in cleaned
        assert "afflicted" in cleaned
        assert "flow" in cleaned
        assert "efficient" in cleaned
        assert "classification" in cleaned
        assert "ﬁ" not in cleaned
        assert "ﬂ" not in cleaned

    def test_line_break_hyphenation_repair_single_words(self):
        text_with_hyphens = (
            "The rela-\ntional data-\nbase model uses nor-\nmalization, func-\n"
            "tional depend-\nencies, and serializ-\nability."
        )
        repaired = repair_line_break_hyphenation(text_with_hyphens)
        assert "relational" in repaired
        assert "database" in repaired
        assert "normalization" in repaired
        assert "functional" in repaired
        assert "dependencies" in repaired
        assert "serializability" in repaired

    def test_line_break_hyphenation_preserves_genuine_compounds(self):
        compounds_text = (
            "We configured client-\nserver systems with two-\nphase locking, "
            "read-\nonly replicas, and key-\nvalue stores."
        )
        repaired = repair_line_break_hyphenation(compounds_text)
        assert "client-server" in repaired
        assert "two-phase" in repaired
        assert "read-only" in repaired
        assert "key-value" in repaired

    def test_paragraph_breaks_and_control_characters_preserved(self):
        raw = "Paragraph 1.\x00\x07\n\n\n\nParagraph 2.\t\tStill Paragraph 2."
        cleaned = clean_extracted_text(raw)
        assert "\x00" not in cleaned
        assert "\x07" not in cleaned
        assert "Paragraph 1.\n\nParagraph 2. Still Paragraph 2." == cleaned

    def test_bounds_at_maximum_characters(self):
        huge_text = "A" * (MAX_EXTRACTED_CHARS + 500)
        cleaned = clean_extracted_text(huge_text)
        assert len(cleaned) == MAX_EXTRACTED_CHARS


class TestStep2KeyphraseExtraction:
    """Validates candidate concept extraction from student notes."""

    def test_sklearn_is_verified_available(self):
        assert SKLEARN_AVAILABLE is True, "scikit-learn should be available in active runtime"

    def test_keyphrase_extraction_technical_terms(self):
        text = (
            "In this database lecture, we studied relational normalization. "
            "We analyzed functional dependencies, candidate keys, First Normal Form (1NF), "
            "Second Normal Form (2NF), Third Normal Form (3NF), and BCNF. "
            "Additionally, we discussed Two-Phase Locking (2PL) protocols and ACID properties."
        )
        keyphrases = extract_document_keyphrases(text, top_n=10)
        assert len(keyphrases) >= 3

        phrase_names = [p[0] for p in keyphrases]
        phrase_upper = [p[0].upper() for p in keyphrases]

        # Verify meaningful technical terms and acronyms are extracted
        assert any("1NF" in p or "2NF" in p or "3NF" in p or "BCNF" in p for p in phrase_upper)
        assert any("NORMAL" in p or "NORMALIZATION" in p or "DEPENDENC" in p for p in phrase_upper)
        assert any("LOCKING" in p or "ACID" in p or "KEY" in p for p in phrase_upper)

    def test_keyphrases_filter_common_noise_and_stopwords(self):
        noisy_text = (
            "Today in this lecture we covered a summary of some examples and concepts. "
            "The chapter slide notes are provided for study. "
            "The student must learn the definition of database systems."
        )
        keyphrases = extract_document_keyphrases(noisy_text, top_n=5)
        phrase_names_lower = [p[0].lower() for p in keyphrases]
        # Common presentation noise should be filtered
        for noise in ["today", "lecture", "covered", "summary", "chapter", "slide", "study"]:
            assert noise not in phrase_names_lower

    def test_keyphrase_extraction_handles_empty_and_short(self):
        assert extract_document_keyphrases("") == []
        assert extract_document_keyphrases("Too short") == []
        assert extract_document_keyphrases("   \n\t   ") == []


class TestStep2HybridTopicMatching:
    """Validates TF-IDF and hybrid matching on positive, negative, and distractor cases."""

    def test_exact_curriculum_title_match(self):
        notes = (
            "Course: 22AM201 Artificial Intelligence\n"
            "Selected Topic: The Concept of Rationality and Types of Agents.\n"
            "An ideal rational agent maximizes expected performance measure. "
            "We studied reflex agents, goal-based agents, and utility-based agents."
        )
        res = match_notes_to_curriculum(notes)
        assert res["success"] is True
        assert res["relevant"] is True
        assert len(res["matched_topics"]) >= 1
        top = res["matched_topics"][0]
        assert top["course_code"] == "22AM201"
        assert top["confidence"] >= 0.50
        assert "matched_concepts" in top
        assert isinstance(top["matched_concepts"], list)

    def test_dbms_normalization_paraphrased_matches_dbms(self):
        """Notes describing Normalization without exact syllabus heading."""
        notes = (
            "We studied relational database normalization techniques. "
            "Specifically First Normal Form (1NF), Second Normal Form (2NF), Third Normal Form (3NF), "
            "and Boyce-Codd Normal Form (BCNF). "
            "Functional dependencies are used to decompose relations to avoid insertion, deletion, and update anomalies. "
            "Lossless join and dependency preservation were analyzed."
        )
        res = match_notes_to_curriculum(notes)
        assert res["success"] is True
        assert res["relevant"] is True
        assert len(res["matched_topics"]) >= 1

        top = res["matched_topics"][0]
        assert top["course_code"] == "22CS201", f"Expected 22CS201 DBMS, got {top['course_code']} ({top['topic_name']})"
        assert "Normal" in top["topic_name"] or "Dependencies" in top["topic_name"] or "Relational" in top["topic_name"]
        assert top["confidence"] >= 0.40

    def test_dbms_concurrency_paraphrased_matches_dbms(self):
        """Notes describing Concurrency & Transactions without exact syllabus heading."""
        notes = (
            "Transaction processing in database systems requires ACID properties: "
            "Atomicity, Consistency, Isolation, Durability. "
            "To avoid dirty reads and unrepeatable reads, serializability is enforced using "
            "Two-Phase Locking (2PL) protocol. "
            "Shared locks (S) and exclusive locks (X) prevent conflicting read and write operations. "
            "Deadlocks are detected using wait-for graphs."
        )
        res = match_notes_to_curriculum(notes)
        assert res["success"] is True
        assert res["relevant"] is True
        assert len(res["matched_topics"]) >= 1

        top = res["matched_topics"][0]
        assert top["course_code"] == "22CS201", f"Expected 22CS201 DBMS, got {top['course_code']} ({top['topic_name']})"
        assert any(term in top["topic_name"].lower() for term in ["transaction", "concurrency", "properties"])

    def test_unrelated_chocolate_cake_recipe_rejected(self):
        recipe = (
            "Classic Homemade Chocolate Cake Recipe.\n"
            "Ingredients: 2 cups all-purpose flour, 2 cups white granulated sugar, "
            "3/4 cup unsweetened cocoa powder, 2 tsp baking powder, 1.5 tsp baking soda.\n"
            "Bake in preheated oven at 350 degrees F for 30 to 35 minutes until toothpick comes out clean."
        )
        res = match_notes_to_curriculum(recipe)
        assert res["success"] is True
        assert res["relevant"] is False
        assert len(res["matched_topics"]) == 0
        assert "does not cover supported LearnAI curriculum topics" in res["message"]

    def test_unrelated_gardening_tips_rejected(self):
        gardening = (
            "Gardening tips: plant tomatoes in full sun, water deeply twice a week, mulch with straw. "
            "Prune suckers to encourage fruit production and fertilize every three weeks."
        )
        res = match_notes_to_curriculum(gardening)
        assert res["success"] is True
        assert res["relevant"] is False
        assert len(res["matched_topics"]) == 0

    def test_cross_course_distractor_linear_algebra_does_not_match_dbms(self):
        """Linear Algebra notes must match 22MT103, not 22CS201."""
        math_notes = (
            "Lecture on Matrices and Linear Equations (22MT103).\n"
            "We computed eigenvalues and eigenvectors of 3x3 square matrices. "
            "Solving linear systems using Gauss-Jordan elimination and verifying "
            "the Cayley-Hamilton theorem to find powers of a matrix."
        )
        res = match_notes_to_curriculum(math_notes)
        assert res["success"] is True
        assert res["relevant"] is True
        assert len(res["matched_topics"]) >= 1

        top = res["matched_topics"][0]
        assert top["course_code"] == "22MT103"
        assert not any(t["course_code"] == "22CS201" for t in res["matched_topics"][:3])

    def test_empty_and_short_notes_handled_gracefully(self):
        assert match_notes_to_curriculum("")["relevant"] is False
        assert match_notes_to_curriculum("Hi")["relevant"] is False
        assert match_notes_to_curriculum("Short note")["relevant"] is False

    def test_noisy_ocr_text_does_not_crash(self):
        noisy = "!@#$%^&*()_+{}[]:;'<>?,./~` " * 50
        res = match_notes_to_curriculum(noisy)
        assert res["success"] is True
        assert res["relevant"] is False

    def test_long_notes_and_session_persistence(self):
        lecture_text = (
            "Course: 22CS201 Database Management Systems\n"
            "Topic: Relational Model\n"
            "Relational data model fundamentals: tuples, attributes, relations, domains. "
            + ("Detailed study of integrity constraints: primary key, entity integrity, referential integrity with foreign keys. " * 30)
        )
        res = match_notes_to_curriculum(lecture_text, student_id="test-student-456")
        assert res["success"] is True
        assert res["relevant"] is True
        assert res["notes_session_id"] is not None

        # Verify session can be retrieved
        session = get_notes_session(res["notes_session_id"])
        assert session is not None
        assert session["student_id"] == "test-student-456"
        assert "Database Management Systems" in session["text"]
        assert len(session["matched_topics"]) >= 1

    def test_api_response_contract_backward_compatibility(self):
        """Verifies every field required by frontend/js/app.js is present."""
        notes = "22CS104 Python Programming - Lists, Tuples, Dictionaries and Sets in Python."
        res = match_notes_to_curriculum(notes)
        assert "success" in res
        assert "relevant" in res
        assert "partial" in res
        assert "matched_topics" in res
        assert "message" in res
        assert "notes_session_id" in res
        assert "notes_preview" in res

        if res["matched_topics"]:
            top = res["matched_topics"][0]
            for required_key in ["course_code", "course_name", "topic_name", "confidence", "matched_concepts"]:
                assert required_key in top
                assert top["matched_concepts"] is not None
                assert isinstance(top["matched_concepts"], list)
