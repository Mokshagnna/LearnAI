"""
Curriculum Service for LearnAI
Safely loads and provides clean, student-friendly curriculum data from curriculum.json.
Uses native `quiz_topics` if present and valid; falls back gracefully to `topics`.
Preserves original curriculum structure, academic metadata, and complete syllabus context.
Removes syllabus module/unit structural headings from selectable topics.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("learnai.curriculum")

CURRICULUM_FILE = Path(__file__).resolve().parent.parent / "data" / "curriculum.json"

ACRONYMS = {
    'ODE', 'C', 'CNN', 'RNN', 'LSTM', 'GRU', 'SGD', 'AI', 'ML', 'SQL', 'DBMS',
    'HTML', 'CSS', 'I/O', 'DC', 'AC', 'LED', 'P-N', 'AUTOCAD', 'CDNF', 'CCNF',
    'CNF', 'DNF', 'RMSPROP', 'ADAGRAD', 'ADADELTA', 'ADAM', 'VGGNET', 'RESNET',
    'ALEXNET', 'LENET', 'GOOGLENET', 'B1', '2D', '3D', 'R&D', 'SVM', 'PCA',
    'NLP', 'K-NN', 'KNN', 'ANN', 'GAN', 'API', 'REST', 'JSON', 'XML', 'ETL',
    'SSH', 'ARIES', 'FIFO', 'SC', 'RAM', 'ROM', 'CPU', 'ALU'
}

LOWER_WORDS = {
    'a', 'an', 'the', 'and', 'or', 'but', 'nor', 'for', 'at', 'by', 'in', 'of',
    'on', 'to', 'up', 'as', 'into', 'with', 'through', 'from', 'vs', 'via'
}


def to_title_case(text: str) -> str:
    """Formats topic title with proper capitalization for student-friendly display."""
    text = re.sub(r'\s+', ' ', text).strip()
    words = text.split(' ')
    if not words:
        return ''
    res = []
    for i, w in enumerate(words):
        if not w:
            continue
        if w in ('&', '-'):
            res.append(w)
            continue
        # Hyphenated words: e.g. First-Order, McCulloch-Pitts
        if '-' in w and not w.startswith('-'):
            parts = w.split('-')
            sub = []
            for p in parts:
                if p.lower() == 'mcculloch':
                    sub.append('McCulloch')
                elif p.upper() in ACRONYMS:
                    sub.append(p.upper())
                else:
                    sub.append(p.capitalize())
            res.append('-'.join(sub))
            continue
        # Slashed words
        if '/' in w:
            parts = w.split('/')
            sub = [p.upper() if p.upper() in ACRONYMS else p.capitalize() for p in parts]
            res.append('/'.join(sub))
            continue
        clean_w = re.sub(r'[^A-Za-z0-9]', '', w)
        if clean_w.lower() == 'mcculloch':
            prefix = re.match(r'^[^A-Za-z0-9]*', w).group(0)
            suffix = re.search(r'[^A-Za-z0-9]*$', w).group(0)
            res.append(prefix + 'McCulloch' + suffix)
            continue
        if clean_w.upper() in ACRONYMS:
            prefix = re.match(r'^[^A-Za-z0-9]*', w).group(0)
            suffix = re.search(r'[^A-Za-z0-9]*$', w).group(0)
            res.append(prefix + clean_w.upper() + suffix)
            continue
        if i > 0 and w.lower() in LOWER_WORDS:
            res.append(w.lower())
        else:
            res.append(w.capitalize())
    return ' '.join(res)


def clean_topic_display_name(raw_name: str) -> str:
    """
    Cleans raw topic name into a student-friendly display name.
    Strips module/unit headings, OCR hour markers, trailing ellipsis/conjunctions.
    Returns empty string if the entry was purely a structural heading.
    """
    if not raw_name:
        return ''
    name = str(raw_name).strip()

    # Normalize special characters and quotes
    name = re.sub(r'([A-Za-z])[\ufffd\?]([stST])\b', r"\1'\2", name)
    name = re.sub(r'Mcculloch[\ufffd\?\-_–—\s]*pitts', 'McCulloch-Pitts', name, flags=re.I)
    name = re.sub(r'[\ufffd\u2013\u2014]', '-', name)

    # Strip OCR hours / lab strings: e.g. "8L+0T+16P=24 Hours"
    name = re.sub(r'\b\d+L\+\d+T\+\d+P=\d+\s*Hours\b', '', name, flags=re.I)
    name = re.sub(r'\b\d+\s*Hours\b', '', name, flags=re.I)
    name = re.sub(r'\b\d+L\+\d+P\b', '', name, flags=re.I)

    # Strip module / unit prefixes like "Module 1 — Unit 1: ", "UNIT- 2 ", "Unit 1: "
    name = re.sub(r'^(?:Module|Unit|UNIT)\s*[-–—:]*\s*\d+\s*(?:[-–—:]*\s*(?:Unit|UNIT)\s*[-–—:]*\s*\d+)?\s*[-–—:.]*\s*', '', name, flags=re.I)
    name = re.sub(r'^(?:Module|Unit|UNIT)\s+[IVXLCDM]+\s*[-–—:.]*\s*', '', name, flags=re.I)

    # If heading with colon has genuine topic after colon, extract it
    if ':' in name:
        parts = name.split(':', 1)
        prefix, suffix = parts[0].strip(), parts[1].strip()
        if len(suffix) >= 4 and not re.match(r'^(?:note|example|details)\b', suffix, flags=re.I):
            if any(h in prefix.lower() for h in ['basics', 'intro', 'module', 'unit', 'overview', 'concepts', 'architecture', 'techniques', 'generation']):
                name = suffix
            elif len(prefix) > len(suffix) * 2:
                name = suffix

    # Strip leading numbering like '99 '
    name = re.sub(r'^\d+\s+', '', name)

    # Strip trailing punctuation, ellipses, dashes, question marks
    name = re.sub(r'[\s,;:.–—\-•\u2026?]+$', '', name)

    # Strip trailing conjunctions/prepositions
    name = re.sub(r'\s+(?:and|&|or|to|of|in|with|for)$', '', name, flags=re.I)

    # If the text is purely a structural label, discard
    if re.match(r'^(?:Module|Unit|UNIT)\s*(?:\d+|[IVXLCDM]+)?\s*$', name, flags=re.I):
        return ''

    name = name.strip()
    if not name:
        return ''

    cleaned = to_title_case(name)

    # Standardize student-friendly names
    if re.match(r'^Inverse of a Matrix (?:through|by|using)\b', cleaned, flags=re.I):
        cleaned = 'Inverse of a Matrix'

    cleaned = re.sub(r'\bFirst Order Differential Equations\b', 'First-Order Differential Equations', cleaned, flags=re.I)
    cleaned = re.sub(r'\bSecond Order Differential Equations\b', 'Second-Order Differential Equations', cleaned, flags=re.I)

    return cleaned


def normalize_topic_entry(
    raw_topic: Dict[str, Any],
    course_code: str,
    course_name: str,
    index: int,
    is_quiz_topic: bool = True
) -> Dict[str, Any]:
    """
    Normalizes a topic or quiz_topic entry into a standard selectable topic format.
    Ensures consistent keys: id, name, module, unit, parent_topic, parent_section,
    syllabus_context, concepts.
    """
    t_id = raw_topic.get("id") or f"{course_code}-t{index + 1}"
    raw_name = raw_topic.get("name") or f"Topic {index + 1}"
    cleaned_name = clean_topic_display_name(raw_name) or str(raw_name).strip()
    module = raw_topic.get("module")
    unit = raw_topic.get("unit")

    # Parent topic / section
    parent_topic = (
        raw_topic.get("parent_topic")
        or raw_topic.get("source_topic_name")
        or raw_topic.get("parent_section")
        or (cleaned_name if not is_quiz_topic else "")
    )
    parent_section = parent_topic

    # Context & concepts
    syllabus_context = raw_topic.get("syllabus_context") or raw_topic.get("context") or ""
    raw_concepts = raw_topic.get("concepts", [])
    if isinstance(raw_concepts, list):
        concepts = [str(c).strip() for c in raw_concepts if str(c).strip()]
    elif isinstance(raw_concepts, str) and raw_concepts.strip():
        concepts = [raw_concepts.strip()]
    else:
        concepts = []

    concept_context = (
        raw_topic.get("concept_context")
        or (", ".join(concepts) if concepts else "")
        or syllabus_context
    )

    return {
        "id": str(t_id),
        "name": cleaned_name,
        "raw_name": str(raw_name).strip(),
        "course_code": course_code,
        "course_name": course_name,
        "module": module,
        "unit": unit,
        "parent_topic": parent_topic,
        "parent_section": parent_section,
        "source_topic_name": raw_topic.get("source_topic_name") or parent_topic,
        "source_topic_index": raw_topic.get("source_topic_index"),
        "syllabus_context": syllabus_context,
        "concepts": concepts,
        "concept_context": concept_context,
        "is_quiz_topic": is_quiz_topic
    }


class CurriculumService:
    def __init__(self, curriculum_path: Path = CURRICULUM_FILE):
        self.curriculum_path = curriculum_path
        self._raw_curriculum: Dict[str, Any] = {}
        self._normalized_curriculum: Dict[str, Any] = {}
        self._course_map: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self):
        """Loads and normalizes curriculum from curriculum.json."""
        if not self.curriculum_path.exists():
            raise FileNotFoundError(f"Curriculum file not found: {self.curriculum_path}")

        with open(self.curriculum_path, "r", encoding="utf-8") as f:
            self._raw_curriculum = json.load(f)

        raw_courses = self._raw_curriculum.get("courses", [])
        normalized_courses = []
        self._course_map.clear()

        for c in raw_courses:
            code = str(c.get("code") or c.get("course_code") or "").strip()
            name = str(c.get("name") or c.get("course_name") or "").strip()
            if not code:
                continue

            raw_quiz_topics = c.get("quiz_topics")
            has_valid_quiz_topics = (
                isinstance(raw_quiz_topics, list)
                and len(raw_quiz_topics) > 0
                and any(isinstance(item, dict) and item.get("name") for item in raw_quiz_topics)
            )

            selectable_topics: List[Dict[str, Any]] = []
            seen_names = set()

            def add_selectable_topic(t_dict: Dict[str, Any]):
                t_name = t_dict.get("name", "").strip()
                if not t_name or t_name.lower() in seen_names:
                    return
                seen_names.add(t_name.lower())
                selectable_topics.append(t_dict)

            # Special canonical handling for Linear Algebra (22MT103) per user specification
            if code == "22MT103":
                q_map = {qt.get("id"): qt for qt in (raw_quiz_topics or []) if isinstance(qt, dict)}
                t_map = {t.get("name"): t for t in c.get("topics", []) if isinstance(t, dict)}

                # 1. Linear Algebra Concepts (Q001)
                q1 = q_map.get("22MT103-Q001", {})
                add_selectable_topic({
                    "id": q1.get("id", "22MT103-Q001"),
                    "name": "Linear Algebra Concepts",
                    "raw_name": q1.get("name", "Linear Algebra Concepts"),
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": q1.get("parent_topic", "Matrices"),
                    "module": q1.get("module", 1),
                    "unit": q1.get("unit", 1),
                    "syllabus_context": q1.get("syllabus_context", ""),
                    "concepts": q1.get("concepts", ["Definition of matrix", "Types of matrices", "Algebra of matrices"]),
                    "concept_context": "Definition of matrix, Types of matrices, Algebra of matrices",
                    "is_quiz_topic": True
                })

                # 2. Adjoint of a Matrix (Q006)
                q6 = q_map.get("22MT103-Q006", {})
                add_selectable_topic({
                    "id": q6.get("id", "22MT103-Q006"),
                    "name": "Adjoint of a Matrix",
                    "raw_name": q6.get("name", "adjoint of a matrix"),
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": q6.get("parent_topic", "Matrices"),
                    "module": q6.get("module", 1),
                    "unit": q6.get("unit", 1),
                    "syllabus_context": q6.get("syllabus_context", ""),
                    "concepts": q6.get("concepts", ["adjoint of a matrix"]),
                    "concept_context": "adjoint of a matrix",
                    "is_quiz_topic": True
                })

                # 3. Inverse of a Matrix (Q007)
                q7 = q_map.get("22MT103-Q007", {})
                add_selectable_topic({
                    "id": q7.get("id", "22MT103-Q007"),
                    "name": "Inverse of a Matrix",
                    "raw_name": q7.get("name", "inverse of a matrix through adjoint and elementary row operations"),
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": q7.get("parent_topic", "Matrices"),
                    "module": q7.get("module", 1),
                    "unit": q7.get("unit", 1),
                    "syllabus_context": q7.get("syllabus_context", ""),
                    "concepts": q7.get("concepts", ["inverse of a matrix through adjoint and elementary row operations"]),
                    "concept_context": "inverse of a matrix through adjoint and elementary row operations",
                    "is_quiz_topic": True
                })

                # 4. Rank of a Matrix (Q008)
                q8 = q_map.get("22MT103-Q008", {})
                add_selectable_topic({
                    "id": q8.get("id", "22MT103-Q008"),
                    "name": "Rank of a Matrix",
                    "raw_name": q8.get("name", "Rank of a matrix"),
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": q8.get("parent_topic", "Matrices"),
                    "module": q8.get("module", 1),
                    "unit": q8.get("unit", 1),
                    "syllabus_context": q8.get("syllabus_context", ""),
                    "concepts": q8.get("concepts", ["Rank of a matrix"]),
                    "concept_context": "Rank of a matrix",
                    "is_quiz_topic": True
                })

                # 5. Echelon Form (Q009)
                q9 = q_map.get("22MT103-Q009", {})
                add_selectable_topic({
                    "id": q9.get("id", "22MT103-Q009"),
                    "name": "Echelon Form",
                    "raw_name": q9.get("name", "Echelon form"),
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": q9.get("parent_topic", "Matrices"),
                    "module": q9.get("module", 1),
                    "unit": q9.get("unit", 1),
                    "syllabus_context": q9.get("syllabus_context", ""),
                    "concepts": q9.get("concepts", ["Echelon form"]),
                    "concept_context": "Echelon form",
                    "is_quiz_topic": True
                })

                # 6. Normal Form (Q010)
                q10 = q_map.get("22MT103-Q010", {})
                add_selectable_topic({
                    "id": q10.get("id", "22MT103-Q010"),
                    "name": "Normal Form",
                    "raw_name": q10.get("name", "Normal form"),
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": q10.get("parent_topic", "Matrices"),
                    "module": q10.get("module", 1),
                    "unit": q10.get("unit", 1),
                    "syllabus_context": q10.get("syllabus_context", ""),
                    "concepts": q10.get("concepts", ["Normal form"]),
                    "concept_context": "Normal form",
                    "is_quiz_topic": True
                })

                # 7. Applications of Matrices (Unit 2)
                unit2_ctx = ""
                unit2_concepts = []
                if "Applications Of Matrices" in t_map:
                    unit2_ctx = t_map["Applications Of Matrices"].get("syllabus_context", "")
                for qid in ["22MT103-Q002", "22MT103-Q003", "22MT103-Q004"]:
                    if qid in q_map:
                        if not unit2_ctx:
                            unit2_ctx = q_map[qid].get("syllabus_context", "")
                        unit2_concepts.extend(q_map[qid].get("concepts", []))
                add_selectable_topic({
                    "id": "22MT103-app-matrices",
                    "name": "Applications of Matrices",
                    "raw_name": "Applications Of Matrices",
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": "Applications Of Matrices",
                    "module": 1,
                    "unit": 2,
                    "syllabus_context": unit2_ctx,
                    "concepts": unit2_concepts or ["Consistency of system of linear equations", "Gauss elimination method", "Cayley-Hamilton theorem", "Eigen values and Eigen vectors"],
                    "concept_context": ", ".join(unit2_concepts) if unit2_concepts else unit2_ctx,
                    "is_quiz_topic": True
                })

                # 8. First-Order Differential Equations
                q5 = q_map.get("22MT103-Q005", {})
                diff_ctx = q5.get("syllabus_context", "")
                add_selectable_topic({
                    "id": "22MT103-Q005a",
                    "name": "First-Order Differential Equations",
                    "raw_name": "First Order Differential Equations",
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": "First Order Differential Equations & Second Order Differential Equations",
                    "module": 2,
                    "unit": 1,
                    "syllabus_context": diff_ctx,
                    "concepts": ["Introduction to ODE", "variable separable method", "homogenous and non-homogenous differential equations", "linear differential equations", "Bernoulli's equations"],
                    "concept_context": "Introduction to ODE, variable separable method, homogenous and non-homogenous differential equations, linear differential equations, Bernoulli's equations",
                    "is_quiz_topic": True
                })

                # 9. Second-Order Differential Equations
                add_selectable_topic({
                    "id": "22MT103-Q005b",
                    "name": "Second-Order Differential Equations",
                    "raw_name": "Second Order Differential Equations",
                    "course_code": code,
                    "course_name": name,
                    "parent_topic": "First Order Differential Equations & Second Order Differential Equations",
                    "module": 2,
                    "unit": 1,
                    "syllabus_context": diff_ctx,
                    "concepts": ["Linear differential equations with constant coefficients", "RHS of the form eax, xn, sin(ax) or cos(ax)"],
                    "concept_context": "Linear differential equations with constant coefficients with RHS of the form eax, xn, sin(ax) or cos(ax)",
                    "is_quiz_topic": True
                })

            else:
                # General courses
                source_items = raw_quiz_topics if has_valid_quiz_topics else c.get("topics", [])
                for idx, item in enumerate(source_items):
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    raw_name = str(item.get("name", "")).strip()
                    cleaned_name = clean_topic_display_name(raw_name)
                    if not cleaned_name:
                        continue

                    # Check if compound topic like "First Order Differential Equations & Second Order Differential Equations"
                    if " & " in cleaned_name and ("Differential Equations" in cleaned_name or len(cleaned_name) > 30):
                        parts = [clean_topic_display_name(p) for p in cleaned_name.split(" & ")]
                        for p_idx, part in enumerate(parts):
                            if not part or part.lower() in seen_names:
                                continue
                            t_norm = normalize_topic_entry(
                                raw_topic={
                                    **item,
                                    "id": f"{item.get('id', f'{code}-t{idx+1}')}-{p_idx+1}",
                                    "name": part,
                                    "raw_name": part
                                },
                                course_code=code,
                                course_name=name,
                                index=idx,
                                is_quiz_topic=has_valid_quiz_topics
                            )
                            t_norm["name"] = part
                            add_selectable_topic(t_norm)
                        continue

                    t_norm = normalize_topic_entry(
                        raw_topic={**item, "name": cleaned_name},
                        course_code=code,
                        course_name=name,
                        index=idx,
                        is_quiz_topic=has_valid_quiz_topics
                    )
                    t_norm["name"] = cleaned_name
                    add_selectable_topic(t_norm)

            course_obj = {
                "code": code,
                "name": name,
                "year": c.get("year"),
                "semester": c.get("semester"),
                "prerequisite": c.get("prerequisite"),
                "topics": selectable_topics,
                "original_topics": c.get("topics", []),
                "has_native_quiz_topics": has_valid_quiz_topics,
                "quiz_topics_count": len(selectable_topics)
            }

            normalized_courses.append(course_obj)
            self._course_map[code] = course_obj

        self._normalized_curriculum = {
            "program": self._raw_curriculum.get("program"),
            "regulation": self._raw_curriculum.get("regulation"),
            "institution": self._raw_curriculum.get("institution"),
            "courses": normalized_courses
        }

        logger.info(
            f"Curriculum loaded successfully: {len(normalized_courses)} courses, "
            f"{sum(len(c['topics']) for c in normalized_courses)} total selectable topics."
        )

    def get_curriculum(self) -> Dict[str, Any]:
        """Returns the normalized curriculum suitable for API consumers."""
        return self._normalized_curriculum

    def get_expanded_curriculum(self) -> Dict[str, Any]:
        """Compatibility alias for existing endpoint in main.py."""
        return self._normalized_curriculum

    def get_course(self, course_code: str) -> Optional[Dict[str, Any]]:
        return self._course_map.get(course_code)

    def find_course_and_topic(
        self,
        course_code: str,
        topic_name: str,
        topic_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Locates the course and selectable topic.
        Supports lookup by topic_id, exact name, case-insensitive name,
        alias without hyphen, or substring match.
        """
        course = self._course_map.get(course_code)
        if not course:
            raise ValueError(f"Course '{course_code}' not found in curriculum.")

        topics = course.get("topics", [])
        if not topics:
            raise ValueError(f"No selectable topics available for course '{course_code}'.")

        # 1. Lookup by explicit topic_id
        if topic_id:
            for t in topics:
                if t.get("id") == topic_id:
                    return course, t

        clean_target = (topic_name or "").strip().lower()
        if clean_target:
            # 2. Lookup by exact topic name (case-insensitive)
            for t in topics:
                if t["name"].strip().lower() == clean_target:
                    return course, t

            # 3. Lookup ignoring hyphens and punctuation
            clean_alnum_target = re.sub(r'[^a-z0-9]', '', clean_target)
            for t in topics:
                t_alnum = re.sub(r'[^a-z0-9]', '', t["name"].lower())
                if t_alnum and t_alnum == clean_alnum_target:
                    return course, t

            # 4. Lookup by raw_name alias
            for t in topics:
                raw_n = (t.get("raw_name") or "").strip().lower()
                if raw_n == clean_target:
                    return course, t

            # 5. Lookup by substring match
            for t in topics:
                name_lower = t["name"].strip().lower()
                if clean_target in name_lower or name_lower in clean_target:
                    return course, t

            # 6. Lookup by parent_topic match
            for t in topics:
                parent = (t.get("parent_topic") or "").strip().lower()
                if clean_target == parent or clean_target in parent:
                    return course, t

        # Fallback to the first topic if none matched
        return course, topics[0]


# Singleton instance
curriculum_service = CurriculumService()
