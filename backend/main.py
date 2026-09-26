from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from services.llm_service import generate_quiz, generate_analysis
from services.evaluator import evaluate_answers
import json
import uuid

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
CURRICULUM_FILE = BASE_DIR / "data" / "curriculum.json"

with open(CURRICULUM_FILE, "r", encoding="utf-8") as f:
    CURRICULUM = json.load(f)

app = FastAPI(title="LearnAI Quiz Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

QUIZZES = {}

class QuizRequest(BaseModel):
    course_code: str
    topic: str

class SubmitRequest(BaseModel):
    quiz_id: str
    answers: list[str]

def find_course_and_topic(course_code: str, topic_name: str):
    for course in CURRICULUM["courses"]:
        if course["code"] == course_code:
            for topic in course["topics"]:
                if topic["name"] == topic_name:
                    return course, topic
            raise HTTPException(status_code=400, detail="Selected topic is not part of this course.")
    raise HTTPException(status_code=400, detail="Selected course is not available.")

@app.get("/api/courses")
def get_courses():
    return CURRICULUM

@app.post("/api/quiz/generate")
def create_quiz(req: QuizRequest):
    course, topic = find_course_and_topic(req.course_code, req.topic)

    try:
        quiz = generate_quiz(
            course_code=course["code"],
            course_name=course["name"],
            topic_name=topic["name"],
            syllabus_context=topic["context"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    quiz_id = str(uuid.uuid4())
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

    return {
        "quiz_id": quiz_id,
        "course_code": course["code"],
        "subject": course["name"],
        "topic": topic["name"],
        "questions": public_questions
    }

@app.post("/api/quiz/submit")
def submit_quiz(req: SubmitRequest):
    if req.quiz_id not in QUIZZES:
        raise HTTPException(status_code=404, detail="Quiz not found or expired.")

    quiz = QUIZZES[req.quiz_id]
    result = evaluate_answers(quiz["questions"], req.answers)

    analysis = generate_analysis(
        quiz["subject"],
        quiz["topic"],
        result["weak_concepts"],
        result["score"],
        result["total"]
    )

    result["analysis"] = analysis
    return result

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
