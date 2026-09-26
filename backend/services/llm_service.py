import os
import json
import re
from google import genai
from dotenv import load_dotenv

load_dotenv(override=True)

def _get_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env")
    return genai.Client(api_key=api_key)

def _extract_json(text):
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    return json.loads(text)

def generate_quiz(course_code, course_name, topic_name, syllabus_context):
    client = _get_client()
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

    prompt = f"""
You are an educational AI quiz generator for B.Tech CSE-AI&ML students.

Course Code: {course_code}
Course Name: {course_name}
Selected Topic: {topic_name}

Syllabus Context:
{syllabus_context}

Generate exactly 10 multiple-choice questions only from the selected topic.

Requirements:
- Questions 1 to 4 must be Easy
- Questions 5 to 8 must be Medium
- Questions 9 and 10 must be Hard
- Every question must contain exactly four options: A, B, C and D
- Exactly one correct answer
- Include the concept tested
- Include a short explanation
- Do not repeat questions
- Return only valid JSON

Return this structure:

{{
  "subject": "{course_name}",
  "topic": "{topic_name}",
  "questions": [
    {{
      "question": "Question text",
      "options": {{
        "A": "Option A",
        "B": "Option B",
        "C": "Option C",
        "D": "Option D"
      }},
      "correct_answer": "A",
      "difficulty": "Easy",
      "concept": "Concept name",
      "explanation": "Short explanation"
    }}
  ]
}}
"""

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "response_mime_type": "application/json"
        }
    )

    data = _extract_json(response.text)

    questions = data.get("questions", [])

    if len(questions) != 10:
        raise RuntimeError("Gemini did not generate exactly 10 questions")

    expected_difficulties = ["Easy"] * 4 + ["Medium"] * 4 + ["Hard"] * 2

    for index, question in enumerate(questions):
        question["difficulty"] = expected_difficulties[index]

        options = question.get("options", {})

        if set(options.keys()) != {"A", "B", "C", "D"}:
            raise RuntimeError("Gemini returned invalid answer options")

        if question.get("correct_answer") not in {"A", "B", "C", "D"}:
            raise RuntimeError("Gemini returned an invalid correct answer")

    data["subject"] = course_name
    data["topic"] = topic_name

    return data

def generate_analysis(subject, topic, weak_concepts, score, total):
    if not weak_concepts:
        return {
            "summary": f"You performed well in {topic}.",
            "revision": "Review the explanations for any incorrect answers.",
            "recommendation": "Try another AI-generated quiz for additional practice."
        }

    client = _get_client()
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

    weak_text = ", ".join(weak_concepts)

    prompt = f"""
You are an AI tutor for a B.Tech CSE-AI&ML student.

Course: {subject}
Topic: {topic}
Score: {score}/{total}
Weak Concepts: {weak_text}

Give concise personalized revision feedback.

Return only valid JSON using this structure:

{{
  "summary": "Short performance analysis",
  "revision": "What the student should revise",
  "recommendation": "What the student should practice next"
}}
"""

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "response_mime_type": "application/json"
        }
    )

    return _extract_json(response.text)