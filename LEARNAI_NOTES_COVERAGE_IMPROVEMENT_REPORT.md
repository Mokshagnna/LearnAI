# LearnAI — Notes-to-Syllabus Matching Improvement Report

**Project:** LearnAI (Backend + Frontend)
**Scope:** Notes & Slides upload pipeline, topic extraction, syllabus matching, topic deduplication, match-confidence calculation, topic display, and notes-grounded quiz generation.
**Status:** Implementation, testing, and reporting complete. Awaiting review before commit/push/deploy.
**Git:** No commits, pushes, or deploys performed. All pre-existing uncommitted changes preserved.

---

## 1. Root Cause Analysis

| Symptom | Root cause |
|---------|------------|
| **Duplicate / synonymous topic cards** (e.g. "BPTT" and "Backpropagation Through Time" on separate cards) | `notes_matcher.py` deduplicated only by exact `(course_code, topic_name)`. There was no normalisation, alias dictionary, or syllabus-topic-id consolidation, so spelling variants and abbreviations of the same topic survived as separate records. |
| **Repetitive generic concept tags** on every card (e.g. "Neural Networks", "Recurrent Neural Networks" under every RNN topic) | `matched_concepts` was returned unfiltered: raw TF-IDF keyphrases and all syllabus concepts were displayed with no de-duplication, no generic-term removal, and no cap. |
| **Misleading "75% Match / 69% Match" percentages** | The `confidence` value was a hybrid score = `0.50*TF-IDF + 0.25*lexical + 0.25*course-context`, yet the frontend rendered it as `"% Match"`. A similarity score is not a coverage percentage, and TF-IDF similarity was computed against a `syllabus_context` field that repeats the *entire unit topic list* for every sibling topic — making sibling topics (LSTM / GRU / Text Classification) nearly indistinguishable and manufacturing false-positive coverage for topics absent from the notes. |
| **No covered / partially-covered / not-found distinction** | Only a crude boolean `relevant` and a heuristic `partial` flag existed; passing keyword mentions were treated the same as explanatory sections. |
| **No page/slide evidence** | `notes_extractor.py` joined all PDF pages / PPTX slides into one string, discarding page- and slide-level provenance, so evidence could never cite "Page 6" or "Slide 2". |
| **Hard cap of six topics** | `top_matches = deduped_candidates[:6]` forced an arbitrary topic count regardless of document content. |

---

## 2. NLP Improvements

### 2.1 Text extraction with provenance (`notes_extractor.py`)
- Refactored PDF, PPTX, and legacy PPT extraction into per-page / per-slide internal functions (`_extract_pdf_pages`, `_extract_pptx_slides`, `_extract_ppt_slides`).
- Added public `extract_text_with_locations(filename, file_bytes)` returning `[{location, location_type, text}]` (e.g. `"Page 3"`, `"Slide 2"`). Page/slide numbers come from the actual document structure; they are never fabricated.
- `extract_text_from_file` keeps its existing string-returning contract for backward compatibility.

### 2.2 Topic normalisation & aliasing (`notes_matcher.py`)
- `normalize_topic_name()`: lower-case, strip, collapse whitespace, remove punctuation/hyphens/underscores.
- `alias_for()`: curated synonym map (BPTT → Backpropagation Through Time, LSTM ↔ Long Short-Term Memory, GRU → Gated Recurrent Unit, etc.). Genuinely distinct concepts that merely share words (RNN/LSTM/GRU architectures; Text Classification/Sentiment Analysis) are NOT aliased.
- `find_syllabus_topic()`: resolves a candidate to a canonical syllabus topic record via alias → normalised exact → conservative contains-match (length-ratio ≥ 0.75 so "Introduction" does not swallow "Introduction to Transaction").

### 2.3 Deduplication & consolidation (`consolidate_candidates`)
- Groups candidates by canonical syllabus topic id within the same course. Aliases that resolve to the same id merge (BPTT + Backpropagation Through Time → one record).
- Distinct syllabus topic ids are never merged, even when lexically similar (RNN vs LSTM vs GRU stay separate).
- Winning record keeps canonical title/topic id, highest score, union of evidence, and de-duplicated supporting concepts.

### 2.3 Supporting-concept filtering (`filter_supporting_concepts`)
- Removes exact/near-duplicate aliases, generic parent concepts ("neural network", "recurrent neural", "machine learning", …), and concepts that repeat the topic name.
- Prefers specific multi-word technical concepts; caps visible concepts at 4 per topic.

### 2.4 Curriculum fix (`curriculum_service.py`)
- The `" & "` split of compound topics (e.g. "LSTM & GRU & Text Classification") previously gave every part the **full combined concept list**, making siblings indistinguishable. Each part now receives only the concepts that belong to it (exact normalised match), with a safe fallback that preserves all concepts when no clear assignment exists.

---

## 3. Matching Methodology

### 3.1 Scoring (unchanged hybrid, now documented)
`score = 0.50 * sim_norm + 0.25 * lex_score + 0.25 * course_score`
- `sim_norm = min(1.0, tfidf_sim / 0.40)` — TF-IDF cosine similarity against the curriculum topic index. `syllabus_context` was **removed** from the TF-IDF index document so sibling topics are distinguished by their own title/concepts rather than shared unit boilerplate.
- `lex_score` — lexical title/concept overlap.
- `course_score` — explicit course code/name mention.

`score` is stored internally as `internal_score` / `score_type: "hybrid_tfidf_lexical_course"` and is **never** exposed to the user as a coverage percentage.

### 3.2 Coverage classification rules (`classify_coverage`)
| Status | Rule |
|--------|------|
| `covered` | Title/alias present AND ≥2 supporting concepts AND content depth ≥0.35 AND score ≥0.60 — genuine explanatory coverage. |
| `partially_covered` | Some direct evidence (title, a concept, or content depth ≥0.10) with score ≥0.40, but not enough for full coverage. |
| `not_found` | No meaningful evidence, or a keyword-only mention with no title/concept/location evidence. |
| `needs_review` | Reserved for ambiguous/low-quality extraction (defined, available for use). |

A passing keyword mention never becomes `covered`.

### 3.3 Sibling suppression
Within a course + unit, if any topic has **direct evidence** (title/alias or a syllabus concept substantially present in the text), sibling topics matched only through shared `syllabus_context` vocabulary are dropped. This is what keeps GRU / Text Classification / Sentiment Analysis out when the notes only discuss LSTM.

### 3.4 Evidence (`_topic_in_locations`)
For each matched topic, document locations (page/slide/section) containing the topic title or concepts are located, and a real text snippet + matched terms are recorded. No page numbers or quotations are fabricated.

### 3.5 User-facing categories
`coverage_status` → `match_category`: `covered` → "Strong Match", `partially_covered` → "Partial Match", `not_found` → "Not found", `needs_review` → "Needs review".

---

## 4. Database and API Changes

### 4.1 Migration
- New `backend/migrations/004_topic_evidence_and_coverage.sql` (MySQL) and `.rollback.sql`.
- Columns added to `study_material_topics`: `original_topic_title`, `topic_aliases` (JSON), `coverage_status`, `evidence` (JSON), `source_location`, `extraction_version`, `matching_algorithm_version`.

### 4.2 SQLite (`database.py`)
- `study_material_topics` CREATE TABLE now includes the new columns.
- Idempotent `_ensure_column(...)` ALTER TABLE calls migrate existing SQLite databases (including the local `learnai.db`) without dropping data.
- `create_or_update_study_material()` persists the new fields (upsert with `ON DUPLICATE KEY UPDATE` / `ON CONFLICT ... DO UPDATE SET`).
- `get_study_material_detail()` selects and returns the new fields (parsing JSON `topic_aliases`/`evidence`).

### 4.3 API (`main.py`)
- `POST /api/notes/upload`: now extracts per-location text via `extract_text_with_locations`, passes `document_locations` to `match_notes_to_curriculum`, and persists coverage/evidence per topic. Removed the arbitrary 6-topic cap.
- `POST /api/quiz/generate`: new `practice_mode` field (`"notes_grounded"` default, `"general_syllabus"` alternative). Notes grounding (and the notes-alignment requirement) applies only in `notes_grounded` mode. General-syllabus practice generates curriculum-only questions and never claims notes grounding.
- Response now includes `practice_mode` and `notes_grounded`.
- Student ownership is enforced in the database layer (`PermissionError` on cross-student access), not only the frontend.

### 4.4 Backward compatibility
Existing response fields (`success`, `relevant`, `partial`, `matched_topics`, `message`, `notes_session_id`, `notes_preview`, per-topic `confidence`, `score`, `tfidf_similarity`, `matched_concepts`) are preserved.

---

## 5. Frontend Improvements

Preserved the Academic Scholar design system; no new visual language introduced.

### 5.1 Notes results card (`renderMatchedTopicsUI`)
- Coverage badge replaces "% Match": "Strong Match" / "Partial Match" / "Needs Review".
- Supporting concepts limited to ≤4 specific items (generic duplicates removed).
- Evidence row shows real location (Page/Slide) + actual text snippet.
- "Practice Topic" launches notes-grounded mode (passes `notes_session_id` / `study_material_id`).

### 5.2 Saved-material inline topics (`renderInlineTopicsList`)
- Coverage badge instead of "% Match"; concepts capped at 4.

### 5.3 Topic dropdown (`course`/`topic` change handler)
- Optgroup-separated: "Topics found in your notes" (covered), "Partially covered topics", "Other syllabus topics (general practice)".
- Manual topic selection now sets `practice_mode: "general_syllabus"` when no notes are attached.

### 5.4 Styling (`style.css`)
- Added `.badge-success`, `.badge-warning`, `.badge-error` and `.notes-evidence-*` styles.
- Existing spacing, badges, and mobile breakpoints preserved.

### 5.5 Screenshots
Actual screenshots could not be captured in this environment. The following UI states are implemented and verified via the frontend Node harness (DOM-level):
- Extracted topics with coverage badges + evidence (notes results card).
- Partially covered topics ("Partial Match").
- Other syllabus topics via the categorized dropdown (general practice).
- Desktop and mobile layouts use the existing responsive grid/badge classes.

To satisfy the report fully, capture browser screenshots of those four states during your review and insert them here.

---

## 6. Reference PDF Evaluation (CS217 Lecture 13 — RNN)

The actual reference file `CS217_2024_lec13.pdf` was not present in this environment, so evaluation used a faithful structural simulation of the document as described in the task (9 pages: pp.1–3 RNN intro/architecture/structures, pp.4–5 structures/training/forward pass, pp.6–7 BPTT, p.8 vanishing gradient, p.9 LSTM gates).

| Topic (from screenshot) | Classification | Supporting location | Basis |
|-------------------------|----------------|---------------------|-------|
| Architecture of an RNN | Strong match (covered) | Pages 1–2 | Title + architecture text present on p.2 |
| Unfolding of an RNN | Strong match (covered) | Page 2 | "Unfolding of an RNN" on p.2 |
| Backpropagation Through Time | Strong match (covered) | Pages 6–7 | Title + chain-rule text on pp.6–7 |
| Applications – Text Classification | Not found | — | Not in document; correctly excluded |
| Long Short-Term Memory (LSTM) | Partial match (partially_covered) | Page 9 | LSTM introduced on p.9, but combined syllabus topic also lists GRU/Text Classification which are absent |
| Gated Recurrent Units (GRU) | Not found | — | Not in document; correctly excluded |

The number of topics is determined by document content, not hardcoded to six.

---

## 7. Testing Results

**Command:** `python -m pytest -q` (run from `backend/`)
**Result:** 651 passed, 6 skipped, 0 failed.

New deterministic test file: `backend/test_notes_coverage_dedup.py` (22 tests) covering extraction locations, BPTT/LSTM merging, RNN/LSTM/GRU separation, Text Classification vs Sentiment Analysis separation, generic-concept filtering, coverage classifications, evidence pointing to real pages, quiz topic isolation, and student data isolation (cross-student `PermissionError`).

The 6 skipped tests are pre-existing environment-dependent skips (external services), unrelated to this work.

No live LLM API is required for these regression tests; provider-dependent paths are mocked or avoided.

---

## 8. Remaining Limitations

- **Reference PDF not available locally:** `CS217_24_lec13.pdf` was not in the environment; evaluation used a structural simulation. Results should be re-validated against the real PDF.
- **Screenshots:** not captured in this environment; to be taken during review.
- **OCR / scanned PDFs:** handled gracefully (extractor returns empty and reports an error) but no OCR engine is integrated; scanned image-only PDFs yield no text.
- **Keyword-based isolation:** topic isolation (`is_question_relevant_to_topic`) is lexical; a GRU "update gate" question shares the word "gate" with LSTM concepts, so very fine-grained sibling discrimination can occasionally fall back to human review.
- **`needs_review` status:** defined but not yet triggered by the classifier (falls back to `not_found`); could be tuned for very low-quality extractions.
- **Other syllabus topics (Section B)** are reachable via the categorized dropdown / general-syllabus mode; an explicit Section B panel on the notes-results card could be added in a follow-up.

---

## 9. Git Status

```
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  modified:   backend/data/demo_questions.json          (pre-existing)
  modified:   backend/main.py
  modified:   backend/services/auth_utils.py            (pre-existing)
  modified:   backend/services/curriculum_service.py
  modified:   backend/services/database.py
  modified:   backend/services/evaluator.py             (pre-existing)
  modified:   backend/services/llm_service.py           (pre-existing)
  modified:   backend/services/notes_extractor.py
  modified:   backend/services/notes_matcher.py
  modified:   backend/services/providers/base.py        (pre-existing)
  modified:   backend/services/rate_limiter.py          (pre-existing)
  modified:   backend/test_combined_notes_and_materials_ui.py
  modified:   frontend/css/style.css
  modified:   frontend/index.html                       (pre-existing)
  modified:   frontend/js/app.js

Untracked files:
  backend/migrations/004_topic_evidence_and_coverage.sql
  backend/migrations/004_topic_evidence_and_coverage.rollback.sql
  backend/test_notes_coverage_dedup.py
  (.impeccable/, backend/migrations/003_*, backend/services/account_service.py,
   backend/services/output_verifier.py, and other pre-existing untracked files)
```

- No existing uncommitted changes were discarded, reset, or overwritten.
- No commits, pushes, or deploys have been performed.
- No production/staging database migrations were run.

---

**End of report.** Ready for your review.
