const $ = id => document.getElementById(id);

// ── Application State ────────────────────────────────────────────────────────
let curriculum = [];
let currentQuiz = null;
let currentIndex = 0;
let answers = [];
let activeCourseCode = "";
let activeTopicName = "";
let activeTopicId = "";
let isGenerating = false;
let activeAbortController = null;
let studentId = localStorage.getItem("learnai_student_id") || null;

// ── Student ID Management ───────────────────────────────────────────────────
function getStudentId() {
  if (!studentId) {
    studentId = crypto.randomUUID();
    localStorage.setItem("learnai_student_id", studentId);
  }
  return studentId;
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
const ALL_SCREENS = ["setup", "loadingSection", "quizSection", "resultSection", "dashboardSection"];

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
  $("navDashboardBtn")?.classList.toggle("active", isDashboard);
  $("navPracticeBtn")?.classList.toggle("active", !isDashboard);
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
      activeTopicId
    }));
  }
}

function clearSessionState() {
  sessionStorage.removeItem("learnai_active_quiz");
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
      $("setupMsg").innerHTML = '<span style="color:#b4493a;font-weight:600;">No courses available in curriculum.</span>';
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
  } catch (e) {
    console.error("Failed to load courses:", e);
    if (courseSelect) {
      courseSelect.innerHTML = '<option value="">Error loading courses</option>';
    }
    $("setupMsg").innerHTML = '<span style="color:#b4493a;font-weight:600;">Unable to load courses. Please check if the server is running.</span>';
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
    $("setupMsg").innerHTML = '<span style="color:#b4493a;font-weight:600;">Please select both course and topic.</span>';
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
      alertEl.textContent = "⚠️ Please choose an answer to proceed.";
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
    retakeBtn.textContent = "🔄 Retake with New AI Questions";
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
  await startQuizGeneration(courseCode, topic, true, topicId);

  retakeBtn.disabled = false;
  retakeBtn.textContent = "🔄 Retake with New AI Questions";
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
    retakeBtn.textContent = "🔄 Retake with New AI Questions";
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
async function startQuizGeneration(courseCode, topic, isRetake = false, topicId = null) {
  if (isGenerating) return;
  isGenerating = true;

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
    if ($("loadingTopicLabel")) {
      $("loadingTopicLabel").textContent = `${courseLabel} • ${topic}`;
    }

    showScreen("loadingSection");

    activeAbortController = new AbortController();
    const timeoutSec = 50;
    timeoutId = setTimeout(() => activeAbortController?.abort(), timeoutSec * 1000);

    const res = await fetch("/api/quiz/generate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        course_code: courseCode,
        topic: topic,
        topic_id: activeTopicId || undefined,
        student_id: getStudentId(),
        retake: isRetake
      }),
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

    if ($("loadingErrorMsg")) $("loadingErrorMsg").textContent = `⚠️ ${friendlyMsg}`;
    if ($("loadingErrorBox")) $("loadingErrorBox").classList.remove("hidden");

    // Configure retry button
    const retryBtn = $("loadingRetryBtn");
    if (retryBtn) {
      retryBtn.onclick = () => {
        startQuizGeneration(courseCode, topic, isRetake, topicId);
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
    const res = await fetch("/api/quiz/submit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        quiz_id: currentQuiz.quiz_id,
        answers: answers,
        student_id: getStudentId()
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
    const res = await fetch(`/api/student/${sId}/history?limit=10`);
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
      const rowStyle = isCurrent ? "font-weight:700; background:#f0f7f3;" : "";

      tableHtml += `
        <tr style="${rowStyle}">
          <td>${isCurrent ? '⭐ Current (#' + attemptNum + ')' : 'Attempt #' + attemptNum}</td>
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

function populateDashboardCourseFilter() {
  const filterSelect = $("dashboardCourseFilter");
  if (!filterSelect || filterSelect.options.length > 1) return;

  filterSelect.innerHTML = '<option value="">All Courses</option>';
  (curriculum || []).forEach(course => {
    const opt = document.createElement("option");
    opt.value = course.code;
    opt.textContent = `${course.code} - ${course.name}`;
    filterSelect.appendChild(opt);
  });
}

async function loadDashboard() {
  const loadingEl = $("dashboardLoading");
  const errorEl = $("dashboardError");
  const emptyEl = $("dashboardEmptyState");
  const contentEl = $("dashboardContent");

  if (loadingEl) loadingEl.classList.remove("hidden");
  if (errorEl) errorEl.classList.add("hidden");
  if (emptyEl) emptyEl.classList.add("hidden");
  if (contentEl) contentEl.classList.add("hidden");

  try {
    const sId = getStudentId();
    const courseFilter = $("dashboardCourseFilter")?.value || "";
    let url = `/api/student/${encodeURIComponent(sId)}/dashboard?limit=20`;
    if (courseFilter) {
      url += `&course_code=${encodeURIComponent(courseFilter)}`;
    }

    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Failed to load dashboard: HTTP ${res.status}`);
    }

    const data = await res.json();
    renderDashboard(data);
  } catch (err) {
    console.error("Dashboard load error:", err);
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

  const overview = data.overview || {};
  const completed = overview.completed_quizzes || 0;

  if (completed === 0) {
    if (emptyEl) emptyEl.classList.remove("hidden");
    if (contentEl) contentEl.classList.add("hidden");
    return;
  }

  if (emptyEl) emptyEl.classList.add("hidden");
  if (contentEl) contentEl.classList.remove("hidden");

  // 1. Overview Metrics
  if ($("statCompletedQuizzes")) $("statCompletedQuizzes").textContent = overview.completed_quizzes;
  if ($("statTotalQuestions")) $("statTotalQuestions").textContent = overview.total_questions;
  if ($("statTotalCorrectSub")) $("statTotalCorrectSub").textContent = `${overview.total_correct} correct`;
  if ($("statOverallAccuracy")) $("statOverallAccuracy").textContent = `${overview.overall_accuracy}%`;
  if ($("statTopicsPracticed")) $("statTopicsPracticed").textContent = overview.topics_practiced;
  if ($("statLastPracticed")) {
    if (overview.last_practiced_at) {
      const d = new Date(overview.last_practiced_at);
      $("statLastPracticed").textContent = d.toLocaleDateString([], { month: "short", day: "numeric" });
    } else {
      $("statLastPracticed").textContent = "Never";
    }
  }

  // 2. Recommended Topics Next
  const recListEl = $("recommendedTopicsList");
  if (recListEl) {
    const recs = data.recommended_topics || [];
    if (!recs.length) {
      recListEl.innerHTML = "<p class='muted'>No recommendations available at this time.</p>";
    } else {
      recListEl.innerHTML = recs.map(r => {
        const priorityClass = r.priority || "medium";
        const priorityLabel = r.priority === "high" ? "Focus Area" : (r.priority === "medium" ? "Recommended" : "Explore");
        return `
          <div class="rec-card ${priorityClass}">
            <div>
              <div class="rec-meta">
                <span class="rec-priority ${priorityClass}">${escapeHtml(priorityLabel)}</span>
                <span class="tag-chip" style="font-size:11px; margin:0;">${escapeHtml(r.recommended_difficulty || "mixed")}</span>
              </div>
              <h3 class="rec-title">${escapeHtml(r.topic_name)}</h3>
              <div class="quiz-course-label" style="margin-bottom:8px;">${escapeHtml(r.course_code)}</div>
              <div class="rec-reason">${escapeHtml(r.reason)}</div>
            </div>
            <button type="button" class="practice-topic-btn" data-course="${escapeHtml(r.course_code)}" data-topic="${escapeHtml(r.topic_name)}">
              Practice Topic
            </button>
          </div>
        `;
      }).join("");
    }
  }

  // 3. Performance Trends (Chart + List)
  const trendsWrap = $("trendChartWrap");
  const tableWrap = $("recentAttemptsTableWrap");
  const trends = data.recent_trends || [];

  if (trendsWrap) {
    if (!trends.length) {
      trendsWrap.innerHTML = "<p class='muted'>No recent attempts.</p>";
    } else {
      const chronological = [...trends].reverse();
      let chartHtml = `<div class="trend-chart" role="img" aria-label="Recent quiz performance chart">`;
      chronological.forEach((t, i) => {
        const pct = Math.max(6, Math.min(100, Math.round(t.percentage)));
        const barClass = t.percentage >= 70 ? "high" : (t.percentage >= 50 ? "medium" : "low");
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

  if (tableWrap) {
    if (!trends.length) {
      tableWrap.innerHTML = "";
    } else {
      let tHtml = `
        <table class="history-table" style="margin-top:0;">
          <thead>
            <tr>
              <th>Date</th>
              <th>Topic</th>
              <th>Course</th>
              <th>Difficulty</th>
              <th>Score</th>
              <th>Accuracy</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
      `;
      trends.forEach(t => {
        const dateStr = new Date(t.created_at).toLocaleString([], {
          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
        });
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

  // 4. Topic Progress & Mastery
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
          ? tp.weak_concepts.map(w => `<span class="tag-chip" style="background:#fdeeee; color:#b4493a; font-size:11px;">⚠️ ${escapeHtml(w)}</span>`).join("")
          : "";

        const strongChips = (tp.strong_concepts && tp.strong_concepts.length)
          ? tp.strong_concepts.map(s => `<span class="tag-chip" style="background:#e7f4ed; color:#103f2d; font-size:11px;">✓ ${escapeHtml(s)}</span>`).join("")
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
                <div class="accuracy-dual-item" style="text-align:right;">
                  <span class="accuracy-dual-label">Recent (${tp.recent_questions_count} Qs)</span>
                  <span class="accuracy-dual-val" style="color:${recPct >= 70 ? '#16794f' : (recPct < 50 ? '#b4493a' : '#e67e22')}">${tp.recent_accuracy}%</span>
                </div>
              </div>

              <div class="progress" style="margin-bottom:12px;" role="progressbar" aria-valuenow="${cumPct}" aria-valuemin="0" aria-valuemax="100">
                <div style="width:${cumPct}%; height:100%; background:#136b49;"></div>
              </div>

              <div style="margin-bottom:14px;">
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

  // 5. Concept Insights
  const ci = data.concept_insights || {};
  if ($("dashboardStrongConcepts")) {
    const sc = ci.strong_concepts || [];
    $("dashboardStrongConcepts").innerHTML = sc.length
      ? sc.map(c => `<span class="tag-chip" style="background:#e7f4ed; color:#103f2d;">✓ ${escapeHtml(c)}</span>`).join("")
      : "<span class='muted'>No strong concepts identified yet. Complete quizzes to showcase strengths.</span>";
  }

  if ($("dashboardWeakConcepts")) {
    const wc = ci.weak_concepts || [];
    $("dashboardWeakConcepts").innerHTML = wc.length
      ? wc.map(c => `<span class="tag-chip" style="background:#fdeeee; color:#b4493a;">⚠️ ${escapeHtml(c)}</span>`).join("")
      : "<span class='muted'>No active weak concepts! Great work.</span>";
  }

  attachPracticeTopicListeners();
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
$("dashboardCourseFilter")?.addEventListener("change", loadDashboard);

// ── App Initialization ──────────────────────────────────────────────────────
async function initApp() {
  await loadCourses();
  restoreSessionState();
}

initApp();
