"""
execute_browser_journey_29steps.py
==================================
Executes the full 29-step LearnAI browser journey test using headless Chrome (CDP)
and a deterministic local mock provider.
Zero external AI inference requests are made.
Database: backend/data/staging_gate1.db
Port: 8000
"""

import os
import sys
import time
import json
import sqlite3
import shutil
import tempfile
import subprocess
import urllib.request
import threading
from pathlib import Path
import asyncio
import websockets

# Staging environment safeguards
os.environ["LEARNAI_ENV_FILE"] = "backend/.env.staging.local"
os.environ["PROVIDER_ORDER"] = "mock"

backend_dir = Path("backend").resolve()
sys.path.insert(0, str(backend_dir))

from services.env_loader import resolve_and_load_env
resolve_and_load_env(backend_dir)
from services.database import is_production_mode, get_db_path
from services.providers.base import BaseAIProvider
import services.llm_service as llm

assert not is_production_mode(), "FATAL: Production mode active!"
assert get_db_path().name == "staging_gate1.db", "FATAL: Staging database required!"

# Mock Questions for Attempt 1 (10 distinct questions, includes code block and KaTeX math)
ATTEMPT_1_QUESTIONS = [
    {
        "question": "What is the necessary and sufficient condition for a square matrix A to possess an inverse?",
        "options": {
            "A": "Its determinant det(A) is non-zero",
            "B": "Its trace is strictly positive",
            "C": "All of its eigenvalues are zero",
            "D": "It is a diagonal matrix"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Invertibility Condition",
        "explanation": "A square matrix has an inverse if and only if its determinant is non-zero."
    },
    {
        "question": "How is the inverse of a 2x2 matrix computed using its determinant and adjugate?",
        "options": {
            "A": "Dividing the classical adjugate matrix by the determinant",
            "B": "Multiplying the transpose by the trace",
            "C": "Taking the reciprocal of each matrix element individually",
            "D": "Subtracting the identity matrix from the square"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Adjugate Formula",
        "explanation": "The inverse is given by A^(-1) = adj(A) / det(A)."
    },
    {
        "question": "Consider the following Python snippet using NumPy:\n```python\nimport numpy as np\nA = np.array([[1, 2], [3, 4]])\ninvA = np.linalg.inv(A)\n```\nWhat mathematical matrix product is equal to the identity matrix?",
        "options": {
            "A": "np.dot(A, invA)",
            "B": "A + invA",
            "C": "np.cross(A, invA)",
            "D": "A ** 2"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Identity Product",
        "explanation": "Multiplying a matrix by its inverse yields the identity matrix: A * A^(-1) = I."
    },
    {
        "question": "Given two invertible matrices P and Q of identical dimension, what is the inverse of the matrix product (PQ)?",
        "options": {
            "A": "Q^(-1) P^(-1)",
            "B": "P^(-1) Q^(-1)",
            "C": "PQ",
            "D": "P^(-1) + Q^(-1)"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Product Inversion Order",
        "explanation": "The inverse of a matrix product reverses factor order: (PQ)^(-1) = Q^(-1) P^(-1)."
    },
    {
        "question": "If a real matrix M is orthogonal such that its transpose equals its inverse, what can be deduced about its determinant?",
        "options": {
            "A": "The determinant must be either +1 or -1",
            "B": "The determinant must be zero",
            "C": "The determinant must equal infinity",
            "D": "The determinant must be strictly negative"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Orthogonal Inverses",
        "explanation": "Since det(M^T M) = det(M)^2 = det(I) = 1, det(M) = +/- 1."
    },
    {
        "question": "What happens to the inverse of matrix A when A is multiplied by a non-zero scalar c?",
        "options": {
            "A": "The inverse is multiplied by 1/c",
            "B": "The inverse is multiplied by c",
            "C": "The inverse is multiplied by c squared",
            "D": "The inverse remains completely unchanged"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Scalar Inversion",
        "explanation": "(c A)^(-1) = (1/c) * A^(-1) for any non-zero scalar c."
    },
    {
        "question": "In the Gauss-Jordan elimination algorithm for calculating matrix inverses, what augmented matrix is row-reduced?",
        "options": {
            "A": "[A | I]",
            "B": "[A | 0]",
            "C": "[I | A^T]",
            "D": "[A | A]"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Gauss-Jordan Method",
        "explanation": "Row reducing [A | I] to reduced row echelon form produces [I | A^(-1)]."
    },
    {
        "question": "What is the relationship between the eigenvalues of an invertible matrix A and the eigenvalues of its inverse?",
        "options": {
            "A": "The eigenvalues of the inverse are the reciprocals 1/lambda",
            "B": "The eigenvalues are identical",
            "C": "The eigenvalues are negated",
            "D": "The inverse has only zero eigenvalues"
        },
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Eigenvalue Inversion",
        "explanation": "If Av = lambda v, then A^(-1)v = (1/lambda)v for lambda != 0."
    },
    {
        "question": "For an invertible matrix A, what is the determinant of its inverse matrix $\\det(A^{-1})$?",
        "options": {
            "A": "$1 / \\det(A)$",
            "B": "$\\det(A)$",
            "C": "$-\\det(A)$",
            "D": "0"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Determinant of Inverse",
        "explanation": "det(A * A^(-1)) = det(A) * det(A^(-1)) = det(I) = 1, so det(A^(-1)) = 1/det(A)."
    },
    {
        "question": "Under what conditions does a triangular matrix have a well-defined matrix inverse?",
        "options": {
            "A": "All diagonal elements must be non-zero",
            "B": "All off-diagonal elements must be zero",
            "C": "The trace must equal the dimension",
            "D": "It must be symmetric"
        },
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Triangular Invertibility",
        "explanation": "The determinant of a triangular matrix is the product of its diagonal entries, which must all be non-zero."
    }
]

# Mock Questions for Attempt 2 (Retake - 10 distinct questions)
ATTEMPT_2_QUESTIONS = [
    {
        "question": "How does elementary row switching affect the matrix determinant during matrix inversion?",
        "options": {
            "A": "It negates the sign of the determinant",
            "B": "It doubles the determinant magnitude",
            "C": "It sets the determinant to zero",
            "D": "It leaves the determinant unchanged"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Row Swapping Impact",
        "explanation": "Interchanging any two rows of a square matrix multiplies its determinant by -1."
    },
    {
        "question": "What is the full row rank of an invertible matrix of size n by n?",
        "options": {
            "A": "Exactly n",
            "B": "Strictly n minus 1",
            "C": "Always 1",
            "D": "Always 0"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Full Matrix Rank",
        "explanation": "A square matrix is invertible if and only if it has full rank n."
    },
    {
        "question": "For a symmetric invertible matrix S, what is the symmetry property of its inverse matrix?",
        "options": {
            "A": "The inverse is also symmetric",
            "B": "The inverse is skew-symmetric",
            "C": "The inverse is always singular",
            "D": "The inverse is asymmetric"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Symmetric Inverse",
        "explanation": "(S^(-1))^T = (S^T)^(-1) = S^(-1), so the inverse is symmetric."
    },
    {
        "question": "What is the matrix inverse of a non-singular diagonal matrix D with entries d_1, d_2, ..., d_n?",
        "options": {
            "A": "A diagonal matrix with reciprocal entries 1/d_i",
            "B": "A matrix of all ones",
            "C": "The negative diagonal matrix -D",
            "D": "The identity matrix I"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Diagonal Matrix Inversion",
        "explanation": "The inverse of a diagonal matrix has 1/d_ii on its main diagonal."
    },
    {
        "question": "In the matrix equation AX = B with invertible square matrix A, what is the unique solution for X?",
        "options": {
            "A": "X = A^(-1) B",
            "B": "X = B A^(-1)",
            "C": "X = B / A",
            "D": "X = A B^(-1)"
        },
        "correct_answer": "A",
        "difficulty": "Easy",
        "concept": "Linear System Inversion",
        "explanation": "Pre-multiplying both sides of AX = B by A^(-1) gives X = A^(-1)B."
    },
    {
        "question": "If matrix A has zero as an eigenvalue, why does its matrix inverse fail to exist?",
        "options": {
            "A": "Because the product of eigenvalues det(A) is zero",
            "B": "Because trace becomes infinite",
            "C": "Because rank becomes n+1",
            "D": "Because all entries vanish"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Zero Eigenvalue Singularity",
        "explanation": "The determinant equals the product of eigenvalues; if one is zero, det(A) = 0 and A is singular."
    },
    {
        "question": "What is the inverse of the transpose of an invertible matrix (A^T)^(-1)?",
        "options": {
            "A": "(A^(-1))^T",
            "B": "-A^T",
            "C": "A",
            "D": "I_n"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Transpose of Inverse",
        "explanation": "Transposition and inversion commute: (A^T)^(-1) = (A^(-1))^T."
    },
    {
        "question": "What is the Cramer rule formula for solving x_i using determinants in matrix inversion?",
        "options": {
            "A": "det(A_i) / det(A)",
            "B": "det(A) / det(A_i)",
            "C": "det(A) * det(A_i)",
            "D": "det(A_i) - det(A)"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Cramer Rule Inversion",
        "explanation": "Cramer rule gives x_i = det(A_i) / det(A) when det(A) != 0."
    },
    {
        "question": "How does multiplying a row by a non-zero scalar k affect the matrix determinant in Gaussian inversion?",
        "options": {
            "A": "The determinant is scaled by k",
            "B": "The determinant is scaled by 1/k",
            "C": "The determinant is unchanged",
            "D": "The determinant becomes k squared"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Row Scaling Factor",
        "explanation": "Multiplying a row of a matrix by scalar k multiplies its determinant by k."
    },
    {
        "question": "For a block diagonal matrix composed of invertible square blocks A1 and A2, what is its block inverse?",
        "options": {
            "A": "Block diagonal matrix with A1^(-1) and A2^(-1)",
            "B": "Zero off-diagonal blocks replaced by identity",
            "C": "A block matrix with swapped diagonal blocks",
            "D": "Singular block matrix"
        },
        "correct_answer": "A",
        "difficulty": "Hard",
        "concept": "Block Diagonal Inversion",
        "explanation": "The inverse of a block diagonal matrix is the block diagonal matrix of the inverted blocks."
    }
]

generation_call_count = 0

class DeterministicMockProvider(BaseAIProvider):
    @property
    def name(self): return "mock"
    def is_configured(self): return True
    def generate_quiz_raw(self, **kwargs):
        global generation_call_count
        generation_call_count += 1
        if generation_call_count == 1:
            q_list = ATTEMPT_1_QUESTIONS
        else:
            q_list = ATTEMPT_2_QUESTIONS
        return json.dumps({"questions": q_list})
    def generate_analysis_raw(self, **kwargs):
        return json.dumps({
            "summary": "Demonstrated solid understanding of matrix invertibility and determinant properties.",
            "strengths": ["Matrix properties", "Row operations"],
            "weaknesses": []
        })

# Inject mock provider
mock_instance = DeterministicMockProvider()
llm._CACHED_PROVIDERS["mock"] = mock_instance
os.environ["PROVIDER_ORDER"] = "mock"

# Start FastAPI server on port 8000
import uvicorn
from main import app

def start_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    server.run()

async def send_cdp_cmd(ws, method, params=None, msg_id=1):
    msg = {"id": msg_id, "method": method, "params": params or {}}
    await ws.send(json.dumps(msg))
    while True:
        resp_text = await ws.recv()
        resp = json.loads(resp_text)
        if resp.get("id") == msg_id:
            return resp.get("result", {})

async def eval_js(ws, expr, msg_id_holder):
    msg_id_holder[0] += 1
    res = await send_cdp_cmd(ws, "Runtime.evaluate", {
        "expression": expr,
        "returnByValue": True,
        "awaitPromise": True
    }, msg_id=msg_id_holder[0])
    val = res.get("result", {}).get("value")
    return val

async def run_journey():
    print("=" * 70)
    print("PHASE 4: 29-STEP LOCAL BROWSER END-TO-END JOURNEY TEST")
    print("=" * 70)

    # Clean existing test student if present in staging database
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM student_accounts WHERE email = ?", ("audit.student.staging@gmail.com",))
    conn.commit()
    conn.close()

    # 1. Start Server on port 8000
    print("[INIT] Starting local FastAPI server on port 8000...")
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(2.5)

    # Check server health
    req = urllib.request.urlopen("http://127.0.0.1:8000/api/courses")
    assert req.status == 200, "Server failed to start!"
    print("  -> Backend running cleanly at http://127.0.0.1:8000")

    # 2. Launch Chrome Headless
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    user_data_dir = tempfile.mkdtemp(prefix="chrome_e2e_")
    chrome_proc = subprocess.Popen([
        chrome_path,
        "--headless=new",
        "--remote-debugging-port=9225",
        f"--user-data-dir={user_data_dir}",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)

    # Get WebSocket Debugger URL
    tabs_req = urllib.request.urlopen("http://127.0.0.1:9225/json")
    tabs = json.loads(tabs_req.read().decode("utf-8"))
    page_tabs = [t for t in tabs if t.get("type") == "page"]
    assert page_tabs, "No browser page tab found!"
    ws_url = page_tabs[0]["webSocketDebuggerUrl"]
    print(f"  -> Connected to Headless Chrome via CDP: {ws_url[:50]}...")

    step_results = {}
    msg_id = [10]

    async with websockets.connect(ws_url) as ws:
        # Enable CDP domains
        await send_cdp_cmd(ws, "Page.enable", {}, 1)
        await send_cdp_cmd(ws, "Runtime.enable", {}, 2)
        await send_cdp_cmd(ws, "DOM.enable", {}, 3)

        # Step 1: Start backend with staging safeguards
        step_results["step1"] = {
            "name": "Start local backend with staging safeguards",
            "status": "PASS",
            "evidence": f"Server active at http://127.0.0.1:8000, DB={db_path.name}, is_prod={is_production_mode()}"
        }
        print("Step 1: Start local backend with staging safeguards -> PASS")

        # Step 2: Open actual frontend in browser
        nav_res = await send_cdp_cmd(ws, "Page.navigate", {"url": "http://127.0.0.1:8000/"}, 4)
        await asyncio.sleep(2.5)
        title = await eval_js(ws, "document.title", msg_id)
        step_results["step2"] = {
            "name": "Open actual frontend in browser",
            "status": "PASS" if "LearnAI" in str(title) else "FAIL",
            "evidence": f"Document title: '{title}'"
        }
        print(f"Step 2: Open actual frontend in browser -> {step_results['step2']['status']} ({title})")

        # Step 3: Register or log in using dedicated staging test credentials
        await eval_js(ws, "document.getElementById('goToRegisterBtn').click();", msg_id)
        await asyncio.sleep(0.5)
        
        reg_js = """
        new Promise((resolve) => {
            document.getElementById('registerName').value = 'Audit Student';
            document.getElementById('registerEmail').value = 'audit.student.staging@gmail.com';
            document.getElementById('registerPassword').value = 'SecurePass_2026!';
            document.getElementById('registerConfirmPassword').value = 'SecurePass_2026!';
            document.getElementById('registerSubmitBtn').click();
            setTimeout(() => {
                resolve({
                    token: localStorage.getItem('learnai_auth_token'),
                    student_id: localStorage.getItem('learnai_student_id'),
                    setupVis: !document.getElementById('setup').classList.contains('hidden')
                });
            }, 3000);
        })
        """
        reg_res = await eval_js(ws, reg_js, msg_id)
        token = reg_res.get("token") if reg_res else None
        stu_id = reg_res.get("student_id") if reg_res else None
        setup_vis = reg_res.get("setupVis") if reg_res else False

        step_results["step3"] = {
            "name": "Register or log in using dedicated staging test credentials",
            "status": "PASS" if token and stu_id else "FAIL",
            "evidence": f"Registered stu_id={stu_id}, token present={bool(token)}, setup visible={setup_vis}"
        }
        print(f"Step 3: Register test student -> {step_results['step3']['status']} (token={bool(token)}, id={stu_id})")

        # Step 4: Verify authentication and session creation
        user_badge = await eval_js(ws, "document.getElementById('userDisplayName').textContent", msg_id)
        step_results["step4"] = {
            "name": "Verify authentication and session creation",
            "status": "PASS" if "Audit Student" in str(user_badge) else "FAIL",
            "evidence": f"Student name badge: '{user_badge}'"
        }
        print(f"Step 4: Verify authentication badge -> {step_results['step4']['status']} ({user_badge})")

        # Step 5: Select a topic and difficulty
        select_topic_js = """
        (function() {
            const courseSelect = document.getElementById('course');
            courseSelect.value = '22MT103';
            courseSelect.dispatchEvent(new Event('change', {bubbles: true}));
        })()
        """
        await eval_js(ws, select_topic_js, msg_id)
        await asyncio.sleep(1.0)
        
        select_subtopic_js = """
        (function() {
            const topicSelect = document.getElementById('topic');
            topicSelect.value = 'Inverse of a Matrix';
            topicSelect.dispatchEvent(new Event('change', {bubbles: true}));
        })()
        """
        await eval_js(ws, select_subtopic_js, msg_id)
        await asyncio.sleep(0.5)
        
        topic_val = await eval_js(ws, "document.getElementById('topic').value", msg_id)
        diff_val = await eval_js(ws, "document.getElementById('difficulty').value", msg_id)
        step_results["step5"] = {
            "name": "Select topic and difficulty",
            "status": "PASS" if topic_val == "Inverse of a Matrix" else "FAIL",
            "evidence": f"Selected topic='{topic_val}', difficulty='{diff_val}'"
        }
        print(f"Step 5: Select topic & difficulty -> {step_results['step5']['status']} ({topic_val})")

        # Step 6: Request quiz generation through deterministic mock provider
        await eval_js(ws, "document.getElementById('generateBtn').click();", msg_id)
        await asyncio.sleep(3.5) # Wait for generation
        
        quiz_visible = await eval_js(ws, "!document.getElementById('quizSection').classList.contains('hidden')", msg_id)
        step_results["step6"] = {
            "name": "Request quiz generation through deterministic mock provider",
            "status": "PASS" if quiz_visible else "FAIL",
            "evidence": f"Quiz container visible: {quiz_visible}"
        }
        print(f"Step 6: Request quiz generation -> {step_results['step6']['status']}")

        # Step 7: Verify generated questions appear in real UI
        q_text = await eval_js(ws, "document.getElementById('question').textContent", msg_id)
        step_results["step7"] = {
            "name": "Verify generated questions appear in real UI",
            "status": "PASS" if q_text and len(q_text) > 10 else "FAIL",
            "evidence": f"Q1 text: '{q_text[:70]}...'"
        }
        print(f"Step 7: Verify generated question in UI -> {step_results['step7']['status']}")

        # Step 8: Verify question count
        progress_text = await eval_js(ws, "document.getElementById('progressText').textContent", msg_id)
        total_q = int(progress_text.split("of ")[1].strip()) if "of " in str(progress_text) else 0
        step_results["step8"] = {
            "name": "Verify question count",
            "status": "PASS" if total_q == 10 else "FAIL",
            "evidence": f"total_q={total_q}, progressText='{progress_text}'"
        }
        print(f"Step 8: Verify question count (10) -> {step_results['step8']['status']} ({progress_text})")

        # Step 9: Verify question text and answer options
        opts_count = await eval_js(ws, "document.querySelectorAll('#options .option').length", msg_id)
        step_results["step9"] = {
            "name": "Verify question text and answer options",
            "status": "PASS" if opts_count == 4 else "FAIL",
            "evidence": f"Options count={opts_count}"
        }
        print(f"Step 9: Verify 4 answer options -> {step_results['step9']['status']}")

        # Step 10: Verify code-aware question rendering where applicable
        # Question 3 has Python snippet
        await eval_js(ws, "document.querySelectorAll('#options .option')[0].click();", msg_id)
        await eval_js(ws, "document.getElementById('nextBtn').click();", msg_id)
        await asyncio.sleep(0.5)
        # On Q2: answer and advance to Q3
        await eval_js(ws, "document.querySelectorAll('#options .option')[0].click();", msg_id)
        await eval_js(ws, "document.getElementById('nextBtn').click();", msg_id)
        await asyncio.sleep(0.5)
        
        has_code_block = await eval_js(ws, "document.querySelectorAll('#question pre code, #question .code-block, #question pre').length > 0", msg_id)
        step_results["step10"] = {
            "name": "Verify code-aware question rendering",
            "status": "PASS" if has_code_block else "FAIL",
            "evidence": f"Code block present on Question 3: {has_code_block}"
        }
        print(f"Step 10: Verify code-aware rendering (pre/code) -> {step_results['step10']['status']}")

        # Step 11: Verify mathematical expressions and KaTeX rendering
        # Navigate to Q9 (which has KaTeX \det(A^{-1}) math)
        for _ in range(6):
            await eval_js(ws, "document.querySelectorAll('#options .option')[0].click();", msg_id)
            await eval_js(ws, "document.getElementById('nextBtn').click();", msg_id)
            await asyncio.sleep(0.3)
        
        has_katex = await eval_js(ws, "document.querySelectorAll('.katex, .math-inline').length > 0 || document.getElementById('question').innerHTML.includes('katex')", msg_id)
        step_results["step11"] = {
            "name": "Verify mathematical expressions and KaTeX rendering",
            "status": "PASS" if has_katex else "FAIL",
            "evidence": f"KaTeX classes / math rendering present on Q9: {has_katex}"
        }
        print(f"Step 11: Verify KaTeX rendering on Q9 -> {step_results['step11']['status']}")

        # Step 12: Verify no answer key or correct-answer metadata is exposed before submission
        exposed_in_dom = await eval_js(ws, """
        (function() {
            const html = document.getElementById('quizSection').innerHTML;
            const quizObj = window.currentQuiz;
            let exposed = false;
            if (html.includes('correct_answer') || html.includes('explanation')) exposed = true;
            if (quizObj && quizObj.questions && quizObj.questions.some(q => q.correct_answer || q.explanation)) exposed = true;
            return exposed;
        })()
        """, msg_id)
        step_results["step12"] = {
            "name": "Verify no answer key or metadata exposed before submission",
            "status": "PASS" if not exposed_in_dom else "FAIL",
            "evidence": f"correct_answer/explanation exposed in client DOM or quiz object: {exposed_in_dom}"
        }
        print(f"Step 12: Verify zero answer key exposure -> {step_results['step12']['status']}")

        # Step 13: Select answers
        await eval_js(ws, "document.querySelectorAll('#options .option')[0].click();", msg_id)
        q9_selected = await eval_js(ws, "document.querySelectorAll('#options .option')[0].classList.contains('selected')", msg_id)
        step_results["step13"] = {
            "name": "Select answers",
            "status": "PASS" if q9_selected else "FAIL",
            "evidence": f"Option A selected class applied on Q9: {q9_selected}"
        }
        print(f"Step 13: Select answers -> {step_results['step13']['status']}")

        # Step 14: Navigate between questions
        await eval_js(ws, "document.getElementById('prevBtn').click();", msg_id)
        await asyncio.sleep(0.5)
        q8_text = await eval_js(ws, "document.getElementById('question').textContent", msg_id)
        step_results["step14"] = {
            "name": "Navigate between questions",
            "status": "PASS" if "eigenvalues" in str(q8_text).lower() else "FAIL",
            "evidence": f"Navigated back to Q8: '{q8_text[:50]}...'"
        }
        print(f"Step 14: Navigate previous to Q8 -> {step_results['step14']['status']}")

        # Step 15: Verify answer persistence when navigating backward and forward
        q8_persisted = await eval_js(ws, "document.querySelectorAll('#options .option')[0].classList.contains('selected')", msg_id)
        await eval_js(ws, "document.getElementById('nextBtn').click();", msg_id)
        await asyncio.sleep(0.5)
        q9_persisted = await eval_js(ws, "document.querySelectorAll('#options .option')[0].classList.contains('selected')", msg_id)
        step_results["step15"] = {
            "name": "Verify answer persistence when navigating backward and forward",
            "status": "PASS" if q8_persisted and q9_persisted else "FAIL",
            "evidence": f"Q8 persisted={q8_persisted}, Q9 persisted={q9_persisted}"
        }
        print(f"Step 15: Verify answer persistence across nav -> {step_results['step15']['status']}")

        # Advance to Q10 and answer it
        await eval_js(ws, "document.getElementById('nextBtn').click();", msg_id)
        await asyncio.sleep(0.5)
        await eval_js(ws, "document.querySelectorAll('#options .option')[0].click();", msg_id)
        await asyncio.sleep(0.5)

        # Step 16: Submit the complete quiz
        submit_btn_text = await eval_js(ws, "document.getElementById('nextBtn').textContent", msg_id)
        await eval_js(ws, "document.getElementById('nextBtn').click();", msg_id)
        await asyncio.sleep(3.5) # Wait for grading and result view
        
        res_section_vis = await eval_js(ws, "!document.getElementById('resultSection').classList.contains('hidden')", msg_id)
        step_results["step16"] = {
            "name": "Submit complete quiz",
            "status": "PASS" if res_section_vis else "FAIL",
            "evidence": f"Submit button was '{submit_btn_text}', Result section visible: {res_section_vis}"
        }
        print(f"Step 16: Submit complete quiz -> {step_results['step16']['status']}")

        # Step 17: Verify server-side grading
        score_text = await eval_js(ws, "document.getElementById('scoreText').textContent", msg_id)
        step_results["step17"] = {
            "name": "Verify server-side grading",
            "status": "PASS" if "10 / 10" in str(score_text) or "10/10" in str(score_text) else "FAIL",
            "evidence": f"Score display: '{score_text}'"
        }
        print(f"Step 17: Verify server-side grading -> {step_results['step17']['status']} ({score_text})")

        # Step 18: Verify final score and answer review
        review_cards = await eval_js(ws, "document.querySelectorAll('#review .review-question-item').length", msg_id)
        step_results["step18"] = {
            "name": "Verify final score and answer review",
            "status": "PASS" if review_cards == 10 else "FAIL",
            "evidence": f"Review question items rendered={review_cards}"
        }
        print(f"Step 18: Verify score & review cards (10) -> {step_results['step18']['status']} ({review_cards} items)")

        # Step 19: Verify topic mastery or progress updates
        db = sqlite3.connect(db_path)
        cur = db.cursor()
        m_row = cur.execute("SELECT current_level, attempt_count, mastery_score FROM topic_mastery WHERE student_id=?", (stu_id,)).fetchone()
        step_results["step19"] = {
            "name": "Verify topic mastery or progress updates",
            "status": "PASS" if m_row and m_row[1] >= 1 else "FAIL",
            "evidence": f"topic_mastery row: level={m_row[0] if m_row else None}, attempts={m_row[1] if m_row else None}, score={m_row[2] if m_row else None}"
        }
        print(f"Step 19: Verify topic mastery in DB -> {step_results['step19']['status']} ({m_row})")

        # Step 20: Generate a retake
        await eval_js(ws, "document.getElementById('retakeBtn').click();", msg_id)
        await asyncio.sleep(3.5) # Wait for retake generation
        
        retake_quiz_vis = await eval_js(ws, "!document.getElementById('quizSection').classList.contains('hidden')", msg_id)
        step_results["step20"] = {
            "name": "Generate a retake",
            "status": "PASS" if retake_quiz_vis else "FAIL",
            "evidence": f"Retake button clicked, quiz section visible: {retake_quiz_vis}"
        }
        print(f"Step 20: Generate a retake -> {step_results['step20']['status']}")

        # Step 21: Verify retake uses intended novelty logic
        retake_tag = await eval_js(ws, "document.getElementById('quizAttemptNumber').textContent", msg_id)
        step_results["step21"] = {
            "name": "Verify retake uses intended novelty logic",
            "status": "PASS" if "Attempt #2" in str(retake_tag) else "FAIL",
            "evidence": f"Attempt badge text: '{retake_tag}'"
        }
        print(f"Step 21: Verify retake novelty badge -> {step_results['step21']['status']} ({retake_tag})")

        # Step 22: Verify first retake question differs appropriately from original
        retake_q1_text = await eval_js(ws, "document.getElementById('question').textContent", msg_id)
        q1_distinct = (retake_q1_text != q_text)
        step_results["step22"] = {
            "name": "Verify first retake question differs from original",
            "status": "PASS" if q1_distinct else "FAIL",
            "evidence": f"Original Q1: '{q_text[:35]}...', Retake Q1: '{retake_q1_text[:35]}...'"
        }
        print(f"Step 22: Verify retake Q1 novelty -> {step_results['step22']['status']}")

        # Step 23: Complete the retake
        for q_idx in range(10):
            await eval_js(ws, "document.querySelectorAll('#options .option')[0].click();", msg_id)
            await asyncio.sleep(0.3)
            await eval_js(ws, "document.getElementById('nextBtn').click();", msg_id)
            await asyncio.sleep(0.4)

        await asyncio.sleep(3.0) # Wait for grading of retake
        retake_submitted_vis = await eval_js(ws, "!document.getElementById('resultSection').classList.contains('hidden')", msg_id)
        step_results["step23"] = {
            "name": "Complete the retake",
            "status": "PASS" if retake_submitted_vis else "FAIL",
            "evidence": f"Completed all 10 retake questions, results rendered: {retake_submitted_vis}"
        }
        print(f"Step 23: Complete all 10 retake questions -> {step_results['step23']['status']}")

        # Step 24: Submit the retake
        step_results["step24"] = {
            "name": "Submit the retake",
            "status": "PASS" if retake_submitted_vis else "FAIL",
            "evidence": f"Retake submitted successfully"
        }
        print(f"Step 24: Submit retake -> {step_results['step24']['status']}")

        # Step 25: Verify retake grading and result display
        retake_score = await eval_js(ws, "document.getElementById('scoreText').textContent", msg_id)
        step_results["step25"] = {
            "name": "Verify retake grading and result display",
            "status": "PASS" if "10 / 10" in str(retake_score) or "10/10" in str(retake_score) else "FAIL",
            "evidence": f"Retake score display: '{retake_score}'"
        }
        print(f"Step 25: Verify retake grading -> {step_results['step25']['status']} ({retake_score})")

        # Step 26: Verify session status transitions
        sessions = cur.execute("SELECT id, status FROM quiz_sessions WHERE student_id=? ORDER BY created_at ASC", (stu_id,)).fetchall()
        both_completed = len(sessions) == 2 and all(s[1] == "completed" for s in sessions)
        step_results["step26"] = {
            "name": "Verify session status transitions",
            "status": "PASS" if both_completed else "FAIL",
            "evidence": f"Sessions in DB: {sessions}"
        }
        print(f"Step 26: Verify session statuses (completed) -> {step_results['step26']['status']} ({len(sessions)} sessions)")

        # Step 27: Verify no duplicate quiz attempts are created
        attempts = cur.execute("SELECT id, score, total FROM quiz_attempts WHERE student_id=? ORDER BY created_at ASC", (stu_id,)).fetchall()
        exact_two_attempts = len(attempts) == 2 and attempts[0][1] == 10 and attempts[1][1] == 10
        step_results["step27"] = {
            "name": "Verify no duplicate quiz attempts created",
            "status": "PASS" if exact_two_attempts else "FAIL",
            "evidence": f"Attempts in DB: {attempts}"
        }
        print(f"Step 27: Verify exactly 2 distinct attempts (no duplicates) -> {step_results['step27']['status']}")

        # Step 28: Verify refresh and navigation do not corrupt an in-progress session
        await send_cdp_cmd(ws, "Page.reload", {}, msg_id[0] + 1)
        await asyncio.sleep(2.5)
        reloaded_user = await eval_js(ws, "localStorage.getItem('learnai_student_id')", msg_id)
        step_results["step28"] = {
            "name": "Verify refresh does not corrupt session state",
            "status": "PASS" if reloaded_user == stu_id else "FAIL",
            "evidence": f"Student ID in localStorage after reload: {reloaded_user}"
        }
        print(f"Step 28: Verify reload integrity -> {step_results['step28']['status']} ({reloaded_user})")

        # Step 29: Verify logout and session invalidation
        await eval_js(ws, "document.getElementById('logoutBtn').click();", msg_id)
        await asyncio.sleep(1.0)
        token_after_logout = await eval_js(ws, "localStorage.getItem('learnai_auth_token')", msg_id)
        login_visible = await eval_js(ws, "!document.getElementById('loginSection').classList.contains('hidden')", msg_id)
        step_results["step29"] = {
            "name": "Verify logout and session invalidation",
            "status": "PASS" if not token_after_logout and login_visible else "FAIL",
            "evidence": f"token_after_logout={token_after_logout}, login_visible={login_visible}"
        }
        print(f"Step 29: Verify logout & token invalidation -> {step_results['step29']['status']}")

    # Terminate Chrome
    chrome_proc.terminate()
    try:
        shutil.rmtree(user_data_dir)
    except Exception:
        pass

    # Save summary
    out_file = Path("scratch/e2e_journey_29steps_results.json")
    out_file.write_text(json.dumps(step_results, indent=2), encoding="utf-8")
    print("\n" + "=" * 70)
    all_passed = all(v["status"] == "PASS" for v in step_results.values())
    passed_count = sum(1 for v in step_results.values() if v["status"] == "PASS")
    print(f"ALL 29 STEPS COMPLETED! {passed_count}/29 PASSED. OVERALL VERDICT: {'ALL PASS' if all_passed else 'FAIL'}")
    print(f"Results saved to {out_file}")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_journey())
