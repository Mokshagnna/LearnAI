const $ = id => document.getElementById(id);

// ── Application State ────────────────────────────────────────────────────────
let curriculum = [];
let currentQuiz = null;
let currentIndex = 0;
let answers = [];
let activeCourseCode = "";
let activeTopicName = "";
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

// ── Mutually Exclusive Screen Management ────────────────────────────────────
// Ensures EXACTLY ONE screen is visible at any time.
const ALL_SCREENS = ["setup", "loadingSection", "quizSection", "resultSection"];

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
}

// ── Session State Persistence across Browser Refresh ─────────────────────────
function saveSessionState() {
  if (currentQuiz && currentQuiz.questions) {
    sessionStorage.setItem("learnai_active_quiz", JSON.stringify({
      currentQuiz,
      currentIndex,
      answers,
      activeCourseCode,
      activeTopicName
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
  try {
    const res = await fetch("/api/courses");
    const data = await res.json();
    curriculum = data.courses || [];

    const courseSelect = $("course");
    courseSelect.innerHTML = '<option value="">Select a course</option>';
    curriculum.forEach(course => {
      const option = document.createElement("option");
      option.value = course.code;
      option.textContent = `${course.code} - ${course.name}`;
      courseSelect.appendChild(option);
    });
  } catch (e) {
    $("setupMsg").textContent = "Unable to load courses. Please check if the server is running.";
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

  (course.topics || []).forEach(topic => {
    const option = document.createElement("option");
    option.value = topic.name;
    option.textContent = topic.name;
    topicSelect.appendChild(option);
  });

  topicSelect.disabled = false;
  topicSelect.value = "";
});

// ── Navigation Buttons ───────────────────────────────────────────────────────
$("generateBtn").addEventListener("click", () => {
  const courseCode = $("course").value;
  const topic = $("topic").value;

  if (!courseCode || !topic) {
    $("setupMsg").innerHTML = '<span style="color:#b4493a;font-weight:600;">Please select both course and topic.</span>';
    return;
  }
  $("setupMsg").innerHTML = "";

  startQuizGeneration(courseCode, topic, false);
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
  await startQuizGeneration(courseCode, topic, true);

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
async function startQuizGeneration(courseCode, topic, isRetake = false) {
  if (isGenerating) return;
  isGenerating = true;

  const generateBtn = $("generateBtn");
  const retakeBtn = $("retakeBtn");
  if (generateBtn) generateBtn.disabled = true;
  if (retakeBtn) retakeBtn.disabled = true;

  activeCourseCode = courseCode;
  activeTopicName = topic;

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

    // ── Success: Initialize Clean Quiz State ──
    currentQuiz = data;
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

    let friendlyMsg = "AI service is temporarily experiencing high demand. Please try again.";
    if (e.name === "AbortError") {
      friendlyMsg = "Quiz generation was cancelled or timed out. Please try again.";
    } else if (e.message && !e.message.includes("{") && !e.message.includes("500") && !e.message.includes("503")) {
      friendlyMsg = e.message;
    }

    if ($("loadingErrorMsg")) $("loadingErrorMsg").textContent = `⚠️ ${friendlyMsg}`;
    if ($("loadingErrorBox")) $("loadingErrorBox").classList.remove("hidden");

    // Configure retry button
    const retryBtn = $("loadingRetryBtn");
    if (retryBtn) {
      retryBtn.onclick = () => {
        startQuizGeneration(courseCode, topic, isRetake);
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
    $("quizCourse").textContent = courseName;
  }
  if ($("quizTitle")) {
    $("quizTitle").textContent = quizData.topic;
  }
  if ($("quizAttemptNumber")) {
    const attempt = quizData.attempt_number || 1;
    $("quizAttemptNumber").textContent = attempt > 1 ? `Attempt #${attempt}` : "Attempt #1";
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

  Object.entries(q.options).forEach(([key, value]) => {
    const isSelected = answers[currentIndex] === key;
    const div = document.createElement("div");
    div.className = "option" + (isSelected ? " selected" : "");
    div.setAttribute("role", "radio");
    div.setAttribute("aria-checked", isSelected ? "true" : "false");
    div.setAttribute("tabindex", "0");
    div.innerHTML = `
      <span class="radio-circle" aria-hidden="true">
        <span class="radio-dot"></span>
      </span>
      <span class="option-letter">${key}.</span>
      <span class="option-text">${value}</span>
    `;

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
    data.details.map((d, i) => `
      <div class="review-item">
        <div><strong>Q${i + 1}. ${d.question}</strong></div>
        <div class="${d.is_correct ? "correct" : "wrong"}">
          ${d.is_correct ? "Correct" : "Incorrect"}
        </div>
        <div>Your answer: ${d.selected_answer || "Not answered"}</div>
        <div>Correct answer: ${d.correct_answer}</div>
        <div class="muted">${d.explanation}</div>
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

// ── App Initialization ──────────────────────────────────────────────────────
async function initApp() {
  await loadCourses();
  restoreSessionState();
}

initApp();
