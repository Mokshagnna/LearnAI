"""
LearnAI - Comprehensive Test Suite for Recommended Review Single-Card Focus Mode
Validates:
1. Normal state: Multi-column responsive grid displaying all Recommended Review course cards.
2. Focused state: Single-card expansion occupying full container width when "View history" is clicked.
3. Other courses hidden: Non-selected courses are hidden from the Recommended Review grid during focus mode.
4. History table complete: All 5 columns (Attempt, Score, Accuracy, Level, Date) fully rendered.
5. "Hide details" toggle: Clicking "Hide details" restores all course cards in original responsive grid.
6. Course switching: Opening one course's history replaces any previously expanded course.
7. Interaction idempotency: Repeated open/hide toggles work reliably without page refresh.
8. Data immutability: Quiz attempts, scores, accuracy, and history records remain untouched.
9. Practice Topic continuity: "Practice Topic" remains fully functional in both normal and expanded states.
10. Disappearing course safe fallback: Stale expanded IDs safely reset to null when course disappears.
11. Responsive table containment: Overflow is contained within table container, preventing page overflow.
"""

import json
import subprocess
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS_PATH = REPO_ROOT / "frontend" / "js" / "app.js"
STYLE_CSS_PATH = REPO_ROOT / "frontend" / "css" / "style.css"


def run_node_dashboard_test(test_body_js: str) -> subprocess.CompletedProcess:
    """Helper to run a Node test with the full app.js environment."""
    node_harness = f"""
    const fs = require('fs');
    const appJsCode = fs.readFileSync({json.dumps(str(APP_JS_PATH))}, 'utf8');

    // DOM & Browser Mocks
    const elements = {{}};
    const elementStore = {{}};
    function createMockElement(id) {{
        if (elementStore[id]) return elementStore[id];
        const el = {{
            id,
            innerHTML: '',
            textContent: '',
            value: '',
            classList: {{
                _classes: new Set(),
                add(...cs) {{ cs.forEach(c => this._classes.add(c)); }},
                remove(...cs) {{ cs.forEach(c => this._classes.delete(c)); }},
                contains(c) {{ return this._classes.has(c); }}
            }},
            attributes: {{}},
            listeners: {{}},
            setAttribute(k, v) {{ this.attributes[k] = String(v); this[k] = v; }},
            getAttribute(k) {{ return this.attributes[k] || this[k] || null; }},
            addEventListener(ev, fn) {{
                if (!this.listeners[ev]) this.listeners[ev] = [];
                this.listeners[ev].push(fn);
            }},
            dispatchEvent(evObj) {{
                const fns = this.listeners[evObj.type] || [];
                fns.forEach(fn => fn(evObj));
            }},
            click() {{
                const ev = {{ type: 'click', preventDefault: () => {{}}, stopPropagation: () => {{}} }};
                this.dispatchEvent(ev);
            }},
            querySelectorAll(sel) {{
                const results = [];
                if (sel === '.rec-card') {{
                    const regex = /<div class="rec-card([^"]*)" id="([^"]+)">/g;
                    let m;
                    while ((m = regex.exec(this.innerHTML)) !== null) {{
                        const c = createMockElement(m[2]);
                        m[1].trim().split(/\\s+/).forEach(cls => cls && c.classList.add(cls));
                        results.push(c);
                    }}
                }}
                if (sel === '.topic-history-toggle-btn') {{
                    const regex = /<button type="button" class="([^"]*topic-history-toggle-btn[^"]*)" aria-expanded="([^"]*)" aria-controls="([^"]*)" data-course-id="([^"]*)" data-item-key="([^"]*)" data-card-id="([^"]*)"[^>]*>([^<]*)<\\/button>/g;
                    let m;
                    while ((m = regex.exec(this.innerHTML)) !== null) {{
                        const bId = 'btn-' + m[6] + '-' + (m[1].includes('btn-hide-details') ? 'hide' : 'view');
                        const b = createMockElement(bId);
                        m[1].trim().split(/\\s+/).forEach(cls => cls && b.classList.add(cls));
                        b.setAttribute('aria-expanded', m[2]);
                        b.setAttribute('aria-controls', m[3]);
                        b.setAttribute('data-course-id', m[4]);
                        b.setAttribute('data-item-key', m[5]);
                        b.setAttribute('data-card-id', m[6]);
                        b.textContent = m[7].trim();
                        results.push(b);
                    }}
                }}
                return results;
            }}
        }};
        elementStore[id] = el;
        return el;
    }}

    function $(id) {{
        if (!elements[id]) {{
            elements[id] = createMockElement(id);
        }}
        return elements[id];
    }}

    global.$ = $;
    global.document = {{
        getElementById: (id) => $(id),
        createElement: (tag) => createMockElement(tag),
        addEventListener: () => {{}},
        removeEventListener: () => {{}},
        querySelectorAll: (sel) => {{
            const grid = $('reviewTopicsList');
            return grid.querySelectorAll(sel);
        }}
    }};
    global.window = {{
        scrollTo: () => {{}},
        location: {{ hostname: 'localhost', port: '8000', protocol: 'http:' }}
    }};
    global.escapeHtml = function(str) {{
        return String(str || '').replace(/[&<>'"]/g, tag => ({{
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        }}[tag] || tag));
    }};
    global.curriculum = [];
    global.attachPracticeTopicListeners = () => {{}};

    // Evaluate core dashboard functions from app.js up to attachPracticeTopicListeners
    const fnStart = appJsCode.indexOf('function getCourseDisplayName(');
    const fnEnd = appJsCode.indexOf('function attachPracticeTopicListeners(');
    eval(appJsCode.substring(fnStart, fnEnd));

    // Test Body
    {test_body_js}
    """;
    return subprocess.run(["node", "-e", node_harness], capture_output=True, text=True)


class TestDashboardFocusedReviewDetails:
    """Test suite covering the 9 verification scenarios and edge cases."""

    SAMPLE_DASHBOARD_DATA = {
        "overview": {
            "completed_quizzes": 3,
            "total_questions": 30,
            "total_correct": 21,
            "overall_accuracy": 70.0,
            "topics_practiced": 3,
            "last_practiced_at": "2026-10-31T14:30:00Z"
        },
        "review_topics": [
            {
                "course_code": "22AI601",
                "course_name": "Deep Learning",
                "topic_name": "Recurrent Neural Networks (RNNs)",
                "priority": "high",
                "reason": "Recent accuracy 50.0% below mastery threshold.",
                "recommended_difficulty": "foundational",
                "latest_score": 5,
                "latest_total": 10,
                "latest_accuracy": 50.0,
                "previous_accuracy": 40.0,
                "score_change": 10.0,
                "change_type": "improved",
                "attempt_count": 3,
                "history": [
                    {"attempt_id": 103, "score": 5, "total": 10, "accuracy": 50.0, "recommended_difficulty": "mixed", "created_at": "2026-10-31T14:00:00Z"},
                    {"attempt_id": 102, "score": 4, "total": 10, "accuracy": 40.0, "recommended_difficulty": "mixed", "created_at": "2026-10-30T10:00:00Z"},
                    {"attempt_id": 101, "score": 3, "total": 10, "accuracy": 30.0, "recommended_difficulty": "foundational", "created_at": "2026-10-29T08:00:00Z"}
                ]
            },
            {
                "course_code": "22CS101",
                "course_name": "Programming in C",
                "topic_name": "Pointers and Memory",
                "priority": "high",
                "reason": "Recent accuracy 40.0% with active pointer misconceptions.",
                "recommended_difficulty": "foundational",
                "latest_score": 4,
                "latest_total": 10,
                "latest_accuracy": 40.0,
                "previous_accuracy": 60.0,
                "score_change": -20.0,
                "change_type": "declined",
                "attempt_count": 2,
                "history": [
                    {"attempt_id": 202, "score": 4, "total": 10, "accuracy": 40.0, "recommended_difficulty": "mixed", "created_at": "2026-10-31T11:00:00Z"},
                    {"attempt_id": 201, "score": 6, "total": 10, "accuracy": 60.0, "recommended_difficulty": "foundational", "created_at": "2026-10-28T12:00:00Z"}
                ]
            },
            {
                "course_code": "22AM201",
                "course_name": "Artificial Intelligence",
                "topic_name": "A* Search Algorithm",
                "priority": "medium",
                "reason": "Strong mastery (85.0%)! Ready to advance to advanced difficulty.",
                "recommended_difficulty": "advanced",
                "latest_score": 9,
                "latest_total": 10,
                "latest_accuracy": 90.0,
                "previous_accuracy": 80.0,
                "score_change": 10.0,
                "change_type": "improved",
                "attempt_count": 2,
                "history": [
                    {"attempt_id": 302, "score": 9, "total": 10, "accuracy": 90.0, "recommended_difficulty": "mixed", "created_at": "2026-10-31T09:00:00Z"},
                    {"attempt_id": 301, "score": 8, "total": 10, "accuracy": 80.0, "recommended_difficulty": "foundational", "created_at": "2026-10-27T16:00:00Z"}
                ]
            }
        ]
    }

    def test_case_01_initial_multi_course_grid(self):
        """Scenario 1: Three or more Recommended Review courses visible in normal responsive grid."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};
        expandedReviewCourseId = null;
        renderReturningDashboard(mockData);

        const grid = $('reviewTopicsList');
        const html = grid.innerHTML;

        // Container should not have has-focused-card class in normal state
        if (grid.classList.contains('has-focused-card')) {{
            throw new Error('FAILED: Container has-focused-card class should not be active initially');
        }}

        // All 3 courses must be present in normal mode
        if (!html.includes('Deep Learning') || !html.includes('Programming in C') || !html.includes('Artificial Intelligence')) {{
            throw new Error('FAILED: Not all 3 courses are rendered in normal multi-course grid');
        }}

        // All 3 topics must be present
        if (!html.includes('Recurrent Neural Networks (RNNs)') || !html.includes('Pointers and Memory') || !html.includes('A* Search Algorithm')) {{
            throw new Error('FAILED: Missing topic names in normal grid');
        }}

        // Buttons must say 'View history' with aria-expanded='false'
        const buttons = grid.querySelectorAll('.topic-history-toggle-btn');
        if (buttons.length !== 3) {{
            throw new Error(`FAILED: Expected 3 toggle buttons, found ${{buttons.length}}`);
        }}
        buttons.forEach(b => {{
            if (b.textContent !== 'View history') throw new Error(`FAILED: Button label is "${{b.textContent}}", expected "View history"`);
            if (b.getAttribute('aria-expanded') !== 'false') throw new Error('FAILED: Button aria-expanded must be "false"');
        }});

        // None of the cards should have rec-card-focused
        const cards = grid.querySelectorAll('.rec-card');
        cards.forEach(c => {{
            if (c.classList.contains('rec-card-focused')) throw new Error('FAILED: Card should not have rec-card-focused in normal state');
        }});

        console.log('SCENARIO_1_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 1 failed: {res.stderr}"
        assert "SCENARIO_1_PASSED" in res.stdout

    def test_case_02_focused_single_card_expansion(self):
        """Scenario 2: Click View history on the first course: only that course remains, expanded to full width."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};
        expandedReviewCourseId = null;
        renderReturningDashboard(mockData);

        // Click 'View history' on the first course (22AI601)
        expandedReviewCourseId = '22AI601';
        renderReviewTopics(mockData.review_topics, mockData);

        const grid = $('reviewTopicsList');
        const html = grid.innerHTML;

        // Container must have has-focused-card class
        if (!grid.classList.contains('has-focused-card')) {{
            throw new Error('FAILED: Container missing has-focused-card class in focused mode');
        }}

        // Selected course card must be present with rec-card-focused
        if (!html.includes('Deep Learning') || !html.includes('Recurrent Neural Networks (RNNs)')) {{
            throw new Error('FAILED: Expanded course Deep Learning not found in focused view');
        }}
        if (!html.includes('rec-card-focused')) {{
            throw new Error('FAILED: Focused card missing rec-card-focused class');
        }}

        // Other course cards must be HIDDEN from the grid
        if (html.includes('Programming in C') || html.includes('Pointers and Memory')) {{
            throw new Error('FAILED: Non-focused course "Programming in C" is still rendered in the grid');
        }}
        if (html.includes('Artificial Intelligence') || html.includes('A* Search Algorithm')) {{
            throw new Error('FAILED: Non-focused course "Artificial Intelligence" is still rendered in the grid');
        }}

        // Button label must change from 'View history' to 'Hide details'
        const buttons = grid.querySelectorAll('.topic-history-toggle-btn');
        const mainBtn = buttons.find(b => b.classList.contains('btn-hide-details'));
        if (!mainBtn) {{
            throw new Error('FAILED: Missing "btn-hide-details" button in focused mode');
        }}
        if (mainBtn.textContent !== 'Hide details') {{
            throw new Error(`FAILED: Expected "Hide details", found "${{mainBtn.textContent}}"`);
        }}
        if (mainBtn.getAttribute('aria-expanded') !== 'true') {{
            throw new Error('FAILED: Button aria-expanded must be "true" in focused mode');
        }}

        console.log('SCENARIO_2_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 2 failed: {res.stderr}"
        assert "SCENARIO_2_PASSED" in res.stdout

    def test_case_03_desktop_table_columns_presence(self):
        """Scenario 3: Verify every history-table column is visible: Attempt, Score, Accuracy, Level, Date."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};
        expandedReviewCourseId = '22AI601';
        renderReviewTopics(mockData.review_topics, mockData);

        const html = $('reviewTopicsList').innerHTML;

        // Check table headers
        const requiredHeaders = ['Attempt', 'Score', 'Accuracy', 'Level', 'Date'];
        requiredHeaders.forEach(hdr => {{
            if (!html.includes(`<th scope="col">${{hdr}}</th>`)) {{
                throw new Error(`FAILED: History table missing column header: ${{hdr}}`);
            }}
        }});

        // Check history rows data from 22AI601 (3 attempts)
        if (!html.includes('5/10') || !html.includes('50%')) {{
            throw new Error('FAILED: Latest attempt row (5/10, 50%) not rendered properly');
        }}
        if (!html.includes('4/10') || !html.includes('40%')) {{
            throw new Error('FAILED: Previous attempt row (4/10, 40%) not rendered properly');
        }}
        if (!html.includes('3/10') || !html.includes('30%')) {{
            throw new Error('FAILED: Earliest attempt row (3/10, 30%) not rendered properly');
        }}

        // History panel must be visible (no 'hidden' class and aria-hidden='false')
        if (html.includes('topic-history-panel hidden')) {{
            throw new Error('FAILED: History panel still has "hidden" class in focused mode');
        }}
        if (!html.includes('aria-hidden="false"')) {{
            throw new Error('FAILED: History panel aria-hidden is not "false" in focused mode');
        }}

        console.log('SCENARIO_3_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 3 failed: {res.stderr}"
        assert "SCENARIO_3_PASSED" in res.stdout

    def test_case_04_hide_details_restores_all_cards(self):
        """Scenario 4: Click Hide details: all courses return in their original grid."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};

        // 1. Enter focus mode
        expandedReviewCourseId = '22AI601';
        renderReviewTopics(mockData.review_topics, mockData);

        // 2. Click 'Hide details'
        expandedReviewCourseId = null;
        renderReviewTopics(mockData.review_topics, mockData);

        const grid = $('reviewTopicsList');
        const html = grid.innerHTML;

        // Grid must not have has-focused-card
        if (grid.classList.contains('has-focused-card')) {{
            throw new Error('FAILED: Container still has has-focused-card after Hide details');
        }}

        // All 3 courses must be back
        if (!html.includes('Deep Learning') || !html.includes('Programming in C') || !html.includes('Artificial Intelligence')) {{
            throw new Error('FAILED: Not all 3 courses restored after Hide details');
        }}

        // All cards must be in unexpanded state
        if (html.includes('rec-card-focused')) {{
            throw new Error('FAILED: Found lingering rec-card-focused after Hide details');
        }}
        if (html.includes('Hide details')) {{
            throw new Error('FAILED: Found lingering "Hide details" button after clearing focus');
        }}

        console.log('SCENARIO_4_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 4 failed: {res.stderr}"
        assert "SCENARIO_4_PASSED" in res.stdout

    def test_case_05_sequential_course_switching(self):
        """Scenario 5: Open the second course: only the second course is displayed."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};

        // 1. Expand Course 1
        expandedReviewCourseId = '22AI601';
        renderReviewTopics(mockData.review_topics, mockData);

        // 2. Switch directly to Course 2 (22CS101)
        expandedReviewCourseId = '22CS101';
        renderReviewTopics(mockData.review_topics, mockData);

        const html = $('reviewTopicsList').innerHTML;

        // Only Course 2 should be displayed
        if (!html.includes('Programming in C') || !html.includes('Pointers and Memory')) {{
            throw new Error('FAILED: Course 2 (Programming in C) not visible in focused mode');
        }}

        // Course 1 and Course 3 must be hidden
        if (html.includes('Deep Learning') || html.includes('Recurrent Neural Networks')) {{
            throw new Error('FAILED: Course 1 (Deep Learning) leaked into Course 2 focused view');
        }}
        if (html.includes('Artificial Intelligence') || html.includes('A* Search Algorithm')) {{
            throw new Error('FAILED: Course 3 (Artificial Intelligence) leaked into Course 2 focused view');
        }}

        // History rows for Course 2 (4/10 and 6/10) must be present
        if (!html.includes('4/10') || !html.includes('6/10')) {{
            throw new Error('FAILED: Course 2 history records (4/10, 6/10) missing from table');
        }}

        console.log('SCENARIO_5_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 5 failed: {res.stderr}"
        assert "SCENARIO_5_PASSED" in res.stdout

    def test_case_06_repeat_toggle_idempotency(self):
        """Scenario 6: Repeat the open/hide interaction several times."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};

        // Cycle 1: Open Course 1 -> Hide
        expandedReviewCourseId = '22AI601';
        renderReviewTopics(mockData.review_topics, mockData);
        expandedReviewCourseId = null;
        renderReviewTopics(mockData.review_topics, mockData);

        // Cycle 2: Open Course 2 -> Hide
        expandedReviewCourseId = '22CS101';
        renderReviewTopics(mockData.review_topics, mockData);
        expandedReviewCourseId = null;
        renderReviewTopics(mockData.review_topics, mockData);

        // Cycle 3: Open Course 3 -> Hide
        expandedReviewCourseId = '22AM201';
        renderReviewTopics(mockData.review_topics, mockData);
        expandedReviewCourseId = null;
        renderReviewTopics(mockData.review_topics, mockData);

        // Cycle 4: Re-open Course 1
        expandedReviewCourseId = '22AI601';
        renderReviewTopics(mockData.review_topics, mockData);

        const html = $('reviewTopicsList').innerHTML;
        if (!html.includes('Deep Learning') || html.includes('Programming in C') || html.includes('Artificial Intelligence')) {{
            throw new Error('FAILED: State corrupted after multiple cycles');
        }}

        console.log('SCENARIO_6_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 6 failed: {res.stderr}"
        assert "SCENARIO_6_PASSED" in res.stdout

    def test_case_07_data_immutability_during_toggles(self):
        """Scenario 9: Verify attempt counts, scores, accuracy, and history records have not changed."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};

        // Capture initial JSON snapshot
        const initialJson = JSON.stringify(mockData);

        // Perform multiple view switches
        expandedReviewCourseId = '22AI601';
        renderReviewTopics(mockData.review_topics, mockData);

        expandedReviewCourseId = null;
        renderReviewTopics(mockData.review_topics, mockData);

        expandedReviewCourseId = '22CS101';
        renderReviewTopics(mockData.review_topics, mockData);

        // Ensure mockData has NOT been modified or mutated in place
        const finalJson = JSON.stringify(mockData);
        if (initialJson !== finalJson) {{
            throw new Error('FAILED: Dashboard data was mutated during view switching');
        }}

        console.log('SCENARIO_7_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 7 failed: {res.stderr}"
        assert "SCENARIO_7_PASSED" in res.stdout

    def test_case_08_practice_topic_preservation(self):
        """Scenario 8: Verify Practice Topic still works in both normal and focused modes."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};

        // Normal mode
        expandedReviewCourseId = null;
        renderReviewTopics(mockData.review_topics, mockData);
        let html = $('reviewTopicsList').innerHTML;
        if (!html.includes('data-course="22AI601"') || !html.includes('data-topic="Recurrent Neural Networks (RNNs)"')) {{
            throw new Error('FAILED: Practice button missing attributes in normal mode');
        }}

        // Focused mode
        expandedReviewCourseId = '22AI601';
        renderReviewTopics(mockData.review_topics, mockData);
        html = $('reviewTopicsList').innerHTML;
        if (!html.includes('data-course="22AI601"') || !html.includes('data-topic="Recurrent Neural Networks (RNNs)"')) {{
            throw new Error('FAILED: Practice button missing attributes in focused mode');
        }}

        console.log('SCENARIO_8_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 8 failed: {res.stderr}"
        assert "SCENARIO_8_PASSED" in res.stdout

    def test_case_09_disappearing_course_graceful_clear(self):
        """Edge Case: If expanded course disappears from refreshed data, safely clear selection."""
        script = f"""
        const mockData = {json.dumps(self.SAMPLE_DASHBOARD_DATA)};

        // User had Course 22AI601 expanded
        expandedReviewCourseId = '22AI601';

        // Data refresh happens where 22AI601 is no longer in review topics (e.g., student mastered it)
        const updatedTopics = mockData.review_topics.filter(r => r.course_code !== '22AI601');
        renderReviewTopics(updatedTopics, mockData);

        // expandedReviewCourseId must be safely cleared to null
        if (expandedReviewCourseId !== null) {{
            throw new Error(`FAILED: Expected expandedReviewCourseId to be cleared to null, got "${{expandedReviewCourseId}}"`);
        }}

        const html = $('reviewTopicsList').innerHTML;
        // Remaining 2 courses must be displayed normally
        if (!html.includes('Programming in C') || !html.includes('Artificial Intelligence')) {{
            throw new Error('FAILED: Remaining courses not displayed after clearing disappeared course');
        }}

        console.log('SCENARIO_9_PASSED');
        """
        res = run_node_dashboard_test(script)
        assert res.returncode == 0, f"Scenario 9 failed: {res.stderr}"
        assert "SCENARIO_9_PASSED" in res.stdout

    def test_case_10_css_layout_and_overflow_containment(self):
        """Scenario 7 & Layout: Validate that CSS rules enforce full grid width and internal overflow scrolling."""
        css_content = STYLE_CSS_PATH.read_text(encoding="utf-8")

        # 1. Container full width on focused mode
        assert ".recommendations-grid.has-focused-card" in css_content
        assert "grid-template-columns: 1fr;" in css_content

        # 2. Focused card occupies 1 / -1
        assert ".rec-card.rec-card-focused" in css_content
        assert "grid-column: 1 / -1;" in css_content
        assert "width: 100%;" in css_content

        # 3. History table container handles horizontal overflow internally
        assert ".history-table-container" in css_content
        assert "overflow-x: auto;" in css_content
        assert "-webkit-overflow-scrolling: touch;" in css_content

        # 4. Hide details button styling
        assert ".topic-history-toggle-btn.btn-hide-details" in css_content
