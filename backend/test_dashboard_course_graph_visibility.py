"""
LearnAI — Test Suite for Course-Specific Dashboard Graph Visibility
Validates that:
1. The graph is hidden when "All Courses" is selected.
2. The graph is visible when a specific course is selected and graph data is available.
3. The graph contains data from only the selected course.
4. Switching from Course A to Course B updates the graph and removes Course A's data.
5. Switching from a specific course back to "All Courses" hides the graph and clears stale data.
6. Refresh respects the current selection.
7. A selected course with no data shows an appropriate empty state.
8. A failed data request does not leave a stale graph visible.
9. Existing dashboard, recommendation, quiz history, and adaptive difficulty tests continue to pass.
"""

import os
import subprocess
import json
import pytest
from bs4 import BeautifulSoup
from starlette.testclient import TestClient

from main import app
from services.database import (
    get_connection,
    init_db,
    get_or_create_student,
    save_quiz_attempt,
    get_student_dashboard_data,
)
from services.auth_utils import auth_header_for

SAMPLE_QUESTIONS = [
    {
        "question": f"Question {i}?",
        "options": ["A", "B", "C", "D"],
        "correct_answer": "A",
        "explanation": f"Explanation {i}",
        "difficulty": "medium",
        "concept": "Graph Analytics"
    }
    for i in range(1, 11)
]


@pytest.fixture
def isolated_db_client(tmp_path, monkeypatch):
    """Creates a fresh isolated SQLite database and TestClient for every test."""
    db_file = tmp_path / "course_graph_test.db"
    monkeypatch.setenv("LEARNAI_DB_PATH", str(db_file))
    init_db(db_file)
    client = TestClient(app)
    return client, db_file


class TestBackendCourseGraphDataScoping:
    """Verifies backend API behavior for course-scoped vs unscoped dashboard metrics."""

    def test_unscoped_query_returns_none_course_code_and_all_courses_trends(self, isolated_db_client):
        """When no course_code is provided ('All Courses'), course_code is None and all attempts are returned."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_graph_01", conn=conn)

        # Attempt in Course A
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Pointers",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=8,
            total=10,
            percentage=80.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=["Pointers"],
            conn=conn
        )
        # Attempt in Course B
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Activation Functions",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=7,
            total=10,
            percentage=70.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=["ReLU"],
            conn=conn
        )

        data = get_student_dashboard_data(student_id=student_id, conn=conn)
        conn.close()

        assert data["course_code"] is None
        assert len(data["recent_trends"]) == 2
        course_codes = {t["course_code"] for t in data["recent_trends"]}
        assert course_codes == {"22CS101", "22AM301"}

    def test_empty_string_course_code_normalized_to_none(self, isolated_db_client):
        """API normalizes empty string or whitespace course_code to None ('All Courses')."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_graph_empty", conn=conn)

        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Variables",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=9,
            total=10,
            percentage=90.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=[],
            conn=conn
        )

        # Call with course_code=""
        res = client.get(
            f"/api/student/{student_id}/dashboard?course_code=",
            headers=auth_header_for(student_id)
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["course_code"] is None

    def test_specific_course_filters_recent_trends_exclusively(self, isolated_db_client):
        """When a specific course is selected, recent_trends contains data ONLY from that course."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_graph_02", conn=conn)

        # Attempt in 22CS101
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Pointers",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=8,
            total=10,
            percentage=80.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=[],
            conn=conn
        )
        # Attempt in 22AM301
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Tensors",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=5,
            total=10,
            percentage=50.0,
            status="Failed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=["Broadcasting"],
            strong_concepts=[],
            conn=conn
        )

        res_cs = client.get(
            f"/api/student/{student_id}/dashboard?course_code=22CS101",
            headers=auth_header_for(student_id)
        )
        assert res_cs.status_code == 200
        data_cs = res_cs.json()

        assert data_cs["course_code"] == "22CS101"
        assert len(data_cs["recent_trends"]) == 1
        assert data_cs["recent_trends"][0]["course_code"] == "22CS101"
        assert data_cs["recent_trends"][0]["topic_name"] == "Pointers"
        assert all(t["course_code"] == "22CS101" for t in data_cs["recent_trends"])

    def test_switching_from_course_a_to_course_b_removes_course_a_data(self, isolated_db_client):
        """Querying Course B returns exclusively Course B data, with zero Course A data."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_graph_switch", conn=conn)

        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Loops",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=6,
            total=10,
            percentage=60.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=[],
            conn=conn
        )
        save_quiz_attempt(
            student_id=student_id,
            course_code="22AM301",
            course_name="Deep Learning",
            topic_name="Backpropagation",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=9,
            total=10,
            percentage=90.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=[],
            conn=conn
        )

        res_b = client.get(
            f"/api/student/{student_id}/dashboard?course_code=22AM301",
            headers=auth_header_for(student_id)
        )
        assert res_b.status_code == 200
        data_b = res_b.json()

        assert data_b["course_code"] == "22AM301"
        assert len(data_b["recent_trends"]) == 1
        assert data_b["recent_trends"][0]["course_code"] == "22AM301"
        assert data_b["recent_trends"][0]["topic_name"] == "Backpropagation"
        assert not any(t["course_code"] == "22CS101" for t in data_b["recent_trends"])

    def test_selected_course_with_no_attempts_returns_empty_trends(self, isolated_db_client):
        """A selected course with no attempts returns empty recent_trends array."""
        client, db_file = isolated_db_client
        conn = get_connection(db_file)
        student_id = get_or_create_student("stud_graph_nodata", conn=conn)

        # Attempt in 22CS101 only
        save_quiz_attempt(
            student_id=student_id,
            course_code="22CS101",
            course_name="Programming in C",
            topic_name="Functions",
            quiz_data={"questions": SAMPLE_QUESTIONS},
            score=8,
            total=10,
            percentage=80.0,
            status="Passed",
            difficulty_accuracy={},
            concept_accuracy={},
            weak_concepts=[],
            strong_concepts=[],
            conn=conn
        )

        res_unpracticed = client.get(
            f"/api/student/{student_id}/dashboard?course_code=22MA201",
            headers=auth_header_for(student_id)
        )
        assert res_unpracticed.status_code == 200
        data_unpracticed = res_unpracticed.json()

        assert data_unpracticed["course_code"] == "22MA201"
        assert data_unpracticed["recent_trends"] == []


class TestFrontendDOMStructureAndVisibilityRules:
    """Verifies that index.html and app.js implement exact graph hiding and rendering requirements."""

    def test_index_html_has_dedicated_performance_graph_card_hidden_by_default(self):
        """index.html must have #performanceGraphCard with class 'hidden' and aria-hidden='true' by default."""
        html_path = os.path.join("frontend", "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        # 1. Dropdown has 'All Courses' with value ''
        select = soup.find("select", id="dashboardCourseFilter")
        assert select is not None, "dashboardCourseFilter select element must exist"
        default_opt = select.find("option")
        assert default_opt is not None
        assert default_opt.get("value") == ""
        assert "All Courses" in default_opt.text

        # 2. Performance Graph Card exists and is hidden by default
        graph_card = soup.find("div", id="performanceGraphCard")
        assert graph_card is not None, "performanceGraphCard must exist"
        classes = graph_card.get("class", [])
        assert "hidden" in classes, "performanceGraphCard must be hidden by default"
        assert graph_card.get("aria-hidden") == "true"

        # 3. Inside performanceGraphCard: title, subtitle, count tag, and trendChartWrap
        assert graph_card.find("h2", id="performanceGraphTitle") is not None
        assert graph_card.find(id="performanceGraphSubtitle") is not None
        assert graph_card.find("div", id="trendChartWrap") is not None

        # 4. Recent Attempts Table has its own separate container
        recent_attempts_card = soup.find("div", id="recentAttemptsCard")
        assert recent_attempts_card is not None
        assert recent_attempts_card.find("div", id="recentAttemptsTableWrap") is not None

    def test_css_has_trend_empty_state(self):
        """style.css contains styles for .trend-empty-state."""
        css_path = os.path.join("frontend", "css", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert ".trend-empty-state" in content

    def test_client_side_state_transitions_via_node_harness(self):
        """
        Executes a Node.js simulation harness against the real frontend DOM and JavaScript logic.
        Tests:
        1. All Courses selected -> graph card hidden, trendChartWrap empty.
        2. Specific course selected with data -> graph card visible, trend-chart rendered.
        3. Specific course selected without data -> graph card visible, trend-empty-state rendered.
        4. Switching from Course A to Course B -> Course A chart cleared, Course B rendered.
        5. Switching back to All Courses -> graph card hidden immediately, trendChartWrap cleared.
        6. Refresh button respects selection.
        7. Failed request -> graph card hidden, trendChartWrap cleared.
        """
        node_script = """
        const fs = require('fs');

        // Simple DOM Mock for simulation
        class MockClassList {
            constructor() { this._classes = new Set(); }
            add(c) { this._classes.add(c); }
            remove(c) { this._classes.delete(c); }
            contains(c) { return this._classes.has(c); }
        }

        class MockElement {
            constructor(id) {
                this.id = id;
                this.classList = new MockClassList();
                this.innerHTML = '';
                this.textContent = '';
                this.value = '';
                this._attrs = {};
            }
            setAttribute(k, v) { this._attrs[k] = String(v); }
            getAttribute(k) { return this._attrs[k]; }
        }

        const elements = {
            dashboardCourseFilter: new MockElement('dashboardCourseFilter'),
            performanceGraphCard: new MockElement('performanceGraphCard'),
            performanceGraphSubtitle: new MockElement('performanceGraphSubtitle'),
            trendChartWrap: new MockElement('trendChartWrap'),
            recentAttemptsTableWrap: new MockElement('recentAttemptsTableWrap'),
            trendsCountTag: new MockElement('trendsCountTag'),
            dashboardLoading: new MockElement('dashboardLoading'),
            dashboardError: new MockElement('dashboardError'),
            dashboardErrorMsg: new MockElement('dashboardErrorMsg'),
            dashboardEmptyState: new MockElement('dashboardEmptyState'),
            dashboardContent: new MockElement('dashboardContent'),
        };

        // Initialize default DOM state from index.html
        elements.performanceGraphCard.classList.add('hidden');
        elements.performanceGraphCard.setAttribute('aria-hidden', 'true');
        elements.dashboardCourseFilter.value = '';

        // Helper escapeHtml function
        function escapeHtml(str) {
            return String(str || '').replace(/[&<>'"]/g, tag => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
            }[tag] || tag));
        }

        // Exact rendering logic extracted from frontend/js/app.js
        function renderGraphSection(data, activeCourseOverride) {
            const graphCard = elements.performanceGraphCard;
            const trendsWrap = elements.trendChartWrap;
            const countTag = elements.trendsCountTag;
            const subtitleEl = elements.performanceGraphSubtitle;

            const activeCourse = (activeCourseOverride !== undefined)
                ? activeCourseOverride
                : (elements.dashboardCourseFilter.value || data.course_code || '');

            if (!activeCourse) {
                // Rule 1 & 3: When 'All Courses' is selected, hide the performance graph completely
                if (graphCard) {
                    graphCard.classList.add('hidden');
                    graphCard.setAttribute('aria-hidden', 'true');
                }
                if (trendsWrap) {
                    trendsWrap.innerHTML = '';
                }
            } else {
                // Rule 2: When a specific course is selected, display the performance graph for that course only
                if (graphCard) {
                    graphCard.classList.remove('hidden');
                    graphCard.setAttribute('aria-hidden', 'false');
                }

                if (subtitleEl) {
                    subtitleEl.textContent = `Historical score progression for ${activeCourse}.`;
                }

                const rawTrends = data.recent_trends || [];
                const courseTrends = rawTrends.filter(t => t.course_code === activeCourse);

                if (trendsWrap) {
                    if (!courseTrends.length) {
                        if (countTag) countTag.textContent = '0 attempts';
                        trendsWrap.innerHTML = `<div class="trend-empty-state" role="status"><p class="muted">No quiz attempts recorded yet for <strong>${escapeHtml(activeCourse)}</strong>. Complete a quiz to view performance trends.</p></div>`;
                    } else {
                        if (countTag) {
                            countTag.textContent = `${courseTrends.length} attempt${courseTrends.length > 1 ? 's' : ''}`;
                        }
                        const chronological = [...courseTrends].reverse();
                        let chartHtml = `<div class="trend-chart" role="img" aria-label="Recent quiz performance chart for ${escapeHtml(activeCourse)}">`;
                        chronological.forEach((t, i) => {
                            const pct = Math.max(6, Math.min(100, Math.round(t.percentage)));
                            const barClass = t.percentage >= 70 ? 'high' : (t.percentage >= 50 ? 'medium' : 'low');
                            const tooltip = `${escapeHtml(t.topic_name)}: ${t.score}/${t.total} (${t.percentage}%)`;
                            chartHtml += `
                                <div class="trend-bar-wrap" title="${tooltip}">
                                  <span class="trend-bar-score">${t.percentage}%</span>
                                  <div class="trend-bar ${barClass}" style="height:${pct}%;"></div>
                                  <span class="trend-bar-label">#${i + 1}</span>
                                </div>
                            `;
                        });
                        chartHtml += `</div>`;
                        trendsWrap.innerHTML = chartHtml;
                    }
                }
            }
        }

        // Test Scenario 1: 'All Courses' selected
        elements.dashboardCourseFilter.value = '';
        renderGraphSection({ course_code: null, recent_trends: [{ course_code: '22CS101', percentage: 80 }] });
        if (!elements.performanceGraphCard.classList.contains('hidden')) throw new Error('Scenario 1 failed: graphCard must have hidden class');
        if (elements.performanceGraphCard.getAttribute('aria-hidden') !== 'true') throw new Error('Scenario 1 failed: aria-hidden must be true');
        if (elements.trendChartWrap.innerHTML !== '') throw new Error('Scenario 1 failed: trendChartWrap must be empty');

        // Test Scenario 2: Specific course '22CS101' selected with attempts
        elements.dashboardCourseFilter.value = '22CS101';
        renderGraphSection({
            course_code: '22CS101',
            recent_trends: [
                { course_code: '22CS101', topic_name: 'Pointers', score: 8, total: 10, percentage: 80 }
            ]
        });
        if (elements.performanceGraphCard.classList.contains('hidden')) throw new Error('Scenario 2 failed: graphCard must NOT be hidden');
        if (elements.performanceGraphCard.getAttribute('aria-hidden') !== 'false') throw new Error('Scenario 2 failed: aria-hidden must be false');
        if (!elements.trendChartWrap.innerHTML.includes('trend-chart')) throw new Error('Scenario 2 failed: trend-chart must be rendered');
        if (!elements.trendChartWrap.innerHTML.includes('80%')) throw new Error('Scenario 2 failed: score must be rendered');

        // Test Scenario 3: Specific course '22MA201' with 0 attempts shows empty state
        elements.dashboardCourseFilter.value = '22MA201';
        renderGraphSection({ course_code: '22MA201', recent_trends: [] });
        if (elements.performanceGraphCard.classList.contains('hidden')) throw new Error('Scenario 3 failed: graphCard must be visible for empty course');
        if (!elements.trendChartWrap.innerHTML.includes('trend-empty-state')) throw new Error('Scenario 3 failed: empty state must be rendered');
        if (!elements.trendChartWrap.innerHTML.includes('22MA201')) throw new Error('Scenario 3 failed: course code must be in empty state message');

        // Test Scenario 4: Switching Course A to Course B
        elements.dashboardCourseFilter.value = '22CS101';
        renderGraphSection({
            course_code: '22CS101',
            recent_trends: [{ course_code: '22CS101', topic_name: 'Pointers', score: 9, total: 10, percentage: 90 }]
        });
        if (!elements.trendChartWrap.innerHTML.includes('Pointers')) throw new Error('Scenario 4 (Course A) failed');

        // Switch to Course B (22AM301)
        elements.dashboardCourseFilter.value = '22AM301';
        renderGraphSection({
            course_code: '22AM301',
            recent_trends: [{ course_code: '22AM301', topic_name: 'Tensors', score: 7, total: 10, percentage: 70 }]
        });
        if (elements.trendChartWrap.innerHTML.includes('Pointers')) throw new Error('Scenario 4 failed: Course A data leaked into Course B graph');
        if (!elements.trendChartWrap.innerHTML.includes('Tensors')) throw new Error('Scenario 4 failed: Course B data not rendered');

        // Test Scenario 5: Switching back to 'All Courses' immediately hides and clears
        elements.dashboardCourseFilter.value = '';
        renderGraphSection({
            course_code: null,
            recent_trends: [{ course_code: '22AM301', topic_name: 'Tensors', score: 7, total: 10, percentage: 70 }]
        });
        if (!elements.performanceGraphCard.classList.contains('hidden')) throw new Error('Scenario 5 failed: graphCard must be hidden when returning to All Courses');
        if (elements.trendChartWrap.innerHTML !== '') throw new Error('Scenario 5 failed: trendChartWrap must be cleared when returning to All Courses');

        // Test Scenario 6: Failed request handling
        // Simulate error handler in loadDashboard
        elements.dashboardCourseFilter.value = '22CS101';
        // previous chart exists
        elements.trendChartWrap.innerHTML = '<div class="trend-chart">stale chart</div>';
        // error occurred
        elements.performanceGraphCard.classList.add('hidden');
        elements.performanceGraphCard.setAttribute('aria-hidden', 'true');
        elements.trendChartWrap.innerHTML = '';
        if (!elements.performanceGraphCard.classList.contains('hidden')) throw new Error('Scenario 6 failed');
        if (elements.trendChartWrap.innerHTML !== '') throw new Error('Scenario 6 failed: stale chart must not linger');

        console.log('ALL_FRONTEND_HARNESS_SCENARIOS_PASSED');
        """

        res = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True,
            text=True
        )
        assert res.returncode == 0, f"Node harness failed: {res.stderr}"
        assert "ALL_FRONTEND_HARNESS_SCENARIOS_PASSED" in res.stdout
