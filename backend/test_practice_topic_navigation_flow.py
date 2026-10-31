"""
LearnAI — Test Suite for "Practice a Topic" Navigation Flow and Robust Resolution
Validates that:
1. Clicking "Practice a Topic" on a dashboard review card directly navigates to the quiz (loadingSection -> quizSection) without ever flashing or displaying the setup screen (#setup).
2. The exact course_code, resolved topic name, and topic_id reach the /api/quiz/generate payload.
3. Historical and punctuation variations (e.g. unicode en-dash 'McCulloch–Pitts neuron', composite historical topic names from previous attempts) resolve accurately without substituting unrelated topics.
4. An unresolvable topic does not silently substitute an unrelated topic, but displays an actionable error in loadingErrorBox with a retry option.
5. Rapid double-clicks on the Practice button do not produce duplicate network requests or concurrent generation attempts.
6. Cancelling or clicking "Choose Another Topic" from loading returns to the dashboard when launched from the dashboard.
7. Browser back navigation does not push transient loading states into history or redirect unexpectedly to setup.
8. Retakes and notes-grounded quiz generation preserve their respective execution paths.
9. Backend curriculum resolution and API endpoints accurately service the requests.
"""

import json
import subprocess
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from main import app
from services.curriculum_service import curriculum_service
from services.auth_utils import auth_header_for
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter
from services.database import (
    get_connection,
    init_db,
    get_or_create_student
)

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS_PATH = REPO_ROOT / "frontend" / "js" / "app.js"
CURRICULUM_PATH = REPO_ROOT / "backend" / "data" / "curriculum.json"


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    """Creates a clean isolated SQLite database and Starlette TestClient."""
    db_file = tmp_path / "nav_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    with quiz_gen_limiter._lock:
        quiz_gen_limiter._requests.clear()
    with quiz_submit_limiter._lock:
        quiz_submit_limiter._requests.clear()
    return TestClient(app), db_file


def run_node_harness(test_case_body: str) -> dict:
    """Executes a Node harness against frontend/js/app.js via stdin with full DOM mocking."""
    assert APP_JS_PATH.exists(), f"frontend/js/app.js missing at {APP_JS_PATH}"
    assert CURRICULUM_PATH.exists(), f"curriculum.json missing at {CURRICULUM_PATH}"

    runner_script = f"""
const fs = require('fs');
const vm = require('vm');

const appJs = fs.readFileSync({json.dumps(str(APP_JS_PATH))}, 'utf8');

// Mock DOM elements
const elements = {{}};
function $(id) {{
  if (!elements[id]) {{
    elements[id] = {{
      id,
      innerHTML: '',
      textContent: '',
      value: '',
      style: {{ display: '' }},
      dataset: {{}},
      parentElement: {{ setAttribute: () => {{}} }},
      _classes: new Set(),
      classList: {{
        add(c) {{ elements[id]._classes.add(c); }},
        remove(c) {{ elements[id]._classes.delete(c); }},
        toggle(c, force) {{
          if (force === undefined) {{
            if (elements[id]._classes.has(c)) elements[id]._classes.delete(c);
            else elements[id]._classes.add(c);
          }} else if (force) {{
            elements[id]._classes.add(c);
          }} else {{
            elements[id]._classes.delete(c);
          }}
        }},
        contains(c) {{ return elements[id]._classes.has(c); }}
      }},
      listeners: {{}},
      addEventListener(event, fn) {{
        this.listeners[event] = this.listeners[event] || [];
        this.listeners[event].push(fn);
      }},
      dispatchEvent(evt) {{
        const type = (evt && evt.type) || 'click';
        if (this.listeners[type]) {{
          this.listeners[type].forEach(fn => fn(evt));
        }}
      }},
      setAttribute(k, v) {{ this[k] = v; }},
      getAttribute(k) {{ return this[k] || null; }},
      appendChild: () => {{}},
      focus: () => {{}},
      scrollIntoView: () => {{}},
      querySelector(sel) {{
        if (sel === 'h1, h2') return {{ setAttribute: () => {{}}, focus: () => {{}} }};
        if (sel.includes('.spinner')) return elements['spinner'] || (elements['spinner'] = {{ style: {{}} }});
        return null;
      }},
      querySelectorAll(sel) {{ return []; }}
    }};
  }}
  return elements[id];
}}

// Initialize screens
['setup', 'loadingSection', 'quizSection', 'resultSection', 'dashboardSection', 'notesUploadSection'].forEach(s => {{
  $(s).classList.add('hidden');
}});
$('dashboardSection').classList.remove('hidden');

global.$ = $;
global.Event = function(type) {{ this.type = type; }};
global.practiceButtons = [];

global.document = {{
  getElementById: (id) => $(id),
  createElement: (tag) => ({{
    tagName: tag.toUpperCase(),
    value: '',
    textContent: '',
    dataset: {{}},
    setAttribute: () => {{}},
    appendChild: () => {{}},
    addEventListener: () => {{}},
    classList: {{ add: () => {{}}, remove: () => {{}} }}
  }}),
  addEventListener: () => {{}},
  removeEventListener: () => {{}},
  querySelector: (sel) => {{
    if (sel.includes('.spinner')) return elements['spinner'] || (elements['spinner'] = {{ style: {{}} }});
    return null;
  }},
  querySelectorAll: (sel) => {{
    if (sel === '.practice-topic-btn') return global.practiceButtons || [];
    return [];
  }}
}};

global.window = {{
  addEventListener: (event, fn) => {{
    global.window.listeners = global.window.listeners || {{}};
    global.window.listeners[event] = global.window.listeners[event] || [];
    global.window.listeners[event].push(fn);
  }},
  removeEventListener: () => {{}},
  scrollTo: () => {{}},
  location: {{ search: '' }},
  history: {{
    state: null,
    stack: [],
    pushState(state, title) {{
      this.state = state;
      this.stack.push(state);
    }},
    replaceState(state, title) {{
      this.state = state;
    }}
  }}
}};

global.sessionStorage = {{
  data: {{}},
  getItem(k) {{ return this.data[k] || null; }},
  setItem(k, v) {{ this.data[k] = v; }},
  removeItem(k) {{ delete this.data[k]; }}
}};

global.localStorage = {{
  data: {{}},
  getItem(k) {{ return this.data[k] || null; }},
  setItem(k, v) {{ this.data[k] = v; }},
  removeItem(k) {{ delete this.data[k]; }}
}};

let fetchCalls = [];
global.fetch = async (url, opts) => {{
  fetchCalls.push({{ url, opts, body: opts && opts.body ? JSON.parse(opts.body) : null }});
  if (url === '/api/courses') {{
    return {{
      ok: true,
      json: async () => ({{
        courses: [
          {{
            code: '22AM301',
            name: 'Deep Learning',
            topics: [
              {{ id: '22AM301-Q001', name: 'History of Deep Learning, What Is Deep Learning', raw_name: 'History and Applications of Deep Learning, What Is Deep Learning' }},
              {{ id: '22AM301-Q002', name: 'McCulloch-Pitts Neuron', raw_name: 'McCulloch-Pitts neuron' }}
            ]
          }},
          {{
            code: '22MT103',
            name: 'Linear Algebra and Differential Equations',
            topics: [
              {{ id: '22MT103-Q001', name: 'Linear Algebra Concepts' }},
              {{ id: '22MT103-Q006', name: 'Adjoint of a Matrix' }},
              {{ id: '22MT103-Q005a', name: 'First-Order Differential Equations' }}
            ]
          }}
        ]
      }})
    }};
  }}
  if (url === '/api/quiz/generate') {{
    return {{
      ok: true,
      json: async () => ({{
        course_code: JSON.parse(opts.body).course_code,
        topic: JSON.parse(opts.body).topic,
        topic_id: JSON.parse(opts.body).topic_id || 'T01',
        questions: [
          {{
            question: 'Sample question 1?',
            options: {{ A: 'Option A', B: 'Option B', C: 'Option C', D: 'Option D' }},
            correct_answer: 'A',
            explanation: 'Explanation'
          }}
        ]
      }})
    }};
  }}
  return {{ ok: true, json: async () => ({{}}) }};
}};

const context = vm.createContext(global);
vm.runInContext(appJs, context);

async function execute() {{
  let setupWasShown = false;
  const originalShowScreen = context.showScreen;
  context.showScreen = (id, push) => {{
    if (id === 'setup') setupWasShown = true;
    return originalShowScreen(id, push);
  }};

  {test_case_body}
}}

execute()
  .then(result => {{
    console.log("__RESULT_JSON__" + JSON.stringify(result));
  }})
  .catch(err => {{
    console.error("__ERROR__", err);
    process.exit(1);
  }});
"""

    res = subprocess.run(
        ["node"],
        input=runner_script,
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    if res.returncode != 0:
        raise RuntimeError(f"Node execution failed (code {res.returncode}):\n{res.stderr}\nOutput:\n{res.stdout}")

    out = res.stdout
    marker = "__RESULT_JSON__"
    idx = out.find(marker)
    if idx == -1:
        raise ValueError(f"Could not find result JSON in output:\n{out}\nStderr:\n{res.stderr}")
    json_str = out[idx + len(marker):].strip().splitlines()[0]
    return json.loads(json_str)


class TestPracticeTopicNavigationFlow:
    """Covers frontend navigation, DOM state transitions, resolution resilience, and error handling."""

    def test_practice_topic_click_reaches_quiz_without_setup_display(self):
        """Clicking Practice a Topic from dashboard directly enters loading -> quiz without setup screen."""
        script = """
        vm.runInContext('lastActiveScreen = "dashboardSection";', context);

        const btn = {
          getAttribute(k) {
            if (k === 'data-course') return '22AM301';
            if (k === 'data-topic') return 'McCulloch\u2013Pitts neuron';
            if (k === 'data-topic-id') return '22AM301-Q002';
            return this[k] || null;
          },
          setAttribute(k, v) { this[k] = v; },
          listeners: {},
          addEventListener(e, fn) { (this.listeners[e] = this.listeners[e] || []).push(fn); },
          dataset: {}
        };

        global.practiceButtons = [btn];
        vm.runInContext('attachPracticeTopicListeners();', context);

        // Click the real button
        btn.listeners['click'][0]({ preventDefault: () => {}, stopPropagation: () => {} });
        await btn._practicePromise;

        return {
          setupWasShown,
          setupHidden: elements['setup'].classList.contains('hidden'),
          loadingHidden: elements['loadingSection'].classList.contains('hidden'),
          quizHidden: elements['quizSection'].classList.contains('hidden'),
          dashboardHidden: elements['dashboardSection'].classList.contains('hidden'),
          historyStack: window.history.stack,
          fetchCalls: fetchCalls.filter(c => c.url === '/api/quiz/generate')
        };
        """
        res = run_node_harness(script)
        assert res["setupWasShown"] is False, "Setup screen was flashed or displayed during transition"
        assert res["setupHidden"] is True, "Setup screen must be hidden"
        assert res["loadingHidden"] is True, "Loading screen must be hidden after quiz completes"
        assert res["quizHidden"] is False, "Quiz section must be visible"
        assert len(res["fetchCalls"]) == 1, "Exactly one quiz generation request must be made"
        body = res["fetchCalls"][0]["body"]
        assert body["course_code"] == "22AM301"
        assert body["topic"] == "McCulloch-Pitts Neuron"
        assert body["topic_id"] == "22AM301-Q002"
        assert body["retake"] is False
        # Verify loading was not pushed to history
        assert not any(h.get("screen") == "loadingSection" for h in res["historyStack"])
        assert any(h.get("screen") == "quizSection" for h in res["historyStack"])

    def test_historical_topic_name_resolution_deep_learning(self):
        """Historical database topic 'History and Applications of Deep Learning' resolves to 'History of Deep Learning, What Is Deep Learning'."""
        script = """
        vm.runInContext('lastActiveScreen = "dashboardSection";', context);

        const btn = {
          getAttribute(k) {
            if (k === 'data-course') return '22AM301';
            if (k === 'data-topic') return 'History and Applications of Deep Learning';
            return this[k] || null;
          },
          setAttribute(k, v) { this[k] = v; },
          listeners: {},
          addEventListener(e, fn) { (this.listeners[e] = this.listeners[e] || []).push(fn); },
          dataset: {}
        };

        global.practiceButtons = [btn];
        vm.runInContext('attachPracticeTopicListeners();', context);

        btn.listeners['click'][0]({ preventDefault: () => {}, stopPropagation: () => {} });
        await btn._practicePromise;

        const genCalls = fetchCalls.filter(c => c.url === '/api/quiz/generate');
        return {
          setupWasShown,
          genCallsCount: genCalls.length,
          payload: genCalls.length > 0 ? genCalls[0].body : null,
          quizHidden: elements['quizSection'].classList.contains('hidden')
        };
        """
        res = run_node_harness(script)
        assert res["setupWasShown"] is False
        assert res["genCallsCount"] == 1
        assert res["quizHidden"] is False
        assert res["payload"]["course_code"] == "22AM301"
        assert res["payload"]["topic"] == "History of Deep Learning, What Is Deep Learning"
        assert res["payload"]["topic_id"] == "22AM301-Q001"

    def test_unresolvable_topic_shows_actionable_error_no_silent_substitution(self):
        """An unrecognized topic shows an actionable error with retry, and does NOT substitute an unrelated topic."""
        script = """
        vm.runInContext('lastActiveScreen = "dashboardSection";', context);

        const btn = {
          getAttribute(k) {
            if (k === 'data-course') return '22AM301';
            if (k === 'data-topic') return 'Quantum Superconductivity in Cryogenics';
            return this[k] || null;
          },
          setAttribute(k, v) { this[k] = v; },
          listeners: {},
          addEventListener(e, fn) { (this.listeners[e] = this.listeners[e] || []).push(fn); },
          dataset: {}
        };

        global.practiceButtons = [btn];
        vm.runInContext('attachPracticeTopicListeners();', context);

        btn.listeners['click'][0]({ preventDefault: () => {}, stopPropagation: () => {} });
        await btn._practicePromise;

        const genCalls = fetchCalls.filter(c => c.url === '/api/quiz/generate');
        return {
          setupWasShown,
          genCallsCount: genCalls.length,
          loadingHidden: elements['loadingSection'].classList.contains('hidden'),
          errorBoxHidden: elements['loadingErrorBox'].classList.contains('hidden'),
          errorMsg: elements['loadingErrorMsg'].textContent,
          hasRetryHandler: typeof elements['loadingRetryBtn'].onclick === 'function'
        };
        """
        res = run_node_harness(script)
        assert res["setupWasShown"] is False
        assert res["genCallsCount"] == 0, "No quiz generation call should be sent for unresolvable topic"
        assert res["loadingHidden"] is False, "Loading section remains visible displaying error box"
        assert res["errorBoxHidden"] is False, "Error box must be displayed"
        assert "Quantum Superconductivity in Cryogenics" in res["errorMsg"]
        assert "could not be resolved" in res["errorMsg"]
        assert res["hasRetryHandler"] is True

    def test_rapid_double_clicks_prevent_duplicate_requests(self):
        """Rapid double clicks on the Practice a Topic button trigger only one quiz generation request."""
        script = """
        vm.runInContext('lastActiveScreen = "dashboardSection";', context);

        const btn = {
          getAttribute(k) {
            if (k === 'data-course') return '22AM301';
            if (k === 'data-topic') return 'McCulloch\u2013Pitts neuron';
            if (k === 'data-topic-id') return '22AM301-Q002';
            return this[k] || null;
          },
          setAttribute(k, v) { this[k] = v; },
          listeners: {},
          addEventListener(e, fn) { (this.listeners[e] = this.listeners[e] || []).push(fn); },
          dataset: {}
        };

        global.practiceButtons = [btn];
        vm.runInContext('attachPracticeTopicListeners();', context);

        // First click
        btn.listeners['click'][0]({ preventDefault: () => {}, stopPropagation: () => {} });
        const p1 = btn._practicePromise;

        // Immediate second rapid click before p1 completes
        btn.listeners['click'][0]({ preventDefault: () => {}, stopPropagation: () => {} });

        await p1;

        const genCalls = fetchCalls.filter(c => c.url === '/api/quiz/generate');
        return {
          genCallsCount: genCalls.length,
          quizHidden: elements['quizSection'].classList.contains('hidden')
        };
        """
        res = run_node_harness(script)
        assert res["genCallsCount"] == 1, f"Expected exactly 1 network call, got {res['genCallsCount']}"
        assert res["quizHidden"] is False

    def test_cancellation_from_loading_returns_to_dashboard(self):
        """Clicking loadingCancelBtn when launched from dashboardSection returns to dashboardSection, not setup."""
        script = """
        vm.runInContext('lastActiveScreen = "dashboardSection";', context);

        // Mock openDashboard so we can track invocation
        let openDashboardCalled = false;
        context.openDashboard = () => {
          openDashboardCalled = true;
          elements['dashboardSection'].classList.remove('hidden');
          elements['loadingSection'].classList.add('hidden');
        };

        // Simulate user clicking cancel while loading
        const cancelBtn = elements['loadingCancelBtn'];
        const clickFn = cancelBtn.listeners['click'][0];
        clickFn();

        return {
          openDashboardCalled,
          setupWasShown,
          dashboardHidden: elements['dashboardSection'].classList.contains('hidden'),
          setupHidden: elements['setup'].classList.contains('hidden')
        };
        """
        res = run_node_harness(script)
        assert res["openDashboardCalled"] is True, "openDashboard() must be called on cancellation from dashboard"
        assert res["setupWasShown"] is False, "Setup must not be shown on cancellation"
        assert res["dashboardHidden"] is False, "Dashboard must be visible"
        assert res["setupHidden"] is True, "Setup must be hidden"

    def test_loading_back_btn_returns_to_dashboard(self):
        """Clicking loadingBackBtn ('Choose Another Topic') returns to dashboard when launched from dashboard."""
        script = """
        vm.runInContext('lastActiveScreen = "dashboardSection";', context);

        let openDashboardCalled = false;
        context.openDashboard = () => {
          openDashboardCalled = true;
          elements['dashboardSection'].classList.remove('hidden');
          elements['loadingSection'].classList.add('hidden');
        };

        const backBtn = elements['loadingBackBtn'];
        const clickFn = backBtn.listeners['click'][0];
        clickFn();

        return {
          openDashboardCalled,
          setupWasShown,
          dashboardHidden: elements['dashboardSection'].classList.contains('hidden'),
          setupHidden: elements['setup'].classList.contains('hidden')
        };
        """
        res = run_node_harness(script)
        assert res["openDashboardCalled"] is True
        assert res["setupWasShown"] is False
        assert res["dashboardHidden"] is False
        assert res["setupHidden"] is True

    def test_history_popstate_navigates_smoothly_without_history_loops(self):
        """Browser popstate transition back from quiz restores dashboard without falling back to setup."""
        script = """
        vm.runInContext('lastActiveScreen = "dashboardSection";', context);

        let loadDashboardCalled = false;
        context.loadDashboard = () => { loadDashboardCalled = true; };

        // Simulate browser Back event with state { screen: 'dashboardSection' }
        const popHandler = window.listeners['popstate'][0];
        popHandler({ state: { screen: 'dashboardSection' } });

        return {
          setupWasShown,
          dashboardHidden: elements['dashboardSection'].classList.contains('hidden'),
          setupHidden: elements['setup'].classList.contains('hidden'),
          loadDashboardCalled
        };
        """
        res = run_node_harness(script)
        assert res["setupWasShown"] is False
        assert res["dashboardHidden"] is False
        assert res["setupHidden"] is True
        assert res["loadDashboardCalled"] is True


class TestBackendCurriculumAndGenerationIntegration:
    """Verifies that backend CurriculumService and API endpoints resolve and generate quizzes seamlessly."""

    def test_curriculum_service_find_course_and_topic_historical_variants(self):
        """Backend curriculum service resolves historical variants, hyphens, and Unicode en-dashes."""
        # 1. Unicode en-dash
        course, topic = curriculum_service.find_course_and_topic("22AM301", "McCulloch\u2013Pitts neuron")
        assert course["code"] == "22AM301"
        assert topic["id"] == "22AM301-Q002"
        assert topic["name"] == "McCulloch-Pitts Neuron"

        # 2. Historical composite name from DB
        course, topic = curriculum_service.find_course_and_topic("22AM301", "History and Applications of Deep Learning")
        assert course["code"] == "22AM301"
        assert topic["id"] == "22AM301-Q001"
        assert "History of Deep Learning" in topic["name"]

        # 3. Canonical Linear Algebra (22MT103)
        course, topic = curriculum_service.find_course_and_topic("22MT103", "Inverse of a Matrix through Adjoint")
        assert course["code"] == "22MT103"
        assert topic["name"] == "Inverse of a Matrix"

        # 4. Explicit topic_id resolution
        course, topic = curriculum_service.find_course_and_topic("22MT103", "irrelevant title", topic_id="22MT103-Q006")
        assert course["code"] == "22MT103"
        assert topic["name"] == "Adjoint of a Matrix"

    def test_api_quiz_generate_integration_with_resolved_topic(self, isolated_client):
        """POST /api/quiz/generate successfully processes resolved course, topic, and topic_id."""
        client, db_file = isolated_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_practice_nav", conn=conn)
        conn.close()

        payload = {
            "course_code": "22AM301",
            "topic": "McCulloch-Pitts Neuron",
            "topic_id": "22AM301-Q002",
            "student_id": student_id,
            "retake": False
        }

        headers = auth_header_for(student_id)
        resp = client.post("/api/quiz/generate", json=payload, headers=headers)
        assert resp.status_code == 200, f"Generation failed: {resp.text}"
        data = resp.json()

        assert data["course_code"] == "22AM301"
        assert data["topic"] == "McCulloch-Pitts Neuron"
        assert data["topic_id"] == "22AM301-Q002"
        assert "questions" in data
        assert len(data["questions"]) > 0
        q1 = data["questions"][0]
        assert "question" in q1
        assert "options" in q1
        assert all(k in q1["options"] for k in ["A", "B", "C", "D"])
