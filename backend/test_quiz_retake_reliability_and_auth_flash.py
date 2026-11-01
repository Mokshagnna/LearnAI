"""
LearnAI Quiz Retake Reliability, Generation Performance, Authentication Flash & Review UI Test Suite
Tests:
1. Exact requested question count enforcement (5, 10, 20 questions) in adaptive distribution, prompts, and validation.
2. Replenishment logic when provider returns fewer questions.
3. Targeted replacement on retakes when duplicates are detected.
4. Error handling when requested question count cannot be met (never silent partial quiz).
5. Database connection reuse and provider caching.
6. Authentication initialization states (checking, authenticated, unauthenticated, network_error) with zero flash.
7. Clean academic question review UI styling without aggressive red bars or borders.
8. Preservation of mathematical expressions (KaTeX) and code formatting in review.
"""

import json
import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.adaptive import (
    get_difficulty_distribution,
    ADAPTIVE_LEVEL_FOUNDATIONAL,
    ADAPTIVE_LEVEL_MIXED,
    ADAPTIVE_LEVEL_ADVANCED
)
from services.providers.base import (
    get_quiz_prompt,
    validate_quiz_data,
    BaseAIProvider
)
from services.llm_service import (
    generate_quiz,
    _get_providers,
    _load_stored_quiz_fallback,
    _load_local_demo_questions,
    AIError
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "frontend" / "index.html"
APP_JS = PROJECT_ROOT / "frontend" / "js" / "app.js"
STYLE_CSS = PROJECT_ROOT / "frontend" / "css" / "style.css"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Question Count & Adaptive Distribution Invariants
# ─────────────────────────────────────────────────────────────────────────────

class TestAdaptiveQuestionCounts:
    """Verify exact count enforcement across all supported question quantities (5, 10, 20)."""

    @pytest.mark.parametrize("count", [5, 10, 15, 20])
    @pytest.mark.parametrize("level", [ADAPTIVE_LEVEL_FOUNDATIONAL, ADAPTIVE_LEVEL_MIXED, ADAPTIVE_LEVEL_ADVANCED])
    def test_difficulty_distribution_sums_to_exact_count(self, count, level):
        dist = get_difficulty_distribution(level, total_questions=count)
        assert sum(dist.values()) == count
        assert dist.get("Easy", 0) >= 0
        assert dist.get("Medium", 0) >= 0
        assert dist.get("Hard", 0) >= 0
        if count >= 5:
            if level == ADAPTIVE_LEVEL_FOUNDATIONAL:
                assert dist["Easy"] >= dist["Hard"]
            elif level == ADAPTIVE_LEVEL_ADVANCED:
                assert dist["Hard"] >= dist["Easy"]

    def test_quiz_prompt_contains_exact_requested_count(self):
        for num in [5, 10, 20]:
            prompt = get_quiz_prompt(
                course_code="EE201",
                course_name="Circuit Analysis",
                topic_name="Ohm's Law",
                syllabus_context="V = I * R fundamentals",
                num_questions=num
            )
            assert f"EXACTLY {num} multiple-choice questions" in prompt
            assert f"ALL {num} questions MUST strictly and directly assess" in prompt

    def test_validate_quiz_data_respects_custom_expected_count(self):
        distinct_topics = [
            ("What is the SI unit of electrical resistance?", "Ohm", "Volt", "Ampere", "Watt"),
            ("Which statement accurately represents Ohm's Law?", "V = I * R", "V = I / R", "V = R / I", "V = I + R"),
            ("What happens to current if voltage is halved and resistance remains constant?", "Halved", "Doubled", "Quadrupled", "Unchanged"),
            ("Which instrument measures electrical potential difference across a load?", "Voltmeter", "Ammeter", "Ohmmeter", "Wattmeter"),
            ("In an ideal conductor with zero resistance, what is the voltage drop?", "0 V", "10 V", "Infinite", "Indeterminate"),
            ("What is the reciprocal of electrical resistance known as?", "Conductance", "Inductance", "Capacitance", "Reactance"),
            ("In a simple circuit with a 12V battery and 4 ohm resistor, what is the current?", "3 A", "48 A", "0.33 A", "16 A"),
            ("Which material behaves as an ohmic conductor at standard room temperature?", "Copper wire", "Diode", "Transistor", "Thermistor"),
            ("How is an ammeter connected to measure current through a resistor?", "In series", "In parallel", "In bridge", "Across terminals"),
            ("What is electrical power in terms of voltage V and current I?", "P = V * I", "P = V / I", "P = I / V", "P = V + I"),
            ("What does the slope of a V-I graph represent for an ohmic component?", "Resistance", "Conductance", "Capacitance", "Inductance"),
            ("If resistance in a branch is increased, what occurs to the branch current?", "Decreases", "Increases", "Remains same", "Oscillates")
        ]

        sample_questions = []
        for i, (q_text, a, b, c, d) in enumerate(distinct_topics):
            sample_questions.append({
                "question": q_text,
                "options": {"A": a, "B": b, "C": c, "D": d},
                "correct_answer": "A",
                "difficulty": "Medium",
                "concept": "Ohm's Law",
                "explanation": f"Explanation for question {i + 1}",
                "option_explanations": {"A": "Correct", "B": "Incorrect", "C": "Incorrect", "D": "Incorrect"}
            })

        data = {"questions": sample_questions}

        # Validate with expected_count=5
        res_5 = validate_quiz_data(
            data,
            course_name="Circuit Analysis",
            topic_name="Ohm's Law",
            expected_count=5
        )
        assert len(res_5["questions"]) == 5
        assert res_5["unique_count"] == 5

        # Validate with expected_count=10
        res_10 = validate_quiz_data(
            data,
            course_name="Circuit Analysis",
            topic_name="Ohm's Law",
            expected_count=10
        )
        assert len(res_10["questions"]) == 10
        assert res_10["unique_count"] == 10


# ─────────────────────────────────────────────────────────────────────────────
# 2. Quiz Generation Reliability, Replenishment, Targeted Replacement & Speed
# ─────────────────────────────────────────────────────────────────────────────

DISTINCT_QUESTIONS = [
    ("What is the SI unit of electrical resistance?", "Ohm", "Volt", "Ampere", "Watt"),
    ("Which statement accurately represents Ohm's Law?", "V = I * R", "V = I / R", "V = R / I", "V = I + R"),
    ("What happens to current if voltage is halved and resistance remains constant?", "Halved", "Doubled", "Quadrupled", "Unchanged"),
    ("Which instrument measures electrical potential difference across a load?", "Voltmeter", "Ammeter", "Ohmmeter", "Wattmeter"),
    ("In an ideal conductor with zero resistance, what is the voltage drop?", "0 V", "10 V", "Infinite", "Indeterminate"),
    ("What is the reciprocal of electrical resistance known as?", "Conductance", "Inductance", "Capacitance", "Reactance"),
    ("In a simple circuit with a 12V battery and 4 ohm resistor, what is the current?", "3 A", "48 A", "0.33 A", "16 A"),
    ("Which material behaves as an ohmic conductor at standard room temperature?", "Copper wire", "Diode", "Transistor", "Thermistor"),
    ("How is an ammeter connected to measure current through a resistor?", "In series", "In parallel", "In bridge", "Across terminals"),
    ("What is electrical power in terms of voltage V and current I?", "P = V * I", "P = V / I", "P = I / V", "P = V + I"),
    ("What does the slope of a V-I graph represent for an ohmic component?", "Resistance", "Conductance", "Capacitance", "Inductance"),
    ("If resistance in a branch is increased, what occurs to the branch current?", "Decreases", "Increases", "Remains same", "Oscillates")
]


def make_question(idx, q_text, a=None, b=None, c=None, d=None):
    return {
        "question": q_text,
        "options": {
            "A": a or f"Unique Option A for item {idx}",
            "B": b or f"Unique Option B for item {idx}",
            "C": c or f"Unique Option C for item {idx}",
            "D": d or f"Unique Option D for item {idx}"
        },
        "correct_answer": "A",
        "difficulty": "Medium",
        "concept": "Ohm's Law",
        "explanation": f"Step by step explanation for question {idx}",
        "option_explanations": {"A": "Correct: Matches definition.", "B": "Incorrect: Misconception.", "C": "Incorrect: Wrong formula.", "D": "Incorrect: Unrelated."}
    }


def make_mock_conn():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    # Return ("student_123",) for student check, and None for topic mastery check
    cursor.fetchone.side_effect = [("student_123",)] + [None] * 50
    cursor.fetchall.return_value = []
    return conn


class TestQuizGenerationReliability:
    """Test AI quiz generation replenishment, targeted replacement, connection reuse, and strict count enforcement."""

    def test_provider_instances_are_cached(self):
        providers1 = _get_providers()
        providers2 = _get_providers()
        assert len(providers1) == len(providers2)
        for p1, p2 in zip(providers1, providers2):
            assert p1 is p2

    @patch("services.database.get_connection")
    @patch("services.llm_service._get_providers")
    def test_mock_provider_replenishes_from_8_to_10_questions(self, mock_get_providers, mock_conn):
        fake_conn = make_mock_conn()
        mock_conn.return_value = fake_conn

        mock_p = MagicMock(spec=BaseAIProvider)
        mock_p.name = "mock_ai"
        mock_p.is_configured.return_value = True

        first_8 = [make_question(i, DISTINCT_QUESTIONS[i][0]) for i in range(8)]
        replenish_2 = [make_question(i, DISTINCT_QUESTIONS[i][0]) for i in range(8, 10)]

        mock_p.generate_quiz_raw.side_effect = [
            json.dumps({"questions": first_8}),
            json.dumps({"questions": replenish_2})
        ]
        mock_get_providers.return_value = [mock_p]

        quiz = generate_quiz(
            course_code="EE201",
            course_name="Circuit Analysis",
            topic_name="Ohm's Law",
            syllabus_context="Ohm's Law basics",
            student_id="test_student",
            num_questions=10
        )

        assert len(quiz["questions"]) == 10
        assert mock_p.generate_quiz_raw.call_count >= 2
        assert fake_conn.close.called

    @patch("services.database.get_connection")
    @patch("services.llm_service._get_providers")
    def test_mock_provider_retake_targeted_replacement(self, mock_get_providers, mock_conn):
        fake_conn = make_mock_conn()
        mock_conn.return_value = fake_conn

        mock_p = MagicMock(spec=BaseAIProvider)
        mock_p.name = "mock_ai"
        mock_p.is_configured.return_value = True

        dup_q1 = make_question(0, "What is Ohm's Law exact formula previously seen?")
        dup_q2 = make_question(1, "What is the unit of electrical resistance previously seen?")
        novel_8 = [make_question(i, DISTINCT_QUESTIONS[i][0]) for i in range(2, 10)]
        initial_10 = [dup_q1, dup_q2] + novel_8

        replacement_2 = [
            make_question(10, DISTINCT_QUESTIONS[10][0]),
            make_question(11, DISTINCT_QUESTIONS[11][0])
        ]

        mock_p.generate_quiz_raw.side_effect = [
            json.dumps({"questions": initial_10}),
            json.dumps({"questions": replacement_2})
        ]
        mock_get_providers.return_value = [mock_p]

        with patch("services.llm_service.get_question_fingerprints", return_value=[
            "what is ohm's law exact formula previously seen?",
            "what is the unit of electrical resistance previously seen?"
        ]):
            quiz = generate_quiz(
                course_code="EE201",
                course_name="Circuit Analysis",
                topic_name="Ohm's Law",
                syllabus_context="Ohm's Law basics",
                student_id="test_student",
                num_questions=10
            )

        assert len(quiz["questions"]) == 10
        q_texts = [q["question"] for q in quiz["questions"]]
        assert DISTINCT_QUESTIONS[10][0] in q_texts
        assert DISTINCT_QUESTIONS[11][0] in q_texts

    @patch("services.database.get_connection")
    @patch("services.llm_service._get_providers")
    def test_unmet_question_count_raises_clear_error_never_partial_quiz(self, mock_get_providers, mock_conn):
        fake_conn = make_mock_conn()
        mock_conn.return_value = fake_conn

        mock_p = MagicMock(spec=BaseAIProvider)
        mock_p.name = "mock_ai"
        mock_p.is_configured.return_value = True

        only_4 = [make_question(i, DISTINCT_QUESTIONS[i][0]) for i in range(4)]
        mock_p.generate_quiz_raw.return_value = json.dumps({"questions": only_4})
        mock_get_providers.return_value = [mock_p]

        with pytest.raises(Exception) as exc_info:
            generate_quiz(
                course_code="EE201",
                course_name="Circuit Analysis",
                topic_name="Ohm's Law",
                syllabus_context="Ohm's Law basics",
                student_id="test_student",
                num_questions=10
            )
        assert "questions" in str(exc_info.value).lower() or "provider" in str(exc_info.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Authentication Flash Fix & Zero-Flash State Machine
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthInitializationAndZeroFlash:
    """Verify that login screen does not flash for authenticated users and explicit auth checking exists."""

    def test_login_section_is_hidden_by_default_in_markup(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="loginSection"' in html
        assert 'id="loginSection" class="card auth-card hidden"' in html

    def test_auth_checking_section_exists_in_markup(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="authCheckingSection"' in html
        assert 'id="authCheckingSpinner"' in html
        assert 'id="authCheckingErrorBox"' in html
        assert 'id="authCheckingRetryBtn"' in html
        assert 'id="authCheckingLoginBtn"' in html

    def test_head_contains_synchronous_auth_state_check(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "localStorage.getItem('learnai_auth_token')" in html
        assert "classList.add('unauthenticated')" in html

    def test_style_css_supports_unauthenticated_and_checking_states(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert "#authCheckingSection" in css
        assert "html.unauthenticated #authCheckingSection" in css
        assert "html.unauthenticated #loginSection" in css
        assert ".auth-loading-state" in css
        assert ".auth-error-state" in css

    def test_app_js_includes_auth_checking_section_in_auth_screens(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert '"authCheckingSection"' in js
        assert "checkSessionStatus" in js

    def test_app_js_distinguishes_checking_authenticated_unauthenticated_and_network_error(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert 'status: "authenticated"' in js
        assert 'status: "unauthenticated"' in js
        assert 'status: "network_error"' in js
        assert '$("authCheckingSpinner")?.classList.add("hidden")' in js
        assert '$("authCheckingErrorBox")?.classList.remove("hidden")' in js


# ─────────────────────────────────────────────────────────────────────────────
# 4. Question Review UI Restyling: Restrained Academic Design
# ─────────────────────────────────────────────────────────────────────────────

class TestQuestionReviewAcademicUI:
    """Verify that 'Why the other options are incorrect' UI has restrained, clean academic styling."""

    def test_style_css_removes_aggressive_red_card_styling(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert "border-left: 4px solid #DC2626;" not in css
        assert "border-left: 4px solid" not in css
        assert "background: #FFF8F8;" not in css

    def test_style_css_has_clean_academic_card_styling(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert ".review-distractor-entry" in css
        assert "background: var(--color-surface, #FFFFFF);" in css
        assert "border: 1px solid var(--color-border, #E5E7EB);" in css

    def test_style_css_has_restrained_status_tag_and_choice_badge(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert ".distractor-status-tag" in css
        assert ".distractor-badge-row .choice-badge" in css
        assert "border-radius: 4px;" in css

    def test_style_css_has_neutral_divider_for_explanation(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert ".distractor-explanation" in css
        assert "border-top: 1px solid #F3F4F6;" in css
        assert "border-top: 1px dashed #FECACA;" not in css

    def test_katex_and_code_preserved_in_distractors(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert ".distractor-option-content .inline-code" in css
        assert ".distractor-explanation-text" in css
        assert ".katex" in css
        assert ".option-code" in css
