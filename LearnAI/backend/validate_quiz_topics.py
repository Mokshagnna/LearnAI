"""
Validation Script for LearnAI Curriculum and Quiz Topics
Validates:
1. Exact file backend/data/curriculum.json integrity.
2. All courses, sections, and native quiz_topics.
3. Fallback logic when quiz_topics is empty/missing.
4. Correct course-to-topic mapping.
5. No missing fields, empty strings, nulls, or invalid data types.
"""

import sys
import json
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from services.curriculum_service import CurriculumService, normalize_topic_entry

def run_validation():
    curriculum_path = backend_dir / "data" / "curriculum.json"
    print(f"[CHECK 1] Verifying curriculum file exists at: {curriculum_path}")
    assert curriculum_path.exists(), "curriculum.json does not exist!"

    with open(curriculum_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    courses = raw_data.get("courses", [])
    print(f"[CHECK 2] Course count check: Found {len(courses)} courses.")
    assert len(courses) == 55, f"Expected 55 courses, found {len(courses)}"

    # Validate each course
    total_quiz_topics = 0
    total_orig_topics = 0
    courses_with_quiz_topics = 0

    for idx, c in enumerate(courses):
        code = c.get("code")
        name = c.get("name")
        assert code and name, f"Course index {idx} missing code or name"
        
        orig_topics = c.get("topics", [])
        assert isinstance(orig_topics, list), f"Course {code} topics must be a list"
        total_orig_topics += len(orig_topics)

        quiz_topics = c.get("quiz_topics", [])
        assert isinstance(quiz_topics, list), f"Course {code} quiz_topics must be a list"
        
        if len(quiz_topics) > 0:
            courses_with_quiz_topics += 1
            total_quiz_topics += len(quiz_topics)

            for qidx, qt in enumerate(quiz_topics):
                assert isinstance(qt, dict), f"Course {code} quiz_topic #{qidx} is not a dict"
                assert qt.get("id"), f"Course {code} quiz_topic #{qidx} missing id"
                assert qt.get("name"), f"Course {code} quiz_topic #{qidx} missing name"
                assert qt.get("parent_topic"), f"Course {code} quiz_topic #{qidx} missing parent_topic"
                assert "syllabus_context" in qt, f"Course {code} quiz_topic #{qidx} missing syllabus_context"
                assert isinstance(qt.get("concepts", []), list), f"Course {code} quiz_topic #{qidx} concepts must be list"

    print(f"[CHECK 3] Total original syllabus units: {total_orig_topics}")
    print(f"[CHECK 4] Total quiz_topics: {total_quiz_topics}")
    print(f"[CHECK 5] Courses with native quiz_topics: {courses_with_quiz_topics}/{len(courses)}")

    # Instantiate CurriculumService
    print("[CHECK 6] Loading via CurriculumService...")
    svc = CurriculumService(curriculum_path=curriculum_path)
    loaded_curr = svc.get_curriculum()
    assert len(loaded_curr["courses"]) == 55, "CurriculumService failed to load 55 courses"
    loaded_topic_count = sum(len(c["topics"]) for c in loaded_curr["courses"])
    assert loaded_topic_count == 760, f"Expected 760 topics, got {loaded_topic_count}"

    # Test specific course queries: Deep Learning
    print("[CHECK 7] Verifying Deep Learning (22AM301)...")
    dl_course = svc.get_course("22AM301")
    assert dl_course is not None, "22AM301 not found"
    assert len(dl_course["topics"]) == 20, f"Expected 20 Deep Learning quiz topics, got {len(dl_course['topics'])}"
    
    # Query by topic name
    c_res, t_res = svc.find_course_and_topic("22AM301", "Perceptron learning rule")
    assert t_res["id"] == "22AM301-Q003"
    assert t_res["parent_topic"] == "Deep Learning Intuition & Neural Network Basics"
    assert "Perceptron learning rule" in t_res["name"]

    # Query by topic ID
    c_res2, t_res2 = svc.find_course_and_topic("22AM301", "anything", topic_id="22AM301-Q014")
    assert t_res2["id"] == "22AM301-Q014"
    assert "CNN" in t_res2["name"] or "Convolution" in t_res2["name"]

    # Test Fallback Simulation
    print("[CHECK 8] Testing Fallback Behavior...")
    mock_raw = {
        "courses": [
            {
                "code": "FB101",
                "name": "Fallback Subject",
                "topics": [
                    {
                        "name": "Unit 1: Classical Methods",
                        "module": 1,
                        "unit": 1,
                        "syllabus_context": "Foundations of classical computing"
                    }
                ]
            }
        ]
    }
    mock_file = backend_dir / "data" / "_test_fallback.json"
    try:
        with open(mock_file, "w", encoding="utf-8") as f:
            json.dump(mock_raw, f)

        mock_svc = CurriculumService(curriculum_path=mock_file)
        mock_c = mock_svc.get_course("FB101")
        assert mock_c["has_native_quiz_topics"] is False
        assert len(mock_c["topics"]) == 1
        assert mock_c["topics"][0]["name"] == "Unit 1: Classical Methods"
        assert mock_c["topics"][0]["is_quiz_topic"] is False
        print("Fallback test succeeded!")
    finally:
        if mock_file.exists():
            mock_file.unlink()

    print("\n==========================================")
    print("SUCCESS: ALL VALIDATION CHECKS PASSED (55 courses, 760 quiz topics)")
    print("==========================================")

if __name__ == "__main__":
    run_validation()
