import re

# 1. Update frontend/index.html
with open('frontend/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

target_html = '\n        <button id="exitQuizBtn" type="button" class="secondary" aria-label="Exit quiz and change topic">Exit Quiz &amp; Change Topic</button>\n'
if target_html not in html:
    # Try CRLF
    target_html = '\r\n        <button id="exitQuizBtn" type="button" class="secondary" aria-label="Exit quiz and change topic">Exit Quiz &amp; Change Topic</button>\r\n'

assert target_html in html, "Target button not found in index.html"
html = html.replace(target_html, '')
with open('frontend/index.html', 'w', encoding='utf-8', newline='') as f:
    f.write(html)
print("Updated index.html")

# 2. Update frontend/css/style.css
with open('frontend/css/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

target_css = """#exitQuizBtn {
  border: 1px solid #cbd5e1;
  color: #64748b;
  background: transparent;
}

#exitQuizBtn:hover {
  background: #fef2f2;
  color: #dc2626;
  border-color: #fca5a5;
}
"""
if target_css not in css:
    target_css = target_css.replace('\n', '\r\n')

assert target_css in css, "Target css not found in style.css"
css = css.replace(target_css, '')
with open('frontend/css/style.css', 'w', encoding='utf-8', newline='') as f:
    f.write(css)
print("Updated style.css")

# 3. Update frontend/js/app.js
with open('frontend/js/app.js', 'r', encoding='utf-8') as f:
    js = f.read()

# 3a. Remove exitQuizBtn event listener
target_js_exit = """$("exitQuizBtn")?.addEventListener("click", async () => {
  if (confirm("Exit quiz and change topic? Your unfinished quiz progress will be discarded.")) {
    const sId = currentQuiz ? (currentQuiz.quiz_id || currentQuiz.session_id) : null;
    if (sId) {
      try {
        await authenticatedFetch("/api/quiz/abandon", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sId })
        });
      } catch (e) {
        console.warn("[QUIZ] Failed to abandon quiz on server:", e);
      }
    }
    clearSessionState();
    showScreen("setup");
    if ($("setupMsg")) $("setupMsg").textContent = "";
  }
});
"""
if target_js_exit not in js:
    target_js_exit = target_js_exit.replace('\n', '\r\n')

assert target_js_exit in js, "Target exitQuizBtn listener not found in app.js"
js = js.replace(target_js_exit, '')

# 3b. handlePracticeNav
target_practice = """  const hasActive = await loadActiveQuizState();
  if (hasActive) {
    showScreen("setup");
    if ($("setupMsg")) $("setupMsg").textContent = "";
    return;
  }"""
repl_practice = """  const hasActive = await loadActiveQuizState();
  if (hasActive) {
    resumeActiveQuiz();
    return;
  }"""
if target_practice not in js:
    target_practice = target_practice.replace('\n', '\r\n')
    repl_practice = repl_practice.replace('\n', '\r\n')
assert target_practice in js, "Target practice nav not found in app.js"
js = js.replace(target_practice, repl_practice)

# 3c. login submit handler
target_login = """    const hasActive = await loadActiveQuizState();
    if (hasActive) {
      showScreen("setup");
    } else {"""
repl_login = """    const hasActive = await loadActiveQuizState();
    if (hasActive) {
      resumeActiveQuiz();
    } else {"""
if target_login not in js:
    target_login = target_login.replace('\n', '\r\n')
    repl_login = repl_login.replace('\n', '\r\n')
assert target_login in js, "Target login handler not found in app.js"
js = js.replace(target_login, repl_login)

# 3d. initApp
target_init = """    const hasActive = await loadActiveQuizState();

    let restoredResult = false;
    if (hasActive) {
      showScreen("setup");
    } else {"""
repl_init = """    const hasActive = await loadActiveQuizState();

    let restoredResult = false;
    if (hasActive) {
      resumeActiveQuiz();
    } else {"""
if target_init not in js:
    target_init = target_init.replace('\n', '\r\n')
    repl_init = repl_init.replace('\n', '\r\n')
assert target_init in js, "Target initApp not found in app.js"
js = js.replace(target_init, repl_init)

# 3e. initApp history replaceState
target_hist = """        window.history.replaceState({ screen: restoredResult ? "resultSection" : "setup" }, "");"""
repl_hist = """        window.history.replaceState({ screen: hasActive ? "quizSection" : (restoredResult ? "resultSection" : "setup") }, "");"""
if target_hist not in js:
    target_hist = target_hist.replace('\n', '\r\n')
    repl_hist = repl_hist.replace('\n', '\r\n')
assert target_hist in js, "Target init history not found in app.js"
js = js.replace(target_hist, repl_hist)

# 3f. popstate handler
target_pop = """    showScreen(targetScreen, false);
    if (targetScreen === "dashboardSection") {
      loadDashboard();
    }"""
repl_pop = """    if (targetScreen === "quizSection") {
      resumeActiveQuiz();
    } else {
      showScreen(targetScreen, false);
      if (targetScreen === "dashboardSection") {
        loadDashboard();
      }
    }"""
if target_pop not in js:
    target_pop = target_pop.replace('\n', '\r\n')
    repl_pop = repl_pop.replace('\n', '\r\n')
assert target_pop in js, "Target popstate not found in app.js"
js = js.replace(target_pop, repl_pop)

with open('frontend/js/app.js', 'w', encoding='utf-8', newline='') as f:
    f.write(js)
print("Updated app.js")
