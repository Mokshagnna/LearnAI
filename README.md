# LearnAI — AI-Powered Adaptive Quiz Platform

**LearnAI** is a syllabus-grounded, adaptive quiz platform for B.Tech CSE (AI & ML) students at VFSTR Deemed to be University. Students select a course and topic from the R22 curriculum, and the backend generates a fresh 10-question multiple-choice quiz using one of three configurable AI providers. After submission, the system provides deterministic scoring, concept-level performance analysis, and AI-generated revision feedback. Subsequent retakes receive difficulty-adapted question sets that avoid previously seen questions and concepts.

---

## Table of Contents

1. [Key Features](#key-features)
2. [Technology Stack](#technology-stack)
3. [Project Structure](#project-structure)
4. [Prerequisites](#prerequisites)
5. [Installation and Setup](#installation-and-setup)
6. [Environment Configuration](#environment-configuration)
7. [Running the Application](#running-the-application)
8. [API Documentation](#api-documentation)
9. [Curriculum Configuration](#curriculum-configuration)
10. [Quiz Generation Workflow](#quiz-generation-workflow)
11. [Evaluation and Adaptive Learning](#evaluation-and-adaptive-learning)
12. [Novelty and Deduplication Engine](#novelty-and-deduplication-engine)
13. [Database](#database)
14. [Error Handling and Troubleshooting](#error-handling-and-troubleshooting)
15. [Testing and Verification](#testing-and-verification)
16. [Security and Git Practices](#security-and-git-practices)
17. [Deployment](#deployment)
18. [Future Improvements](#future-improvements)
19. [Repository](#repository)

---

## Key Features

| Feature | Status | Notes |
|---------|--------|-------|
| Course and topic selection from R22 syllabus | Implemented | 55 courses, `quiz_topics` index per course |
| AI quiz generation (10 MCQs per quiz) | Implemented | Multi-provider with configurable priority |
| Difficulty distribution (Easy / Medium / Hard) | Implemented | Adaptive based on student history |
| Answer evaluation and scoring | Implemented | Deterministic Python logic |
| Concept-wise performance breakdown | Implemented | Correct/total per concept |
| Difficulty-wise performance breakdown | Implemented | Correct/total per difficulty level |
| Strong and weak concept identification | Implemented | Threshold: < 60% accuracy = weak |
| AI-generated revision feedback | Implemented | Summary, revision guidance, next-step recommendation |
| Quiz retakes with fresh questions | Implemented | New quiz_id on every call |
| Question novelty / deduplication | Implemented | Jaccard similarity on text + concept fingerprints |
| Adaptive difficulty adjustment | Implemented | Rule-based; mastery thresholds: 0.6 / 0.8 |
| Student history persistence | Implemented | SQLite; per-student, per-course, per-topic |
| Session state recovery on page refresh | Implemented | sessionStorage in the browser |
| Local demo question fallback | Implemented | demo_questions.json used when no API keys present |
| Student login / authentication | Not implemented | Student ID is a UUID generated in-browser |
| PDF / notes upload | Not implemented | — |
| Public student dashboard | Not implemented | History API exists; no dedicated history UI screen |

---

## Technology Stack

### Backend

| Component | Technology |
|-----------|-----------|
| Web framework | FastAPI |
| ASGI server | Uvicorn |
| Database | SQLite (Python sqlite3 stdlib) |
| AI provider — primary | Groq (llama-3.3-70b-versatile default) |
| AI provider — secondary | NVIDIA Build (meta/llama-3.3-70b-instruct default) |
| AI provider — tertiary | Google Gemini (gemini-1.5-flash default) |
| Similarity computation | scikit-learn (Jaccard logic in novelty.py) |
| Environment management | python-dotenv |

### Frontend

| Component | Technology |
|-----------|-----------|
| Markup | Plain HTML5 |
| Styling | Vanilla CSS (frontend/css/style.css) |
| Logic | Vanilla JavaScript (frontend/js/app.js) |
| Served by | FastAPI StaticFiles mount at / |

---

## Project Structure

```
LearnAI/
├── README.md
├── render.yaml                     # Render.com deployment config
├── backend/
│   ├── main.py                     # FastAPI app, API routes, CORS, static file mount
│   ├── requirements.txt            # Python dependencies
│   ├── .env.example                # Environment variable template (safe to commit)
│   ├── .env                        # Real secrets — do NOT commit
│   ├── Procfile                    # Process definition for Render/Heroku
│   ├── data/
│   │   ├── curriculum.json         # Full R22 B.Tech CSE-AI&ML syllabus (55 courses)
│   │   ├── demo_questions.json     # Offline fallback question bank
│   │   └── learnai.db              # SQLite database (auto-created; gitignored)
│   ├── services/
│   │   ├── llm_service.py          # Multi-provider orchestration, novelty repair loop
│   │   ├── evaluator.py            # Deterministic answer scoring and concept analysis
│   │   ├── adaptive.py             # Rule-based difficulty recommendation
│   │   ├── novelty.py              # Jaccard-based question and concept deduplication
│   │   ├── database.py             # SQLite schema, CRUD operations
│   │   ├── curriculum_service.py   # Curriculum loading, topic normalization
│   │   ├── topic_extractor.py      # Converts raw syllabus units to selectable topics
│   │   └── providers/
│   │       ├── __init__.py
│   │       ├── base.py             # Abstract provider, shared prompt, JSON extractor, validator
│   │       ├── gemini_provider.py  # Google Gemini integration (google-genai SDK)
│   │       ├── groq_provider.py    # Groq integration (groq SDK)
│   │       └── nvidia_provider.py  # NVIDIA Build via OpenAI-compatible API
│   ├── test_api.py                 # Integration smoke test (requires running server)
│   ├── test_suite_global.py        # Unit tests for novelty detection and evaluation
│   ├── validate_curriculum_expansion.py  # Validates curriculum extraction across 55 courses
│   └── validate_quiz_topics.py     # Validates curriculum.json integrity and topic mapping
└── frontend/
    ├── index.html                  # Single-page HTML (4 screen sections)
    ├── css/
    │   └── style.css
    └── js/
        └── app.js                  # All frontend logic (~790 lines)
```

---

## Prerequisites

- **Python 3.10 or later** (the code uses the `str | None` union type syntax)
- **pip**
- At least one API key from: [Groq](https://console.groq.com/), [NVIDIA Build](https://build.nvidia.com/), or [Google AI Studio](https://aistudio.google.com/)

---

## Installation and Setup

```bash
# 1. Clone the repository
git clone https://github.com/Mokshagnna/LearnAI.git
cd LearnAI

# 2. Create and activate a virtual environment inside the backend directory
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your environment file
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
```

Then open `backend/.env` and add at least one real API key (see [Environment Configuration](#environment-configuration)).

---

## Environment Configuration

Copy `backend/.env.example` to `backend/.env` and fill in the values for whichever providers you want to use. You need **at least one** key configured.

```
# Provider order (first = highest priority)
PROVIDER_ORDER=groq,nvidia,gemini

# Timing
OVERALL_DEADLINE_SECONDS=25.0     # Total wall-clock budget for quiz generation
PROVIDER_TIMEOUT_SECONDS=12.0     # Budget allocated per provider attempt
MAX_PROVIDER_RETRIES=1            # Extra attempts on malformed output
MAX_NOVELTY_REPAIR_ATTEMPTS=2     # Extra AI calls to reduce repeated questions

# Groq (recommended: fastest response times)
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# NVIDIA Build
NVIDIA_API_KEY=your_nvidia_api_key_here
NVIDIA_MODEL=meta/llama-3.3-70b-instruct

# Google Gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-1.5-flash
GEMINI_FALLBACK_MODEL=gemini-1.5-flash-8b

# Misc
ENABLE_LOCAL_DEMO_FALLBACK=true   # Fall back to demo_questions.json if all providers fail
DEBUG=false
```

`PROVIDER_ORDER` controls which provider is tried first. The system automatically falls back to the next configured provider if a request fails, times out, or returns malformed output.

---

## Running the Application

```bash
# From the backend/ directory, with the virtual environment active:
uvicorn main:app --reload
```

The application is available at **http://127.0.0.1:8000**.

The FastAPI backend serves the `frontend/` directory as static files at the root path, so no separate web server is needed for local development.

The SQLite database (`backend/data/learnai.db`) is created automatically on first startup.

---

## API Documentation

All API routes are defined in `backend/main.py`.

### GET /api/courses

Returns the full list of courses and their selectable topics.

**Response:**
```json
{
  "courses": [
    {
      "code": "22AM301",
      "name": "Deep Learning",
      "year": 2,
      "semester": 1,
      "topics": [
        {
          "id": "22AM301-Q001",
          "name": "Introduction to Deep Learning",
          "module": 1,
          "unit": 1,
          "parent_topic": "Introduction",
          "concepts": ["Artificial Neural Networks", "Perceptron", "Activation Functions"]
        }
      ]
    }
  ]
}
```

---

### POST /api/quiz/generate

Generates a fresh 10-question MCQ quiz. Correct answers are **never** included in the response.

**Request body:**
```json
{
  "course_code": "22AM301",
  "topic": "Introduction to Deep Learning",
  "topic_id": "22AM301-Q001",
  "student_id": "uuid-string-or-null",
  "retake": false
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| course_code | string | Yes | e.g. "22AM301" |
| topic | string | Yes | Exact topic name from /api/courses |
| topic_id | string | No | Stable topic ID for accurate matching |
| student_id | string | No | UUID from localStorage; a new one is created if absent |
| retake | boolean | No | Informational flag only; the backend always generates fresh questions |

**Successful response (200):**
```json
{
  "quiz_id": "uuid",
  "attempt_id": "uuid",
  "attempt_number": 2,
  "course_code": "22AM301",
  "subject": "Deep Learning",
  "topic": "Introduction to Deep Learning",
  "topic_id": "22AM301-Q001",
  "parent_topic": "Introduction",
  "module": 1,
  "unit": 1,
  "questions": [
    {
      "id": 0,
      "question": "Question text...",
      "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
      "difficulty": "Easy",
      "concept": "Activation Functions"
    }
  ],
  "is_fallback": false,
  "fallback_label": "",
  "novelty_warning": false,
  "novel_count": 10,
  "recommended_difficulty": "mixed",
  "adaptive_applied": false,
  "adaptive_explanation": {
    "level": "initial",
    "label": "Initial difficulty",
    "description": "Initial difficulty — learning your current level.",
    "detail": "..."
  },
  "provider_used": "groq"
}
```

**HTTP error codes:**

| Code | Meaning |
|------|---------|
| 400 | Invalid course_code or topic not found in curriculum |
| 500 | API key or configuration error |
| 503 | All AI providers temporarily unavailable |

---

### POST /api/quiz/submit

Submits answers and returns scored results with AI feedback. The quiz attempt is saved to the database on this call.

**Request body:**
```json
{
  "quiz_id": "uuid-from-generate",
  "answers": ["A", "C", "B", "D", "A", "B", "C", "D", "A", "B"],
  "student_id": "uuid-string-or-null"
}
```

`answers` is an ordered list of answer letters (A, B, C, or D), one per question.

**Successful response (200):**
```json
{
  "score": 7,
  "total": 10,
  "percentage": 70.0,
  "status": "Good",
  "weak_concepts": ["Backpropagation"],
  "strong_concepts": ["Activation Functions", "Loss Functions"],
  "difficulty_accuracy": {
    "Easy":   {"correct": 4, "total": 4, "accuracy": 100.0},
    "Medium": {"correct": 2, "total": 4, "accuracy": 50.0},
    "Hard":   {"correct": 1, "total": 2, "accuracy": 50.0}
  },
  "concept_accuracy": {
    "Activation Functions": {"correct": 2, "total": 2, "accuracy": 100.0},
    "Backpropagation":      {"correct": 0, "total": 1, "accuracy": 0.0}
  },
  "details": [
    {
      "question": "...",
      "selected_answer": "A",
      "correct_answer": "A",
      "is_correct": true,
      "explanation": "...",
      "concept": "Activation Functions",
      "difficulty": "Easy"
    }
  ],
  "analysis": {
    "summary": "AI-generated summary...",
    "revision": "AI-generated revision guidance...",
    "recommendation": "AI-generated next step..."
  },
  "student_id": "uuid"
}
```

**HTTP error codes:**

| Code | Meaning |
|------|---------|
| 404 | quiz_id not found (quiz expired or server restarted) |

---

### GET /api/student/{student_id}/history

Returns a student's quiz attempt history, newest first.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| limit | 20 | Maximum number of attempts to return |

---

### GET /api/student/{student_id}/performance

Returns aggregated concept and difficulty performance across all attempts.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| course_code | (all courses) | Filter by course code |

**Response:**
```json
{
  "student_id": "uuid",
  "concept_performance": {
    "Activation Functions": {"correct": 8, "total": 10}
  },
  "difficulty_performance": {
    "Easy":   {"correct": 18, "total": 20},
    "Medium": {"correct": 12, "total": 20},
    "Hard":   {"correct": 5,  "total": 10}
  }
}
```

---

## Curriculum Configuration

The curriculum is loaded from `backend/data/curriculum.json`. It follows the R22 B.Tech CSE-AI & ML regulation from VFSTR and contains **55 courses** across all years and semesters.

### Top-level structure

```json
{
  "program": "B.Tech CSE - Artificial Intelligence and Machine Learning",
  "regulation": "R22",
  "institution": "VFSTR Deemed to be University",
  "courses": [ ... ]
}
```

### Course object

```json
{
  "code": "22AM301",
  "name": "Deep Learning",
  "year": 2,
  "semester": 1,
  "prerequisite": "...",
  "topics": [ ... ],
  "quiz_topics": [ ... ]
}
```

- **`topics`** — Unit-level syllabus sections used to build `syllabus_context` for the AI prompt.
- **`quiz_topics`** — Derived, student-selectable topic index used by the frontend dropdown and API.

### Quiz topic object

```json
{
  "id": "22AM301-Q001",
  "name": "Introduction to Deep Learning",
  "module": 1,
  "unit": 1,
  "parent_topic": "Introduction",
  "source_topic_index": 0,
  "source_topic_name": "Introduction",
  "syllabus_context": "Full syllabus text for the parent unit...",
  "concepts": [
    "Artificial Neural Networks",
    "Perceptron",
    "Activation Functions"
  ]
}
```

The `concepts` list is injected into the AI prompt to ensure balanced coverage across the unit's key learning objectives.

If a course has no `quiz_topics` field or it is empty, `curriculum_service.py` falls back to the raw `topics` array.

---

## Quiz Generation Workflow

```
Student selects Course + Topic
        |
        v
POST /api/quiz/generate
        |
        +-- Curriculum validation (course code + topic name/id lookup)
        |
        +-- Student context loaded from DB
        |     +-- topic attempt history
        |     +-- accumulated weak concepts
        |
        +-- Adaptive difficulty calculated (rule-based)
        |     +-- maps to Easy/Medium/Hard distribution (e.g. 4-4-2)
        |
        +-- Novelty context built
        |     +-- question fingerprints from DB (up to 50 recent)
        |     +-- concept history from DB (up to 30 recent)
        |
        +-- Full prompt assembled
        |     +-- base prompt (topic, syllabus context, concepts, difficulty dist.)
        |     +-- novelty exclusion block (previously seen questions/concepts)
        |     +-- adaptive emphasis block (foundational / advanced directives)
        |
        +-- Provider orchestration loop
        |     for each configured provider (in PROVIDER_ORDER):
        |       +-- attempt with per-provider timeout budget
        |       +-- parse JSON from raw text (regex + json.loads)
        |       +-- validate structure (10 questions, 4 options, correct_answer, etc.)
        |       +-- bounded replenishment (up to 2 extra calls if < 10 valid questions)
        |       +-- novelty evaluation (Jaccard similarity vs. history)
        |       +-- novelty repair (up to MAX_NOVELTY_REPAIR_ATTEMPTS extra calls)
        |
        +-- Difficulty labels applied to questions (enforces exact distribution)
        |
        +-- Correct answers stripped from public response
        |
        +-- quiz_id assigned and quiz cached in-memory (QUIZZES dict)
                |
                v
        Response returned to frontend
```

**Fallback chain when all providers fail:**
1. Best partially-novel quiz generated during the loop (if any).
2. Local `demo_questions.json` entries matching `course_code::topic_name` (if `ENABLE_LOCAL_DEMO_FALLBACK=true`).
3. HTTP 503 response: "AI service is temporarily experiencing high demand."

---

## Evaluation and Adaptive Learning

### Scoring (deterministic Python — evaluator.py)

Answers are graded by exact string comparison against the stored correct answer. No AI is involved in the scoring step.

| Percentage | Status label |
|-----------|-------------|
| < 30% | Needs Strong Revision |
| 30–49% | Needs Revision |
| 50–69% | Developing |
| 70–84% | Good |
| >= 85% | Excellent |

**Weak concept threshold:** A concept is flagged as weak if the student answered fewer than 60% of questions testing it correctly. If the student scores 100%, all tested concepts are marked strong.

### AI Feedback (generate_analysis in llm_service.py)

After scoring, the same provider chain generates a three-field feedback object:
- `summary` — Overall performance comment
- `revision` — Targeted guidance on weak concepts
- `recommendation` — Suggested next study action

If all providers fail or time out within a 10-second deadline, a deterministic fallback message is returned. The analysis never blocks quiz results from being displayed.

### Adaptive Difficulty (adaptive.py) — Rule-Based

The system inspects a student's cumulative `concept_performance` and `difficulty_performance` from the database.

| Overall mastery | Recommended difficulty | Distribution |
|----------------|----------------------|-------------|
| No history | mixed | 4 Easy, 4 Medium, 2 Hard |
| < 60% | foundational | 6 Easy, 3 Medium, 1 Hard |
| 60–79% | mixed | 4 Easy, 4 Medium, 2 Hard |
| >= 80% | advanced | 2 Easy, 4 Medium, 4 Hard |

**Topic-specific override:** If weak concepts from the current topic have a per-concept accuracy below 40%, the recommendation falls back to "foundational" regardless of overall mastery.

This is a purely rule-based, deterministic system — not a trained machine-learning model.

---

## Novelty and Deduplication Engine

`novelty.py` prevents the AI from repeating questions or testing the same concepts across multiple retakes by the same student on the same topic.

**Text similarity** is computed as a weighted blend of:
- Content keyword overlap (Jaccard on significant words)
- Bigram overlap (Jaccard on word bigrams)
- Trigram overlap (Jaccard on word trigrams)

A question is considered a near-duplicate if the combined similarity score exceeds **0.45** (`TEXT_DUPLICATE_THRESHOLD`).

**Concept similarity** checks whether two concept tags refer to the same learning objective using Jaccard on content keywords, with threshold **0.50** (`CONCEPT_SIMILARITY_THRESHOLD`).

The novelty exclusion block injected into the prompt lists previously seen question texts and concept tags so the AI avoids repeating them. If the generated quiz still contains duplicates, up to `MAX_NOVELTY_REPAIR_ATTEMPTS` targeted repair calls are made.

---

## Database

The SQLite database is stored at `backend/data/learnai.db` and created automatically by `init_db()` on startup. It is gitignored and should not be committed.

### Tables

**students**

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | UUID, generated client-side or server-side |
| created_at | TEXT | ISO timestamp |
| last_active | TEXT | Updated on each quiz generation |

**quiz_attempts**

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | UUID |
| student_id | TEXT FK | References students.id |
| course_code | TEXT | e.g. 22AM301 |
| course_name | TEXT | e.g. Deep Learning |
| topic_name | TEXT | Selected topic |
| quiz_data | TEXT | Full quiz JSON (serialized) |
| score | INTEGER | Correct answers |
| total | INTEGER | Total questions |
| percentage | REAL | Score percentage |
| status | TEXT | e.g. Good |
| difficulty_accuracy | TEXT | JSON: per-difficulty stats |
| concept_accuracy | TEXT | JSON: per-concept stats |
| weak_concepts | TEXT | JSON array |
| strong_concepts | TEXT | JSON array |
| recommended_difficulty | TEXT | mixed, foundational, or advanced |
| created_at | TEXT | ISO timestamp |

**question_metadata**

Stores each individual question for comprehensive novelty lookups.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | UUID |
| attempt_id | TEXT FK | References quiz_attempts.id |
| student_id | TEXT | |
| course_code | TEXT | |
| topic_name | TEXT | |
| question_index | INTEGER | Position in the quiz (0-based) |
| question_text | TEXT | Raw question string |
| question_fingerprint | TEXT | Normalized text used for similarity checks |
| concept | TEXT | |
| difficulty | TEXT | |
| question_type | TEXT | Always "mcq" |
| correct_answer | TEXT | A, B, C, or D |
| created_at | TEXT | |

---

## Error Handling and Troubleshooting

### Server won't start

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| ModuleNotFoundError | Virtual environment not active or packages not installed | Activate venv and run `pip install -r requirements.txt` |
| FileNotFoundError: curriculum.json | Wrong working directory | Run uvicorn from the `backend/` directory |

### No questions generated

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| 503 response | No provider key configured | Add at least one key to .env |
| 503 with keys present | All providers timed out or rate-limited | Wait and retry; check API dashboard for quota |
| is_fallback: true in response | API keys present but all failed; demo questions used | Normal fallback behavior |

### Quiz ID not found (404 on submit)

Quizzes are held in an in-memory Python dictionary. If the server restarts between generate and submit, the quiz is lost. The frontend detects this and prompts the user to generate a new quiz.

### Curriculum validation failures

Run from the `backend/` directory:

```bash
python validate_quiz_topics.py
python validate_curriculum_expansion.py
```

### CORS errors in browser

The backend allows all origins by default. CORS errors typically mean the frontend HTML file was opened directly in the browser instead of being served by the FastAPI process. Always access the app through `http://127.0.0.1:8000`.

---

## Testing and Verification

### Unit tests — novelty detection and evaluation logic

Self-contained; no running server required:

```bash
# From backend/
python test_suite_global.py
```

Covers: exact duplicate detection, near-duplicate and paraphrase detection, within-quiz duplicate detection, topic relevance checking, and the `evaluate_answers` scoring function.

### Integration smoke test — full API round-trip

Starts a local server on port 8001, runs generate → submit → history → performance, then terminates the server. **Requires at least one AI provider to be configured.**

```bash
# From backend/
python test_api.py
```

### Curriculum validation

```bash
python validate_quiz_topics.py           # Checks all 55 courses
python validate_curriculum_expansion.py  # Checks topic extraction completeness
```

No test results are claimed here. Run the scripts and inspect the output.

---

## Security and Git Practices

- **Never commit `backend/.env`** — it contains real API keys and is listed in `.gitignore`.
- Use `backend/.env.example` as the committed template; it contains only placeholder values.
- **Never commit `backend/data/learnai.db`** — it contains student quiz history.
- Do not commit `venv/` or `__pycache__/` directories.

Recommended `.gitignore` entries:

```gitignore
# Secrets
backend/.env

# Database
backend/data/learnai.db

# Python artifacts
__pycache__/
*.pyc
*.pyo
*.pyd
venv/
.venv/
*.egg-info/
dist/
build/
```

---

## Deployment

A `render.yaml` is included for one-click deployment to [Render.com](https://render.com/) (free tier).

```yaml
services:
  - type: web
    name: learnai
    env: python
    rootDir: backend
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
    plan: free
    envVars:
      - key: GEMINI_API_KEY
        sync: false
```

Set API keys as environment variables in the Render dashboard. Add `GROQ_API_KEY` and `NVIDIA_API_KEY` as additional environment variables if using those providers.

**Note:** The SQLite database is stored on the container filesystem. On Render's free tier, the filesystem is ephemeral — the database and student history are reset on each deploy or restart. For persistent history in production, migration to a managed database (e.g. PostgreSQL) would be required.

---

## Future Improvements

The following features are not yet implemented:

- **Student authentication** — Identity is currently an anonymous UUID stored in `localStorage`.
- **History screen in the UI** — The `/api/student/{id}/history` endpoint exists; a frontend page to display it does not.
- **Persistent production database** — The current SQLite database is local and reset on stateless hosting restarts.
- **PDF / notes upload** — Would allow students to supply their own study material for question generation.
- **Admin / instructor dashboard** — No instructor-facing tooling is built.
- **Configurable question count** — Currently hard-coded to 10 questions.
- **Per-question timer** — The quiz screen does not enforce a time limit.

---

## Repository

**GitHub:** https://github.com/Mokshagnna/LearnAI

**Program:** B.Tech CSE — Artificial Intelligence and Machine Learning  
**Institution:** VFSTR Deemed to be University  
**Curriculum:** R22 Regulation
