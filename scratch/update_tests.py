with open('backend/tests/test_stage3_lifecycle.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace TestFrontendWiringAndElements and add TestAutomaticRestorationAndSafeguards
old_wiring = '''class TestFrontendWiringAndElements:
    """Verify frontend code templates, CSS, and JS wiring."""

    def test_index_html_contains_active_banner_and_exit_button(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="activeQuizBanner"' in html
        assert 'id="resumeActiveQuizBtn"' in html
        assert 'id="discardActiveQuizBtn"' in html
        assert 'id="exitQuizBtn"' in html

    def test_style_css_has_active_banner_and_exit_button_styles(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert ".active-quiz-banner" in css
        assert "#exitQuizBtn" in css

    def test_app_js_wiring_and_exports(self):
        js = APP_JS.read_text(encoding="utf-8")
        assert "loadActiveQuizState" in js
        assert "resumeActiveQuiz" in js
        assert "lastCompletedResult" in js
        assert "abandon_session_id" in js
        assert "/api/quiz/completed/latest" in js
        assert "/api/quiz/abandon" in js
        assert "learnai_result_sync" in js'''

new_wiring = '''class TestFrontendWiringAndElements:
    """Verify frontend code templates, CSS, and JS wiring for automatic restoration and exit button removal."""

    def test_index_html_exit_button_removed_and_nav_controls_intact(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        # Exit quiz button is completely removed
        assert 'id="exitQuizBtn"' not in html
        assert "Exit Quiz &amp; Change Topic" not in html
        # Previous and Next controls remain intact in navigation row
        assert 'id="prevBtn"' in html
        assert 'id="nextBtn"' in html
        # Fallback banner remains on setup screen
        assert 'id="activeQuizBanner"' in html
        assert 'id="resumeActiveQuizBtn"' in html
        assert 'id="discardActiveQuizBtn"' in html

    def test_style_css_exit_button_styles_removed(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        # Exit button rules removed
        assert "#exitQuizBtn" not in css
        # Nav row and banner layout styles preserved
        assert ".nav-row" in css
        assert ".active-quiz-banner" in css

    def test_app_js_exit_handler_removed_and_restoration_wiring(self):
        js = APP_JS.read_text(encoding="utf-8")
        # Exit button event listener removed
        assert '$("exitQuizBtn")' not in js
        assert 'exitQuizBtn' not in js
        # Core restoration functions present
        assert "loadActiveQuizState" in js
        assert "resumeActiveQuiz" in js
        assert "saveSessionState" in js
        assert "lastCompletedResult" in js
        assert "/api/quiz/completed/latest" in js
        assert "/api/quiz/abandon" in js

    def test_handle_practice_nav_automatically_resumes_active_quiz(self):
        js = APP_JS.read_text(encoding="utf-8")
        # handlePracticeNav must call resumeActiveQuiz() directly when active quiz exists
        pattern = r"const hasActive = await loadActiveQuizState\(\);\s*if \(hasActive\) \{\s*resumeActiveQuiz\(\);"
        import re
        assert re.search(pattern, js) is not None, "handlePracticeNav does not directly call resumeActiveQuiz()"

    def test_init_app_automatically_resumes_active_quiz_on_load_or_refresh(self):
        js = APP_JS.read_text(encoding="utf-8")
        # initApp must call resumeActiveQuiz() directly when active quiz exists
        pattern = r"const hasActive = await loadActiveQuizState\(\);[\s\S]*?if \(hasActive\) \{\s*resumeActiveQuiz\(\);"
        import re
        assert re.search(pattern, js) is not None, "initApp does not directly call resumeActiveQuiz()"

    def test_popstate_automatically_resumes_quiz_section(self):
        js = APP_JS.read_text(encoding="utf-8")
        pattern = r'if \(targetScreen === "quizSection"\) \{\s*resumeActiveQuiz\(\);'
        import re
        assert re.search(pattern, js) is not None, "popstate does not call resumeActiveQuiz for quizSection"

    def test_question_index_and_answer_persistence_preserved_in_client(self):
        js = APP_JS.read_text(encoding="utf-8")
        # saveSessionState serializes currentIndex, answers, and currentQuiz
        assert "currentIndex," in js
        assert "answers," in js
        assert "learnai_active_quiz" in js
        # answers[currentIndex] updated on option selection
        assert "answers[currentIndex] = key;" in js
        assert "saveSessionState();" in js

    def test_account_isolation_guards_in_load_active_quiz_state(self):
        js = APP_JS.read_text(encoding="utf-8")
        # Must verify activeStudentId against quiz ownership and purge on mismatch
        assert "data.currentQuiz.student_id !== activeStudentId" in js
        assert "clearSessionState();" in js'''

if old_wiring not in content:
    old_wiring = old_wiring.replace('\n', '\r\n')
    new_wiring = new_wiring.replace('\n', '\r\n')

assert old_wiring in content, "Old wiring block not found in test_stage3_lifecycle.py"
content = content.replace(old_wiring, new_wiring)

with open('backend/tests/test_stage3_lifecycle.py', 'w', encoding='utf-8', newline='') as f:
    f.write(content)
print("Updated test_stage3_lifecycle.py")
