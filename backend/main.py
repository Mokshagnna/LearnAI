from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from services.llm_service import (
    generate_quiz,
    generate_analysis,
    AIError,
    AIServiceUnavailableError,
    AIConfigError,
    AIRateLimitError,
    AITimeoutError
)
from services.evaluator import evaluate_answers
from services.database import (
    init_db,
    get_or_create_student,
    save_quiz_attempt,
    get_student_history,
    get_connection,
    create_quiz_session,
    get_quiz_session,
    submit_quiz_atomic,
    get_student_dashboard_data
)
from services.auth_utils import (
    sign_student_identity,
    verify_student_identity,
    get_authenticated_student_id,
    get_optional_authenticated_student_id,
    get_token_from_request,
    DEFAULT_TOKEN_TTL_SECONDS,
    validate_production_auth_config,
)
from services.db.base import is_production_mode, validate_production_db_config
from services.curriculum_service import curriculum_service
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter, get_client_ip
from services.metrics import metrics
from services.logger_utils import setup_secure_logging, request_id_var
from dotenv import load_dotenv
import os
import json
import uuid
import logging
import time
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

_ENV_FILE = BASE_DIR / ".env"
if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=True)
else:
    load_dotenv(override=True)

setup_secure_logging()
logger = logging.getLogger("learnai.api")


def validate_production_environment() -> None:
    """
    Validate all production configuration requirements on startup.
    Fails fast if production mode is active but required configuration is missing or unsafe.
    Never prints credentials or secret values.
    """
    if not is_production_mode():
        return

    validate_production_auth_config()
    validate_production_db_config()


# Validate production environment before initializing database
validate_production_environment()

# Initialize database on startup
init_db()


DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

app = FastAPI(
    title="LearnAI Quiz Platform",
    docs_url="/docs" if DEBUG else None,
    redoc_url="/redoc" if DEBUG else None,
    openapi_url="/openapi.json" if DEBUG else None,
)

@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = request_id_var.set(req_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response
    finally:
        request_id_var.reset(token)

allowed_origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
allowed_origins = [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]
is_wildcard = (len(allowed_origins) == 1 and allowed_origins[0] == "*") or not allowed_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins else ["*"],
    allow_credentials=False if is_wildcard else True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
# Database-backed quiz_sessions table is the sole authoritative session store (Stage C)

class QuizRequest(BaseModel):
    course_code: str = Field(..., min_length=2, max_length=50)
    topic: str = Field(..., min_length=1, max_length=250)
    topic_id: str | None = Field(default=None, max_length=100)
    student_id: str | None = Field(default=None, max_length=100)
    difficulty: str | None = Field(default=None, max_length=50)
    # retake=True signals frontend is requesting a fresh quiz for the same topic.
    # Backend always generates fresh — this flag is used only for logging.
    retake: bool = False

class SubmitRequest(BaseModel):
    quiz_id: str = Field(..., min_length=1, max_length=100)
    answers: list[str] = Field(..., min_length=1, max_length=50)
    student_id: str | None = Field(default=None, max_length=100)
    difficulty: str | None = Field(default=None, max_length=50)
    auth_token: str | None = Field(default=None, max_length=500)

def find_course_and_topic(course_code: str, topic_name: str, topic_id: str | None = None):
    try:
        return curriculum_service.find_course_and_topic(course_code, topic_name, topic_id=topic_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
def liveness_check():
    """Minimal, unauthenticated liveness probe for orchestrators and load balancers."""
    return {"status": "ok"}

@app.get("/api/health")
def health_check(detailed: bool = False):
    """
    Readiness & health check endpoint.
    Returns basic status and database readiness by default.
    Sensitive operational telemetry and provider details are only returned in DEBUG mode or with detailed=True.
    """
    db_status = "connected"
    try:
        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except Exception as e:
        logger.error(f"Health check DB check failure: {e}")
        db_status = "error"

    response = {
        "status": "healthy" if db_status == "connected" else "degraded",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": "1.0.0",
        "database": db_status
    }

    if DEBUG or detailed or os.getenv("EXPOSE_HEALTH_METRICS", "false").lower() in ("true", "1", "yes"):
        from services.llm_service import _get_providers
        providers = _get_providers()
        configured = [p.name for p in providers if p.is_configured()]
        response["configured_providers"] = configured
        response["metrics"] = metrics.get_summary()

    return response

@app.post("/api/auth/session")
def create_auth_session(request: Request):
    """
    Issue a server-minted pseudonymous student identity and signed HMAC token.
    Zero-friction: no passwords or registration required.
    """
    token = get_token_from_request(request)
    if token:
        is_valid, existing_student_id, _ = verify_student_identity(token)
        if is_valid and existing_student_id:
            new_token = sign_student_identity(existing_student_id)
            return {
                "student_id": existing_student_id,
                "token": new_token,
                "expires_at": int(time.time()) + DEFAULT_TOKEN_TTL_SECONDS
            }

    new_student_id = str(uuid.uuid4())
    get_or_create_student(new_student_id)
    token = sign_student_identity(new_student_id)
    return {
        "student_id": new_student_id,
        "token": token,
        "expires_at": int(time.time()) + DEFAULT_TOKEN_TTL_SECONDS
    }

@app.post("/api/auth/refresh")
def refresh_auth_session(request: Request):
    """
    Refresh an existing, valid HMAC token.
    Requires Authorization: Bearer <token>.
    """
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication token required. Header 'Authorization: Bearer <token>' is missing."
        )

    is_valid, student_id, err = verify_student_identity(token)
    if not is_valid:
        if err == "Token expired":
            raise HTTPException(status_code=401, detail="Authentication token expired. Please establish a new session.")
        raise HTTPException(status_code=401, detail=f"Invalid authentication token: {err}")

    new_token = sign_student_identity(student_id)
    return {
        "student_id": student_id,
        "token": new_token,
        "expires_at": int(time.time()) + DEFAULT_TOKEN_TTL_SECONDS
    }

@app.get("/api/courses")
def get_courses():
    return curriculum_service.get_curriculum()

@app.post("/api/quiz/generate")
def create_quiz(req: QuizRequest, request: Request):
    client_ip = get_client_ip(request)
    allowed, retry_after, _ = quiz_gen_limiter.check_rate_limit(client_ip)
    if not allowed:
        metrics.record_rate_limit_exceeded()
        logger.warning(f"[RATE LIMIT EXCEEDED] /api/quiz/generate client={client_ip} retry_after={retry_after}s")
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please wait before generating another quiz.", "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)}
        )

    # Authenticate / resolve student identity before invoking AI generation
    auth_student_id = get_optional_authenticated_student_id(request)
    if auth_student_id:
        if req.student_id and req.student_id.strip() and req.student_id.strip() != auth_student_id:
            raise HTTPException(
                status_code=403,
                detail="Forbidden: student_id does not match authenticated token."
            )
        student_id = auth_student_id
    elif req.student_id and req.student_id.strip():
        requested_id = req.student_id.strip()
        conn = get_connection()
        existing_row = conn.execute("SELECT id FROM students WHERE id = ?", (requested_id,)).fetchone()
        conn.close()
        if existing_row:
            raise HTTPException(
                status_code=401,
                detail="Authentication token required to access existing student identity."
            )
        student_id = get_or_create_student(requested_id)
    else:
        student_id = str(uuid.uuid4())
        student_id = get_or_create_student(student_id)

    metrics.record_quiz_request()
    course, topic = find_course_and_topic(req.course_code, req.topic, topic_id=req.topic_id)
    t_start = time.perf_counter()
    retake_tag = "[RETAKE]" if req.retake else "[FIRST]"
    parent_label = topic.get("parent_topic") or topic.get("parent_section") or ""
    logger.info(
        f"[ENDPOINT START] {retake_tag} /api/quiz/generate "
        f"course={course['code']} topic='{topic['name']}' (parent='{parent_label}') student={student_id}"
    )

    # Build focused syllabus context for the selected topic/concept
    concepts_list = topic.get("concepts", [])
    concepts_str = ", ".join(concepts_list) if concepts_list else topic.get("concept_context", "")
    parent_ctx = topic.get("syllabus_context") or topic.get("context", "")
    mod_parts = []
    if topic.get("module") is not None:
        mod_parts.append(f"Module {topic['module']}")
    if topic.get("unit") is not None:
        mod_parts.append(f"Unit {topic['unit']}")
    mod_info = ", ".join(mod_parts) if mod_parts else ""

    focused_syllabus_lines = [
        f"Selected Topic: {topic['name']}"
    ]
    if parent_label:
        focused_syllabus_lines.append(f"Parent Section / Unit: {parent_label}" + (f" ({mod_info})" if mod_info else ""))
    elif mod_info:
        focused_syllabus_lines.append(f"Unit Info: {mod_info}")
    if concepts_str:
        focused_syllabus_lines.append(f"Syllabus Key Concepts to Test: {concepts_str}")
    if parent_ctx:
        focused_syllabus_lines.append(f"Unit Reference Background: {parent_ctx}")

    focused_syllabus = "\n".join(focused_syllabus_lines)

    try:
        quiz = generate_quiz(
            course_code=course["code"],
            course_name=course["name"],
            topic_name=topic["name"],
            syllabus_context=focused_syllabus,
            student_id=student_id,
            concepts=concepts_list,
            difficulty=req.difficulty
        )
    except AIError as e:
        duration = time.perf_counter() - t_start
        status_code = getattr(e, "status_code", 503)
        cat = getattr(e, "category", "AI_ERROR")
        metrics.record_quiz_failure("all", duration)
        logger.warning(
            f"[ENDPOINT FAILED] /api/quiz/generate ({status_code}) duration={duration:.2f}s "
            f"category={cat}: {e}"
        )
        raise HTTPException(status_code=status_code, detail=str(e))
    except Exception as e:
        duration = time.perf_counter() - t_start
        metrics.record_quiz_failure("all", duration)
        logger.error(
            f"[ENDPOINT FAILED] /api/quiz/generate unexpected error duration={duration:.2f}s: {e}",
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="An unexpected error occurred while preparing your quiz.")

    duration = time.perf_counter() - t_start
    metrics.record_quiz_success(quiz.get("provider_used", "unknown"), duration)

    # Always generate a fresh server-side quiz_id — never reuse the previous one
    quiz_id = str(uuid.uuid4())
    quiz["course_code"] = course["code"]
    quiz["topic_id"] = topic.get("id")
    quiz["parent_section"] = topic.get("parent_section")
    quiz["module"] = topic.get("module")
    quiz["unit"] = topic.get("unit")

    public_questions = []
    for i, q in enumerate(quiz["questions"]):
        public_questions.append({
            "id": i,
            "question": q["question"],
            "options": q["options"],
            "difficulty": q["difficulty"],
            "concept": q["concept"]
        })

    # Persist the active quiz session to database (sole authoritative store)
    # Never return a usable quiz to the user if the session cannot be persisted
    auth_token = sign_student_identity(student_id)

    try:
        create_quiz_session(
            session_id=quiz_id,
            student_id=student_id,
            course_code=course["code"],
            course_name=course["name"],
            topic_name=topic["name"],
            questions=quiz["questions"],  # trusted server snapshot with answers & explanations
            recommended_difficulty=quiz.get("recommended_difficulty", "mixed"),
            adaptive_metadata={
                **(quiz.get("adaptive_metadata") or {}),
                "adaptive_explanation": quiz.get("adaptive_explanation"),
                "attempt_number": quiz.get("attempt_number", 1),
                "novel_count": quiz.get("novel_count", len(public_questions)),
                "provider_used": quiz.get("provider_used", "unknown"),
                "is_fallback": quiz.get("is_fallback", False)
            }
        )
    except Exception as e:
        duration = time.perf_counter() - t_start
        metrics.record_quiz_failure("all", duration)
        logger.error(f"[ENDPOINT FAILED] /api/quiz/generate failed to persist quiz session {quiz_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to persist quiz session. Please try again.")

    logger.info(
        f"[ENDPOINT COMPLETE] /api/quiz/generate quiz_id={quiz_id} "
        f"attempt_id={quiz.get('attempt_id', 'n/a')} "
        f"questions={len(public_questions)} "
        f"is_fallback={quiz.get('is_fallback', False)} "
        f"provider={quiz.get('provider_used', 'unknown')} "
        f"novelty_repairs={quiz.get('novelty_repairs', 0)} "
        f"duration={duration:.2f}s retake={req.retake}"
    )

    return {
        "quiz_id": quiz_id,
        "attempt_id": quiz.get("attempt_id", quiz_id),
        "attempt_number": quiz.get("attempt_number", 1),
        "student_id": student_id,
        "auth_token": auth_token,
        "course_code": course["code"],
        "subject": course["name"],
        "topic": topic["name"],
        "topic_id": topic.get("id"),
        "parent_topic": topic.get("parent_topic") or topic.get("parent_section"),
        "parent_section": topic.get("parent_section"),
        "module": topic.get("module"),
        "unit": topic.get("unit"),
        "questions": public_questions,
        "is_fallback": quiz.get("is_fallback", False),
        "fallback_label": quiz.get("fallback_label", ""),
        "novelty_warning": quiz.get("novelty_warning", False),
        "novel_count": quiz.get("novel_count", len(public_questions)),
        "recommended_difficulty": quiz.get("recommended_difficulty", "mixed"),
        "adaptive_applied": quiz.get("adaptive_applied", False),
        "adaptive_explanation": quiz.get("adaptive_explanation"),
        "adaptive_metadata": quiz.get("adaptive_metadata"),
        "provider_used": quiz.get("provider_used", "unknown")
    }

@app.get("/api/quiz/session/{session_id}")
def get_session(session_id: str, request: Request, student_id: str | None = None):
    """
    Retrieve active or completed quiz session.
    Enforces student ownership and server-verified HMAC authentication.
    Returns sanitized public questions (no answers or explanations).
    """
    raw_session = get_quiz_session(session_id, auto_expire=False)
    if not raw_session:
        raise HTTPException(status_code=404, detail="Quiz session not found or expired.")

    token = get_token_from_request(request)
    if token:
        is_valid, auth_student_id, err = verify_student_identity(token)
        if not is_valid:
            if err == "Token expired":
                raise HTTPException(status_code=401, detail="Authentication token expired. Please refresh your session.")
            raise HTTPException(status_code=401, detail=f"Invalid authentication token: {err}")
        if raw_session["student_id"] != auth_student_id:
            raise HTTPException(status_code=403, detail="Access denied: quiz session belongs to another student.")
        effective_student_id = auth_student_id
    else:
        # Token is missing: require valid credentials
        if not student_id or not student_id.strip():
            raise HTTPException(status_code=400, detail="student_id is required to access quiz session.")
        clean_student_id = student_id.strip()
        if raw_session["student_id"] != clean_student_id:
            raise HTTPException(status_code=403, detail="Access denied: quiz session belongs to another student.")
        raise HTTPException(
            status_code=401,
            detail="Authentication token required. Header 'Authorization: Bearer <token>' is missing."
        )

    try:
        session = get_quiz_session(session_id, student_id=effective_student_id, auto_expire=True)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied: quiz session belongs to another student.")
    except Exception as e:
        logger.error(f"Error retrieving quiz session {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve quiz session.")

    if not session:
        raise HTTPException(status_code=404, detail="Quiz session not found or expired.")

    if session["status"] == "expired":
        raise HTTPException(status_code=400, detail="This quiz session has expired (exceeded 24 hours). Please generate a new quiz.")

    # Sanitize questions for public output
    public_questions = []
    for i, q in enumerate(session.get("questions", [])):
        public_questions.append({
            "id": i,
            "question": q["question"],
            "options": q["options"],
            "difficulty": q.get("difficulty", "Medium"),
            "concept": q.get("concept", "General")
        })

    return {
        "session_id": session["id"],
        "student_id": session["student_id"],
        "course_code": session["course_code"],
        "subject": session["course_name"],
        "topic": session["topic_name"],
        "status": session["status"],
        "recommended_difficulty": session.get("recommended_difficulty", "mixed"),
        "adaptive_metadata": session.get("adaptive_metadata"),
        "created_at": session["created_at"],
        "expires_at": session["expires_at"],
        "questions": public_questions
    }

@app.post("/api/quiz/submit")
def submit_quiz(req: SubmitRequest, request: Request):
    client_ip = get_client_ip(request)
    allowed, retry_after, _ = quiz_submit_limiter.check_rate_limit(client_ip)
    if not allowed:
        logger.warning(f"[RATE LIMIT EXCEEDED] /api/quiz/submit client={client_ip} retry_after={retry_after}s")
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please wait before submitting another quiz.", "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)}
        )

    metrics.record_submission()

    raw_session = get_quiz_session(req.quiz_id, auto_expire=False)
    if not raw_session:
        raise HTTPException(status_code=404, detail="Quiz not found or expired.")

    if not req.student_id or not req.student_id.strip():
        raise HTTPException(status_code=400, detail="student_id is required to submit a quiz session.")

    clean_student_id = req.student_id.strip()

    # Enforce ownership mismatch first
    if raw_session["student_id"] != clean_student_id:
        raise HTTPException(status_code=403, detail="Access denied: quiz session belongs to another student.")

    # Extract and verify authentication token
    token = get_token_from_request(request) or req.auth_token
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication token required. Header 'Authorization: Bearer <token>' is missing."
        )

    is_valid, auth_student_id, err = verify_student_identity(token)
    if not is_valid:
        if err == "Token expired":
            raise HTTPException(status_code=401, detail="Authentication token expired. Please refresh your session.")
        raise HTTPException(status_code=401, detail=f"Invalid authentication token: {err}")

    if auth_student_id != clean_student_id:
        raise HTTPException(status_code=403, detail="Access denied: student_id does not match authenticated token.")

    def _analysis_wrapper(subject, topic, weak_concepts, score, total):
        try:
            return generate_analysis(subject, topic, weak_concepts, score, total)
        except Exception as e:
            logger.warning(f"AI analysis generation failed in submit_quiz: {e}")
            weak_str = ", ".join(weak_concepts) if weak_concepts else "None identified"
            return {
                "summary": "AI feedback is temporarily unavailable. Please try again later.",
                "revision": f"Review concepts: {weak_str}." if weak_concepts else "Review the question explanations below.",
                "recommendation": "Review the detailed question explanations below and practice more questions."
            }

    try:
        result = submit_quiz_atomic(
            session_id=req.quiz_id,
            student_id=clean_student_id,
            answers=req.answers,
            evaluator_fn=evaluate_answers,
            analysis_generator_fn=_analysis_wrapper
        )
        return result
    except LookupError as e:
        logger.warning(f"[SUBMIT 404] {e}")
        raise HTTPException(status_code=404, detail="Quiz not found or expired.")
    except PermissionError as e:
        logger.warning(f"[SUBMIT 403] {e}")
        raise HTTPException(status_code=403, detail="Access denied: quiz session belongs to another student.")
    except ValueError as e:
        logger.warning(f"[SUBMIT 400] {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[SUBMIT 500] Unexpected submission error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while evaluating and saving your quiz.")

@app.get("/api/student/{student_id}/history")
def get_history(student_id: str, request: Request, limit: int = 20):
    if not student_id or not student_id.strip() or len(student_id.strip()) > 100:
        raise HTTPException(status_code=400, detail="Invalid student_id parameter.")
    clean_id = student_id.strip()

    token = get_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication token required. Header 'Authorization: Bearer <token>' is missing."
        )

    is_valid, auth_student_id, err = verify_student_identity(token)
    if not is_valid:
        if err == "Token expired":
            raise HTTPException(status_code=401, detail="Authentication token expired. Please refresh your session.")
        raise HTTPException(status_code=401, detail=f"Invalid authentication token: {err}")

    if auth_student_id != clean_id:
        raise HTTPException(status_code=403, detail="Access denied: cannot view another student's history.")

    try:
        history = get_student_history(clean_id, limit)
        return {"student_id": clean_id, "attempts": history}
    except Exception as e:
        logger.error(f"Failed to get student history: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve history")

@app.get("/api/student/{student_id}/performance")
def get_performance(student_id: str, request: Request, course_code: str | None = None):
    if not student_id or not student_id.strip() or len(student_id.strip()) > 100:
        raise HTTPException(status_code=400, detail="Invalid student_id parameter.")
    clean_id = student_id.strip()

    token = get_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication token required. Header 'Authorization: Bearer <token>' is missing."
        )

    is_valid, auth_student_id, err = verify_student_identity(token)
    if not is_valid:
        if err == "Token expired":
            raise HTTPException(status_code=401, detail="Authentication token expired. Please refresh your session.")
        raise HTTPException(status_code=401, detail=f"Invalid authentication token: {err}")

    if auth_student_id != clean_id:
        raise HTTPException(status_code=403, detail="Access denied: cannot view another student's performance.")

    from services.database import get_concept_performance, get_difficulty_performance
    try:
        concept_perf = get_concept_performance(clean_id, course_code)
        diff_perf = get_difficulty_performance(clean_id, course_code)
        return {
            "student_id": clean_id,
            "concept_performance": concept_perf,
            "difficulty_performance": diff_perf
        }
    except Exception as e:
        logger.error(f"Failed to get student performance: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve performance")

@app.get("/api/student/{student_id}/dashboard")
def get_dashboard(student_id: str, request: Request, course_code: str | None = None, limit: int = 20):
    if not student_id or not student_id.strip() or len(student_id.strip()) > 100:
        raise HTTPException(status_code=400, detail="Invalid student_id parameter.")
    clean_id = student_id.strip()

    token = get_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication token required. Header 'Authorization: Bearer <token>' is missing."
        )

    is_valid, auth_student_id, err = verify_student_identity(token)
    if not is_valid:
        if err == "Token expired":
            raise HTTPException(status_code=401, detail="Authentication token expired. Please refresh your session.")
        raise HTTPException(status_code=401, detail=f"Invalid authentication token: {err}")

    if auth_student_id != clean_id:
        raise HTTPException(status_code=403, detail="Access denied: cannot view another student's dashboard.")

    try:
        data = get_student_dashboard_data(student_id=clean_id, course_code=course_code, limit=limit)
        return data
    except Exception as e:
        logger.error(f"Failed to get student dashboard: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve dashboard")

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
