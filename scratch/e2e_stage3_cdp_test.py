import os
import sys
import time
import json
import sqlite3
import subprocess
import tempfile
import urllib.request
import asyncio
import websockets
from pathlib import Path
from unittest.mock import patch

# Configure test environment
os.environ["STUDENT_SIGNING_SECRET"] = "stage3-cdp-secret-32-chars-long!"
os.environ["STUDENT_TOKEN_TTL_SECONDS"] = "86400"
os.environ["TESTING"] = "true"
os.environ["DATABASE_URL"] = ""
os.environ["ENVIRONMENT"] = "test"
os.environ["RENDER"] = ""

backend_dir = Path("backend").resolve()
sys.path.insert(0, str(backend_dir))

from services.database import init_db
import uvicorn
from main import app
import threading

SERVER_PORT = 8003
CDP_PORT = 9224
test_db_path = tempfile.mktemp(suffix=".db", prefix="stage3_e2e_")
init_db(test_db_path)

def start_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=SERVER_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.run()

async def run_e2e_tests():
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    time.sleep(2.0)

    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    user_data_dir = tempfile.mkdtemp(prefix="chrome_stage3_")
    chrome_proc = subprocess.Popen([
        chrome_path,
        "--headless=new",
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
        "--disable-gpu",
        "--disable-extensions",
        "about:blank"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)

    try:
        tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json").read())
        page = next(t for t in tabs if t.get("type") == "page")
        ws_url = page["webSocketDebuggerUrl"]

        msg_id = 0
        async with websockets.connect(ws_url) as ws:
            async def cmd(method, params=None):
                nonlocal msg_id
                msg_id += 1
                await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                while True:
                    r = json.loads(await ws.recv())
                    if r.get("id") == msg_id:
                        return r.get("result", {})

            async def eval_js(expr):
                res = await cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
                return res.get("result", {}).get("value")

            await cmd("Page.enable")
            await cmd("Runtime.enable")

            # Automatically accept dialog prompts (confirm/alert)
            await cmd("Page.setInterceptFileChooserDialog", {"enabled": False})
            
            # Listen to dialogs and auto-accept
            async def handle_dialog(msg):
                pass

            print("\n=== STEP 1: Initial Page Load ===")
            await cmd("Page.navigate", {"url": f"http://127.0.0.1:{SERVER_PORT}/"})
            await asyncio.sleep(2.5)

            title = await eval_js("document.title")
            print(f"Page Title: {title}")
            active_section = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            print(f"Initial Screen for unauthenticated user: {active_section}")
            assert active_section == "loginSection", f"Expected loginSection, got {active_section}"

            print("\n=== STEP 2: Register New Account ===")
            await eval_js("document.getElementById('goToRegisterBtn')?.click()")
            await asyncio.sleep(0.5)
            await eval_js("""
                document.getElementById('registerName').value = 'Test Student';
                document.getElementById('registerEmail').value = 'student_' + Math.random().toString(36).substring(2, 8) + '@stage3.edu';
                document.getElementById('registerPassword').value = 'TestPassword123!';
                document.getElementById('registerConfirmPassword').value = 'TestPassword123!';
                document.getElementById('registerForm').dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
            """)
            await asyncio.sleep(2.0)

            logged_in_user = await eval_js("document.getElementById('userDisplayName')?.textContent")
            active_screen = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            print(f"User Display: {logged_in_user}")
            print(f"Register message: {await eval_js("document.getElementById('registerMsg')?.textContent")}")
            print(f"Register button text: {await eval_js("document.getElementById('registerSubmitBtn')?.textContent")}")
            print(f"Active Screen after registration: {active_screen}")
            assert active_screen == "setup", f"Expected setup, got {active_screen}"

            print("\n=== STEP 3: Refresh Page (Persistent Cookie Test) ===")
            await cmd("Page.navigate", {"url": f"http://127.0.0.1:{SERVER_PORT}/"})
            await asyncio.sleep(2.0)

            active_screen_refreshed = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            logged_in_user_refreshed = await eval_js("document.getElementById('userDisplayName')?.textContent")
            print(f"Active Screen after refresh: {active_screen_refreshed}")
            print(f"User Display after refresh: {logged_in_user_refreshed}")
            assert active_screen_refreshed == "setup", f"Expected setup, got {active_screen_refreshed}"
            assert "Test Student" in logged_in_user_refreshed

            print("\n=== STEP 4: Seed Active Quiz Session & Test Resumption Banner ===")
            # Seed an active quiz session for this student in localStorage & backend
            await eval_js("""
                const activeSid = localStorage.getItem('learnai_student_id');
                const sampleQuiz = {
                    quiz_id: 'test_sess_001',
                    student_id: activeSid,
                    session_id: 'test_sess_001',
                    course_code: '22MT103',
                    topic: 'Matrices',
                    questions: [
                        { id: 0, question: 'Q1?', options: {A:'1', B:'2', C:'3', D:'4'}, correct_answer: 'A', concept: 'Matrices', difficulty: 'Easy' },
                        { id: 1, question: 'Q2?', options: {A:'1', B:'2', C:'3', D:'4'}, correct_answer: 'B', concept: 'Matrices', difficulty: 'Easy' }
                    ]
                };
                window.LearnAIApp.setCurriculum([{ code: '22MT103', name: 'Mathematics', topics: [{ name: 'Matrices' }] }]);
                localStorage.setItem('learnai_active_quiz', JSON.stringify({
                    currentQuiz: sampleQuiz,
                    currentIndex: 1,
                    answers: ['A', ''],
                    activeCourseCode: '22MT103',
                    activeTopicName: 'Matrices'
                }));
            """)

            # Navigate to practice
            await eval_js("window.LearnAIApp.handlePracticeNav()")
            await asyncio.sleep(1.0)

            load_res = await eval_js("window.LearnAIApp.loadActiveQuizState()")
            print(f"loadActiveQuizState returned: {load_res}")
            raw_stored = await eval_js("localStorage.getItem('learnai_active_quiz')")
            print(f"raw_stored in localStorage: {raw_stored[:100] if raw_stored else None}")
            active_sid = await eval_js("localStorage.getItem('learnai_student_id')")
            print(f"localStorage student_id: {active_sid}")
            banner_hidden = await eval_js("document.getElementById('activeQuizBanner')?.classList.contains('hidden')")
            banner_details = await eval_js("document.getElementById('activeQuizBannerDetails')?.textContent")
            active_screen_banner = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            print(f"Active Screen: {active_screen_banner}")
            print(f"Resumption Banner Visible: {not banner_hidden}")
            print(f"Resumption Banner Details: {banner_details}")
            assert active_screen_banner == "setup", "Should remain on setup with banner, NOT hijack into quiz"
            assert not banner_hidden, "Active quiz banner must be visible"
            assert "Matrices" in banner_details and "Question 2 of 2" in banner_details

            print("\n=== STEP 5: Click Resume Quiz ===")
            await eval_js("document.getElementById('resumeActiveQuizBtn')?.click()")
            await asyncio.sleep(0.5)
            quiz_screen = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            print(f"Screen after clicking Resume: {quiz_screen}")
            assert quiz_screen == "quizSection"

            print("\n=== STEP 6: Click Exit Quiz & Change Topic ===")
            # Override confirm to return true
            await eval_js("window.confirm = () => true;")
            await eval_js("document.getElementById('exitQuizBtn')?.click()")
            await asyncio.sleep(1.0)

            screen_after_exit = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            banner_after_exit = await eval_js("document.getElementById('activeQuizBanner')?.classList.contains('hidden')")
            print(f"Screen after Exit Quiz: {screen_after_exit}")
            print(f"Banner Hidden after Exit: {banner_after_exit}")
            assert screen_after_exit == "setup"
            assert banner_after_exit is True

            print("\n=== STEP 7: Completed Result Persistence across Navigation & Refresh ===")
            sample_result = {
                "session_id": "test_completed_001",
                "score": 10,
                "total": 10,
                "percentage": 100,
                "status": "Mastered",
                "course_code": "22MT103",
                "topic": "Matrices",
                "difficulty_accuracy": {"Easy": {"correct": 10, "total": 10, "accuracy": 100}},
                "concept_accuracy": {"Matrices": {"correct": 10, "total": 10, "accuracy": 100}},
                "strong_concepts": ["Matrices"],
                "weak_concepts": [],
                "details": [
                    {
                        "question": "What is det(I)?",
                        "options": {"A": "1", "B": "0", "C": "-1", "D": "2"},
                        "selected_answer": "A",
                        "correct_answer": "A",
                        "is_correct": True,
                        "explanation": "Determinant of identity matrix is 1.",
                        "concept": "Matrices",
                        "difficulty": "Easy"
                    }
                ]
            }

            # Submit / show result
            await eval_js(f"""
                window.LearnAIApp.setLastCompletedResult({json.dumps(sample_result)});
                // Simulate showResult
                window.LearnAIApp.clearSessionState();
                showResult({json.dumps(sample_result)});
            """)
            await asyncio.sleep(1.0)
            res_screen = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            score_text = await eval_js("document.getElementById('scoreText')?.textContent")
            print(f"Result Screen displayed: {res_screen}, Score: {score_text}")
            assert res_screen == "resultSection"
            assert "10 / 10" in score_text

            # Navigate to Dashboard
            await eval_js("document.getElementById('navDashboardBtn')?.click()")
            await asyncio.sleep(1.0)
            dash_screen = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            print(f"Navigated to: {dash_screen}")
            assert dash_screen == "dashboardSection"

            # Return to Practice
            await eval_js("document.getElementById('navPracticeBtn')?.click()")
            await asyncio.sleep(1.0)
            back_screen = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            back_score = await eval_js("document.getElementById('scoreText')?.textContent")
            print(f"Returned to Practice Screen: {back_screen}, Score: {back_score}")
            assert back_screen == "resultSection", f"Expected resultSection to be preserved, got {back_screen}"
            assert "10 / 10" in back_score

            # Click Choose Another Topic
            await eval_js("document.getElementById('newTopicBtn')?.click()")
            await asyncio.sleep(0.5)
            after_choose = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            print(f"Screen after Choose Another Topic: {after_choose}")
            assert after_choose == "setup"

            print("\n=== STEP 8: Logout Verification ===")
            await eval_js("document.getElementById('logoutBtn')?.click()")
            await asyncio.sleep(1.5)
            logout_screen = await eval_js("document.querySelector('main > section:not(.hidden)')?.id")
            token_after_logout = await eval_js("localStorage.getItem('learnai_auth_token')")
            session_after_logout = await eval_js("localStorage.getItem('learnai_session_active')")
            print(f"Screen after Logout: {logout_screen}")
            print(f"Tokens in storage: token={token_after_logout}, session={session_after_logout}")
            assert logout_screen == "loginSection"
            assert token_after_logout is None
            assert session_after_logout is None

            print("\n=== ALL E2E BROWSER CHECKS PASSED SUCCESSFULLY! ===")

    finally:
        chrome_proc.terminate()
        try:
            os.remove(test_db_path)
        except Exception:
            pass

if __name__ == '__main__':
    asyncio.run(run_e2e_tests())
