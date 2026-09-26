const $ = id => document.getElementById(id);

let curriculum = [];
let currentQuiz = null;
let currentIndex = 0;
let answers = [];

async function loadCourses() {
  try {
    const res = await fetch("/api/courses");
    const data = await res.json();
    curriculum = data.courses;

    curriculum.forEach(course => {
      const option = document.createElement("option");
      option.value = course.code;
      option.textContent = `${course.short} - ${course.name}`;
      $("course").appendChild(option);
    });
  } catch (e) {
    $("setupMsg").textContent = "Unable to load courses.";
  }
}

$("course").addEventListener("change", () => {
  const course = curriculum.find(c => c.code === $("course").value);
  const topicSelect = $("topic");
  topicSelect.innerHTML = "";

  if (!course) {
    topicSelect.disabled = true;
    topicSelect.innerHTML = '<option value="">Select a course first</option>';
    return;
  }

  const first = document.createElement("option");
  first.value = "";
  first.textContent = "Select a topic";
  topicSelect.appendChild(first);

  course.topics.forEach(topic => {
    const option = document.createElement("option");
    option.value = topic.name;
    option.textContent = topic.name;
    topicSelect.appendChild(option);
  });

  topicSelect.disabled = false;
});

$("generateBtn").addEventListener("click", generateQuiz);

$("prevBtn").addEventListener("click", () => {
  if (currentIndex > 0) {
    currentIndex--;
    renderQuestion();
  }
});

$("nextBtn").addEventListener("click", async () => {
  if (!answers[currentIndex]) {
    alert("Please choose an answer.");
    return;
  }

  if (currentIndex < currentQuiz.questions.length - 1) {
    currentIndex++;
    renderQuestion();
  } else {
    await submitQuiz();
  }
});

$("retakeBtn").addEventListener("click", async () => {
  $("resultSection").classList.add("hidden");
  await generateQuiz();
});

$("newTopicBtn").addEventListener("click", () => {
  $("resultSection").classList.add("hidden");
  $("quizSection").classList.add("hidden");
  $("setup").classList.remove("hidden");
  $("setupMsg").textContent = "";
});

async function generateQuiz() {
  const courseCode = $("course").value;
  const topic = $("topic").value;

  if (!courseCode || !topic) {
    $("setupMsg").textContent = "Please select both course and topic.";
    return;
  }

  $("setupMsg").textContent = "OpenAI is generating your quiz...";
  $("generateBtn").disabled = true;

  try {
    const res = await fetch("/api/quiz/generate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        course_code: courseCode,
        topic: topic
      })
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Failed to generate quiz.");
    }

    currentQuiz = data;
    currentIndex = 0;
    answers = Array(data.questions.length).fill("");

    $("setup").classList.add("hidden");
    $("resultSection").classList.add("hidden");
    $("quizSection").classList.remove("hidden");
    $("quizTitle").textContent = `${data.subject} • ${data.topic}`;
    renderQuestion();
  } catch (e) {
    $("setupMsg").textContent = e.message;
  } finally {
    $("generateBtn").disabled = false;
  }
}

function renderQuestion() {
  const q = currentQuiz.questions[currentIndex];

  $("progressText").textContent = `Question ${currentIndex + 1} of ${currentQuiz.questions.length}`;
  $("difficultyBadge").textContent = q.difficulty;
  $("concept").textContent = `Concept: ${q.concept}`;
  $("question").textContent = q.question;
  $("progressBar").style.width = `${((currentIndex + 1) / currentQuiz.questions.length) * 100}%`;

  const options = $("options");
  options.innerHTML = "";

  Object.entries(q.options).forEach(([key, value]) => {
    const div = document.createElement("div");
    div.className = "option" + (answers[currentIndex] === key ? " selected" : "");
    div.textContent = `${key}. ${value}`;

    div.addEventListener("click", () => {
      answers[currentIndex] = key;
      renderQuestion();
    });

    options.appendChild(div);
  });

  $("prevBtn").style.visibility = currentIndex === 0 ? "hidden" : "visible";
  $("nextBtn").textContent =
    currentIndex === currentQuiz.questions.length - 1 ? "Submit Quiz" : "Next";
}

async function submitQuiz() {
  $("nextBtn").disabled = true;
  $("nextBtn").textContent = "Analyzing...";

  try {
    const res = await fetch("/api/quiz/submit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        quiz_id: currentQuiz.quiz_id,
        answers: answers
      })
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Failed to submit quiz.");
    }

    showResult(data);
  } catch (e) {
    alert(e.message);
  } finally {
    $("nextBtn").disabled = false;
  }
}

function showResult(data) {
  $("quizSection").classList.add("hidden");
  $("resultSection").classList.remove("hidden");

  $("scoreText").textContent = `${data.score} / ${data.total}`;
  $("statusText").textContent = data.status;
  $("percentageText").textContent = `${data.percentage}% accuracy`;

  $("strongConcepts").innerHTML = data.strong_concepts.length
    ? data.strong_concepts.map(x => `<span class="tag-chip">${x}</span>`).join("")
    : "<span class='muted'>No strong concept identified yet.</span>";

  $("weakConcepts").innerHTML = data.weak_concepts.length
    ? data.weak_concepts.map(x => `<span class="tag-chip">${x}</span>`).join("")
    : "<span class='muted'>No major weak concept detected.</span>";

  $("analysisSummary").textContent = data.analysis.summary;
  $("analysisRevision").textContent = data.analysis.revision;
  $("analysisRecommendation").textContent = data.analysis.recommendation;

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

  window.scrollTo({top: 0, behavior: "smooth"});
}

loadCourses();
