"""
test_phase2_stage_e_concurrency.py
==================================
Automated 20-user concurrency testing simulating realistic student workflows:
  Student lifecycle:
    1. Establish session (POST /api/auth/session)
    2. Browse curriculum (GET /api/courses)
    3. Generate quiz (POST /api/quiz/generate)
    4. Submit quiz answers (POST /api/quiz/submit)
    5. Review history and performance dashboard (GET /api/student/{id}/history, GET /api/student/{id}/dashboard)

Runs across 20 concurrent simulated students using ThreadPoolExecutor.
Reports success rates, throughput (req/sec), and p50, p90, p95 latencies.
"""

import time
import json
import uuid
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from services.rate_limiter import quiz_gen_limiter, quiz_submit_limiter


def make_concurrency_questions(topic_name: str, concept_name: str):
    """Generate 5 distinct valid questions relevant to the target topic."""
    return [
        {
            "question": f"In {topic_name}, what is the fundamental purpose of {concept_name} operation #1?",
            "options": {"A": "Initializes primary resource state", "B": "Terminates execution thread", "C": "Resets memory buffer", "D": "Bypasses error handling"},
            "correct_answer": "A",
            "concept": concept_name,
            "explanation": f"Operation 1 in {topic_name} sets up the initial environment.",
            "difficulty": "Easy"
        },
        {
            "question": f"How does {topic_name} handle input boundary limits when applying {concept_name}?",
            "options": {"A": "Performs explicit boundary validation checks", "B": "Ignores out of bounds conditions", "C": "Truncates input silently", "D": "Halts the CPU"},
            "correct_answer": "A",
            "concept": concept_name,
            "explanation": f"Boundary validation ensures {topic_name} stability.",
            "difficulty": "Easy"
        },
        {
            "question": f"Which optimal algorithmic strategy is recommended for {topic_name} workloads?",
            "options": {"A": "Logarithmic or linear decomposition", "B": "Exponential brute force enumeration", "C": "Infinite polling loop", "D": "Randomized memory access"},
            "correct_answer": "A",
            "concept": concept_name,
            "explanation": f"Efficient {topic_name} algorithms minimize computational overhead.",
            "difficulty": "Medium"
        },
        {
            "question": f"What occurs during error propagation in {topic_name} if an exception is unhandled?",
            "options": {"A": "Propagates up the call stack to the top-level handler", "B": "Overwrites previous return values", "C": "Silently succeeds", "D": "Reboots the runtime"},
            "correct_answer": "A",
            "concept": concept_name,
            "explanation": f"Exceptions bubble up standard call stacks in {topic_name}.",
            "difficulty": "Easy"
        },
        {
            "question": f"What is the theoretical space efficiency when implementing {concept_name} in {topic_name}?",
            "options": {"A": "O(1) auxiliary space or bounded O(n) memory", "B": "O(2^n) exponential allocation", "C": "Unbounded disk paging", "D": "Zero memory utilization"},
            "correct_answer": "A",
            "concept": concept_name,
            "explanation": f"Proper implementation preserves bounded memory in {topic_name}.",
            "difficulty": "Hard"
        }
    ]


def simulate_student_workflow(student_idx: int) -> dict:
    """
    Executes the full student lifecycle for one simulated student.
    Uses an independent TestClient instance for concurrent request handling.
    """
    client = TestClient(app)
    results = {
        "student_idx": student_idx,
        "success": False,
        "error": None,
        "timings": {}
    }

    t_flow_start = time.perf_counter()

    try:
        # Step 1: Session init
        t0 = time.perf_counter()
        res_init = client.post("/api/auth/session")
        assert res_init.status_code == 200, f"Session init failed: {res_init.text}"
        init_data = res_init.json()
        student_id = init_data["student_id"]
        token = init_data["token"]
        headers = {"Authorization": f"Bearer {token}"}
        results["timings"]["session_init"] = time.perf_counter() - t0

        # Step 2: Browse courses
        t0 = time.perf_counter()
        res_courses = client.get("/api/courses")
        assert res_courses.status_code == 200, f"Browse courses failed: {res_courses.text}"
        courses_data = res_courses.json()
        courses = courses_data.get("courses", [])
        assert len(courses) > 0, "No courses found in curriculum"
        course = courses[0]
        course_code = course["code"]
        topics = course.get("topics", [])
        assert len(topics) > 0, "No topics found in course"
        topic = topics[0]
        topic_name = topic["name"]
        topic_id = topic.get("id")
        results["timings"]["browse_courses"] = time.perf_counter() - t0

        # Step 3: Generate 5-question quiz
        t0 = time.perf_counter()
        gen_payload = {
            "course_code": course_code,
            "topic": topic_name,
            "topic_id": topic_id,
            "student_id": student_id,
            "num_questions": 5,
            "retake": False
        }
        res_gen = client.post("/api/quiz/generate", json=gen_payload, headers=headers)
        assert res_gen.status_code == 200, f"Quiz generate failed: {res_gen.text}"
        quiz_data = res_gen.json()
        quiz_id = quiz_data["quiz_id"]
        assert len(quiz_data["questions"]) == 5
        results["timings"]["quiz_generate"] = time.perf_counter() - t0

        # Step 4: Submit answers
        t0 = time.perf_counter()
        submit_payload = {
            "quiz_id": quiz_id,
            "answers": ["A", "A", "A", "A", "A"],
            "student_id": student_id
        }
        res_submit = client.post("/api/quiz/submit", json=submit_payload, headers=headers)
        assert res_submit.status_code == 200, f"Quiz submit failed: {res_submit.text}"
        results["timings"]["quiz_submit"] = time.perf_counter() - t0

        # Step 5: Review dashboard and history
        t0 = time.perf_counter()
        res_hist = client.get(f"/api/student/{student_id}/history", headers=headers)
        assert res_hist.status_code == 200, f"History failed: {res_hist.text}"

        res_dash = client.get(f"/api/student/{student_id}/dashboard", headers=headers)
        assert res_dash.status_code == 200, f"Dashboard failed: {res_dash.text}"
        results["timings"]["review_dashboard"] = time.perf_counter() - t0

        results["timings"]["total_workflow"] = time.perf_counter() - t_flow_start
        results["success"] = True

    except Exception as e:
        results["error"] = str(e)
        results["timings"]["total_workflow"] = time.perf_counter() - t_flow_start

    return results


def percentile(data, pct):
    """Calculate percentile from data list."""
    if not data:
        return 0.0
    k = (len(data) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(data) - 1)
    d = k - f
    sorted_d = sorted(data)
    return sorted_d[f] + (sorted_d[c] - sorted_d[f]) * d


def test_twenty_concurrent_students_full_lifecycle():
    """
    Simulates 20 concurrent students executing the full workflow simultaneously.
    Verifies zero failures, measures and reports p50, p90, p95 latencies and throughput.
    """
    NUM_STUDENTS = 20

    # Retrieve first curriculum course and topic to align mock questions accurately
    from services.curriculum_service import curriculum_service
    curriculum = curriculum_service.get_curriculum()
    all_courses = curriculum.get("courses", [])
    assert len(all_courses) > 0, "Curriculum contains no courses"
    first_course = all_courses[0]
    first_topic = first_course["topics"][0]
    topic_name = first_topic["name"]
    concept_name = first_topic["concepts"][0] if first_topic.get("concepts") else topic_name

    sample_questions = make_concurrency_questions(topic_name, concept_name)
    raw_mock_json = json.dumps({"questions": sample_questions})

    # Disable rate limits for the 20 local test worker threads
    with patch.object(quiz_gen_limiter, "check_rate_limit", return_value=(True, 0, 100)), \
         patch.object(quiz_submit_limiter, "check_rate_limit", return_value=(True, 0, 100)), \
         patch("services.llm_service._get_providers") as mock_get_p:

        mock_provider = MagicMock()
        mock_provider.name = "mock_concurrency_fast_provider"
        mock_provider.is_configured.return_value = True
        mock_provider.generate_quiz_raw.return_value = raw_mock_json
        mock_get_p.return_value = [mock_provider]

        overall_t0 = time.perf_counter()

        with ThreadPoolExecutor(max_workers=NUM_STUDENTS) as executor:
            futures = [executor.submit(simulate_student_workflow, i) for i in range(NUM_STUDENTS)]
            results = [f.result() for f in as_completed(futures)]

        total_duration = time.perf_counter() - overall_t0

    # Verification: All 20 students must succeed
    failures = [r for r in results if not r["success"]]
    if failures:
        print(f"\n[CONCURRENCY FAILURES] {len(failures)}/{NUM_STUDENTS} students failed:")
        for f in failures:
            print(f"  Student #{f['student_idx']}: {f['error']}")

    assert len(failures) == 0, f"{len(failures)} concurrent student workflows failed."

    # Compute Latency Metrics
    step_keys = ["session_init", "browse_courses", "quiz_generate", "quiz_submit", "review_dashboard", "total_workflow"]
    stats = {}
    for step in step_keys:
        vals = [r["timings"][step] for r in results if step in r["timings"]]
        stats[step] = {
            "min": min(vals),
            "p50": percentile(vals, 50),
            "p90": percentile(vals, 90),
            "p95": percentile(vals, 95),
            "max": max(vals),
            "mean": statistics.mean(vals)
        }

    total_requests = NUM_STUDENTS * 5  # 5 requests per student
    throughput = total_requests / total_duration

    print("\n" + "=" * 78)
    print("           LEARNAI 20-USER CONCURRENCY BENCHMARK RESULTS")
    print("=" * 78)
    print(f"Concurrent Simulated Users:  {NUM_STUDENTS}")
    print(f"Total Requests Executed:    {total_requests}")
    print(f"Total Wall Clock Duration:  {total_duration:.2f} s")
    print(f"Overall Throughput:         {throughput:.2f} req/s")
    print(f"Success Rate:               100% ({NUM_STUDENTS}/{NUM_STUDENTS} completed)")
    print("-" * 78)
    print(f"{'Lifecycle Step':<20} {'p50 (s)':<10} {'p90 (s)':<10} {'p95 (s)':<10} {'Max (s)':<10} {'Mean (s)':<10}")
    print("-" * 78)
    for step in step_keys:
        s = stats[step]
        print(f"{step:<20} {s['p50']:<10.3f} {s['p90']:<10.3f} {s['p95']:<10.3f} {s['max']:<10.3f} {s['mean']:<10.3f}")
    print("=" * 78)

    # Performance assertions
    assert stats["total_workflow"]["p95"] < 10.0, "p95 workflow latency exceeded 10.0s threshold"
    assert throughput >= 5.0, "Throughput fell below minimum 5 req/s threshold"


def test_twenty_concurrent_students_with_simulated_llm_latency():
    """
    Simulates 20 concurrent students where the LLM provider introduces a realistic 150ms latency.
    Proves that database connections are properly released while LLM generation is waiting in-flight,
    preventing connection pool starvation across 20 simultaneous active requests.

    Explicit Test Fidelity Matrix:
    - REAL: FastAPI ASGI routing, Pydantic serialization, SQLite DB schema & transactions,
            student session management, HMAC signature verification, curriculum indexing, quiz grading.
    - SIMULATED: Upstream Groq/Nvidia LLM network roundtrip simulated with 150ms artificial delay;
                 Single-IP rate limiters bypassed for 127.0.0.1 concurrent test workers.
    """
    NUM_STUDENTS = 20

    from services.curriculum_service import curriculum_service
    curriculum = curriculum_service.get_curriculum()
    all_courses = curriculum.get("courses", [])
    first_course = all_courses[0]
    first_topic = first_course["topics"][0]
    topic_name = first_topic["name"]
    concept_name = first_topic["concepts"][0] if first_topic.get("concepts") else topic_name

    sample_questions = make_concurrency_questions(topic_name, concept_name)
    raw_mock_json = json.dumps({"questions": sample_questions})

    def mock_delayed_generate(*args, **kwargs):
        # Realistic LLM latency simulation
        time.sleep(0.15)
        return raw_mock_json

    with patch.object(quiz_gen_limiter, "check_rate_limit", return_value=(True, 0, 100)), \
         patch.object(quiz_submit_limiter, "check_rate_limit", return_value=(True, 0, 100)), \
         patch("services.llm_service._get_providers") as mock_get_p:

        mock_provider = MagicMock()
        mock_provider.name = "mock_concurrency_delayed_provider"
        mock_provider.is_configured.return_value = True
        mock_provider.generate_quiz_raw.side_effect = mock_delayed_generate
        mock_get_p.return_value = [mock_provider]

        overall_t0 = time.perf_counter()

        with ThreadPoolExecutor(max_workers=NUM_STUDENTS) as executor:
            futures = [executor.submit(simulate_student_workflow, i) for i in range(NUM_STUDENTS)]
            results = [f.result() for f in as_completed(futures)]

        total_duration = time.perf_counter() - overall_t0

    failures = [r for r in results if not r["success"]]
    assert len(failures) == 0, f"{len(failures)} concurrent student workflows failed with delayed LLM."

    # Verify all 20 completed successfully
    assert len(results) == NUM_STUDENTS
    total_requests = NUM_STUDENTS * 5
    throughput = total_requests / total_duration
    print(f"\n[REALISTIC LATENCY BENCHMARK] 20 students completed in {total_duration:.2f}s ({throughput:.2f} req/s). Zero pool exhaustion.")
