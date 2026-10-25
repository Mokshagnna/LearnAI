# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary users are undergraduate engineering students in B.Tech Computer Science and Engineering (Artificial Intelligence & Machine Learning) at VFSTR (Vignan's Foundation for Science, Technology & Research) Deemed to be University.

They use the platform in situations ranging from regular course revision to high-pressure exam preparation (unit tests, lab internals, and semester end examinations). Their primary job is diagnosing knowledge gaps, validating concept mastery against the official syllabus, and practicing representative assessment questions under focused conditions.

## Product Purpose

LearnAI exists to provide distraction-free, syllabus-grounded formative assessments that give engineering students instantaneous, rigorous, and actionable insight into their topic-level understanding.

Success means a student can choose any syllabus topic or upload their course lecture materials, immediately sit for a high-quality 10-question adaptive assessment, receive objective concept-level diagnosis, and know precisely what to review next without extraneous friction.

## Positioning

Unlike generic flashcard generators or open-ended conversational AI tutors that drift off-syllabus, hallucinate question difficulty, and introduce distracting gamification, LearnAI is an authoritative, curriculum-anchored academic assessment system.

Its core differentiators are:
1. **Curriculum Grounding:** Every generated question is strictly bounded by the official VFSTR R22 curriculum units or student-provided lecture notes and slides.
2. **Deterministic Evaluation:** Scoring, concept attribution, and mastery thresholds are computed by deterministic Python logic rather than stochastic LLM evaluation.
3. **Adaptive Progression & Novelty Defense:** Mathematical novelty deduplication (Jaccard similarity and concept fingerprinting) prevents question repetition across retakes, while empirical performance history governs difficulty progression.

## Operating Context

Students interact with LearnAI on laptops, desktop monitors, and mobile devices in university computer labs, library reading rooms, and hostel study desks.

Factual artifacts and workflows include:
- Official course units and codes across 55 R22 curriculum courses (e.g., 22AM201 Machine Learning, 22CS104 Data Structures, 22TP103 Soft Skills).
- Lecture slides and notes in diverse formats: legacy binary PowerPoint (`.ppt`), modern presentations (`.pptx`), Word documents (`.docx`), PDFs (`.pdf`), plain text (`.txt`), and scanned slide captures (`.png`, `.jpg`).
- Repeated practice loops: Topic selection / Notes upload → Adaptive Quiz → Deterministic Evaluation & Review → Focused Retake or Dashboard Review.

## Capabilities and Constraints

### Confirmed Capabilities
- **Curriculum Selection:** 55 R22 courses with indexed, selectable quiz topics.
- **Notes-to-Quiz Ingestion:** Multi-format file uploader with multi-signal curriculum matching, student-session isolation, and prompt injection defensive delimiters.
- **Multi-Provider AI Orchestration:** 10-question MCQ generation with failover across Groq, NVIDIA Build, Google Gemini, and local offline demo banks.
- **Deterministic Evaluation:** Accuracy scoring, concept-level strength/weakness diagnostics (threshold: < 60% accuracy requires review), and pedagogical feedback.
- **Novelty Engine:** Jaccard text overlap and concept fingerprint analysis to guarantee fresh questions on retakes.
- **Performance Dashboard:** Course-filtered score trend charts, syllabus topic mastery progress bars, and prioritized Recommended Review cards.

### Confirmed Technical & Design Constraints
- **Architecture:** Single-page web application served via FastAPI StaticFiles.
- **Zero Frontend Frameworks:** Implemented in pure HTML5, vanilla CSS custom properties, and vanilla ES6 JavaScript. No React/Vue/Angular runtime, no CSS preprocessors, and no icon font dependencies (inline SVGs only).
- **Academic Scholar Design System:** Oxford Forest Green (`#1e4635`), warm stone canvas (`#f8f9fa`), and pure white panels (`#ffffff`). Zero neon glows, purple gradients, childish badges, or floating game animations.
- **Optimal Reading Measure:** Maximum 720px reading width for quiz stems and answer options to prevent eye strain during testing.
- **Session & Auth Management:** In-browser student session state preserved across page refreshes via `sessionStorage` and bearer auth tokens.
- **Data Persistence:** SQLite database (`backend/data/learnai.db`) for student attempt history.

## Brand Commitments

- **Name:** LearnAI
- **Visual Aesthetic:** Academic Scholar — clean, calm, structured, and authoritative.
- **Voice & Tone:** Scholarly, objective, encouraging, and direct. The interface respects the student's intellect; it avoids patronizing praise, exclamation-heavy copy, and superficial game mechanics (streaks, badges, confetti).
- **Core Palette:** Oxford Forest Green (`--color-brand: #1e4635`), Warm Stone Canvas (`--color-bg: #f8f9fa`), White Surface (`--color-surface: #ffffff`), Charcoal Text (`--color-text-primary: #111827`), with restrained functional accents for Success, Warning, and Error states.

## Evidence on Hand

- **Curriculum Dataset:** `backend/data/curriculum.json` containing verified syllabus topics and units for all 55 R22 courses.
- **Design System Specification:** `DESIGN.md` (version 2.0.0) documenting the complete token architecture, contrast ratios, and component rules.
- **Test Fixtures & Automation:** 72 passing automated tests in `backend/` verifying evaluation math, legacy PPT parsing, session authorization, and UI state transitions.
- **Document Extractors:** Production extractors in `backend/services/notes_extractor.py` and `notes_matcher.py`.

## Product Principles

1. **Curriculum Truth Over Generative Drift:** The syllabus is the single source of truth. Assessment questions must strictly align with course topics and verified lecture content.
2. **Calm Academic Rigor:** Assessment requires mental focus. Visual noise, distracting gamification, and excessive decoration are strictly eliminated.
3. **Deterministic & Transparent Diagnosis:** Students must understand why an answer is right or wrong, how their score was calculated, and exactly which concepts need reinforcement.
4. **Frictionless Practice Loops:** Moving from topic selection to quiz generation, and from evaluation to retake, must be seamless, instant, and reliable.

## Accessibility & Inclusion

- **Contrast Compliance:** All text meets or exceeds WCAG 2.1 AAA contrast targets (minimum 7:1 for body and reading text; minimum 3:1 for form borders and structural UI).
- **Keyboard Operability:** Every interactive element is reachable via keyboard with an unambiguous 2px brand focus indicator.
- **Motion Restraint:** Full support for `prefers-reduced-motion: reduce`.
- **Assistive Technology:** Semantic HTML headings, `role="progressbar"`, and `aria-live="polite"` regions for asynchronous alerts and toasts.
