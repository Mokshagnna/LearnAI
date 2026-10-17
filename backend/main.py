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
from services.database import init_db, get_or_create_student, save_quiz_attempt, get_student_history, get_connection
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

QUIZZES: dict = {}

class QuizRequest(BaseModel):
    course_code: str = Field(..., min_length=2, max_length=50)
    topic: str = Field(..., min_length=1, max_length=250)
    topic_id: str | None = Field(default=None, max_length=100)
    student_id: str | None = Field(default=None, max_length=100)
    # retake=True signals frontend is requesting a fresh quiz for the same topic.
    # Backend always generates fresh — this flag is used only for logging.
    retake: bool = False

class SubmitRequest(BaseModel):
    quiz_id: str = Field(..., min_length=1, max_length=100)
    answers: list[str] = Field(..., min_length=1, max_length=50)
    student_id: str | None = Field(default=None, max_length=100)

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

    metrics.record_quiz_request()
    course, topic = find_course_and_topic(req.course_code, req.topic, topic_id=req.topic_id)
    t_start = time.perf_counter()
    retake_tag = "[RETAKE]" if req.retake else "[FIRST]"
    parent_label = topic.get("parent_topic") or topic.get("parent_section") or ""
    logger.info(
        f"[ENDPOINT START] {retake_tag} /api/quiz/generate "
        f"course={course['code']} topic='{topic['name']}' (parent='{parent_label}') student={req.student_id}"
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
            student_id=req.student_id,
            concepts=concepts_list
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
    # Store the quiz with metadata; strip correct_answers from public payload
    quiz["course_code"] = course["code"]
    quiz["topic_id"] = topic.get("id")
    quiz["parent_section"] = topic.get("parent_section")
    quiz["module"] = topic.get("module")
    quiz["unit"] = topic.get("unit")
    QUIZZES[quiz_id] = quiz

    public_questions = []
    for i, q in enumerate(quiz["questions"]):
        public_questions.append({
            "id": i,
            "question": q["question"],
            "options": q["options"],
            "difficulty": q["difficulty"],
            "concept": q["concept"]
        })

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
        "provider_used": quiz.get("provider_used", "unknown")
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
    if req.quiz_id not in QUIZZES:
        raise HTTPException(status_code=404, detail="Quiz not found or expired.")

    quiz = QUIZZES[req.quiz_id]
    result = evaluate_answers(quiz["questions"], req.answers)

    try:
        analysis = generate_analysis(
            quiz["subject"],
            quiz["topic"],
            result["weak_concepts"],
            result["score"],
            result["total"]
        )
    except Exception as e:
        logger.warning(f"AI analysis generation failed in submit_quiz: {e}")
        weak_str = ", ".join(result["weak_concepts"]) if result.get("weak_concepts") else "None identified"
        analysis = {
            "summary": "AI feedback is temporarily unavailable. Please try again later.",
            "revision": f"Review concepts: {weak_str}." if result.get("weak_concepts") else "Review the question explanations below.",
            "recommendation": "Review the detailed question explanations below and practice more questions."
        }

    result["analysis"] = analysis

    # Save quiz attempt to database
    student_id = get_or_create_student(req.student_id)
    try:
        save_quiz_attempt(
            student_id=student_id,
            course_code=quiz.get("course_code", ""),
            course_name=quiz["subject"],
            topic_name=quiz["topic"],
            quiz_data=quiz,
            score=result["score"],
            total=result["total"],
            percentage=result["percentage"],
            status=result["status"],
            difficulty_accuracy=result["difficulty_accuracy"],
            concept_accuracy=result["concept_accuracy"],
            weak_concepts=result["weak_concepts"],
            strong_concepts=result["strong_concepts"],
            recommended_difficulty=quiz.get("recommended_difficulty")
        )
        result["student_id"] = student_id
    except Exception as e:
        logger.warning(f"Failed to save quiz attempt to database: {e}")

    return result

@app.get("/api/student/{student_id}/history")
def get_history(student_id: str, limit: int = 20):
    try:
        history = get_student_history(student_id, limit)
        return {"student_id": student_id, "attempts": history}
    except Exception as e:
        logger.error(f"Failed to get student history: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve history")

@app.get("/api/student/{student_id}/performance")
def get_performance(student_id: str, course_code: str | None = None):
    from services.database import get_concept_performance, get_difficulty_performance
    try:
        concept_perf = get_concept_performance(student_id, course_code)
        diff_perf = get_difficulty_performance(student_id, course_code)
        return {
            "student_id": student_id,
            "concept_performance": concept_perf,
            "difficulty_performance": diff_perf
        }
    except Exception as e:
        logger.error(f"Failed to get student performance: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve performance")

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
