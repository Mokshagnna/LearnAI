# LearnAI – Current Private Prototype

## Current scope

For now, the website contains only:

- CV — 22AM803 Computer Vision
- DL — 22AM301 Deep Learning
- WT — 22CS303 Web Technologies

The course and topic lists are taken from the uploaded R22 B.Tech CSE-AI&ML curriculum.

## Student flow

1. Student opens the local website.
2. Student selects CV, DL or WT.
3. The Topic dropdown automatically displays only the topics for that selected course.
4. Student selects one topic.
5. The FastAPI backend validates that the topic belongs to the course.
6. The backend sends the selected course, selected topic and syllabus context to OpenAI.
7. OpenAI generates exactly 10 MCQs:
   - 4 Easy
   - 4 Medium
   - 2 Hard
8. Questions are displayed one at a time.
9. Student submits the quiz.
10. The system shows score, strong concepts, weak concepts, explanations and AI revision feedback.
11. Retake generates a fresh question set for the same topic.

## Run in VS Code

Open the `LearnAI_CV_DL_WT` folder.

In Terminal:

cd backend

Create environment:

python -m venv venv

Windows:

venv\Scripts\activate

Install packages:

pip install -r requirements.txt

Copy `.env.example` to `.env` and add your key:

OPENAI_API_KEY=your_real_api_key_here

Run:

uvicorn main:app --reload

Open:

http://127.0.0.1:8000

## Main architecture

Student
→ Select Course
→ Display Course Topics
→ Select Topic
→ FastAPI
→ Curriculum Validation
→ OpenAI LLM
→ 10 Questions
→ Quiz
→ Evaluation
→ Weak Concept Analysis
→ AI Revision Feedback

## Future submission

Later you can add:
- Remaining courses
- Login/signup
- Database
- Student dashboard/history
- PDF/notes upload
- Public cloud deployment
