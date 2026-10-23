const $ = id => document.getElementById(id);

// ── Application State ────────────────────────────────────────────────────────
let curriculum = [];
let currentQuiz = null;
let currentIndex = 0;
let answers = [];
let activeCourseCode = "";
let activeTopicName = "";
let activeTopicId = "";
let activeNotesSessionId = null;
let isGenerating = false;
let activeAbortController = null;
let studentId = localStorage.getItem("learnai_student_id") || null;
let authToken = localStorage.getItem("learnai_auth_token") || null;

// ── Student Authentication & Session Management ──────────────────────────────
async function ensureAuthSession() {
  if (authToken && studentId) {
    return { token: authToken, studentId: studentId };
  }
  try {
    const res = await fetch("/api/auth/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    if (res.ok) {
      const data = await res.json();
      authToken = data.token;
      studentId = data.student_id;
      localStorage.setItem("learnai_auth_token", authToken);
      localStorage.setItem("learnai_student_id", studentId);
      return { token: authToken, studentId: studentId };
    }
  } catch (err) {
    console.warn("Failed to initialize server auth session:", err);
  }
  return { token: authToken, studentId: studentId };
}

function getStudentId() {
  return studentId;
}

function getAuthToken() {
  return authToken;
}

async function authenticatedFetch(url, options = {}) {
  await ensureAuthSession();
  options.headers = options.headers || {};
  if (authToken) {
    options.headers["Authorization"] = `Bearer ${authToken}`;
  }
  let res = await fetch(url, options);
  if (res.status === 401) {
    console.warn("[AUTH] 401 Unauthorized received. Renewing session...");
    localStorage.removeItem("learnai_auth_token");
    authToken = null;
    await ensureAuthSession();
    if (authToken) {
      options.headers["Authorization"] = `Bearer ${authToken}`;
      res = await fetch(url, options);
    }
  }
  return res;
}

// ── HTML Safety & Escaping Helper ───────────────────────────────────────────
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ── Mutually Exclusive Screen Management ────────────────────────────────────
// Ensures EXACTLY ONE screen is visible at any time.
const ALL_SCREENS = ["setup", "loadingSection", "quizSection", "resultSection", "dashboardSection", "notesUploadSection"];

function showScreen(screenId) {
  ALL_SCREENS.forEach(id => {
    const el = $(id);
    if (el) {
      if (id === screenId) {
        el.classList.remove("hidden");
      } else {
        el.classList.add("hidden");
      }
    }
  });

  const isDashboard = screenId === "dashboardSection";
  const isNotes = screenId === "notesUploadSection";
  $("navDashboardBtn")?.classList.toggle("active", isDashboard);
  $("navNotesBtn")?.classList.toggle("active", isNotes);
  $("navPracticeBtn")?.classList.toggle("active", !isDashboard && !isNotes);

  // Safe programmatic focus on screen heading for screen readers & keyboard navigation
  const activeEl = $(screenId);
  if (activeEl) {
    const heading = activeEl.querySelector("h1, h2");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
  }
}

// ── Session State Persistence across Browser Refresh ─────────────────────────
function saveSessionState() {
  if (currentQuiz && currentQuiz.questions) {
    sessionStorage.setItem("learnai_active_quiz", JSON.stringify({
      currentQuiz,
      currentIndex,
      answers,
      activeCourseCode,
      activeTopicName,
      activeTopicId,
      activeNotesSessionId
    }));
  }
}

function clearSessionState() {
  sessionStorage.removeItem("learnai_active_quiz");
  activeNotesSessionId = null;
}

function restoreSessionState() {
  try {
    const raw = sessionStorage.getItem("learnai_active_quiz");
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (data && data.currentQuiz && Array.isArray(data.currentQuiz.questions)) {
      currentQuiz = data.currentQuiz;
      currentIndex = data.currentIndex || 0;
      answers = data.answers || Array(currentQuiz.questions.length).fill("");
      activeCourseCode = data.activeCourseCode || currentQuiz.course_code;
      activeTopicName = data.activeTopicName || currentQuiz.topic;
      activeTopicId = data.activeTopicId || currentQuiz.topic_id || "";
      activeNotesSessionId = data.activeNotesSessionId || null;

      // Restore select dropdowns
      if ($("course") && activeCourseCode) {
        $("course").value = activeCourseCode;
        $("course").dispatchEvent(new Event("change"));
        setTimeout(() => {
          if ($("topic") && activeTopicName) {
            $("topic").value = activeTopicName;
          }
        }, 50);
      }

      // Restore quiz header cleanly
      renderQuizHeader(currentQuiz);

      renderQuestion();
      showScreen("quizSection");
      return true;
    }
  } catch (err) {
    clearSessionState();
  }
  return false;
}

// ── Load Curriculum & Course Select ──────────────────────────────────────────
async function loadCourses() {
  const courseSelect = $("course");
  try {
    const res = await fetch("/api/courses");
    if (!res.ok) {
      throw new Error(`Failed to fetch curriculum: HTTP ${res.status}`);
    }
    const data = await res.json();
    curriculum = data.courses || [];

    if (!curriculum.length) {
      $("setupMsg").innerHTML = '<span class="error-msg">No courses available in curriculum.</span>';
      return;
    }

    courseSelect.innerHTML = '<option value="">Select a course</option>';
    curriculum.forEach(course => {
      const option = document.createElement("option");
      option.value = course.code;
      option.textContent = `${course.code} - ${course.name}`;
      courseSelect.appendChild(option);
    });
    $("setupMsg").innerHTML = "";
    populateDashboardCourseFilter();
  } catch (e) {
    console.error("Failed to load courses:", e);
    if (courseSelect) {
      courseSelect.innerHTML = '<option value="">Error loading courses</option>';
    }
    $("setupMsg").innerHTML = '<span class="error-msg">Unable to load courses. Please check if the server is running.</span>';
  }
}

$("course").addEventListener("change", () => {
  const course = curriculum.find(c => c.code === $("course").value);
  const topicSelect = $("topic");
  topicSelect.innerHTML = "";

  if (!course) {
    topicSelect.disabled = true;
    topicSelect.innerHTML = '<option value="">Select a course first</option>';
    topicSelect.value = "";
    return;
  }

  const first = document.createElement("option");
  first.value = "";
  first.textContent = "Select a topic";
  topicSelect.appendChild(first);

  const seen = new Set();
  (course.topics || []).forEach(topic => {
    if (!topic || !topic.name) return;
    const name = String(topic.name).trim();
    if (!name || seen.has(name.toLowerCase())) return;
    seen.add(name.toLowerCase());

    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    option.dataset.topicId = topic.id || "";
    option.dataset.topicName = name;
    option.dataset.parentTopic = topic.parent_topic || topic.parent_section || topic.source_topic_name || "";
    option.dataset.module = (topic.module !== undefined && topic.module !== null) ? topic.module : "";
    option.dataset.unit = (topic.unit !== undefined && topic.unit !== null) ? topic.unit : "";
    topicSelect.appendChild(option);
  });

  topicSelect.disabled = false;
  topicSelect.value = "";
});

// ── Navigation Buttons ───────────────────────────────────────────────────────
$("generateBtn").addEventListener("click", () => {
  const courseCode = $("course").value;
  const topicSelect = $("topic");
  const topic = topicSelect.value;
  const selectedOption = topicSelect.selectedOptions ? topicSelect.selectedOptions[0] : null;
  const topicId = selectedOption?.dataset?.topicId || "";

  if (!courseCode || !topic) {
    $("setupMsg").innerHTML = '<span class="error-msg">Please select both course and topic.</span>';
    return;
  }
  $("setupMsg").innerHTML = "";

  startQuizGeneration(courseCode, topic, false, topicId);
});

$("prevBtn").addEventListener("click", () => {
  if (currentIndex > 0) {
    currentIndex--;
    renderQuestion();
  }
});

$("nextBtn").addEventListener("click", async () => {
  if (!answers[currentIndex]) {
    const alertEl = $("quizAlert");
    if (alertEl) {
      alertEl.textContent = "Please choose an answer to proceed.";
      const optEl = $("options");
      if (optEl) {
        optEl.classList.remove("shake");
        void optEl.offsetWidth; // force DOM reflow
        optEl.classList.add("shake");
      }
    } else {
      alert("Please choose an answer.");
    }
    return;
  }

  if (currentIndex < currentQuiz.questions.length - 1) {
    currentIndex++;
    renderQuestion();
  } else {
    await submitQuiz();
  }
});

// ── Retake Flow: Clean State Transition (Results → Loading → New Quiz) ──────
$("retakeBtn").addEventListener("click", async () => {
  if (isGenerating) return;

  const retakeBtn = $("retakeBtn");
  // Prevent double-click duplicate attempts
  if (retakeBtn.dataset.processing === "true") return;
  retakeBtn.dataset.processing = "true";
  retakeBtn.disabled = true;
  retakeBtn.textContent = "Initiating retake...";

  // Use active course/topic
  const courseCode = activeCourseCode || (currentQuiz ? currentQuiz.course_code : $("course").value);
  const topic = activeTopicName || (currentQuiz ? currentQuiz.topic : $("topic").value);
  const topicId = activeTopicId || (currentQuiz ? currentQuiz.topic_id : null);

  if (!courseCode || !topic) {
    alert("Unable to detect course and topic for retake. Please choose from setup.");
    goToHome();
    retakeBtn.disabled = false;
    retakeBtn.textContent = "Retake with New AI Questions";
    retakeBtn.dataset.processing = "false";
    return;
  }

  // 1. Completely wipe quiz and result state
  currentQuiz = null;
  currentIndex = 0;
  answers = [];

  // 2. Clear all result view DOM elements so old marks can NEVER leak
  clearResultDisplay();

  // 3. Clear question view DOM elements
  if ($("options")) $("options").innerHTML = "";
  if ($("question")) $("question").textContent = "";
  if ($("concept")) $("concept").textContent = "";
  if ($("progressBar")) $("progressBar").style.width = "0%";
  if ($("progressPercent")) $("progressPercent").textContent = "0% completed";

  // 4. Launch fresh quiz generation for retake
  await startQuizGeneration(courseCode, topic, true, topicId, activeNotesSessionId);

  retakeBtn.disabled = false;
  retakeBtn.textContent = "Retake with New AI Questions";
  retakeBtn.dataset.processing = "false";
});

// ── Explicit Home Navigation ────────────────────────────────────────────────
function goToHome() {
  if (isGenerating) return;
  clearSessionState();
  showScreen("setup");
  $("setupMsg").textContent = "";
}

$("newTopicBtn").addEventListener("click", goToHome);
$("brandLogo")?.addEventListener("click", goToHome);
$("loadingBackBtn")?.addEventListener("click", goToHome);
$("loadingCancelBtn")?.addEventListener("click", () => {
  if (activeAbortController) {
    activeAbortController.abort();
    activeAbortController = null;
  }
  isGenerating = false;
  const generateBtn = $("generateBtn");
  const retakeBtn = $("retakeBtn");
  if (generateBtn) generateBtn.disabled = false;
  if (retakeBtn) {
    retakeBtn.disabled = false;
    retakeBtn.textContent = "Retake with New AI Questions";
    retakeBtn.dataset.processing = "false";
  }
  goToHome();
});

// ── Clear Results Display ───────────────────────────────────────────────────
function clearResultDisplay() {
  if ($("scoreText")) $("scoreText").textContent = "";
  if ($("statusText")) $("statusText").textContent = "";
  if ($("percentageText")) $("percentageText").textContent = "";
  if ($("difficultyPerformance")) $("difficultyPerformance").innerHTML = "";
  if ($("conceptPerformance")) $("conceptPerformance").innerHTML = "";
  if ($("strongConcepts")) $("strongConcepts").innerHTML = "";
  if ($("weakConcepts")) $("weakConcepts").innerHTML = "";
  if ($("analysisSummary")) $("analysisSummary").textContent = "";
  if ($("analysisRevision")) $("analysisRevision").textContent = "";
  if ($("analysisRecommendation")) $("analysisRecommendation").textContent = "";
  if ($("review")) $("review").innerHTML = "";
  if ($("attemptHistorySection")) $("attemptHistorySection").classList.add("hidden");
  const histList = $("attemptHistoryList");
  if (histList) histList.innerHTML = "";
}

// ── Core Quiz Generation (First Attempt & Retake) ────────────────────────────
async function startQuizGeneration(courseCode, topic, isRetake = false, topicId = null, notesSessionId = null) {
  if (isGenerating) return;
  isGenerating = true;

  if (notesSessionId) {
    activeNotesSessionId = notesSessionId;
  }

  const generateBtn = $("generateBtn");
  const retakeBtn = $("retakeBtn");
  if (generateBtn) generateBtn.disabled = true;
  if (retakeBtn) retakeBtn.disabled = true;

  activeCourseCode = courseCode;
  activeTopicName = topic;
  activeTopicId = topicId || "";

  let timeoutId = null;
  const spinner = document.querySelector("#loadingSection .spinner");

  try {
    // Clear any previous setup message
    if ($("setupMsg")) $("setupMsg").innerHTML = "";

    // Sync select dropdowns
    if ($("course")) $("course").value = courseCode;
    if ($("topic") && $("topic").value !== topic) {
      $("course").dispatchEvent(new Event("change"));
      await new Promise(r => setTimeout(r, 40));
      if ($("topic")) $("topic").value = topic;
    }

    // ── Setup Loading Screen (Calm, Minimal, Academic) ──
    if (spinner) spinner.style.display = "block";
    if ($("loadingErrorBox")) $("loadingErrorBox").classList.add("hidden");

    // Populate course and topic label (small, secondary)
    const courseObj = curriculum.find(c => c.code === courseCode);
    const courseLabel = courseObj ? `${courseObj.code} - ${courseObj.name}` : courseCode;
    const notesTag = activeNotesSessionId ? " [Notes-Grounded]" : "";
    if ($("loadingTopicLabel")) {
      $("loadingTopicLabel").textContent = `${courseLabel} • ${topic}${notesTag}`;
    }

    showScreen("loadingSection");

    activeAbortController = new AbortController();
    const timeoutSec = 50;
    timeoutId = setTimeout(() => activeAbortController?.abort(), timeoutSec * 1000);

    const quizPayload = {
      course_code: courseCode,
      topic: topic,
      topic_id: activeTopicId || undefined,
      student_id: getStudentId() || undefined,
      retake: isRetake
    };
    if (activeNotesSessionId) {
      quizPayload.notes_session_id = activeNotesSessionId;
    }

    const res = await authenticatedFetch("/api/quiz/generate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(quizPayload),
      signal: activeAbortController.signal
    });
    if (timeoutId) clearTimeout(timeoutId);

    const data = await res.json();

    if (!res.ok) {
      const errMsg = (data && data.detail)
        ? (typeof data.detail === "object" ? data.detail.message : data.detail)
        : "AI service is temporarily busy. Please try again.";
      throw new Error(errMsg);
    }

    if (data.auth_token) {
      authToken = data.auth_token;
      localStorage.setItem("learnai_auth_token", authToken);
    }
    if (data.student_id) {
      studentId = data.student_id;
      localStorage.setItem("learnai_student_id", studentId);
    }

    // ── Validate questions before displaying them ──
    if (!data || !Array.isArray(data.questions) || data.questions.length === 0) {
      throw new Error("Received empty or malformed quiz from AI service. Please click Retry.");
    }

    const invalidQ = data.questions.find((q, idx) => {
      if (!q || typeof q.question !== "string" || !q.question.trim()) return true;
      if (!q.options || typeof q.options !== "object") return true;
      const opts = q.options;
      const required = ["A", "B", "C", "D"];
      const hasAllFour = required.every(k => typeof opts[k] === "string" && opts[k].trim().length > 0);
      if (!hasAllFour) return true;
      if (q.correct_answer !== undefined && !required.includes(q.correct_answer)) return true;
      return false;
    });

    if (invalidQ) {
      throw new Error("One or more questions contained incomplete or blank answer options. Please click Retry.");
    }

    // ── Success: Initialize Clean Quiz State ──
    currentQuiz = data;
    if (data.topic_id) activeTopicId = data.topic_id;
    currentIndex = 0;
    answers = Array(data.questions.length).fill("");

    // Render directly to Question 1 with no intermediate flashes
    renderQuestion();
    showScreen("quizSection");
    saveSessionState();

  } catch (e) {
    if (timeoutId) clearTimeout(timeoutId);

    // Stay on loading section with clean, human-readable error with Retry & Cancel
    if (spinner) spinner.style.display = "none";

    let friendlyMsg = "AI service is temporarily busy. Please try again shortly.";
    if (e.name === "AbortError") {
      friendlyMsg = "Quiz generation was cancelled or timed out. Please try again.";
    } else if (e.message && typeof e.message === "string" && e.message.trim().length > 0 && !e.message.startsWith("{")) {
      friendlyMsg = e.message;
    }

    if ($("loadingErrorMsg")) $("loadingErrorMsg").textContent = friendlyMsg;
    if ($("loadingErrorBox")) $("loadingErrorBox").classList.remove("hidden");

    // Configure retry button
    const retryBtn = $("loadingRetryBtn");
    if (retryBtn) {
      retryBtn.onclick = () => {
        startQuizGeneration(courseCode, topic, isRetake, topicId, activeNotesSessionId);
      };
    }

  } finally {
    isGenerating = false;
    if (generateBtn) generateBtn.disabled = false;
    if (retakeBtn) retakeBtn.disabled = false;
  }
}

// ── Dedicated Quiz Header Renderer (Clean, Calm, Academic) ──────────────────
function renderQuizHeader(quizData) {
  if (!quizData) return;

  const courseObj = curriculum.find(c => c.code === quizData.course_code);
  const courseName = courseObj ? `${courseObj.code} - ${courseObj.name}` : (quizData.course_code || quizData.subject || "");

  if ($("quizCourse")) {
    const modTag = (quizData.module !== undefined && quizData.unit !== undefined)
      ? ` • Module ${quizData.module}, Unit ${quizData.unit}`
      : "";
    $("quizCourse").textContent = `${courseName}${modTag}`;
  }
  if ($("quizTitle")) {
    $("quizTitle").textContent = quizData.topic;
  }
  if ($("quizAttemptNumber")) {
    const attempt = quizData.attempt_number || 1;
    const diff = (quizData.recommended_difficulty || "mixed").toUpperCase();
    $("quizAttemptNumber").textContent = attempt > 1 ? `Attempt #${attempt} • ${diff}` : `Attempt #1 • ${diff}`;
  }
}

// ── Render Question ─────────────────────────────────────────────────────────
function renderQuestion() {
  if (!currentQuiz || !currentQuiz.questions || !currentQuiz.questions[currentIndex]) {
    return;
  }

  const q = currentQuiz.questions[currentIndex];
  const totalQuestions = currentQuiz.questions.length;
  const progressPercent = Math.round(((currentIndex + 1) / totalQuestions) * 100);

  // Sync header and progress
  renderQuizHeader(currentQuiz);
  if ($("progressText")) $("progressText").textContent = `Question ${currentIndex + 1} of ${totalQuestions}`;
  if ($("difficultyBadge")) $("difficultyBadge").textContent = q.difficulty || "Standard";
  if ($("concept")) $("concept").textContent = q.concept ? `Concept: ${q.concept}` : "";
  if ($("question")) $("question").textContent = q.question;
  if ($("progressBar")) {
    $("progressBar").style.width = `${progressPercent}%`;
    const progressEl = $("progressBar").parentElement;
    if (progressEl) progressEl.setAttribute("aria-valuenow", progressPercent);
  }
  if ($("progressPercent")) $("progressPercent").textContent = `${progressPercent}% completed`;

  if ($("quizAlert")) $("quizAlert").textContent = "";

  const options = $("options");
  if (!options) return;
  options.innerHTML = "";

  Object.entries(q.options || {}).forEach(([key, value]) => {
    const isSelected = answers[currentIndex] === key;
    const div = document.createElement("div");
    div.className = "option" + (isSelected ? " selected" : "");
    div.setAttribute("role", "radio");
    div.setAttribute("aria-checked", isSelected ? "true" : "false");
    div.setAttribute("tabindex", "0");

    const radioCircle = document.createElement("span");
    radioCircle.className = "radio-circle";
    radioCircle.setAttribute("aria-hidden", "true");
    const radioDot = document.createElement("span");
    radioDot.className = "radio-dot";
    radioCircle.appendChild(radioDot);

    const letterSpan = document.createElement("span");
    letterSpan.className = "option-letter";
    letterSpan.textContent = `${key}.`;

    const textSpan = document.createElement("span");
    textSpan.className = "option-text";
    textSpan.textContent = value; // Safe! Code snippets like <?php, <%, etc. render verbatim!

    div.appendChild(radioCircle);
    div.appendChild(letterSpan);
    div.appendChild(textSpan);

    const selectOption = () => {
      answers[currentIndex] = key;
      if ($("quizAlert")) $("quizAlert").textContent = "";
      saveSessionState();
      renderQuestion();
    };

    div.addEventListener("click", selectOption);
    div.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectOption();
      }
    });

    options.appendChild(div);
  });

  if ($("prevBtn")) $("prevBtn").style.visibility = currentIndex === 0 ? "hidden" : "visible";
  if ($("nextBtn")) {
    const isLast = currentIndex === totalQuestions - 1;
    $("nextBtn").textContent = isLast ? "Submit Quiz" : "Next";
    $("nextBtn").setAttribute("aria-label", 
      isLast ? "Submit quiz" : "Next question");
  }
}

// ── Keyboard Navigation (1-4, A-D, Left/Right Arrows) ───────────────────────
document.addEventListener("keydown", (e) => {
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA")) return;
  const quizSection = $("quizSection");
  if (!quizSection || quizSection.classList.contains("hidden")) return;
  if (!currentQuiz || !currentQuiz.questions || !currentQuiz.questions[currentIndex]) return;

  const key = e.key.toUpperCase();
  const keyMap = { "1": "A", "2": "B", "3": "C", "4": "D", "A": "A", "B": "B", "C": "C", "D": "D" };
  if (keyMap[key]) {
    const optKey = keyMap[key];
    const q = currentQuiz.questions[currentIndex];
    if (q.options && q.options[optKey]) {
      e.preventDefault();
      answers[currentIndex] = optKey;
      if ($("quizAlert")) $("quizAlert").textContent = "";
      saveSessionState();
      renderQuestion();
    }
  } else if (e.key === "ArrowLeft" && currentIndex > 0) {
    e.preventDefault();
    currentIndex--;
    renderQuestion();
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    $("nextBtn").click();
  }
});

// ── Submit Quiz ─────────────────────────────────────────────────────────────
async function submitQuiz() {
  const nextBtn = $("nextBtn");
  nextBtn.disabled = true;
  nextBtn.textContent = "Analyzing answers with AI...";

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 45000);

  try {
    const res = await authenticatedFetch("/api/quiz/submit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        quiz_id: currentQuiz.quiz_id,
        answers: answers,
        student_id: getStudentId(),
        auth_token: getAuthToken()
      }),
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    const data = await res.json();

    if (!res.ok) {
      if (res.status === 404 || (res.status === 400 && typeof data.detail === "string" && data.detail.toLowerCase().includes("expired"))) {
        clearSessionState();
      }
      throw new Error(data.detail || "Failed to submit quiz.");
    }

    clearSessionState();
    showResult(data);
  } catch (e) {
    clearTimeout(timeoutId);
    let msg = "Failed to submit quiz. Please try again.";
    if (e.name === "AbortError") {
      msg = "Analysis timed out. Please click Submit Quiz again.";
    } else if (e.message) {
      msg = e.message;
    }
    alert(msg);
  } finally {
    nextBtn.disabled = false;
    nextBtn.textContent = "Submit Quiz";
  }
}

// ── Show Results (Isolated to Result Screen) ────────────────────────────────
function showResult(data) {
  // Clear any existing review elements
  $("review").innerHTML = "";

  $("scoreText").textContent = `${data.score} / ${data.total}`;
  $("statusText").textContent = data.status;
  $("percentageText").textContent = `${data.percentage}% accuracy`;

  // Display difficulty-wise performance
  if (data.difficulty_accuracy) {
    let diffHtml = "";
    for (const [diff, stats] of Object.entries(data.difficulty_accuracy)) {
      const acc = stats.accuracy;
      let scoreClass = "mixed";
      if (acc >= 80) scoreClass = "correct";
      else if (acc < 50) scoreClass = "wrong";
      
      diffHtml += `
        <div class="difficulty-item">
          <span class="difficulty-label">${diff}</span>
          <span class="difficulty-score ${scoreClass}">${acc}%</span>
          <span class="difficulty-detail">(${stats.correct}/${stats.total})</span>
        </div>`;
    }
    $("difficultyPerformance").innerHTML = diffHtml || "<span class='muted'>No data</span>";
  }

  // Display concept-wise performance
  if (data.concept_accuracy) {
    let conceptHtml = "";
    for (const [concept, stats] of Object.entries(data.concept_accuracy)) {
      const acc = stats.accuracy;
      let scoreClass = "mixed";
      if (acc >= 80) scoreClass = "correct";
      else if (acc < 50) scoreClass = "wrong";
      
      conceptHtml += `
        <div class="concept-item">
          <span class="concept-label">${concept}</span>
          <span class="concept-score ${scoreClass}">${acc}%</span>
          <span class="concept-detail">(${stats.correct}/${stats.total})</span>
        </div>`;
    }
    $("conceptPerformance").innerHTML = conceptHtml || "<span class='muted'>No data</span>";
  }

  $("strongConcepts").innerHTML = (data.strong_concepts && data.strong_concepts.length)
    ? data.strong_concepts.map(x => `<span class="tag-chip">${x}</span>`).join("")
    : "<span class='muted'>No strong concept identified yet.</span>";

  $("weakConcepts").innerHTML = (data.weak_concepts && data.weak_concepts.length)
    ? data.weak_concepts.map(x => `<span class="tag-chip">${x}</span>`).join("")
    : "<span class='muted'>No major weak concept detected.</span>";

  const analysis = (typeof data.analysis === "object" && data.analysis !== null)
    ? data.analysis
    : {
        summary: typeof data.analysis === "string" ? data.analysis : "AI feedback is temporarily unavailable.",
        revision: "Review the question explanations below to revise your concepts.",
        recommendation: "Review your weak concepts and try another quiz once the service is available."
      };

  $("analysisSummary").textContent = analysis.summary || "";
  $("analysisRevision").textContent = analysis.revision || "";
  $("analysisRecommendation").textContent = analysis.recommendation || "";

  $("review").innerHTML =
    "<h3>Answer Review</h3>" +
    (data.details || []).map((d, i) => `
      <div class="review-item">
        <div><strong>Q${i + 1}. ${escapeHtml(d.question)}</strong></div>
        <div class="${d.is_correct ? "correct" : "wrong"}">
          ${d.is_correct ? "Correct" : "Incorrect"}
        </div>
        <div>Your answer: ${escapeHtml(d.selected_answer || "Not answered")}</div>
        <div>Correct answer: ${escapeHtml(d.correct_answer)}</div>
        <div class="muted">${escapeHtml(d.explanation)}</div>
      </div>
    `).join("");

  // Load and display attempt history for this topic
  loadAttemptHistory();

  // Show ONLY resultSection
  showScreen("resultSection");
  window.scrollTo({top: 0, behavior: "smooth"});
}

// ── Fetch & Render Attempt History ──────────────────────────────────────────
async function loadAttemptHistory() {
  try {
    const sId = getStudentId();
    if (!sId) return;
    const res = await authenticatedFetch(`/api/student/${sId}/history?limit=10`);
    if (!res.ok) return;

    const data = await res.json();
    const attempts = data.attempts || [];

    // Filter attempts for this topic
    const topicAttempts = attempts.filter(a =>
      a.course_code === activeCourseCode && a.topic_name === activeTopicName
    );

    if (topicAttempts.length <= 1) {
      $("attemptHistorySection")?.classList.add("hidden");
      return;
    }

    let tableHtml = `
      <table class="history-table">
        <thead>
          <tr>
            <th>Attempt</th>
            <th>Date & Time</th>
            <th>Score</th>
            <th>Accuracy</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
    `;

    topicAttempts.forEach((att, idx) => {
      const attemptNum = topicAttempts.length - idx;
      const dateStr = new Date(att.created_at).toLocaleString([], {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
      });
      const isCurrent = idx === 0;
      const rowClass = isCurrent ? "history-row-current" : "";

      tableHtml += `
        <tr class="${rowClass}">
          <td>${isCurrent ? 'Current (#' + attemptNum + ')' : 'Attempt #' + attemptNum}</td>
          <td>${dateStr}</td>
          <td>${att.score} / ${att.total}</td>
          <td>${att.percentage}%</td>
          <td>${att.status}</td>
        </tr>
      `;
    });

    tableHtml += `</tbody></table>`;
    $("attemptHistoryList").innerHTML = tableHtml;
    $("attemptHistorySection").classList.remove("hidden");

  } catch (err) {
    // Non-critical, ignore if history fails to load
  }
}

// ── Student Progress Dashboard Logic ─────────────────────────────────────────

async function openDashboard() {
  if (isGenerating) return;
  showScreen("dashboardSection");
  window.scrollTo({ top: 0, behavior: "smooth" });

  populateDashboardCourseFilter();
  await loadDashboard();
}

function populateDashboardCourseFilter(data) {
  const filterSelect = $("dashboardCourseFilter");
  if (!filterSelect) return;

  const currentVal = filterSelect.value || "";
  const existingValues = new Set(Array.from(filterSelect.options).map(o => o.value));

  // Initialize with curriculum courses if only default 'All Courses' exists
  if (existingValues.size <= 1 && Array.isArray(curriculum) && curriculum.length > 0) {
    filterSelect.innerHTML = '<option value="">All Courses</option>';
    curriculum.forEach(course => {
      const opt = document.createElement("option");
      opt.value = course.code;
      opt.textContent = `${course.code} - ${course.name}`;
      filterSelect.appendChild(opt);
      existingValues.add(course.code);
    });
  }

  // Also include any courses present in student data (topic_progress or recent_trends)
  if (data) {
    const dataCourses = new Map();
    (data.topic_progress || []).forEach(tp => {
      if (tp.course_code && !existingValues.has(tp.course_code)) {
        dataCourses.set(tp.course_code, tp.course_code);
      }
    });
    (data.recent_trends || []).forEach(rt => {
      if (rt.course_code && !existingValues.has(rt.course_code)) {
        const label = rt.course_name ? `${rt.course_code} - ${rt.course_name}` : rt.course_code;
        dataCourses.set(rt.course_code, label);
      }
    });
    dataCourses.forEach((label, code) => {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = label;
      filterSelect.appendChild(opt);
      existingValues.add(code);
    });
  }

  if (currentVal && existingValues.has(currentVal)) {
    filterSelect.value = currentVal;
  }
}

let currentDashboardRequestId = 0;

function onDashboardCourseFilterChange() {
  const filterVal = $("dashboardCourseFilter")?.value || "";
  const graphCard = $("performanceGraphCard");
  const trendsWrap = $("trendChartWrap");
  const countTag = $("trendsCountTag");
  const subtitleEl = $("performanceGraphSubtitle");

  // Immediate UI update upon user action:
  // If switching to 'All Courses', immediately hide the graph card and clear stale chart content.
  // If switching to a specific course, immediately clear previous chart content and metadata so it cannot linger while loading.
  if (!filterVal) {
    if (graphCard) {
      graphCard.classList.add("hidden");
      graphCard.setAttribute("aria-hidden", "true");
    }
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
  } else {
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
    if (subtitleEl) {
      subtitleEl.textContent = `Historical score progression for ${filterVal}.`;
    }
  }

  loadDashboard();
}

async function loadDashboard() {
  const requestId = ++currentDashboardRequestId;
  const loadingEl = $("dashboardLoading");
  const errorEl = $("dashboardError");
  const emptyEl = $("dashboardEmptyState");
  const contentEl = $("dashboardContent");
  const graphCard = $("performanceGraphCard");
  const trendsWrap = $("trendChartWrap");
  const countTag = $("trendsCountTag");
  const subtitleEl = $("performanceGraphSubtitle");

  const courseFilter = $("dashboardCourseFilter")?.value || "";

  // Ensure graph is immediately hidden if 'All Courses' is selected, or clear stale chart if specific course
  if (!courseFilter) {
    if (graphCard) {
      graphCard.classList.add("hidden");
      graphCard.setAttribute("aria-hidden", "true");
    }
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
  } else {
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
    if (subtitleEl) {
      subtitleEl.textContent = `Historical score progression for ${courseFilter}.`;
    }
  }

  if (loadingEl) loadingEl.classList.remove("hidden");
  if (errorEl) errorEl.classList.add("hidden");
  if (emptyEl) emptyEl.classList.add("hidden");
  if (contentEl) contentEl.classList.add("hidden");

  try {
    await ensureAuthSession();
    const sId = getStudentId();
    if (!sId) {
      throw new Error("Unable to establish authenticated student session.");
    }
    let url = `/api/student/${encodeURIComponent(sId)}/dashboard?limit=20`;
    if (courseFilter) {
      url += `&course_code=${encodeURIComponent(courseFilter)}`;
    }

    const res = await authenticatedFetch(url);
    if (!res.ok) {
      throw new Error(`Failed to load dashboard: HTTP ${res.status}`);
    }

    const data = await res.json();
    // Guard against race conditions if rapid course changes occurred
    if (requestId !== currentDashboardRequestId) {
      return;
    }
    renderDashboard(data);
  } catch (err) {
    console.error("Dashboard load error:", err);
    // On error, guarantee stale graph data is removed and graph card is hidden
    if (graphCard) {
      graphCard.classList.add("hidden");
      graphCard.setAttribute("aria-hidden", "true");
    }
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    const countTag = $("trendsCountTag");
    if (countTag) {
      countTag.textContent = "";
    }
    if (loadingEl) loadingEl.classList.add("hidden");
    if (errorEl) {
      errorEl.classList.remove("hidden");
      const msg = $("dashboardErrorMsg");
      if (msg) msg.textContent = "Unable to load dashboard data. Please try again.";
    }
  }
}

function renderDashboard(data) {
  const loadingEl = $("dashboardLoading");
  const emptyEl = $("dashboardEmptyState");
  const contentEl = $("dashboardContent");

  if (loadingEl) loadingEl.classList.add("hidden");

  // Ensure course filter options include all relevant courses
  populateDashboardCourseFilter(data);

  const overview = data.overview || {};
  const completed = overview.completed_quizzes || 0;
  const overallCompleted = (data.overall_completed_quizzes !== undefined)
    ? Number(data.overall_completed_quizzes)
    : null;

  // hasCompleted represents whether the student has completed any quizzes overall
  const hasCompleted = (data.has_completed_quizzes !== undefined)
    ? Boolean(data.has_completed_quizzes)
    : (overallCompleted !== null ? overallCompleted > 0 : completed > 0);

  const filterEl = $("dashboardCourseFilter");
  const selectedCourse = filterEl ? (filterEl.value || "").trim() : "";
  const courseFilterActive = Boolean(selectedCourse || (data.course_code || "").trim());

  // First-time student screen is shown ONLY when the student has never completed ANY quiz
  // and no course filter is currently active. When filtering to a specific course that has
  // 0 attempts, show the course dashboard with its dedicated empty state.
  if (!hasCompleted && !courseFilterActive) {
    if (contentEl) contentEl.classList.add("hidden");
    if (emptyEl) emptyEl.classList.remove("hidden");
    return;
  }

  if (emptyEl) emptyEl.classList.add("hidden");
  if (contentEl) contentEl.classList.remove("hidden");

  renderReturningDashboard(data);
}

function getCourseDisplayName(courseCode, data = null, fallbackName = "") {
  if (fallbackName && typeof fallbackName === "string" && fallbackName.trim() && fallbackName.trim().toLowerCase() !== (courseCode || "").trim().toLowerCase()) {
    return fallbackName.trim();
  }
  const cleanCode = (courseCode || "").trim().toLowerCase();
  if (!cleanCode) return "Course Practice";

  // 1. Check global curriculum array loaded by loadCourses()
  if (Array.isArray(curriculum) && curriculum.length > 0) {
    const found = curriculum.find(c => (c.code || "").trim().toLowerCase() === cleanCode);
    if (found && found.name && found.name.trim()) {
      return found.name.trim();
    }
  }

  // 2. Check data.recent_trends if available in dashboard data
  if (data && Array.isArray(data.recent_trends)) {
    const trendMatch = data.recent_trends.find(t => (t.course_code || "").trim().toLowerCase() === cleanCode);
    if (trendMatch && trendMatch.course_name && trendMatch.course_name.trim()) {
      return trendMatch.course_name.trim();
    }
  }

  // 3. Check dropdown options in #dashboardCourseFilter or #course
  const selectIds = ["dashboardCourseFilter", "course"];
  for (const id of selectIds) {
    const sel = document.getElementById(id);
    if (!sel || !sel.options) continue;
    for (const opt of sel.options) {
      if ((opt.value || "").trim().toLowerCase() === cleanCode) {
        const text = (opt.textContent || "").trim();
        if (text.includes(" - ")) {
          const parts = text.split(" - ");
          if (parts.length > 1 && parts.slice(1).join(" - ").trim()) {
            return parts.slice(1).join(" - ").trim();
          }
        } else if (text && text.toLowerCase() !== cleanCode) {
          return text;
        }
      }
    }
  }

  // 4. Fallback lookup map for common course codes
  const KNOWN_COURSES = {
    "22am201": "Artificial Intelligence",
    "22tp103": "Programming In C",
    "22cs104": "Python Programming",
    "22cs101": "Programming In C",
    "22am301": "Deep Learning",
    "22am204": "Machine Learning",
    "22cs201": "Database Management Systems",
    "22cs207": "Operating Systems",
    "22cs204": "Computer Networks"
  };
  if (KNOWN_COURSES[cleanCode]) {
    return KNOWN_COURSES[cleanCode];
  }

  // 5. Default fallback: Never display raw alphanumeric course code like '22AM201'
  return "Course Practice";
}

function renderReturningDashboard(data) {
  const overview = data.overview || {};

  // 1. Overview Metrics
  if ($("statCompletedQuizzes")) $("statCompletedQuizzes").textContent = overview.completed_quizzes || 0;
  if ($("statTotalQuestions")) $("statTotalQuestions").textContent = overview.total_questions || 0;
  if ($("statTotalCorrectSub")) $("statTotalCorrectSub").textContent = `${overview.total_correct || 0} correct`;
  if ($("statOverallAccuracy")) $("statOverallAccuracy").textContent = `${overview.overall_accuracy || 0}%`;
  if ($("statTopicsPracticed")) $("statTopicsPracticed").textContent = overview.topics_practiced || 0;
  if ($("statLastPracticed")) {
    if (overview.last_practiced_at) {
      const d = new Date(overview.last_practiced_at);
      $("statLastPracticed").textContent = d.toLocaleDateString([], { month: "short", day: "numeric" });
    } else {
      $("statLastPracticed").textContent = "Never";
    }
  }

  // 2. Recommended Review (Topics Needing Review)
  const reviewListEl = $("reviewTopicsList");
  const reviewTopics = data.review_topics || data.recommended_topics || [];

  if (reviewListEl) {
    if (!reviewTopics.length) {
      reviewListEl.innerHTML = `
        <div class="on-track-card">
          <div class="on-track-icon" aria-hidden="true">&#x2713;</div>
          <div>
            <strong>All Practiced Topics on Track</strong>
            <p class="muted">You have maintained solid performance across practiced topics. Choose a topic from Practice Quiz to begin a new session.</p>
          </div>
        </div>
      `;
    } else {
      reviewListEl.innerHTML = reviewTopics.map((r, idx) => {
        const isHigh = r.priority === "high";
        const cardId = `rec-card-${idx}`;

        const courseName = getCourseDisplayName(r.course_code, data, r.course_name);
        const topicName = (r.topic_name || "").trim();
        const attemptsCount = (typeof r.attempt_count === "number" && r.attempt_count > 0) ? r.attempt_count : 1;
        const attemptsText = attemptsCount > 1 ? `${attemptsCount} attempts` : "1 attempt";

        const latestScore = (r.latest_score !== undefined && r.latest_score !== null) ? r.latest_score : null;
        const latestTotal = (r.latest_total !== undefined && r.latest_total !== null) ? r.latest_total : null;
        const latestAcc = (r.latest_accuracy !== undefined && r.latest_accuracy !== null)
          ? r.latest_accuracy
          : (r.recent_accuracy || 0);

        const hasComparison = Boolean(r.attempt_count > 1 && r.score_change !== null && r.score_change !== undefined);
        let deltaHtml = "";
        if (hasComparison) {
          const changeVal = r.score_change;
          const prevAcc = (r.previous_accuracy !== undefined && r.previous_accuracy !== null) ? r.previous_accuracy : "--";
          if (r.change_type === "improved" || changeVal > 0) {
            deltaHtml = `
              <span class="rec-delta rec-delta-improved" title="Improved by ${changeVal}% compared to previous attempt (${prevAcc}%)">
                <span class="delta-arrow" aria-hidden="true">&uarr;</span> +${changeVal}% vs prev (${prevAcc}%)
              </span>
            `;
          } else if (r.change_type === "declined" || changeVal < 0) {
            deltaHtml = `
              <span class="rec-delta rec-delta-declined" title="Declined by ${Math.abs(changeVal)}% compared to previous attempt (${prevAcc}%)">
                <span class="delta-arrow" aria-hidden="true">&darr;</span> ${changeVal}% vs prev (${prevAcc}%)
              </span>
            `;
          } else {
            deltaHtml = `
              <span class="rec-delta rec-delta-neutral" title="Score unchanged compared to previous attempt">
                No change (${prevAcc}%)
              </span>
            `;
          }
        } else if (r.attempt_count === 1) {
          deltaHtml = `<span class="rec-delta rec-delta-single">First attempt</span>`;
        }



        const historyItems = (Array.isArray(r.history) && r.history.length > 1) ? r.history : [];
        const hasHistory = historyItems.length > 1;

        let historyHtml = "";
        if (hasHistory) {
          const rows = historyItems.map((h, hIdx) => {
            const isLatest = hIdx === 0;
            const dateStr = h.created_at ? new Date(h.created_at).toLocaleDateString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "--";
            const rowClass = isLatest ? "history-row-current" : "";
            const attemptLabel = isLatest ? "Latest" : `#${historyItems.length - hIdx}`;
            return `
              <tr class="${rowClass}">
                <td>${isLatest ? `<strong>${attemptLabel}</strong>` : attemptLabel}</td>
                <td><strong>${h.score}/${h.total}</strong></td>
                <td>${h.accuracy}%</td>
                <td><span class="level-chip">${escapeHtml(h.recommended_difficulty || "mixed")}</span></td>
                <td>${dateStr}</td>
              </tr>
            `;
          }).join("");

          historyHtml = `
            <div id="history-panel-${cardId}" class="topic-history-panel hidden" aria-hidden="true">
              <div class="topic-history-header">Attempt History</div>
              <div class="table-container">
                <table class="history-table" aria-label="Attempt history for ${escapeHtml(r.topic_name)}">
                  <thead>
                    <tr>
                      <th scope="col">Attempt</th>
                      <th scope="col">Score</th>
                      <th scope="col">Accuracy</th>
                      <th scope="col">Level</th>
                      <th scope="col">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${rows}
                  </tbody>
                </table>
              </div>
            </div>
          `;
        }

        const topicContentHtml = topicName
          ? `<strong class="rec-topic-name">${escapeHtml(topicName)}</strong>`
          : `<span class="rec-topic-name rec-topic-muted">Topic unavailable</span>`;

        return `
          <div class="rec-card ${isHigh ? 'card-review-high' : 'card-review-adv'}" id="${cardId}">
            <div class="rec-card-body">
              <div class="rec-card-header">
                <h3 class="rec-course-name rec-title">${escapeHtml(courseName)}</h3>
                <div class="rec-topic-row">
                  <span class="rec-topic-label">Topic:</span>
                  ${topicContentHtml}
                </div>
                <div class="rec-attempts-wrap">
                  <span class="rec-attempts-pill">${escapeHtml(attemptsText)}</span>
                </div>
              </div>

              <!-- Primary Score & Progress Section -->
              <div class="rec-score-box">
                <div class="rec-score-header">
                  <div class="rec-score-primary">
                    <span class="rec-score-pct">${latestAcc}%</span>
                    ${latestScore !== null && latestTotal !== null ? `<span class="rec-score-fraction">(${latestScore}/${latestTotal})</span>` : ""}
                  </div>
                  ${deltaHtml}
                </div>
                <div class="rec-progress-track" role="progressbar" aria-valuenow="${latestAcc}" aria-valuemin="0" aria-valuemax="100" aria-label="Latest score: ${latestAcc}%">
                  <div class="rec-progress-fill ${isHigh ? 'fill-warning' : 'fill-brand'}" style="width: ${Math.min(100, Math.max(0, latestAcc))}%"></div>
                </div>
              </div>

              <p class="rec-reason">${escapeHtml(r.reason)}</p>
            </div>

            <div class="rec-card-footer">
              <div class="rec-actions">
                <button type="button" class="practice-topic-btn ${isHigh ? 'btn-primary' : 'secondary'}" data-course="${escapeHtml(r.course_code)}" data-topic="${escapeHtml(r.topic_name)}">
                  Practice Topic
                </button>
                ${hasHistory ? `
                  <button type="button" class="secondary topic-history-toggle-btn" aria-expanded="false" aria-controls="history-panel-${cardId}">
                    View history
                  </button>
                ` : ""}
              </div>
              ${historyHtml}
            </div>
          </div>
        `;
      }).join("");
    }
  }

  // Backward compatibility alias for any caller expecting recommendedTopicsList
  const recListEl = $("recommendedTopicsList");
  if (recListEl && reviewListEl) {
    recListEl.innerHTML = reviewListEl.innerHTML;
  }

  // 4. Performance Trends (Conditional Course-Specific Graph + Attempts List)
  const graphCard = $("performanceGraphCard");
  const trendsWrap = $("trendChartWrap");
  const tableWrap = $("recentAttemptsTableWrap");
  const countTag = $("trendsCountTag");
  const subtitleEl = $("performanceGraphSubtitle");

  // Determine current active course filter:
  // If the course filter element is in the DOM, its value is the user's explicit selection.
  // When 'All Courses' is selected, activeCourse is empty string.
  const filterEl = $("dashboardCourseFilter");
  const activeCourse = filterEl ? (filterEl.value || "").trim() : ((data.course_code || "").trim());

  if (!activeCourse) {
    // Rule 1 & 3: When 'All Courses' is selected, hide the performance graph completely
    if (graphCard) {
      graphCard.classList.add("hidden");
      graphCard.setAttribute("aria-hidden", "true");
    }
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
  } else {
    // Rule 2: When a specific course is selected, display the performance graph for that course only
    if (graphCard) {
      graphCard.classList.remove("hidden");
      graphCard.setAttribute("aria-hidden", "false");
    }

    if (subtitleEl) {
      subtitleEl.textContent = `Historical score progression for ${activeCourse}.`;
    }

    const rawTrends = data.recent_trends || [];
    // Ensure the graph uses data belonging exclusively to the selected course (case/whitespace resilient)
    const normActive = activeCourse.toLowerCase();
    const courseTrends = rawTrends.filter(t => (t.course_code || "").trim().toLowerCase() === normActive);

    if (trendsWrap) {
      if (!courseTrends.length) {
        if (countTag) countTag.textContent = "0 attempts";
        trendsWrap.innerHTML = `
          <div class="trend-empty-state" role="status">
            <p class="muted">No quiz attempts recorded yet for <strong>${escapeHtml(activeCourse)}</strong>. Complete a quiz to view performance trends.</p>
          </div>
        `;
      } else {
        if (countTag) {
          countTag.textContent = `${courseTrends.length} attempt${courseTrends.length > 1 ? "s" : ""}`;
        }
        const chronological = [...courseTrends].reverse();
        let chartHtml = `
          <div class="trend-chart-wrapper">
            <div class="trend-y-axis" aria-hidden="true">
              <span class="trend-y-tick">100%</span>
              <span class="trend-y-tick">50%</span>
              <span class="trend-y-tick">0%</span>
            </div>
            <div class="trend-plot-area">
              <div class="trend-grid" aria-hidden="true">
                <div class="trend-grid-line line-100"></div>
                <div class="trend-grid-line line-50"></div>
                <div class="trend-grid-line line-0"></div>
              </div>
              <div class="trend-chart" role="region" aria-label="Recent quiz performance chart for ${escapeHtml(activeCourse)}">
        `;

        chronological.forEach((t, i) => {
          const rawScore = Number(t.percentage) || 0;
          const pct = Math.max(0, Math.min(100, Math.round(rawScore)));
          const maxBarHeight = 64;
          const barHeight = Math.max(6, Math.min(maxBarHeight, Math.round((pct / 100) * maxBarHeight)));
          const barClass = t.percentage >= 70 ? "high" : (t.percentage >= 50 ? "medium" : "low");

          // Calculate score change from previous consecutive attempt
          let deltaHtml = "";
          let deltaAria = "";
          let tooltipDeltaHtml = "";

          if (i === 0) {
            deltaHtml = `<span class="trend-delta-badge initial" aria-hidden="true">1st</span>`;
            deltaAria = "Initial attempt for this course.";
            tooltipDeltaHtml = `<div class="tooltip-detail"><span class="tooltip-delta neu">First recorded attempt</span></div>`;
          } else {
            const prevScore = Number(chronological[i - 1].percentage) || 0;
            const diff = Math.round((pct - prevScore) * 10) / 10;
            if (diff > 0) {
              deltaHtml = `<span class="trend-delta-badge positive" aria-hidden="true">↑ ${diff} pp</span>`;
              deltaAria = `Score change: +${diff} percentage points compared to attempt #${i}.`;
              tooltipDeltaHtml = `<div class="tooltip-detail">Score change: <span class="tooltip-delta pos">+${diff} percentage points vs #${i}</span></div>`;
            } else if (diff < 0) {
              const absDiff = Math.abs(diff);
              deltaHtml = `<span class="trend-delta-badge negative" aria-hidden="true">↓ ${absDiff} pp</span>`;
              deltaAria = `Score change: -${absDiff} percentage points compared to attempt #${i}.`;
              tooltipDeltaHtml = `<div class="tooltip-detail">Score change: <span class="tooltip-delta neg">-${absDiff} percentage points vs #${i}</span></div>`;
            } else {
              deltaHtml = `<span class="trend-delta-badge neutral" aria-hidden="true">— 0 pp</span>`;
              deltaAria = `Score change: 0 percentage points (unchanged) compared to attempt #${i}.`;
              tooltipDeltaHtml = `<div class="tooltip-detail">Score change: <span class="tooltip-delta neu">0 percentage points vs #${i}</span></div>`;
            }
          }

          const topicName = escapeHtml(t.topic_name || "Quiz");
          const nativeTooltip = `${topicName}: ${t.score}/${t.total} (${t.percentage}%)`;
          const fullAriaLabel = `Attempt #${i + 1}: ${t.score} out of ${t.total} questions correct, ${t.percentage}% on ${topicName}. ${deltaAria}`;

          chartHtml += `
            <div class="trend-bar-wrap" tabindex="0" role="group" aria-label="${fullAriaLabel}" title="${nativeTooltip}">
              <span class="trend-bar-score">${t.percentage}%</span>
              <div class="trend-bar ${barClass}" style="height:${barHeight}px;" data-percentage="${pct}"></div>
              <div class="trend-column-footer">
                <span class="trend-bar-label">#${i + 1}</span>
                ${deltaHtml}
              </div>
              <div class="trend-tooltip" role="tooltip">
                <div class="tooltip-title">Attempt #${i + 1} • ${topicName}</div>
                <div class="tooltip-sub">${t.score}/${t.total} questions correct (${t.percentage}%)</div>
                ${tooltipDeltaHtml}
              </div>
            </div>
          `;
        });

        chartHtml += `
              </div>
            </div>
          </div>
        `;
        trendsWrap.innerHTML = chartHtml;
      }
    }
  }

  // Attempts table: displays scoped course attempts if a course is selected, or all attempts if 'All Courses'
  if (tableWrap) {
    const rawTrends = data.recent_trends || [];
    const normActive = activeCourse.toLowerCase();
    const tableTrends = activeCourse
      ? rawTrends.filter(t => (t.course_code || "").trim().toLowerCase() === normActive)
      : rawTrends;

    if (!tableTrends.length) {
      tableWrap.innerHTML = "<p class='muted' style='padding: var(--space-4) var(--space-2);'>No recent attempts recorded.</p>";
    } else {
      let tHtml = `
        <table class="history-table" aria-label="Recent quiz attempts">
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Topic</th>
              <th scope="col">Course</th>
              <th scope="col">Difficulty</th>
              <th scope="col">Score</th>
              <th scope="col">Accuracy</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
      `;
      tableTrends.forEach(t => {
        const dateStr = t.created_at ? new Date(t.created_at).toLocaleString([], {
          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
        }) : "--";
        tHtml += `
          <tr>
            <td>${dateStr}</td>
            <td><strong>${escapeHtml(t.topic_name)}</strong></td>
            <td>${escapeHtml(t.course_code)}</td>
            <td><span class="level-badge ${escapeHtml(t.recommended_difficulty || 'mixed')}">${escapeHtml(t.recommended_difficulty || 'mixed')}</span></td>
            <td>${t.score} / ${t.total}</td>
            <td><strong>${t.percentage}%</strong></td>
            <td>${escapeHtml(t.status)}</td>
          </tr>
        `;
      });
      tHtml += `</tbody></table>`;
      tableWrap.innerHTML = tHtml;
    }
  }

  // 5. Topic Progress & Mastery
  const topicListEl = $("topicProgressList");
  if (topicListEl) {
    const topics = data.topic_progress || [];
    if (!topics.length) {
      topicListEl.innerHTML = "<p class='muted'>No topic mastery records found.</p>";
    } else {
      topicListEl.innerHTML = topics.map(tp => {
        const level = tp.current_level || "mixed";
        const cumPct = Math.min(100, Math.max(0, tp.cumulative_accuracy));
        const recPct = Math.min(100, Math.max(0, tp.recent_accuracy));

        const weakChips = (tp.weak_concepts && tp.weak_concepts.length)
          ? tp.weak_concepts.map(w => `<span class="tag-chip tag-chip-weak">${escapeHtml(w)}</span>`).join("")
          : "";

        const strongChips = (tp.strong_concepts && tp.strong_concepts.length)
          ? tp.strong_concepts.map(s => `<span class="tag-chip tag-chip-strong">${escapeHtml(s)}</span>`).join("")
          : "";

        return `
          <div class="topic-card">
            <div>
              <div class="topic-card-header">
                <div>
                  <div class="quiz-course-label">${escapeHtml(tp.course_code)}</div>
                  <h3 class="topic-card-title">${escapeHtml(tp.topic_name)}</h3>
                </div>
                <span class="level-badge ${escapeHtml(level)}">${escapeHtml(level)}</span>
              </div>

              <div class="accuracy-dual-row">
                <div class="accuracy-dual-item">
                  <span class="accuracy-dual-label">Cumulative (${tp.total_questions} Qs)</span>
                  <span class="accuracy-dual-val">${tp.cumulative_accuracy}%</span>
                </div>
                <div class="accuracy-dual-item accuracy-dual-right">
                  <span class="accuracy-dual-label">Recent (${tp.recent_questions_count} Qs)</span>
                  <span class="accuracy-dual-val ${recPct >= 70 ? 'acc-high' : (recPct < 50 ? 'acc-low' : 'acc-mid')}">${tp.recent_accuracy}%</span>
                </div>
              </div>

              <div class="progress topic-progress-bar" role="progressbar" aria-valuenow="${cumPct}" aria-valuemin="0" aria-valuemax="100">
                <div class="progress-bar-fill" style="width:${cumPct}%;"></div>
              </div>

              <div class="topic-chips-wrap">
                ${weakChips}
                ${strongChips}
              </div>
            </div>

            <button type="button" class="secondary practice-topic-btn" data-course="${escapeHtml(tp.course_code)}" data-topic="${escapeHtml(tp.topic_name)}">
              Practice This Topic
            </button>
          </div>
        `;
      }).join("");
    }
  }

  // 6. Concept Insights
  const ci = data.concept_insights || {};
  if ($("dashboardStrongConcepts")) {
    const sc = ci.strong_concepts || [];
    $("dashboardStrongConcepts").innerHTML = sc.length
      ? sc.map(c => `<span class="tag-chip tag-chip-strong">${escapeHtml(c)}</span>`).join("")
      : "<span class='muted'>No strong concepts identified yet. Complete quizzes to showcase strengths.</span>";
  }

  if ($("dashboardWeakConcepts")) {
    const wc = ci.weak_concepts || [];
    $("dashboardWeakConcepts").innerHTML = wc.length
      ? wc.map(c => `<span class="tag-chip tag-chip-weak">${escapeHtml(c)}</span>`).join("")
      : "<span class='muted'>No active weak concepts identified. Great work!</span>";
  }

  attachPracticeTopicListeners();
  attachHistoryToggleListeners();
}

function attachHistoryToggleListeners() {
  document.querySelectorAll(".topic-history-toggle-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const panelId = btn.getAttribute("aria-controls");
      if (!panelId) return;
      const panel = document.getElementById(panelId);
      if (!panel) return;
      const isHidden = panel.classList.contains("hidden");
      if (isHidden) {
        panel.classList.remove("hidden");
        panel.setAttribute("aria-hidden", "false");
        btn.setAttribute("aria-expanded", "true");
        btn.textContent = "Hide history";
      } else {
        panel.classList.add("hidden");
        panel.setAttribute("aria-hidden", "true");
        btn.setAttribute("aria-expanded", "false");
        btn.textContent = "View history";
      }
    });
  });
}

function attachPracticeTopicListeners() {
  document.querySelectorAll(".practice-topic-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const courseCode = btn.getAttribute("data-course");
      const topicName = btn.getAttribute("data-topic");
      selectAndPracticeTopic(courseCode, topicName);
    });
  });
}

function selectAndPracticeTopic(courseCode, topicName) {
  if (isGenerating) return;
  goToHome();

  const courseSelect = $("course");
  const topicSelect = $("topic");

  if (courseSelect && courseCode) {
    courseSelect.value = courseCode;
    courseSelect.dispatchEvent(new Event("change"));

    setTimeout(() => {
      if (topicSelect && topicName) {
        topicSelect.value = topicName;
        topicSelect.dispatchEvent(new Event("change"));
      }
      const genBtn = $("generateBtn");
      if (genBtn) {
        genBtn.scrollIntoView({ behavior: "smooth", block: "center" });
        genBtn.focus();
      }
    }, 100);
  }
}

// ── Dashboard Navigation & Button Listeners ──────────────────────────────────
$("navPracticeBtn")?.addEventListener("click", goToHome);
$("navDashboardBtn")?.addEventListener("click", openDashboard);
$("viewDashboardFromResultBtn")?.addEventListener("click", openDashboard);
$("dashboardStartQuizBtn")?.addEventListener("click", goToHome);
$("dashboardRefreshBtn")?.addEventListener("click", loadDashboard);
$("dashboardRetryBtn")?.addEventListener("click", loadDashboard);
$("dashboardCourseFilter")?.addEventListener("change", onDashboardCourseFilterChange);

// ── Notes-to-Quiz Feature State & Handlers ──────────────────────────────────
let selectedNotesFiles = [];
let isDraggingNotes = false;
let isUploadingNotes = false;
let notesToastTimer = null;
let lastExtractionResult = null;
let selectedTopicIndex = 0;

function showNotesToast(message, type = "success") {
  const container = $("notesToastContainer");
  if (!container) return;
  if (notesToastTimer) {
    clearTimeout(notesToastTimer);
    notesToastTimer = null;
  }
  const isSuccess = type === "success";
  container.innerHTML = `
    <div class="fixed top-6 right-6 z-50 px-4 py-3 rounded shadow-lg text-white animate-fade-in ${isSuccess ? 'bg-green-600' : 'bg-red-600'}" role="alert">
      <div class="flex items-center gap-2">
        <span>${isSuccess ? '✅' : '❌'}</span>
        <span>${escapeHtml(message)}</span>
        <button type="button" class="ml-3 text-white/80 hover:text-white text-lg notes-toast-close" aria-label="Close">×</button>
      </div>
    </div>
  `;
  container.querySelector(".notes-toast-close")?.addEventListener("click", () => {
    container.innerHTML = "";
  });
  notesToastTimer = setTimeout(() => {
    container.innerHTML = "";
    notesToastTimer = null;
  }, 5000);
}

function announceNotesStatus(message) {
  const statusEl = $("notesLiveStatus");
  if (!statusEl || !message) return;
  if (statusEl.textContent === message) {
    statusEl.textContent = "";
  }
  statusEl.textContent = message;
}

function handleNotesFiles(files) {
  if (!files || files.length === 0) return;
  const maxBytes = 5 * 1024 * 1024;
  const allowedExtensions = [".jpg", ".jpeg", ".png", ".webp", ".pdf", ".docx", ".txt", ".ppt", ".pptx"];

  const fileArr = Array.from(files);
  for (const f of fileArr) {
    if (f.size > maxBytes) {
      showNotesToast(`File "${f.name}" exceeds the 5 MB limit.`, "error");
      return;
    }
    const dotIdx = f.name.lastIndexOf(".");
    const ext = dotIdx >= 0 ? f.name.slice(dotIdx).toLowerCase() : "";
    if (!allowedExtensions.includes(ext)) {
      showNotesToast(`Unsupported file type for "${f.name}". Please upload PDF, DOCX, TXT, PPT, PPTX or photos.`, "error");
      return;
    }
  }

  // Deduplicate and append
  const newFiles = fileArr.filter(
    f => !selectedNotesFiles.some(p => p.name === f.name && p.size === f.size)
  );
  if (newFiles.length === 0) {
    if (fileArr.length > 0) {
      announceNotesStatus("Selected file is already staged.");
    }
    return;
  }
  selectedNotesFiles = [...selectedNotesFiles, ...newFiles];
  renderNotesFilesUI();

  const total = selectedNotesFiles.length;
  if (newFiles.length === 1) {
    announceNotesStatus(`Staged ${newFiles[0].name}. ${total} file${total === 1 ? "" : "s"} ready for upload.`);
  } else {
    announceNotesStatus(`Staged ${newFiles.length} files. ${total} files ready for upload.`);
  }
}

function removeNotesFile(index) {
  const removedFile = selectedNotesFiles[index];
  selectedNotesFiles.splice(index, 1);
  if ($("notesFileInput")) {
    $("notesFileInput").value = "";
  }
  renderNotesFilesUI();

  const total = selectedNotesFiles.length;
  const fileName = removedFile ? removedFile.name : "File";
  if (total === 0) {
    announceNotesStatus(`Removed ${fileName}. No files staged.`);
    const dropzone = $("notesDropzone");
    if (dropzone && (document.activeElement === document.body || !document.activeElement)) {
      dropzone.focus?.();
    }
  } else {
    announceNotesStatus(`Removed ${fileName}. ${total} file${total === 1 ? "" : "s"} ready for upload.`);
    const container = $("notesFilesList");
    if (container) {
      const remainingBtns = container.querySelectorAll(".notes-pill-remove, .notes-single-remove");
      const nextIdx = Math.min(index, remainingBtns.length - 1);
      if (remainingBtns[nextIdx]) {
        remainingBtns[nextIdx].focus?.();
      }
    }
  }
}

function renderNotesFilesUI() {
  const container = $("notesFilesList");
  const uploadBtn = $("notesUploadBtn");
  if (!container || !uploadBtn) return;

  if (selectedNotesFiles.length === 0) {
    container.innerHTML = "";
    container.classList.add("hidden");
    uploadBtn.disabled = true;
    uploadBtn.className = "w-full py-2 px-4 rounded-lg font-semibold text-base transition focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-opacity-50 shadow-lg flex items-center justify-center gap-2 bg-gray-300 text-gray-500 cursor-not-allowed";
    return;
  }

  container.classList.remove("hidden");
  uploadBtn.disabled = false;
  uploadBtn.className = "w-full py-2 px-4 rounded-lg font-semibold text-base transition focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-opacity-50 shadow-lg flex items-center justify-center gap-2 bg-gray-700 text-white hover:bg-gray-800 active:scale-95";

  if (selectedNotesFiles.length === 1) {
    const file = selectedNotesFiles[0];
    const isImage = file.type.startsWith("image/");
    const previewUrl = isImage ? URL.createObjectURL(file) : null;

    container.innerHTML = `
      <div class="flex items-center gap-3 bg-gray-50 rounded-lg p-2 shadow-sm border border-gray-200 mb-2 animate-fade-in">
        ${isImage ? `
          <img src="${previewUrl}" alt="Preview" class="w-16 h-16 object-cover rounded-md border border-gray-200" />
        ` : `
          <div class="w-16 h-16 flex items-center justify-center bg-gray-200 rounded-md text-gray-500 text-xl">
            <svg class="w-7 h-7 text-gray-500" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="16" y1="13" x2="8" y2="13"></line>
              <line x1="16" y1="17" x2="8" y2="17"></line>
            </svg>
          </div>
        `}
        <div class="flex-1 truncate">
          <div class="font-medium text-gray-800 truncate" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</div>
          <div class="text-xs text-gray-500">${(file.size / 1024).toFixed(1)} KB</div>
        </div>
        <button type="button" class="ml-2 px-2 py-1 text-xs flex items-center justify-center gap-1 bg-red-50 text-red-600 rounded hover:bg-red-100 transition border border-red-200 shadow-sm notes-single-remove" aria-label="Remove ${escapeHtml(file.name)}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    `;
    container.querySelector(".notes-single-remove")?.addEventListener("click", () => {
      removeNotesFile(0);
    });
  } else {
    // Multi-file pill rendering
    container.innerHTML = `
      <div style="max-height: 180px; overflow-y: auto;" class="notes-pills-scroll-wrap">
        <div class="flex flex-wrap gap-3 items-center">
          ${selectedNotesFiles.map((f, idx) => `
            <span class="notes-pill inline-flex items-center max-w-xs pl-3 pr-1 py-1 rounded-full bg-gray-100 text-gray-800 text-sm font-medium shadow-sm border border-gray-200" style="min-width: 0;" title="${escapeHtml(f.name)}">
              <span class="truncate max-w-[120px] text-xs">${escapeHtml(f.name)}</span>
              <button type="button" class="notes-pill-remove ml-1.5 p-0.5 rounded-full hover:bg-red-100 text-red-600 transition border border-transparent focus:outline-none focus:ring-2 focus:ring-red-200" data-index="${idx}" aria-label="Remove ${escapeHtml(f.name)}">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </span>
          `).join("")}
        </div>
      </div>
    `;
    container.querySelectorAll(".notes-pill-remove").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.index, 10);
        removeNotesFile(idx);
      });
    });
  }
}

async function handleNotesUpload() {
  if (selectedNotesFiles.length === 0 || isUploadingNotes) return;

  isUploadingNotes = true;
  const uploadBtn = $("notesUploadBtn");
  const spinner = $("notesSpinner");
  const btnText = $("notesUploadBtnText");
  const progressWrap = $("notesProgressWrap");
  const progressBar = $("notesProgressBar");

  if (uploadBtn) uploadBtn.disabled = true;
  if (spinner) spinner.classList.remove("hidden");
  if (btnText) btnText.textContent = "Processing...";
  if (progressWrap) progressWrap.classList.remove("hidden");
  if (progressBar) progressBar.style.width = "40%";

  try {
    const formData = new FormData();
    selectedNotesFiles.forEach(f => {
      formData.append("files", f);
    });

    await ensureAuthSession();
    const headers = {};
    if (authToken) {
      headers["Authorization"] = `Bearer ${authToken}`;
    }

    if (progressBar) progressBar.style.width = "70%";

    const res = await fetch("/api/notes/extract-topics", {
      method: "POST",
      headers,
      body: formData
    });

    const data = await res.json();
    if (progressBar) progressBar.style.width = "100%";

    if (!res.ok) {
      const errMsg = (data && data.detail)
        ? (typeof data.detail === "object" ? JSON.stringify(data.detail) : data.detail)
        : "Unable to process files. Please verify file integrity.";
      throw new Error(errMsg);
    }

    lastExtractionResult = data;
    selectedTopicIndex = 0;

    if (data.matched_topics && data.matched_topics.length > 0) {
      showNotesToast(data.message || `Found ${data.matched_topics.length} matching curriculum topic(s).`, "success");
    } else {
      showNotesToast(data.message || "No curriculum topics matched your notes.", "error");
    }

    renderMatchedTopicsUI(data);

  } catch (err) {
    showNotesToast(err.message || "Failed to extract notes. Please try again.", "error");
  } finally {
    isUploadingNotes = false;
    if (spinner) spinner.classList.add("hidden");
    if (btnText) btnText.textContent = "Upload & Find Topics";
    if (uploadBtn) {
      uploadBtn.disabled = selectedNotesFiles.length === 0;
    }
    setTimeout(() => {
      if (progressWrap) progressWrap.classList.add("hidden");
      if (progressBar) progressBar.style.width = "0%";
    }, 400);
  }
}

function renderMatchedTopicsUI(data) {
  const card = $("notesTopicResultsCard");
  const listContainer = $("notesTopicOptions");
  const partialAlert = $("notesPartialAlert");
  const irrelevantAlert = $("notesIrrelevantAlert");
  const generateQuizBtn = $("notesGenerateQuizBtn");
  const badge = $("notesResultsBadge");

  if (!card || !listContainer) return;
  card.classList.remove("hidden");

  // Scroll smoothly to results card
  setTimeout(() => {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 100);

  if (!data || !data.relevant || !data.matched_topics || data.matched_topics.length === 0) {
    if (badge) {
      badge.className = "badge badge-error";
      badge.textContent = "No Match";
    }
    if (irrelevantAlert) irrelevantAlert.classList.remove("hidden");
    if (partialAlert) partialAlert.classList.add("hidden");
    if (generateQuizBtn) generateQuizBtn.classList.add("hidden");
    listContainer.innerHTML = "";
    if ($("notesIrrelevantMsg") && data.message) {
      $("notesIrrelevantMsg").textContent = data.message;
    }
    return;
  }

  // Relevant match found
  if (badge) {
    badge.className = "badge badge-success";
    badge.textContent = "Curriculum Match Found";
  }
  if (irrelevantAlert) irrelevantAlert.classList.add("hidden");
  if (generateQuizBtn) generateQuizBtn.classList.remove("hidden");

  if (data.partial && partialAlert) {
    partialAlert.classList.remove("hidden");
  } else if (partialAlert) {
    partialAlert.classList.add("hidden");
  }

  listContainer.innerHTML = data.matched_topics.map((t, idx) => {
    const isChecked = idx === selectedTopicIndex;
    const pct = Math.round((t.confidence || 0) * 100);
    const concepts = (t.matched_concepts || []).map(c => `
      <span class="notes-concept-chip">${escapeHtml(c)}</span>
    `).join("");

    return `
      <div class="notes-topic-item ${isChecked ? 'selected' : ''}" data-index="${idx}" role="button" tabindex="0">
        <input type="radio" name="matchedTopicRadio" class="notes-topic-radio" id="topicRadio_${idx}" value="${idx}" ${isChecked ? 'checked' : ''} />
        <div class="notes-topic-body">
          <div class="notes-topic-header-row">
            <span class="notes-course-tag">${escapeHtml(t.course_code)} - ${escapeHtml(t.course_name)}</span>
            <span class="notes-confidence-badge">${pct}% Match</span>
          </div>
          <div class="notes-topic-name">${escapeHtml(t.topic_name)}</div>
          ${concepts ? `<div class="notes-concepts-row">${concepts}</div>` : ''}
        </div>
      </div>
    `;
  }).join("");

  listContainer.querySelectorAll(".notes-topic-item").forEach(item => {
    item.addEventListener("click", () => {
      const idx = parseInt(item.dataset.index, 10);
      selectedTopicIndex = idx;
      listContainer.querySelectorAll(".notes-topic-item").forEach((el, i) => {
        const isSel = i === idx;
        el.classList.toggle("selected", isSel);
        const radio = el.querySelector(".notes-topic-radio");
        if (radio) radio.checked = isSel;
      });
    });
  });
}

function initNotesUploaderListeners() {
  const dropzone = $("notesDropzone");
  const fileInput = $("notesFileInput");
  const uploadBtn = $("notesUploadBtn");
  const generateQuizBtn = $("notesGenerateQuizBtn");
  const resetBtn = $("notesResetBtn");

  if (dropzone) {
    dropzone.addEventListener("click", () => {
      if (fileInput) fileInput.click();
    });
    dropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (fileInput) fileInput.click();
      }
    });
    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      isDraggingNotes = true;
      dropzone.classList.remove("border-gray-300", "bg-gray-50");
      dropzone.classList.add("border-blue-500", "bg-blue-50");
    });
    dropzone.addEventListener("dragleave", () => {
      isDraggingNotes = false;
      dropzone.classList.remove("border-blue-500", "bg-blue-50");
      dropzone.classList.add("border-gray-300", "bg-gray-50");
    });
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      isDraggingNotes = false;
      dropzone.classList.remove("border-blue-500", "bg-blue-50");
      dropzone.classList.add("border-gray-300", "bg-gray-50");
      if (e.dataTransfer && e.dataTransfer.files) {
        handleNotesFiles(e.dataTransfer.files);
      }
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length > 0) {
        handleNotesFiles(e.target.files);
      }
      e.target.value = "";
    });
  }

  if (uploadBtn) {
    uploadBtn.addEventListener("click", handleNotesUpload);
  }

  if (generateQuizBtn) {
    generateQuizBtn.addEventListener("click", () => {
      if (!lastExtractionResult || !lastExtractionResult.matched_topics) return;
      const topicObj = lastExtractionResult.matched_topics[selectedTopicIndex] || lastExtractionResult.matched_topics[0];
      if (!topicObj) return;

      startQuizGeneration(
        topicObj.course_code,
        topicObj.topic_name,
        false,
        topicObj.topic_id,
        lastExtractionResult.notes_session_id
      );
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      selectedNotesFiles = [];
      lastExtractionResult = null;
      selectedTopicIndex = 0;
      renderNotesFilesUI();
      announceNotesStatus("Notes files cleared.");
      if ($("notesTopicResultsCard")) {
        $("notesTopicResultsCard").classList.add("hidden");
      }
      if ($("notesUploaderCard")) {
        $("notesUploaderCard").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }

  $("navNotesBtn")?.addEventListener("click", () => {
    if (isGenerating) return;
    showScreen("notesUploadSection");
  });

  $("setupNotesBtn")?.addEventListener("click", () => {
    if (isGenerating) return;
    showScreen("notesUploadSection");
  });
}

// ── App Initialization ──────────────────────────────────────────────────────
async function initApp() {
  await ensureAuthSession();
  await loadCourses();
  initNotesUploaderListeners();
  restoreSessionState();
}

initApp();
