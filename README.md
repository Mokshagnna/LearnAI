# LearnAI — Agentic-AI Notes to Quiz Generator

**LearnAI** is an agentic, syllabus-grounded adaptive quiz platform for B.Tech CSE (AI & ML) students at VFSTR Deemed to be University. Students register, log in, upload personal study notes (PDF, DOCX, PPTX, or plain text), and select a course + topic from the full R22 curriculum. The backend generates a fresh 10-question multiple-choice quiz driven by real AI inference across a resilient multi-provider chain (Groq → Cloudflare Workers AI → NVIDIA → Gemini). After submission, the system performs deterministic server-side scoring, concept-level performance analysis, and AI-generated revision feedback. Subsequent retakes receive difficulty-adapted question sets that explicitly avoid previously seen questions and concepts.

> **Live deployment:** [https://learnai-fzgd.onrender.com](https://learnai-fzgd.onrender.com)

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
11. [Session Persistence & Active Quiz Restoration](#session-persistence--active-quiz-restoration)
12. [Authentication & Credential Security](#authentication--credential-security)
13. [Evaluation and Adaptive Learning](#evaluation-and-adaptive-learning)
14. [Novelty and Deduplication Engine](#novelty-and-deduplication-engine)
15. [Notes Upload & Topic Isolation](#notes-upload--topic-isolation)
16. [Database](#database)
17. [Error Handling and Troubleshooting](#error-handling-and-troubleshooting)
18. [Testing and Verification](#testing-and-verification)
19. [Security and Git Practices](#security-and-git-practices)
20. [Deployment](#deployment)
21. [Future Improvements](#future-improvements)
22. [Repository](#repository)

---

## Key Features

| Feature | Status | Notes |
|---------|--------|-------|
| Student registration & login | ✅ Implemented | Argon2id password hashing; SHA-256 session tokens |
| Course and topic selection from R22 syllabus | ✅ Implemented | 55 courses; `quiz_topics` index per course |
| Notes upload (PDF, DOCX, PPTX, TXT) | ✅ Implemented | Up to 5 MB; magic-byte file validation |
| AI quiz generation (10 MCQs per quiz) | ✅ Implemented | 4-provider chain with configurable priority |
| Dual-Guard Topic Isolation | ✅ Implemented | TF-IDF competing topic suppression + downstream heuristic validation |
| Difficulty distribution (Easy / Medium / Hard) | ✅ Implemented | Adaptive based on student history |
| Server-side answer grading | ✅ Implemented | Correct answers stripped from client payload; never exposed |
| Concept-wise performance breakdown | ✅ Implemented | Correct/total per concept |
| Difficulty-wise performance breakdown | ✅ Implemented | Correct/total per difficulty level |
| Strong and weak concept identification | ✅ Implemented | Threshold: < 60% accuracy = weak |
| AI-generated revision feedback | ✅ Implemented | Summary, revision guidance, next-step recommendation |
| Quiz retakes with fresh novel questions | ✅ Implemented | New quiz_id on every call; active deduplication |
| Question novelty / deduplication | ✅ Implemented | Weighted Jaccard on text + concept fingerprints |
| Adaptive difficulty adjustment | ✅ Implemented | Rule-based; mastery thresholds: 0.6 / 0.8 |
| Student history persistence | ✅ Implemented | TiDB (MySQL-compatible cloud DB) + SQLite local fallback |
| Persistent authentication & zero login flash | ✅ Implemented | Multi-layered storage & cookie verification restores authenticated UI immediately |
| Active quiz preservation across navigation | ✅ Implemented | Questions, progress, and selected answers retained across tabs & page refreshes |
| Accessible password eye visibility toggle | ✅ Implemented | Oxford Forest design tokens, keyboard accessible (`Enter`/`Space`), aria-supported |
| Complete credential clearing on logout | ✅ Implemented | Comprehensive DOM property, attribute, and form reset prevents credential lingering |
| Session state recovery on page refresh | ✅ Implemented | In-memory fast path and `sessionStorage` in the browser |
| KaTeX math rendering | ✅ Implemented | Auto-detected and rendered offline in-browser |
| Syntax-highlighted code blocks | ✅ Implemented | 120-line zero-dependency regex lexer (Python, Java, C++, SQL, Bash) |
| Provider quota manager with cooldown | ✅ Implemented | Thread-safe file-backed per-provider rate limiting |
| IP-based rate limiting | ✅ Implemented | Sliding-window; configurable per-minute limits |
| Health check endpoints | ✅ Implemented | `/health` and `/api/health` with DB connectivity probe |
| Local demo question fallback | ✅ Implemented | `demo_questions.json` used when no API keys present |
| Public student dashboard | ✅ Implemented | Quiz history, concept graphs, performance trends |
| Handwritten notebook OCR | 🔮 Future Scope | Experimental Gemini Vision path; requires live Gemini credentials |
| Formal WCAG automated contrast audit | 🔮 Future Scope | Designed to WCAG 2.1 AA token ratios; Axe-core integration pending |

---

## Technology Stack

### Backend

| Component | Technology |
|-----------|-----------|
| Web framework | FastAPI |
| ASGI server | Uvicorn (`--workers 1`, single-process async) |
| Database — production | TiDB (MySQL-compatible cloud DB via PyMySQL + DBUtils pool) |
| Database — local dev | SQLite (auto-created if `DATABASE_URL` is unset) |
| AI provider — primary | Groq LPU (`llama-3.1-8b-instant` / `llama-3.3-70b-versatile`) |
| AI provider — secondary | Cloudflare Workers AI (`@cf/meta/llama-3.1-8b-instruct`) |
| AI provider — tertiary | NVIDIA Build (`meta/llama-3.1-8b-instruct`) |
| AI provider — quaternary | Google Gemini (`gemini-2.0-flash`) |
| Password hashing | argon2-cffi (Argon2id; `time_cost=3, memory_cost=65536`) |
| Session security | SHA-256 HMAC token hashing |
| File ingestion | pypdf, python-docx, python-pptx |
| Similarity computation | scikit-learn (Jaccard in `novelty.py`) |
| Math rendering — backend | KaTeX detection regex in `output_verifier.py` |
| HTTP client | httpx (async, for Cloudflare REST API) |
| Environment management | python-dotenv |
| Test framework | Pytest 9.x |

### Frontend

| Component | Technology |
|-----------|-----------|
| Markup | Plain HTML5 (single-page, 4 screen sections) |
| Styling | Vanilla CSS (`frontend/css/style.css`; WCAG 2.1 AA token design) |
| Logic | Vanilla JavaScript (`frontend/js/app.js`) |
| Math rendering | KaTeX (bundled offline in `frontend/vendor/katex/`) |
| Code highlighting | Zero-dependency inline regex tokenizer |
| Served by | FastAPI `StaticFiles` mount at `/` |

---

## Project Structure

```
LearnAI/
├── README.md
├── render.yaml                           # Render.com deployment config (--workers 1)
├── design.md                            # UI/UX design system specification
├── backend/
│   ├── main.py                          # FastAPI app: routes, auth middleware, static mount
│   ├── requirements.txt                 # Python dependencies
│   ├── Procfile                         # Process file: uvicorn --workers 1
│   ├── conftest.py                      # Pytest shared fixtures
│   ├── .env.example                     # Environment variable template (safe to commit)
│   ├── data/
│   │   ├── curriculum.json              # Full R22 B.Tech CSE-AI&ML syllabus (55 courses)
│   │   ├── demo_questions.json          # Offline fallback question bank
│   │   └── learnai.db                  # SQLite DB (auto-created locally; gitignored)
│   ├── services/
│   │   ├── llm_service.py               # Multi-provider orchestration + novelty repair loop
│   │   ├── evaluator.py                 # Deterministic answer scoring & concept analysis
│   │   ├── adaptive.py                  # Rule-based difficulty recommendation
│   │   ├── novelty.py                   # Weighted Jaccard deduplication engine
│   │   ├── database.py                  # TiDB/SQLite schema, CRUD, connection pool
│   │   ├── curriculum_service.py        # Curriculum loading and topic normalization
│   │   ├── topic_extractor.py           # Converts raw syllabus units to selectable topics
│   │   ├── notes_extractor.py           # PDF/DOCX/PPTX/TXT parsing (magic-byte validation)
│   │   ├── notes_matcher.py             # TF-IDF topic isolation, relevant context selector
│   │   ├── academic_verifier.py         # Post-generation heuristic correctness validation
│   │   ├── account_service.py           # Argon2id auth, session token lifecycle
│   │   ├── auth_utils.py                # SHA-256 session token hashing utilities
│   │   ├── quota_manager.py             # Thread-safe per-provider cooldown & quota tracking
│   │   ├── rate_limiter.py              # Sliding-window IP rate limiter
│   │   ├── output_verifier.py           # LLM output structure & KaTeX detection
│   │   ├── short_answer_evaluator.py    # Short-answer scoring (supplementary)
│   │   ├── metrics.py                   # Provider latency & usage metrics
│   │   ├── env_loader.py                # Environment variable loader with validation
│   │   ├── logger_utils.py              # Structured logging helpers
│   │   ├── backup.py                    # DB backup utilities
│   │   └── providers/
│   │       ├── __init__.py
│   │       ├── base.py                  # Abstract provider, shared prompt, JSON extractor
│   │       ├── groq_provider.py         # Groq LPU integration
│   │       ├── cloudflare_provider.py   # Cloudflare Workers AI (REST API via httpx)
│   │       ├── nvidia_provider.py       # NVIDIA Build (OpenAI-compatible API)
│   │       └── gemini_provider.py       # Google Gemini (google-genai SDK)
│   └── [60+ test files]                 # Comprehensive pytest suite (1,079 tests)
└── frontend/
    ├── index.html                       # Single-page app (auth, dashboard, quiz, results)
    ├── css/
    │   └── style.css                    # WCAG 2.1 AA design tokens, responsive layout
    ├── js/
    │   └── app.js                       # Full SPA logic, auth flows, quiz lifecycle
    └── vendor/
        └── katex/                       # Offline KaTeX math renderer (fonts + JS + CSS)
```

---

## Prerequisites

- **Python 3.10 or later** (uses `str | None` union syntax and `asyncio` primitives)
- **pip**
- At least one API key from: [Groq](https://console.groq.com/), [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/), [NVIDIA Build](https://build.nvidia.com/), or [Google AI Studio](https://aistudio.google.com/)
- **Optional (production):** A TiDB or MySQL-compatible database URL for persistent student history

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

Then open `backend/.env` and add your API keys (see [Environment Configuration](#environment-configuration)).

---

## Environment Configuration

Copy `backend/.env.example` to `backend/.env` and fill in the values for the providers you want to use.

```env
# Provider execution order
PROVIDER_ORDER=groq,cloudflare,nvidia,gemini

# Timing budgets
OVERALL_DEADLINE_SECONDS=40.0     # Total wall-clock budget for quiz generation
PROVIDER_TIMEOUT_SECONDS=25.0     # Budget allocated per provider attempt
MAX_PROVIDER_RETRIES=1            # Extra retry attempts on transient failures
MAX_NOVELTY_REPAIR_ATTEMPTS=2     # Extra repair calls to reduce repeated questions

# Groq (recommended: fastest inference via LPU)
GROQ_API_KEY=your_groq_api_key_here
GROQ_SECONDARY_API_KEY=your_secondary_groq_key_here   # Optional backup key
GROQ_MODEL=llama-3.1-8b-instant
GROQ_FALLBACK_MODEL=llama-3.3-70b-versatile

# Cloudflare Workers AI (independent fallback)
CLOUDFLARE_ENABLED=true
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id_here
CLOUDFLARE_API_TOKEN=your_cloudflare_api_token_here
CLOUDFLARE_MODEL=@cf/meta/llama-3.1-8b-instruct
CLOUDFLARE_TIMEOUT_SECONDS=20.0

# NVIDIA Build
NVIDIA_API_KEY=your_nvidia_api_key_here
NVIDIA_MODEL=meta/llama-3.1-8b-instruct
NVIDIA_FALLBACK_MODEL=meta/llama-3.1-70b-instruct

# Google Gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash

# Database (SQLite used locally if unset)
# DATABASE_URL=mysql+pymysql://<user>:<password>@<host>:<port>/<db>?ssl_ca=<path>

# Session security (generate with: openssl rand -hex 32)
SESSION_SECRET_KEY=replace_with_a_secure_random_secret

# Runtime
ENVIRONMENT=development
DEBUG=false
ALLOWED_ORIGINS=*

# Rate limiting (requests per minute per IP)
RATE_LIMIT_QUIZ_GEN_PER_MIN=10
RATE_LIMIT_QUIZ_SUBMIT_PER_MIN=30
```

`PROVIDER_ORDER` controls the fallback chain. The orchestrator automatically skips any provider whose credentials are missing or which is in an active cooldown window.

---

## Running the Application

```bash
# From the backend/ directory, with the virtual environment active:
uvicorn main:app --reload
```

The application is available at **http://127.0.0.1:8000**.

The FastAPI backend serves the `frontend/` directory as static files at the root path, so no separate web server is needed for local development. The database (`backend/data/learnai.db`) is created automatically on first startup if `DATABASE_URL` is unset.

> **Production note:** Always start with `--workers 1`. The quota manager uses an in-process `threading.Lock` backed by a local file; running multiple OS workers causes stale cooldown state across processes.

---

## API Documentation

All API routes are defined in `backend/main.py`. Interactive docs are available at `http://127.0.0.1:8000/docs` (Swagger UI).

### GET /health

Basic liveness probe.

```json
{ "status": "ok" }
```

### GET /api/health

Deep health check including database connectivity and provider status.

```json
{
  "status": "healthy",
  "database": "connected",
  "providers": { "groq": "active", "cloudflare": "active" }
}
```

---

### POST /api/auth/register

Registers a new student account.

**Request body:**
```json
{
  "username": "student123",
  "password": "securepassword"
}
```

**Response (201):**
```json
{
  "student_id": "uuid",
  "username": "student123",
  "session_token": "hex-token"
}
```

---

### POST /api/auth/login

Authenticates a returning student.

**Request body:**
```json
{
  "username": "student123",
  "password": "securepassword"
}
```

**Response (200):**
```json
{
  "student_id": "uuid",
  "username": "student123",
  "session_token": "hex-token"
}
```

---

### GET /api/courses

Returns the full list of courses and their selectable topics. No authentication required.

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

### POST /api/notes/upload

Uploads student notes for a specific topic. Accepts PDF, DOCX, PPTX, or plain text (max 5 MB).

**Request:** `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| file | File | The notes file (PDF/DOCX/PPTX/TXT) |
| topic_id | string | The quiz topic ID from `/api/courses` |
| student_id | string | Authenticated student UUID |

**Response (200):**
```json
{
  "status": "ok",
  "characters_extracted": 4821,
  "topic_id": "22AM301-Q001"
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
  "student_id": "uuid-string",
  "retake": false
}
```

**Response (200):**
```json
{
  "quiz_id": "uuid",
  "attempt_number": 2,
  "course_code": "22AM301",
  "subject": "Deep Learning",
  "topic": "Introduction to Deep Learning",
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
  "novel_count": 10,
  "recommended_difficulty": "mixed",
  "provider_used": "groq"
}
```

**HTTP error codes:**

| Code | Meaning |
|------|---------|
| 400 | Invalid `course_code` or topic not found in curriculum |
| 401 | Missing or invalid session token |
| 429 | Rate limit exceeded |
| 503 | All AI providers temporarily unavailable |

---

### POST /api/quiz/submit

Submits answers and returns scored results with AI feedback. Grading is performed entirely server-side.

**Request body:**
```json
{
  "quiz_id": "uuid-from-generate",
  "answers": ["A", "C", "B", "D", "A", "B", "C", "D", "A", "B"],
  "student_id": "uuid-string"
}
```

**Response (200):**
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
    "summary": "AI-generated performance comment...",
    "revision": "AI-generated revision guidance...",
    "recommendation": "AI-generated next-step advice..."
  }
}
```

---

### GET /api/student/{student_id}/history

Returns a student's quiz attempt history (newest first).

| Parameter | Default | Description |
|-----------|---------|-------------|
| limit | 20 | Maximum number of attempts to return |

---

### GET /api/student/{student_id}/performance

Returns aggregated concept and difficulty performance across all attempts.

| Parameter | Default | Description |
|-----------|---------|-------------|
| course_code | (all) | Filter by course code |

---

## Curriculum Configuration

The curriculum is loaded from `backend/data/curriculum.json`. It follows the R22 B.Tech CSE-AI & ML regulation from VFSTR and contains **55 courses** across all years and semesters.

```json
{
  "program": "B.Tech CSE - Artificial Intelligence and Machine Learning",
  "regulation": "R22",
  "institution": "VFSTR Deemed to be University",
  "courses": [...]
}
```

Each course contains a `quiz_topics` array of student-selectable topics with pre-computed `syllabus_context` and `concepts` lists. If `quiz_topics` is absent, the service falls back to the raw `topics` array.

---

## Quiz Generation Workflow

```
Student selects Course + Topic (+ optional uploaded notes)
        |
        v
POST /api/quiz/generate
        |
        +-- Authentication guard (session_token → student_id)
        |
        +-- Curriculum validation (course_code + topic_id lookup)
        |
        +-- Notes context retrieval (notes_matcher.py)
        |     +-- TF-IDF competing topic suppression
        |     +-- Top-k relevant chunks selected
        |
        +-- Student context loaded from DB
        |     +-- topic attempt history
        |     +-- accumulated weak concepts
        |
        +-- Adaptive difficulty calculated (adaptive.py)
        |     +-- maps to Easy/Medium/Hard distribution (e.g. 4-4-2)
        |
        +-- Novelty context built (novelty.py)
        |     +-- question fingerprints from DB (up to 50 recent)
        |     +-- concept history from DB (up to 30 recent)
        |
        +-- Full prompt assembled
        |     +-- base prompt (topic, syllabus context, concepts, difficulty dist.)
        |     +-- notes context block (from student's uploaded notes)
        |     +-- novelty exclusion block (previously seen Qs/concepts)
        |     +-- negative topic constraints (competing topic suppression)
        |
        +-- Provider orchestration loop (quota_manager.py checks cooldowns)
        |     for each provider in PROVIDER_ORDER (cooldown-skipped if active):
        |       +-- attempt with per-provider timeout budget
        |       +-- parse JSON from raw LLM output (regex + json.loads)
        |       +-- structural validation (10 Qs, 4 options, correct_answer)
        |       +-- academic correctness verification (academic_verifier.py)
        |       +-- bounded replenishment (up to 2 extra calls if < 10 valid)
        |       +-- novelty evaluation (Jaccard similarity vs. history)
        |       +-- novelty repair (up to MAX_NOVELTY_REPAIR_ATTEMPTS calls)
        |
        +-- Difficulty labels enforced (exact distribution applied)
        |
        +-- Correct answers stripped from public response
        |
        +-- quiz_id assigned; quiz cached in-memory (QUIZZES dict)
                |
                v
        Response returned to frontend
```

**Fallback chain when all providers fail:**
1. Best partially-novel quiz generated during the loop (if any).
2. Local `demo_questions.json` entries matching `course_code::topic_name`.
3. HTTP 503: `"AI service is temporarily experiencing high demand."`

---

## Session Persistence & Active Quiz Restoration

LearnAI guarantees state preservation for active study sessions across user interactions and browser interruptions:

### Active Quiz Persistence Across Navigation & Refresh
- **Cross-Tab Continuity:** Students can freely navigate between **Practice Quiz**, **Notes & Slides**, and the **Student Dashboard** without losing an in-progress quiz.
- **State Integrity:** The active quiz object, question list, current question index, selected option answers, and elapsed progress are persisted in client session storage and mirrored on the backend.
- **Non-Destructive Navigation:** Course and topic selectors are synchronized using non-destructive population logic (`populateTopicDropdown`). Internal navigation does not trigger synthetic reset events or wipe active question state.
- **Page Refresh Recovery:** Upon page reload (`location.reload()`), `restoreSessionState()` re-hydrates the active quiz session without flashing the setup screen. Promise deduplication prevents race conditions during rapid state transitions.
- **Retake vs. Active Quiz Isolation:** Completed quizzes are never restored as in-progress. Selecting a new topic or initiating a retake explicitly generates a new quiz session with a distinct `quiz_id`.

---

## Authentication & Credential Security

The authentication architecture incorporates strict credential lifecycle management and accessible UI controls:

### Persistent Authentication Lifecycle
- **Session Verification:** Authenticated students retain their session across browser restarts and page refreshes via SHA-256 session tokens and HttpOnly session cookies.
- **Zero Login Flash:** The application checks the session marker synchronously during early DOM initialization to render the authenticated interface immediately, avoiding jarring authentication screen flashes.
- **Protected Student Identity:** The session loader enforces registered student identity boundaries (`stu_...`), preventing anonymous temporary UUID generation from overwriting logged-in student accounts.

### Complete Credential Clearing on Logout
- **Server Session Invalidation:** Calling `/api/auth/logout` revokes the session on the server and expires the authentication cookie.
- **Multi-Layer DOM Sanitization:** To ensure user privacy on shared laboratory terminals, `handleLogout()` executes comprehensive credential cleanup:
  - Resets all authentication forms (`loginForm`, `registerForm`, `forgotPasswordForm`, `resetPasswordForm`).
  - Clears DOM input properties (`.value = ""`).
  - Clears DOM default values (`.defaultValue = ""`).
  - Resets HTML attributes (`.setAttribute("value", "")`).
  - Purges authentication keys and tokens from local and session storage.
  - Resets password toggle buttons to masked state (`type="password"`).

### Accessible Password Eye Visibility Controls
- **Oxford Forest Token Alignment:** Password input fields feature a dedicated toggle button neatly embedded inside the right edge (`right: 6px`, 32×32px touch target) with a mandatory `padding-right: 44px` clearance on the input element, completely eliminating text overlap across all viewports.
- **Accessible & Form-Safe:** Toggle controls are explicitly configured with `type="button"` to prevent accidental form submissions.
- **Full Keyboard Support:** Interactive toggle handles `click`, `Enter`, and `Space` key events, dynamically updating `aria-label`, `title`, and `aria-pressed`.
- **Dynamic Event Delegation:** Supported by document-level event delegation, ensuring dynamically rendered or restored forms maintain responsive password visibility controls.
- **Mobile Responsive:** Verified across compact viewports (375×667) for precise alignment and responsive usability.

---

## Evaluation and Adaptive Learning

### Scoring (deterministic — `evaluator.py`)

Answers are graded by exact string comparison against server-stored correct answers. No AI is involved.

| Percentage | Status |
|-----------|--------|
| < 30% | Needs Strong Revision |
| 30–49% | Needs Revision |
| 50–69% | Developing |
| 70–84% | Good |
| ≥ 85% | Excellent |

**Weak concept threshold:** < 60% accuracy on any concept → flagged weak.

### Adaptive Difficulty (`adaptive.py`)

| Overall mastery | Recommended | Distribution |
|----------------|-------------|-------------|
| No history | mixed | 4 Easy, 4 Medium, 2 Hard |
| < 60% | foundational | 6 Easy, 3 Medium, 1 Hard |
| 60–79% | mixed | 4 Easy, 4 Medium, 2 Hard |
| ≥ 80% | advanced | 2 Easy, 4 Medium, 4 Hard |

This is a purely rule-based system — not a trained ML model.

### AI Feedback (`llm_service.py::generate_analysis`)

After scoring, the same provider chain generates a 3-field feedback object: `summary`, `revision`, `recommendation`. A deterministic fallback message is returned if all providers fail within a 10-second deadline.

---

## Novelty and Deduplication Engine

`novelty.py` prevents the AI from repeating questions or concepts across retakes for the same student and topic.

**Text similarity** — weighted blend of:
- Content keyword overlap (Jaccard on significant words)
- Bigram overlap (Jaccard on word bigrams)
- Trigram overlap (Jaccard on word trigrams)

Duplicate threshold: **0.45** (`TEXT_DUPLICATE_THRESHOLD`).

**Concept similarity** — Jaccard on content keywords. Threshold: **0.50** (`CONCEPT_SIMILARITY_THRESHOLD`).

The novelty exclusion block is injected into every prompt, listing previously seen question texts and concept tags. If the generated quiz still contains duplicates, up to `MAX_NOVELTY_REPAIR_ATTEMPTS` targeted repair calls are made before accepting the best available set.

---

## Notes Upload & Topic Isolation

`notes_extractor.py` accepts PDF, DOCX, PPTX, and plain-text files (max 5 MB). Files are validated by magic bytes (`%PDF-`, `PK\x03\x04`, `RIFF`) before parsing; malformed or oversized files are rejected.

`notes_matcher.py` implements the **Dual-Guard Topic Isolation** architecture:

1. **Upstream guard (pre-prompt):** TF-IDF vectorization identifies context windows dominated by competing topics. Sections with below-threshold topical similarity are suppressed before being injected into the prompt.
2. **Downstream guard (post-generation):** `academic_verifier.py` applies deterministic heuristics to validate that generated questions genuinely relate to the requested topic and concepts.

This prevents topic bleed — where notes containing multiple subjects cause the AI to generate questions about the wrong topic.

---

## Database

The application supports two database backends:

| Environment | Backend | Config |
|-------------|---------|--------|
| Local development | SQLite | Auto-created at `backend/data/learnai.db` |
| Production | TiDB / MySQL | `DATABASE_URL` env var (PyMySQL + DBUtils pool) |

### Core Tables

**students** — `id` (UUID PK), `username`, `password_hash`, `created_at`, `last_active`

**quiz_attempts** — Full attempt record including score, concept/difficulty accuracy, weak/strong concepts, and the full serialized quiz JSON (with correct answers, stored server-side only).

**question_metadata** — Per-question records for comprehensive novelty lookups, including `question_fingerprint`, `concept`, and `difficulty`.

**student_notes** — Uploaded notes metadata and extracted text, keyed by `student_id` + `topic_id`.

---

## Error Handling and Troubleshooting

### Server won't start

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `ModuleNotFoundError` | venv not active or packages not installed | Activate venv, run `pip install -r requirements.txt` |
| `FileNotFoundError: curriculum.json` | Wrong working directory | Run uvicorn from the `backend/` directory |
| `DATABASE_URL invalid in production` | `ENVIRONMENT=production` requires a valid DB URL | Set `DATABASE_URL` in `.env` or switch to `ENVIRONMENT=development` |

### No questions generated

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| 503 response | No provider key configured | Add at least one API key to `.env` |
| 503 with keys present | All providers timed out or rate-limited | Wait and retry; check API dashboards for quota |
| `is_fallback: true` | All providers failed; demo questions served | Normal fallback; check provider keys and connectivity |

### Quiz ID not found (404 on submit)

Quizzes are held in an in-memory Python dictionary. If the server restarts between generate and submit, the quiz is lost. The frontend detects this and prompts the user to generate a new quiz.

### Curriculum validation failures

```bash
# From backend/
python validate_quiz_topics.py
python validate_curriculum_expansion.py
```

### CORS errors in browser

Always access the app through `http://127.0.0.1:8000`. Opening `index.html` directly bypasses the FastAPI server.

---

## Testing and Verification

The backend is covered by a comprehensive automated test suite of **1,079 tests** (Pytest 9.x), achieving **1,073 passed** and **6 conditional skips** with **zero failures**.

```bash
# Run the full test suite from backend/
pytest backend/ -q
# Expected: 1073 passed, 6 skipped in ~82s
```

The 6 skipped tests are in `test_smoke_test_tidb_tls.py` and are skipped when live TiDB TLS credentials are not present in the local environment. They are not failures.

### Persistent Authentication & Quiz Restoration Suite

```bash
# Run the persistent authentication, password toggle, and quiz restoration suite
pytest backend/test_persistent_auth_and_quiz_restoration.py -v

# Run core student authentication and retake reliability tests
pytest backend/test_student_auth_system.py backend/test_phase2_stage_i_auth.py backend/test_quiz_retake_reliability_and_auth_flash.py -v
```

### Real Browser 10-Checkpoint Verification
Automated browser verification via Chrome DevTools Protocol (CDP) tests 10 critical end-to-end scenarios against a live browser engine:
1. **Masked Password:** Form displays masked password input with aligned Oxford Forest eye control.
2. **Password Unmask:** Toggle click unmasks password (`type="text"`, eye-off SVG, `aria-label="Hide password"`).
3. **Password Remask:** Subsequent click returns password to masked state (`type="password"`, eye SVG, `aria-label="Show password"`).
4. **Active Quiz Progress:** Quiz advances through Question 1 and records Option B on Question 2 with active progress styling.
5. **Notes Navigation:** Navigating to Notes & Slides and returning to Practice Quiz retains Question 2, Option B, and all questions.
6. **Dashboard Navigation:** Navigating to Dashboard and returning to Practice Quiz retains Question 2, Option B, and all questions.
7. **Page Refresh Recovery:** Full browser reload restores in-progress quiz at Question 2 with selected option intact.
8. **Logout Sanitization:** Logout revokes session, clears email/password DOM values, defaults, attributes, and resets eye toggles.
9. **Form Switching Sanitization:** Navigating between Login and Register screens preserves clean, unpopulated form inputs.
10. **Mobile Viewport (375×667):** Eye toggle control maintains correct right-edge spacing and touch targets without text collision.

### Key test modules

| File / Group | Coverage Area | Count |
|---|---|---|
| `test_phase2_stage_b_security.py` | Auth, session tokens, student isolation | — |
| `test_code_aware_quiz_rendering.py` | Code block syntax, KaTeX detection, XSS escaping | 144 tests |
| `test_phase2_stage_e_concurrency.py` | Concurrent request handling (5-thread) | — |
| `test_cloudflare_provider.py` | Cloudflare Workers AI integration | — |
| `test_strict_topic_quiz_generation.py` | Dual-Guard topic isolation accuracy | — |
| `test_notes_upload_and_extraction.py` | PDF/DOCX/PPTX file parsing | — |
| `test_smoke_test_tidb_tls.py` | TiDB TLS connectivity (skipped locally) | 6 tests |

### End-to-end browser journey (Module 5)

A 29-checkpoint automated browser journey using headless Google Chrome via Chrome DevTools Protocol (CDP) verified the full student lifecycle (registration, topic selection, quiz generation, answer submission, scoring, and retakes) with zero uncaught JavaScript exceptions. The generation step was evaluated using a calibrated deterministic response pool.

---

## Security and Git Practices

- **Never commit `backend/.env`** — it contains real API keys and is in `.gitignore`.
- **Never commit `backend/data/learnai.db`** — it contains student quiz history.
- Use `backend/.env.example` as the committed template (placeholder values only).
- Do not commit `venv/`, `__pycache__/`, or any `*.pyc` files.
- In production, `SESSION_SECRET_KEY` must be a cryptographically random string (`openssl rand -hex 32`).
- API keys are never logged or included in HTTP responses.

Recommended `.gitignore` entries:

```gitignore
# Secrets
backend/.env
backend/.env.*
!backend/.env.example

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

A `render.yaml` is included for deployment to [Render.com](https://render.com/).

```yaml
services:
  - type: web
    name: learnai
    env: python
    rootDir: backend
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
    plan: free
    healthCheckPath: /health
```

> **Critical:** Always deploy with `--workers 1`. The in-process quota manager uses `threading.Lock()` which cannot coordinate across OS processes. Multi-worker deployment requires migrating quota state to Redis or a database table.

**Required environment variables in Render dashboard:**

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | TiDB/MySQL connection URI |
| `SESSION_SECRET_KEY` | Cryptographically random string |
| `GROQ_API_KEY` | Groq LPU API key (primary provider) |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI API token |
| `NVIDIA_API_KEY` | NVIDIA Build API key (optional fallback) |
| `GEMINI_API_KEY` | Google Gemini API key (optional fallback) |

**Note on persistence:** On Render's free tier, the filesystem is ephemeral. Configure `DATABASE_URL` pointing to a persistent TiDB or MySQL instance to retain student history across deploys and restarts.

---

## Future Improvements

- **Redis-backed quota manager** — Enables safe horizontal scaling with `--workers > 1`.
- **Handwritten notebook OCR** — Offline image-to-text pipeline for scanned handwritten notes.
- **Formal WCAG 2.1 AA automated audit** — Axe-core or Lighthouse integration in the CI test suite.
- **Configurable question count** — Currently hard-coded to 10 questions per quiz.
- **Per-question timer** — The quiz screen does not enforce a time limit.
- **Admin / instructor dashboard** — No instructor-facing tooling is built.
- **Short-answer question type** — Infrastructure exists in `short_answer_evaluator.py`; frontend integration pending.

---

## Repository

**GitHub:** https://github.com/Mokshagnna/LearnAI  
**Live App:** https://learnai-fzgd.onrender.com  
**Branch:** `main` | **HEAD:** `fbda6ca`

**Program:** B.Tech CSE — Artificial Intelligence and Machine Learning  
**Institution:** VFSTR Deemed to be University  
**Department:** ACSE (School of Computing and Informatics)  
**Curriculum:** R22 Regulation  

**Team:**
- T. Naga Venkata Mallikharjuna (241FA18272)
- B. Venkata Sai Ganapathi Naidu (241FA18476)
- Sk. Irshad Vali (241FA18473)
- G. Mokshagnna (241FA18302)

**Supervisor:** Ms. I. Leela Priya, M.Tech., Assistant Professor, Dept. of ACSE
