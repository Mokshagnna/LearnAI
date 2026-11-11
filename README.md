# Agentic-AI Notes to Quiz Generator

Syllabus-grounded adaptive quiz platform powered by multi-provider AI inference.

**Live:** [https://learnai-fzgd.onrender.com](https://learnai-fzgd.onrender.com)

[![CI](https://github.com/Mokshagnna/LearnAI/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Mokshagnna/LearnAI/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-latest-009688)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Overview

LearnAI generates 10-question multiple-choice quizzes from a structured university curriculum (R22 B.Tech CSE-AI&ML, 55 courses). Students upload their own notes (PDF, DOCX, PPTX, TXT) or select a syllabus topic directly. The backend calls an LLM through a prioritized provider fallback chain, scores answers deterministically on the server, and adjusts difficulty on subsequent attempts using rule-based adaptivity. Quiz sessions are persisted in the database so an in-progress quiz survives page refreshes and tab switches.

---

## Screenshots

Screenshots coming soon. The application includes a practice quiz screen, notes upload panel, results and analysis view, and a student progress dashboard.

---

## Features

- **Accounts** — Registration and login with Argon2id password hashing, HMAC session tokens, and HttpOnly cookies.
- **Practice Quiz** — AI-generated 10-question MCQs with a fixed difficulty distribution (4 Easy / 4 Medium / 2 Hard by default).
- **Notes & Slides Upload** — Accepts PDF, DOCX, PPTX, and plain text (up to 5 MB). Files are validated by magic bytes before parsing. Extracted text is matched to curriculum topics via TF-IDF and used to ground quiz questions.
- **Student Dashboard** — Per-topic attempt history, concept-level accuracy breakdowns, difficulty performance, and mastery tracking.
- **Adaptive Difficulty** — Rule-based promotion/demotion across three levels (Foundational, Mixed, Advanced) based on recent score history.
- **Question Novelty** — Retakes deduplicate against previously seen questions using weighted Jaccard similarity on text trigrams and concept fingerprints, with targeted LLM repair calls for remaining duplicates.
- **Multi-Provider AI Fallback** — Configurable provider chain (Groq, Cloudflare Workers AI, NVIDIA, Gemini) with per-provider timeouts, quota tracking, and an overall 40-second generation deadline.
- **Deterministic Scoring** — Answers are graded server-side by exact string match. Correct answers are never sent to the client until after submission.
- **Quiz Session Persistence** — Active quizzes are stored in the database. Navigating between tabs, refreshing the page, or closing and reopening the browser restores the in-progress quiz. Logout abandons all active sessions.

## How It Works

```mermaid
flowchart TD
    A["Select course and topic<br/>R22 syllabus"]:::input
    B["Upload notes<br/>PDF / DOCX / PPTX"]:::input

    A --> G
    B --> G

    subgraph G["POST /api/quiz/generate"]
        direction TB
        G1["1. Auth guard"] --> G2["2. Curriculum validation"]
        G2 --> G3["3. Notes context (TF-IDF)"]
        G3 --> G4["4. Adaptive difficulty"]
        G4 --> G5["5. Novelty exclusion"]
        G5 --> G6["6. AI provider chain"]
        G6 --> G7["7. Validate and repair"]
        G7 --> G8["8. Persist session to DB"]
    end

    subgraph P["Provider fallback order"]
        direction LR
        P1["Groq"] --> P2["Cloudflare"] --> P3["NVIDIA"] --> P4["Gemini"]
    end

    G6 -.-> P1

    G --> Q["Student answers 10 MCQs<br/>Progress survives refresh, tab switch and navigation"]:::input

    Q --> S

    subgraph S["POST /api/quiz/submit"]
        direction TB
        S1["1. Deterministic scoring"] --> S2["2. Concept and difficulty breakdown"]
        S2 --> S3["3. AI-generated feedback"]
        S3 --> S4["4. Update topic mastery"]
    end

    S --> R["Results"]:::input
    R --> D["Student Dashboard"]:::final

    class G1,G2,G3,G4,G5,G7,G8,S1,S2,S3,S4 step
    class G6,P1,P2,P3,P4 key

    classDef input fill:#E8F3EC,stroke:#1F7A52,stroke-width:1.5px,color:#0F172A
    classDef step fill:#FFFFFF,stroke:#94A3B8,color:#0F172A
    classDef key fill:#FFF7E6,stroke:#D97706,stroke-width:1.5px,color:#0F172A
    classDef final fill:#14452F,stroke:#14452F,color:#FFFFFF

    style G fill:#F8FAFC,stroke:#14452F,stroke-width:2px,color:#0F172A
    style S fill:#F8FAFC,stroke:#14452F,stroke-width:2px,color:#0F172A
    style P fill:#FFFBF0,stroke:#F5D9A8,color:#0F172A
```

**Provider fallback chain.** The orchestrator iterates through `PROVIDER_ORDER` (default: `groq,cloudflare`). Each provider gets a per-request timeout budget. If a provider fails, is rate-limited, or is in a cooldown window, it is skipped and the next provider is tried. An overall 40-second deadline caps the entire generation cycle including novelty repair. If all providers fail, an error is returned to the user; there is no fake or mock fallback.

**Adaptive difficulty.** After each scored attempt, the system evaluates recent performance against a 50% benchmark. Score above 50% on a topic promotes difficulty by one level; two consecutive scores below 50% demotes by one level. Three levels exist: Foundational (6E/3M/1H), Mixed (4E/4M/2H), Advanced (2E/4M/4H).

**Deterministic scoring.** The client never receives correct answers during the quiz. On submission, `evaluator.py` compares selected answers to server-stored correct answers by exact string match and computes per-concept and per-difficulty accuracy.

---

## Quiz State Management

The frontend tracks quiz lifecycle through five states:

| State | Description |
|-------|-------------|
| `idle` | No active quiz. Topic selection screen is shown. |
| `generating` | Quiz generation request in flight. Loading screen displayed. |
| `displayed` | Questions rendered but no answer selected yet. Not persisted. |
| `in_progress` | At least one answer selected. Persisted to `sessionStorage` and backed by a database `quiz_sessions` row. |
| `completed` | Quiz submitted. Results displayed. Session cleared from storage. |

**What survives refresh and tab switching:** Once a quiz enters `in_progress`, the question set, current question index, and selected answers are written to `sessionStorage`. On page load, `loadActiveQuizState()` calls `GET /api/quiz/active` to retrieve the server-side session and restores the quiz UI without showing the setup screen.

**What is cleared:** Submitting the quiz transitions the backend session to `completed` and clears `sessionStorage`. Discarding a quiz calls `POST /api/quiz/abandon`. Logging out calls `POST /api/auth/logout`, which abandons all `in_progress` sessions server-side and purges all client-side auth and quiz state.

---

## Architecture & Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vanilla HTML/CSS/JS single-page app served by FastAPI `StaticFiles` |
| Backend | FastAPI + Uvicorn (single worker) |
| AI Providers | Groq (Llama 3.1/3.3), Cloudflare Workers AI, NVIDIA Build, Google Gemini |
| Database (dev) | SQLite (auto-created) |
| Database (prod) | MySQL-compatible (TiDB Cloud via PyMySQL + DBUtils connection pool) |
| Auth | Argon2id hashing, HMAC-SHA256 session tokens, HttpOnly cookies |
| File parsing | pypdf, python-docx, python-pptx, olefile |
| Math rendering | KaTeX (bundled offline in `frontend/vendor/katex/`) |

### Project Structure

```
├── render.yaml                        # Render deployment config
├── backend/
│   ├── main.py                        # FastAPI app, all routes, middleware
│   ├── requirements.txt               # Python dependencies
│   ├── Procfile                       # uvicorn start command
│   ├── conftest.py                    # Pytest fixtures
│   ├── .env.example                   # Environment variable template
│   ├── data/
│   │   └── curriculum.json            # R22 syllabus (55 courses)
│   ├── migrations/                    # Versioned SQL schema migrations
│   ├── services/
│   │   ├── llm_service.py             # Multi-provider orchestration
│   │   ├── evaluator.py               # Deterministic answer scoring
│   │   ├── adaptive.py                # Rule-based difficulty adjustment
│   │   ├── novelty.py                 # Jaccard deduplication engine
│   │   ├── database.py                # Schema, CRUD, connection management
│   │   ├── curriculum_service.py      # Curriculum loading and topic lookup
│   │   ├── notes_extractor.py         # PDF/DOCX/PPTX/TXT parser
│   │   ├── notes_matcher.py           # TF-IDF topic isolation
│   │   ├── account_service.py         # Registration, login, password reset
│   │   ├── auth_utils.py              # HMAC token signing and verification
│   │   ├── quota_manager.py           # Per-provider cooldown tracking
│   │   ├── rate_limiter.py            # Sliding-window IP rate limiter
│   │   └── providers/
│   │       ├── base.py                # Abstract provider, shared prompt
│   │       ├── groq_provider.py
│   │       ├── cloudflare_provider.py
│   │       ├── nvidia_provider.py
│   │       └── gemini_provider.py
│   └── tests/                         # Pytest test suite
├── frontend/
│   ├── index.html                     # SPA shell
│   ├── css/style.css
│   ├── js/app.js                      # Client application logic
│   └── vendor/katex/                  # Offline math renderer
└── .github/workflows/ci.yml
```

---

## Getting Started

**Prerequisites:** Python 3.10+, pip, at least one AI provider API key.

```bash
# Clone
git clone https://github.com/Mokshagnna/LearnAI.git
cd LearnAI

# Set up backend
cd backend
python -m venv venv

# Activate (Windows)
venv\Scripts\activate
# Activate (macOS/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create environment file
copy .env.example .env          # Windows
# cp .env.example .env          # macOS/Linux

# Add your API keys to backend/.env, then run:
uvicorn main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The backend serves the frontend as static files — no separate web server needed.

SQLite database is created automatically at `backend/data/learnai.db` if `DATABASE_URL` is unset.

---

## Configuration

Key environment variables from `backend/.env.example`:

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | At least one provider key | Groq LPU API key |
| `CLOUDFLARE_ACCOUNT_ID` | | Cloudflare account ID |
| `CLOUDFLARE_API_TOKEN` | | Cloudflare Workers AI token |
| `NVIDIA_API_KEY` | | NVIDIA Build API key |
| `GEMINI_API_KEY` | | Google Gemini API key |
| `PROVIDER_ORDER` | No | Comma-separated fallback order (default: `groq,cloudflare`) |
| `OVERALL_DEADLINE_SECONDS` | No | Total generation budget in seconds (default: `40.0`) |
| `PROVIDER_TIMEOUT_SECONDS` | No | Per-provider timeout (default: `25.0`) |
| `DATABASE_URL` | Prod only | MySQL connection string. SQLite used if unset. |
| `SESSION_SECRET_KEY` | Prod only | HMAC signing key (min 32 chars). Generate with `openssl rand -hex 32`. |
| `ENVIRONMENT` | No | `development` or `production` (default: `development`) |
| `RATE_LIMIT_QUIZ_GEN_PER_MIN` | No | Quiz generation rate limit per IP (default: `10`) |
| `RATE_LIMIT_QUIZ_SUBMIT_PER_MIN` | No | Submission rate limit per IP (default: `30`) |

The orchestrator skips any provider whose credentials are missing or which is in an active cooldown window.

---

## API Reference

Primary routes defined in [`backend/main.py`](backend/main.py). See `/docs` (when `DEBUG=true`) for the complete OpenAPI schema, health probes (`/health`, `/api/health`), and session refresh endpoints.

### Authentication & Accounts

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/auth/register` | No | Register new account (full_name, email, password) |
| POST | `/api/auth/login` | No | Authenticate with email and password |
| POST | `/api/auth/logout` | Yes | Invalidate session cookie, abandon active quizzes |
| GET | `/api/auth/me` | Yes | Get currently authenticated student profile |
| POST | `/api/auth/forgot-password` | No | Request single-use password reset token |
| POST | `/api/auth/reset-password` | No | Reset password using valid reset token |

### Quiz & Curriculum

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/courses` | No | List curriculum courses, units, and selectable topics |
| POST | `/api/quiz/generate` | Optional | Generate 10-question MCQ quiz for selected topic |
| POST | `/api/quiz/submit` | Yes | Submit answers, grade deterministically, get AI feedback |
| GET | `/api/quiz/active` | Yes | Retrieve active in-progress quiz session |
| POST | `/api/quiz/abandon` | Yes | Explicitly abandon an active quiz session |

### Student & Analytics

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/student/{id}/dashboard` | Yes | Summary analytics: mastery, recent attempts, strong/weak concepts |
| GET | `/api/student/{id}/history` | Yes | Paginated quiz attempt history |
| GET | `/api/student/{id}/performance` | Yes | Concept-wise and difficulty-wise accuracy breakdown |

### Notes & Study Materials

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/notes/upload` | Optional | Upload document (PDF/DOCX/PPTX/TXT), extract text & align topics |
| GET | `/api/study-materials` | Yes | List saved study materials with associated topics and mastery |
| GET | `/api/study-materials/{id}` | Yes | Retrieve study material details and grounding text |
| DELETE | `/api/study-materials/{id}` | Yes | Archive study material while preserving quiz history |

---

## Testing

```bash
cd backend
pytest tests/ -q
```

The test suite in `backend/tests/` covers provider orchestration, quota management, novelty deduplication, quiz lifecycle (session persistence, abandonment, submission), and staging configuration safeguards.

---

## Deployment

The application is deployed on [Render](https://render.com) as a Python web service.

**`render.yaml` configuration:**
- `rootDir: backend`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`
- Health check: `GET /health`

**Production requirements:**
- `DATABASE_URL` must point to a MySQL-compatible database (TiDB Cloud or similar).
- `SESSION_SECRET_KEY` must be set (minimum 32 characters).
- `ENVIRONMENT=production` enables strict validation — the server refuses to start without valid database and session configuration.
- At least one AI provider API key must be configured.

Single-worker mode (`--workers 1`) is required. The quota manager uses an in-process lock backed by a local file; multiple workers would cause stale cooldown state.

Frontend assets are served by FastAPI via a `StaticFiles` mount. `backend/main.py` resolves `../frontend` relative to itself, which works because Render clones the full repository before switching into `rootDir`.

---

## Design Decisions

- **Deterministic scoring instead of AI grading.** Answers are compared by exact string match against server-stored keys. This makes grading instant, reproducible, and immune to LLM hallucination. AI is only used for post-submission feedback commentary, with a static fallback if providers are unavailable.

- **Rule-based adaptivity, not a trained model.** Difficulty progression follows explicit threshold rules (promote above 50%, demote after two consecutive lows). This is transparent, debuggable, and requires no training data. The three-level system (Foundational/Mixed/Advanced) maps directly to question distribution ratios.

- **Frontend-owned quiz progress with server-backed sessions.** The client writes selected answers to `sessionStorage` for instant UI restoration, while the database `quiz_sessions` table is the authoritative record. This means refresh recovery is fast (no API call needed for the UI), but session integrity is enforced server-side.

- **Abandon on logout.** Logging out abandons all `in_progress` quiz sessions. This prevents a logged-out user from having "phantom" active sessions that block future quiz generation or cause stale restoration on the next login.

- **Topic isolation over open-ended generation.** When notes contain multiple topics, TF-IDF scoring suppresses off-topic context before prompt injection, and post-generation heuristics validate that questions stay within the selected topic boundary. This prevents topic bleed at the cost of occasionally discarding marginally relevant content.

---

## Roadmap & Known Limitations

- No email delivery configured. Password reset tokens are returned in the API response (displayed in the UI during development mode).
- Single-worker deployment limits concurrent request throughput.
- OCR for handwritten notes is not implemented.
- The curriculum is static (`curriculum.json`). Adding courses requires editing the JSON file and redeploying.
- No WebSocket or SSE — quiz generation blocks the HTTP request until completion.

---

## Contributors

 **Mokshagnna** — [GitHub](https://github.com/Mokshagnna)

 **Irshad Vali** — [GitHub](https://github.com/shaikirshadvali2-alt)

 **Ganapathi** — [GitHub](https://github.com/gana465)

 **Mallikharjuna** — [GitHub](https://github.com/Mallikharjuna0725)
