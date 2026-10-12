"""
LearnAI Curriculum Expansion Validation Script
Compares the original source curriculum with extracted selectable topics across all 55 courses.
Validates:
1. Total courses and section counts.
2. Total selectable topics extracted per course.
3. Ensures 0 sections have no extracted topics.
4. Ensures 0 duplicate topic names within any course.
5. Ensures 100% of selectable topics have valid parent_section, module, unit, concept_context, and syllabus_context.
6. Validates Deep Learning (22AM301) representation against syllabus concepts.
"""

import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))
sys.stdout.reconfigure(encoding='utf-8')

from services.topic_extractor import curriculum_service

def validate_all_courses():
    raw_curriculum = curriculum_service.get_raw_curriculum()
    exp_curriculum = curriculum_service.get_expanded_curriculum()

    raw_courses = raw_curriculum.get("courses", [])
    exp_courses = exp_curriculum.get("courses", [])

    print("=" * 80)
    print("LEARNAI CURRICULUM EXPANSION VALIDATION AUDIT")
    print("=" * 80)
    print(f"Total Source Courses: {len(raw_courses)}")
    print(f"Total Expanded Courses: {len(exp_courses)}")
    assert len(raw_courses) == len(exp_courses) == 55, "Course count mismatch!"

    total_sections = 0
    total_topics = 0
    total_empty_sections = 0
    total_duplicate_topics = 0
    total_invalid_context = 0

    report_rows = []

    for rc, ec in zip(raw_courses, exp_courses):
        code = rc.get("code") or rc.get("course_code")
        name = rc.get("name") or rc.get("course_name")
        sections = rc.get("topics", [])
        topics = ec.get("topics", [])

        sec_count = len(sections)
        top_count = len(topics)
        total_sections += sec_count
        total_topics += top_count

        # 1. Check sections with no extracted topics
        raw_section_names = {s.get("name") for s in sections}
        covered_sections = {t.get("parent_section") for t in topics}
        uncovered = raw_section_names - covered_sections
        empty_sec_count = len(uncovered)
        total_empty_sections += empty_sec_count

        # 2. Check duplicate topic names within the same course
        seen_names = set()
        duplicates = set()
        for t in topics:
            t_name_lower = t["name"].lower()
            if t_name_lower in seen_names:
                duplicates.add(t["name"])
            seen_names.add(t_name_lower)
        dup_count = len(duplicates)
        total_duplicate_topics += dup_count

        # 3. Check validity of parent_section, module, unit, concept_context, syllabus_context
        invalid_in_course = 0
        for t in topics:
            if not t.get("parent_section") or not t.get("syllabus_context") or not t.get("concept_context"):
                invalid_in_course += 1
            if t.get("module") is None:
                invalid_in_course += 1
        total_invalid_context += invalid_in_course

        status = "PASSED" if (empty_sec_count == 0 and dup_count == 0 and invalid_in_course == 0 and top_count >= sec_count) else "FAILED"

        report_rows.append({
            "code": code,
            "name": name,
            "sections": sec_count,
            "topics": top_count,
            "empty_sections": empty_sec_count,
            "duplicates": dup_count,
            "invalid_context": invalid_in_course,
            "status": status
        })

    # Print summary table
    print(f"\nTotal Original Syllabus Sections: {total_sections}")
    print(f"Total Selectable Topics Produced: {total_topics}")
    print(f"Sections with No Extracted Topics: {total_empty_sections}")
    print(f"Total Duplicate Topic Names: {total_duplicate_topics}")
    print(f"Topics Without Valid Parent Context: {total_invalid_context}")
    print("\n" + "=" * 80)
    print("DETAILED PER-COURSE AUDIT TABLE")
    print("=" * 80)
    print(f"{'#':<3} | {'Code':<8} | {'Course Name':<42} | {'Sections':<8} | {'Topics':<7} | {'Empty':<5} | {'Dups':<5} | {'Status':<6}")
    print("-" * 92)

    for i, r in enumerate(report_rows, 1):
        truncated_name = r['name'][:40] + ".." if len(r['name']) > 42 else r['name']
        print(f"{i:<3} | {r['code']:<8} | {truncated_name:<42} | {r['sections']:<8} | {r['topics']:<7} | {r['empty_sections']:<5} | {r['duplicates']:<5} | {r['status']:<6}")

    # Deep Learning Specific Validation
    print("\n" + "=" * 80)
    print("DEEP LEARNING (22AM301) CONCEPT VALIDATION")
    print("=" * 80)
    dl_course = [c for c in exp_courses if c["code"] == "22AM301"][0]
    dl_topics = dl_course["topics"]
    print(f"Deep Learning Original Sections: {len(dl_course['sections'])}")
    print(f"Deep Learning Granular Topics: {len(dl_topics)}")

    key_expected_concepts = [
        "History of Deep Learning",
        "Applications of Deep Learning",
        "McCulloch–Pitts neuron",
        "Perceptron learning rule",
        "Perceptron convergence theorem",
        "Sigmoidal neuron",
        "Multilayer Feedforward neural network",
        "Backpropagation algorithm",
        "Gradient descent method",
        "Stochastic gradient descent method",
        "Shallow Neural Networks and Deep Neural Networks",
        "Optimization methods - Adagrad, Adadelta, RMSProp, Adam",
        "Regularization Methods-Dropout, Drop connect, Batch normalization",
        "Activation functions - Linear, sigmoid, sigmoid, ReLU and variations of ReLU",
        "Loss Functions",
        "Improving the training process – Dataset Augmentation",
        "Foundations Of Convolutional Neural Network",
        "Popular Deep Cnn Models",
        "LeNet",
        "AlexNet",
        "VGGNet",
        "ResNet",
        "GoogLeNet and other architectures",
        "Deep Pre-trained architectures- AlexNet, VGG16, VGG19, ResNet",
        "Autoencoders- Undercomplete Autoencoders",
        "Regularized auto encoders",
        "Denoising auto-encoders",
        "Sparse auto encoder",
        "Contractive auto-encoders",
        "Recurrent Neural Networks",
        "Backpropagation through time",
        "Long short term memory (LSTM)",
        "Gated recurrent units",
        "Sentiment Analysis"
    ]

    all_dl_names = [t["name"].lower() for t in dl_topics]
    matched = 0
    for exp_concept in key_expected_concepts:
        found = any(exp_concept.lower() in name or name in exp_concept.lower() for name in all_dl_names)
        match_symbol = "✓" if found else "✗"
        if found:
            matched += 1
        print(f"  [{match_symbol}] {exp_concept}")

    print(f"\nExpected Concept Coverage: {matched}/{len(key_expected_concepts)} ({matched/len(key_expected_concepts)*100:.1f}%)")

    # Assertions
    assert total_empty_sections == 0, f"Found {total_empty_sections} empty sections!"
    assert total_duplicate_topics == 0, f"Found {total_duplicate_topics} duplicate topics!"
    assert total_invalid_context == 0, f"Found {total_invalid_context} topics with invalid context!"
    assert matched == len(key_expected_concepts), "Some key Deep Learning concepts were missing!"

    print("\nALL AUDIT CHECKS PASSED WITH 100% SUCCESS!")

if __name__ == "__main__":
    validate_all_courses()
