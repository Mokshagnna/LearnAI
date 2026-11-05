import asyncio
import json
import os
import subprocess
import tempfile
import time
import urllib.request
import websockets

CHROME_BIN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9222
BASE_URL = "http://127.0.0.1:8000"
ARTIFACT_DIR = r"C:\Users\moksh\.gemini\antigravity\brain\6dbc91e2-991a-4dcf-9161-cd9c9f393fb8"

class CDPClient:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = None
        self.msg_id = 0
        self.pending = {}
        self.network_requests = []
        self.events = []

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, max_size=25*1024*1024)
        asyncio.create_task(self._listen())

    async def _listen(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self.pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    method = msg["method"]
                    params = msg.get("params", {})
                    if method == "Network.requestWillBeSent":
                        req = params.get("request", {})
                        self.network_requests.append({
                            "url": req.get("url"),
                            "method": req.get("method"),
                            "timestamp": time.time()
                        })
                    self.events.append(msg)
        except Exception as e:
            pass

    async def send(self, method, params=None):
        self.msg_id += 1
        mid = self.msg_id
        payload = {"id": mid, "method": method, "params": params or {}}
        fut = asyncio.get_event_loop().create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps(payload))
        return await fut

    async def eval_js(self, expression, await_promise=False):
        res = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise
        })
        val = res.get("result", {}).get("result", {})
        return val.get("value")

    async def screenshot(self, filename):
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        b64 = res.get("result", {}).get("data")
        import base64
        data = base64.b64decode(b64)
        path = os.path.join(ARTIFACT_DIR, filename)
        with open(path, "wb") as f:
            f.write(data)
        print(f"[SCREENSHOT] Saved: {path}")
        return path

async def run_journey():
    temp_dir = tempfile.mkdtemp(prefix="chrome_cdp_")
    cmd = [
        CHROME_BIN,
        "--headless=new",
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={temp_dir}",
        "--disable-gpu",
        "--no-first-run",
        "--window-size=1280,900",
        f"{BASE_URL}/"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Launched Chrome PID {proc.pid}")

    ws_url = None
    for attempt in range(25):
        time.sleep(0.4)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
                tabs = json.loads(r.read().decode())
                if tabs and len(tabs) > 0:
                    ws_url = tabs[0].get("webSocketDebuggerUrl")
                    if ws_url:
                        break
        except Exception:
            pass

    if not ws_url:
        proc.kill()
        raise RuntimeError("Failed to obtain Chrome CDP WebSocket URL")

    print(f"Connected to CDP: {ws_url}")
    cdp = CDPClient(ws_url)
    await cdp.connect()

    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("DOM.enable")
    await cdp.send("Network.enable")

    # Wait for page load
    await asyncio.sleep(2.0)

    # Step 1: Register/Login test account
    test_email = f"student_journey_{int(time.time())}@university.edu"
    test_id = f"22JRN{int(time.time()) % 1000:03d}"
    test_pass = "JourneyPass123!"

    print(f"Registering student: {test_id} ({test_email})")
    reg_code = f"""
    (async () => {{
        try {{
            const res = await fetch('/api/auth/register', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    student_id: '{test_id}',
                    email: '{test_email}',
                    full_name: 'Journey Student',
                    password: '{test_pass}',
                    confirm_password: '{test_pass}'
                }})
            }});
            return {{ status: res.status, data: await res.json() }};
        }} catch(e) {{
            return {{ error: e.toString() }};
        }}
    }})()
    """
    reg_res = await cdp.eval_js(reg_code, await_promise=True)
    print("Registration response:", reg_res)

    # Login via UI
    login_js = f"""
    (async () => {{
        showScreen('loginSection');
        document.getElementById('loginEmail').value = '{test_email}';
        document.getElementById('loginPassword').value = '{test_pass}';
        document.getElementById('loginSubmitBtn').click();
        return true;
    }})()
    """
    await cdp.eval_js(login_js, await_promise=True)
    await asyncio.sleep(2.0)

    auth_check = await cdp.eval_js("currentUser ? currentUser.email : null")
    print(f"Logged in user email: {auth_check}")
    assert auth_check == test_email, f"Expected {test_email}, got {auth_check}"

    # Step 2: Start Quiz
    start_quiz_js = """
    (async () => {
        const course = document.getElementById('course');
        course.value = '22MT103';
        course.dispatchEvent(new Event('change'));
        await new Promise(r => setTimeout(r, 600));
        const topic = document.getElementById('topic');
        // Select first real option
        for (let i = 0; i < topic.options.length; i++) {
            if (topic.options[i].value && !topic.options[i].disabled && topic.options[i].value !== 'Select a topic') {
                topic.selectedIndex = i;
                break;
            }
        }
        topic.dispatchEvent(new Event('change'));
        await new Promise(r => setTimeout(r, 300));
        document.getElementById('generateBtn').click();
        return { course: course.value, topic: topic.value };
    })()
    """
    gen_start = await cdp.eval_js(start_quiz_js, await_promise=True)
    print("Quiz generation started with:", gen_start)

    # Wait for quiz generation
    for _ in range(30):
        await asyncio.sleep(0.5)
        is_quiz = await cdp.eval_js("document.getElementById('quizSection').classList.contains('hidden') === false")
        if is_quiz:
            break

    assert is_quiz, "Quiz did not display within 15 seconds"
    print("Quiz generated and displayed on quizSection!")

    # Step 3: Verify exit button is absent and controls are intact
    exit_btn_present = await cdp.eval_js("document.getElementById('exitQuizBtn') !== null")
    prev_btn_present = await cdp.eval_js("document.getElementById('prevBtn') !== null")
    next_btn_present = await cdp.eval_js("document.getElementById('nextBtn') !== null")
    print(f"exitQuizBtn present: {exit_btn_present} (MUST be False)")
    print(f"prevBtn present: {prev_btn_present} (MUST be True)")
    print(f"nextBtn present: {next_btn_present} (MUST be True)")
    assert not exit_btn_present, "exitQuizBtn still exists in DOM!"
    assert prev_btn_present and next_btn_present, "Navigation controls missing!"

    await cdp.screenshot("cdp_step1_active_quiz_no_exit_btn.png")

    # Step 4: Select answer on Question 1 (e.g. 2nd option, index 1)
    await cdp.eval_js("""
    const opts = document.querySelectorAll('#options .option');
    if (opts.length > 1) opts[1].click();
    """)
    await asyncio.sleep(0.3)
    q1_answer = await cdp.eval_js("answers[0]")
    print(f"Question 1 selected answer: {q1_answer}")
    assert q1_answer is not None and q1_answer != "", "Question 1 option was not recorded!"

    # Step 5: Advance to Question 2 (click Next)
    await cdp.eval_js("document.getElementById('nextBtn').click()")
    await asyncio.sleep(0.6)
    q2_num = await cdp.eval_js("currentIndex")
    print(f"Advanced to question index: {q2_num}")
    assert q2_num == 1, f"Expected index 1, got {q2_num}"

    # Select answer on Question 2 (e.g. 1st option, index 0)
    q2_click_js = """
    (async () => {
        await new Promise(r => setTimeout(r, 300));
        const opts = document.querySelectorAll('#options .option');
        if (opts.length > 0) {
            opts[0].click();
            await new Promise(r => setTimeout(r, 200));
            return answers[currentIndex];
        }
        return null;
    })()
    """
    q2_answer = await cdp.eval_js(q2_click_js, await_promise=True)
    print(f"Question 2 selected answer: {q2_answer}")
    assert q2_answer is not None and q2_answer != "", "Question 2 option was not recorded!"

    # Clear request tracker before navigation test
    cdp.network_requests.clear()

    # Step 6: Navigate to Dashboard
    print("Navigating to Dashboard...")
    await cdp.eval_js("document.getElementById('navDashboardBtn').click()")
    await asyncio.sleep(0.5)
    dash_visible = await cdp.eval_js("document.getElementById('dashboardSection').classList.contains('hidden') === false")
    assert dash_visible, "Dashboard did not become visible"
    print("Dashboard visible.")

    # Step 7: Return to Practice
    print("Returning to Practice...")
    await cdp.eval_js("document.getElementById('navPracticeBtn').click()")
    await asyncio.sleep(0.8)

    # Step 8: Verify Question 2 automatically restored!
    quiz_visible = await cdp.eval_js("document.getElementById('quizSection').classList.contains('hidden') === false")
    setup_visible = await cdp.eval_js("document.getElementById('setup').classList.contains('hidden') === false")
    restored_index = await cdp.eval_js("currentIndex")
    restored_q2_ans = await cdp.eval_js("answers[1]")
    restored_q1_ans = await cdp.eval_js("answers[0]")

    print(f"Returned from Dashboard -> quizSection visible: {quiz_visible}, setup visible: {setup_visible}")
    print(f"Restored index: {restored_index}, Q1 ans: {restored_q1_ans}, Q2 ans: {restored_q2_ans}")
    assert quiz_visible, "quizSection was not automatically restored!"
    assert not setup_visible, "setup screen was incorrectly displayed!"
    assert restored_index == 1, f"Expected index 1 (Question 2), got {restored_index}"
    assert restored_q2_ans == q2_answer, f"Answer to Q2 not preserved: {restored_q2_ans} vs {q2_answer}"
    assert restored_q1_ans == q1_answer, f"Answer to Q1 not preserved: {restored_q1_ans} vs {q1_answer}"

    # Verify no POST /api/quiz/generate occurred
    gen_requests = [r for r in cdp.network_requests if "/api/quiz/generate" in r["url"] and r["method"] == "POST"]
    print(f"POST /api/quiz/generate requests during Dashboard roundtrip: {len(gen_requests)}")
    assert len(gen_requests) == 0, "POST /api/quiz/generate was inappropriately called during navigation!"

    await cdp.screenshot("cdp_step2_dashboard_roundtrip_auto_restored.png")

    # Step 9: Navigate to Notes and return to Practice
    cdp.network_requests.clear()
    print("Navigating to Notes & Slides...")
    await cdp.eval_js("document.getElementById('navNotesBtn').click()")
    await asyncio.sleep(0.5)
    notes_visible = await cdp.eval_js("document.getElementById('notesUploadSection').classList.contains('hidden') === false")
    assert notes_visible, "Notes screen did not show"

    print("Returning to Practice from Notes...")
    await cdp.eval_js("document.getElementById('navPracticeBtn').click()")
    await asyncio.sleep(0.8)

    # Step 10: Verify same quiz and Q2
    quiz_visible = await cdp.eval_js("document.getElementById('quizSection').classList.contains('hidden') === false")
    restored_index = await cdp.eval_js("currentIndex")
    assert quiz_visible, "quizSection not restored from Notes"
    assert restored_index == 1, f"Expected index 1 from Notes, got {restored_index}"
    gen_requests = [r for r in cdp.network_requests if "/api/quiz/generate" in r["url"] and r["method"] == "POST"]
    assert len(gen_requests) == 0, "POST /api/quiz/generate called during Notes roundtrip!"
    print("Notes roundtrip verified cleanly.")

    # Step 11: Refresh page
    cdp.network_requests.clear()
    print("Refreshing page (Page.reload)...")
    await cdp.send("Page.reload")
    await asyncio.sleep(2.5)

    # Step 12: Verify auto-restored after refresh
    quiz_visible = await cdp.eval_js("document.getElementById('quizSection').classList.contains('hidden') === false")
    setup_visible = await cdp.eval_js("document.getElementById('setup').classList.contains('hidden') === false")
    restored_index = await cdp.eval_js("currentIndex")
    restored_q2_ans = await cdp.eval_js("answers[1]")
    restored_q1_ans = await cdp.eval_js("answers[0]")
    exit_btn_still_absent = await cdp.eval_js("document.getElementById('exitQuizBtn') === null")

    print(f"After refresh -> quizSection: {quiz_visible}, setup: {setup_visible}, index: {restored_index}")
    print(f"Q1 ans: {restored_q1_ans}, Q2 ans: {restored_q2_ans}, exitBtn absent: {exit_btn_still_absent}")
    assert quiz_visible, "quizSection was not automatically restored after page refresh!"
    assert not setup_visible, "setup screen displayed after page refresh!"
    assert restored_index == 1, f"Expected index 1 after refresh, got {restored_index}"
    assert restored_q2_ans == q2_answer, "Q2 answer lost after refresh!"
    assert restored_q1_ans == q1_answer, "Q1 answer lost after refresh!"
    assert exit_btn_still_absent, "exitQuizBtn reappeared after refresh!"

    gen_requests = [r for r in cdp.network_requests if "/api/quiz/generate" in r["url"] and r["method"] == "POST"]
    assert len(gen_requests) == 0, "POST /api/quiz/generate called on page refresh!"

    await cdp.screenshot("cdp_step3_refresh_auto_restored.png")

    # Step 14: Answer remaining questions and Submit
    print("Completing questions 3 through 10...")
    await cdp.eval_js("""
    (async () => {
        const total = currentQuiz.questions.length;
        for (let i = currentIndex; i < total; i++) {
            const opts = document.querySelectorAll('#options .option');
            if (opts.length > 0) opts[0].click();
            await new Promise(r => setTimeout(r, 100));
            if (i < total - 1) {
                document.getElementById('nextBtn').click();
                await new Promise(r => setTimeout(r, 100));
            }
        }
    })()
    """, await_promise=True)

    await asyncio.sleep(0.5)
    submit_btn_text = await cdp.eval_js("document.getElementById('nextBtn').textContent")
    print(f"Final question button text: '{submit_btn_text}'")
    assert submit_btn_text == "Submit Quiz", f"Expected 'Submit Quiz', got '{submit_btn_text}'"

    # Click Submit Quiz
    print("Submitting quiz...")
    await cdp.eval_js("document.getElementById('nextBtn').click()")

    # Step 15: Wait for score / results screen
    result_visible = False
    for _ in range(30):
        await asyncio.sleep(0.5)
        result_visible = await cdp.eval_js("document.getElementById('resultSection').classList.contains('hidden') === false")
        if result_visible:
            break

    score_text = await cdp.eval_js("document.getElementById('scoreText') ? document.getElementById('scoreText').textContent : ''")
    print(f"resultSection visible: {result_visible}, Score text: {score_text}")
    assert result_visible, "resultSection did not display upon submission"

    await cdp.screenshot("cdp_step4_result_screen.png")

    # Step 16: Navigate away to Dashboard and return to Practice
    cdp.network_requests.clear()
    print("Navigating to Dashboard from results...")
    await cdp.eval_js("document.getElementById('navDashboardBtn').click()")
    await asyncio.sleep(0.5)

    print("Returning to Practice from Dashboard...")
    await cdp.eval_js("document.getElementById('navPracticeBtn').click()")
    await asyncio.sleep(0.8)

    # Step 17: Verify completed result restored directly!
    result_restored = await cdp.eval_js("document.getElementById('resultSection').classList.contains('hidden') === false")
    setup_restored = await cdp.eval_js("document.getElementById('setup').classList.contains('hidden') === false")
    print(f"Result restored: {result_restored}, Setup visible: {setup_restored}")
    assert result_restored, "Completed result was not restored on returning to Practice!"
    assert not setup_restored, "Setup was unexpectedly displayed when completed result was available!"

    gen_requests = [r for r in cdp.network_requests if "/api/quiz/generate" in r["url"] and r["method"] == "POST"]
    assert len(gen_requests) == 0, "POST /api/quiz/generate called when restoring completed result!"

    await cdp.screenshot("cdp_step5_completed_result_restored.png")

    print("\n=======================================================")
    print("ALL 15 BROWSER JOURNEY VERIFICATION STEPS PASSED 100%!")
    print("=======================================================")

    await cdp.ws.close()
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()

if __name__ == "__main__":
    asyncio.run(run_journey())
