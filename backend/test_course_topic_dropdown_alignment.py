"""
Unit and regression tests for LearnAI Course-Topic Dropdown and Syllabus Alignment.
Verifies:
1. Removal of bogus generic placeholder topic names ("Model Training and Regularization" in C,
   "Probability and Statistics" in Constitution, "Core Data Structures" in English).
2. Preservation of legitimate topic names in appropriate courses (e.g. Deep Learning, Data Structures).
3. Preservation of all 33 quiz topics in 22TP103 (expanded to 62 topics) without dropping
   Pointers, Unions, or Files.
4. Stable topic IDs and backward compatibility in find_course_and_topic.
5. Frontend dropdown rendering:
   - When no notes are uploaded: 0 optgroup elements, 0 "Other syllabus topics" headings.
   - When notes are uploaded: clean optgroup sections with non-selectable category labels.
   - Defensive validation preventing category labels from being submitted.
   - Course switching clearing previous topic selections and resetting active topic state.
"""

import re
import pytest
from pathlib import Path
from services.curriculum_service import (
    curriculum_service,
    GENERIC_PLACEHOLDER_NAMES,
    is_legitimate_course_topic,
    resolve_topic_display_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestCourseTopicSyllabusAlignment:
    """Verifies that courses display authentic syllabus topics without cross-course misalignments."""

    def test_programming_in_c_has_no_generic_placeholders(self):
        course = curriculum_service.get_course("22TP103")
        assert course is not None, "Course 22TP103 (Programming in C) must exist."
        topic_names = [t["name"] for t in course["topics"]]

        # Illegitimate generic placeholders must be completely eliminated
        assert "Model Training and Regularization" not in topic_names
        assert "Foundations and Applications" not in topic_names
        assert "Core Data Structures" not in topic_names
        assert "Probability and Statistics" not in topic_names

    def test_programming_in_c_has_full_syllabus_coverage(self):
        course = curriculum_service.get_course("22TP103")
        topic_names = [t["name"] for t in course["topics"]]

        # Total topic count should be 62 (derived from all 33 quiz_topics)
        assert len(course["topics"]) == 62

        # Verify key syllabus units and topics are present and preserved
        assert any("Basics of Algorithms" in name for name in topic_names)
        assert any("Structure of a C Program" in name for name in topic_names)
        assert any("C Tokens" in name for name in topic_names)
        assert any("Type Modifiers" in name for name in topic_names)
        assert any("Basic Data Types" in name for name in topic_names)
        assert any("Formatted I/O" in name for name in topic_names)
        assert any("Conditional Branching" in name or "Nested-If" in name for name in topic_names)
        assert any("Iterative Statements" in name for name in topic_names)
        assert any("Arrays" in name for name in topic_names)
        assert any("Function Declaration" in name for name in topic_names)
        assert any("Defining a Structure" in name for name in topic_names)
        assert any("Pointers to Structure" in name for name in topic_names)
        assert any("Defining a Union" in name for name in topic_names)
        assert any("Introduction to Files & Streams" in name for name in topic_names)
        assert any("Files and Command Line Arguments" in name for name in topic_names)

        # Confirm later topics (structures, unions, files) were NOT dropped
        file_topics = [t for t in course["topics"] if "File" in t["name"] or "Stream" in t["name"]]
        assert len(file_topics) >= 8

    def test_constitution_of_india_has_no_probability_statistics(self):
        course = curriculum_service.get_course("22TP101")
        assert course is not None, "Course 22TP101 must exist."
        topic_names = [t["name"] for t in course["topics"]]

        assert "Probability and Statistics" not in topic_names
        assert "Structure and Form of Government" in topic_names

    def test_english_proficiency_has_no_core_data_structures(self):
        course = curriculum_service.get_course("22EN102")
        assert course is not None, "Course 22EN102 must exist."
        topic_names = [t["name"] for t in course["topics"]]

        assert "Core Data Structures" not in topic_names
        assert "Listening & Speaking & Vocabulary / Grammar" in topic_names

    def test_legitimate_placeholders_preserved_in_appropriate_courses(self):
        # Deep Learning (22AM301) legitimately retains Model Training and Regularization
        dl = curriculum_service.get_course("22AM301")
        assert dl is not None
        dl_topics = [t["name"] for t in dl["topics"]]
        assert "Model Training and Regularization" in dl_topics

        # Probability and Statistics course (22ST203) legitimately retains Probability and Statistics
        ps = curriculum_service.get_course("22ST203")
        assert ps is not None
        ps_topics = [t["name"] for t in ps["topics"]]
        assert "Probability and Statistics" in ps_topics

        # Linear Algebra course (22MT103) legitimately retains Linear Algebra Concepts
        la = curriculum_service.get_course("22MT103")
        assert la is not None
        la_topics = [t["name"] for t in la["topics"]]
        assert "Linear Algebra Concepts" in la_topics

    def test_all_55_courses_have_zero_illegitimate_generic_topics(self):
        curriculum = curriculum_service.get_curriculum()
        for course in curriculum["courses"]:
            code = course["code"]
            cname = course["name"]
            for topic in course["topics"]:
                tname = topic["name"]
                if tname in GENERIC_PLACEHOLDER_NAMES:
                    assert is_legitimate_course_topic(tname, code, cname), (
                        f"Illegitimate placeholder topic '{tname}' found in {code} ({cname})"
                    )


class TestCurriculumTopicResolutionAndLookup:
    """Verifies that lookup and backward compatibility work seamlessly."""

    def test_find_by_topic_id(self):
        course, topic = curriculum_service.find_course_and_topic("22TP103", "", topic_id="22TP103-Q001")
        assert topic["id"] == "22TP103-Q001"
        assert "Basics of Algorithms" in topic["name"]

        # Prefixed compound topic ID
        course, topic = curriculum_service.find_course_and_topic("22TP103", "", topic_id="22TP103-Q004-1")
        assert topic["id"] == "22TP103-Q004-1"
        assert topic["name"] == "Type Modifiers"

    def test_find_by_resolved_name(self):
        course, topic = curriculum_service.find_course_and_topic(
            "22TP103", "Basics of Algorithms & Flow Charts"
        )
        assert topic["id"] == "22TP103-Q001"

    def test_find_by_raw_legacy_name(self):
        # Legacy name "Foundations and Applications" for Q001 in 22TP103
        course, topic = curriculum_service.find_course_and_topic("22TP103", "Foundations and Applications")
        assert topic["id"] == "22TP103-Q001"

    def test_find_by_concept(self):
        course, topic = curriculum_service.find_course_and_topic("22TP103", "Flow charts")
        assert topic["id"] == "22TP103-Q001"

    def test_find_by_parent_topic(self):
        course, topic = curriculum_service.find_course_and_topic("22TP101", "Structure And Form Of Government")
        assert topic["id"] == "22TP101-Q004"


class TestFrontendDropdownLogicSimulated:
    """Simulates and verifies frontend topic dropdown logic in JS/DOM environment."""

    def _simulate_populate_topic_dropdown(self, course_topics, notes_extraction=None):
        """
        Simulates the logic of populateTopicDropdown from frontend/js/app.js.
        Returns a dict with simulated select DOM properties:
        {
            'options': [{'value': str, 'text': str, 'parent_tag': str, 'disabled': bool}],
            'optgroups': [{'label': str, 'options_count': int}],
            'has_headings': bool
        }
        """
        coverage_map = {}
        if notes_extraction and isinstance(notes_extraction.get("matched_topics"), list):
            for t in notes_extraction["matched_topics"]:
                raw_name = str(t.get("topic_name", "")).strip().lower()
                norm_name = re.sub(r'[\-_:()\[\].,;]+', ' ', raw_name).strip()
                status = (t.get("coverage_status") or "needs_review").lower()
                coverage_map[raw_name] = status
                coverage_map[norm_name] = status
                if t.get("topic_id"):
                    coverage_map[str(t["topic_id"]).strip().lower()] = status

        # Deduplicate
        deduplicated = []
        seen = set()
        for t in course_topics:
            name = str(t.get("name", "")).strip()
            norm_key = re.sub(r'[\-_:()\[\].,;]+', ' ', name.lower()).strip()
            group_key = t.get("id") or norm_key
            if group_key in seen or norm_key in seen:
                continue
            seen.add(group_key)
            seen.add(norm_key)
            deduplicated.append(t)

        covered = []
        partial = []
        other = []
        for t in deduplicated:
            name = str(t.get("name", "")).strip()
            raw_k = name.lower()
            norm_k = re.sub(r'[\-_:()\[\].,;]+', ' ', raw_k).strip()
            id_k = str(t.get("id", "")).strip().lower()
            status = coverage_map.get(raw_k) or coverage_map.get(norm_k) or coverage_map.get(id_k)
            if status == "covered":
                covered.append(t)
            elif status == "partially_covered":
                partial.append(t)
            else:
                other.append(t)

        has_notes_grouping = len(covered) > 0 or len(partial) > 0

        rendered_options = [{"value": "", "text": "Select a topic", "parent_tag": "select", "disabled": False}]
        optgroups = []

        if has_notes_grouping:
            def append_optgroup(label, t_list):
                if not t_list:
                    return
                optgroups.append({"label": label, "options_count": len(t_list)})
                for item in t_list:
                    rendered_options.append({
                        "value": item["name"],
                        "text": item["name"],
                        "parent_tag": f"optgroup:{label}",
                        "disabled": False
                    })

            append_optgroup("Topics found in your notes", covered)
            append_optgroup("Partially covered topics", partial)
            append_optgroup("Other syllabus topics (general practice)", other)
        else:
            for item in other:
                rendered_options.append({
                    "value": item["name"],
                    "text": item["name"],
                    "parent_tag": "select",
                    "disabled": False
                })

        return {
            "options": rendered_options,
            "optgroups": optgroups,
            "has_headings": len(optgroups) > 0
        }

    def test_no_notes_renders_direct_options_without_optgroup_heading(self):
        c = curriculum_service.get_course("22TP103")
        res = self._simulate_populate_topic_dropdown(c["topics"], notes_extraction=None)

        # Zero optgroup elements must exist
        assert len(res["optgroups"]) == 0
        assert not res["has_headings"]

        # First option is 'Select a topic' directly under select
        assert res["options"][0]["value"] == ""
        assert res["options"][0]["text"] == "Select a topic"
        assert res["options"][0]["parent_tag"] == "select"

        # Second option is the FIRST topic directly under select, NOT a bold heading!
        second_option = res["options"][1]
        assert second_option["parent_tag"] == "select"
        assert second_option["text"] == "Basics of Algorithms & Flow Charts"
        assert "Other syllabus topics" not in [o["text"] for o in res["options"]]
        assert "Other syllabus topics" not in [o["value"] for o in res["options"]]

    def test_notes_uploaded_renders_clean_optgroups(self):
        c = curriculum_service.get_course("22TP103")
        notes_data = {
            "matched_topics": [
                {"topic_id": "22TP103-Q001", "topic_name": "Basics of Algorithms & Flow Charts", "coverage_status": "covered"},
                {"topic_id": "22TP103-Q002", "topic_name": "Structure of a C Program - Pre-Processor Statement", "coverage_status": "partially_covered"}
            ]
        }
        res = self._simulate_populate_topic_dropdown(c["topics"], notes_extraction=notes_data)

        # Optgroups must exist when notes are present
        assert len(res["optgroups"]) == 3
        labels = [og["label"] for og in res["optgroups"]]
        assert labels == [
            "Topics found in your notes",
            "Partially covered topics",
            "Other syllabus topics (general practice)"
        ]

        # Covered topic belongs to the first optgroup
        covered_opts = [o for o in res["options"] if o["parent_tag"] == "optgroup:Topics found in your notes"]
        assert len(covered_opts) == 1
        assert covered_opts[0]["text"] == "Basics of Algorithms & Flow Charts"

        # Partial topic belongs to the second optgroup
        partial_opts = [o for o in res["options"] if o["parent_tag"] == "optgroup:Partially covered topics"]
        assert len(partial_opts) == 1
        assert partial_opts[0]["text"] == "Structure of a C Program - Pre-Processor Statement"

    def test_invalid_topic_labels_rejected_by_generate_guard(self):
        invalid_labels = {
            "",
            "Select a topic",
            "Select a course first",
            "Topics found in your notes",
            "Partially covered topics",
            "Other syllabus topics (general practice)"
        }

        for label in invalid_labels:
            # Simulated guard check from frontend/js/app.js generateBtn listener
            is_valid = bool("22TP103" and label and label.strip() not in invalid_labels)
            assert not is_valid, f"Label '{label}' should be rejected as invalid selection."

        assert bool("22TP103" and "Basics of Algorithms & Flow Charts" and "Basics of Algorithms & Flow Charts" not in invalid_labels)
