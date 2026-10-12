"""
Topic Extractor Service for LearnAI
Transforms unit-level syllabus sections into individual selectable syllabus concepts
while preserving the original curriculum structure, academic metadata, and syllabus context.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

CURRICULUM_FILE = Path(__file__).resolve().parent.parent / "data" / "curriculum.json"

OCR_CLEANUPS = [
    (re.compile(r'●+|•+|99\s*'), ' '),
    (re.compile(r'\bUNIT\s*-\s*\d+[^:;.]*?(?=[A-Z]|$)', re.IGNORECASE), ' '),
    (re.compile(r'\b\d+L\+\d+T\+\d+P=\d+\s*Hours\b', re.IGNORECASE), ' '),
    (re.compile(r'\(without\s+proofs?\)', re.IGNORECASE), ''),
    (re.compile(r'\(proofs\s+not\s+Required\)', re.IGNORECASE), ''),
    (re.compile(r'\(Qualitative\)', re.IGNORECASE), ''),
    (re.compile(r'\(up\s+to\s+3\s*x\s*3\s+matrices\s+only\)', re.IGNORECASE), ''),
]

TYPO_REPLACEMENTS = {
    r'\blosss function\b': 'Loss Functions',
    r'\blosss\b': 'Loss',
    r'\bunder complete\b': 'Undercomplete',
    r'\btofmemory\b': 'of Memory',
    r'\bresent\b': 'ResNet',
    r'\bgoogle net\b': 'GoogLeNet',
    r'\bgooglenet\b': 'GoogLeNet',
    r'\bmulti-layer feed forward\b': 'Multilayer Feedforward',
    r'\bback propagation\b': 'Backpropagation',
    r'\bpercepton\b': 'Perceptron',
}

DISCARD_WORDS = {
    "and", "or", "the", "with", "etc", "etc.", "and others", "and other",
    "and other architectures", "and other ensemble methods", "and tools",
    "introduction", "practices", "activities", "review"
}

def clean_raw_text(text: str) -> str:
    for pat, rep in OCR_CLEANUPS:
        text = pat.sub(rep, text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def normalize_title(name: str) -> str:
    name = re.sub(r'^[\s,;:.–—\-•?]+|[\s,;:.–—\-•?]+$', '', name).strip()
    # Strip leading 'and ' or '& '
    name = re.sub(r'^(?:and|&)\s+', '', name, flags=re.IGNORECASE).strip()
    
    for pat, rep in TYPO_REPLACEMENTS.items():
        name = re.sub(pat, rep, name, flags=re.IGNORECASE)
    
    if name and name[0].islower():
        name = name[0].upper() + name[1:]
    
    words = name.split()
    if all(w.islower() for w in words):
        name = name.title()
    return name

def split_section_into_subheaders(section_name: str, context: str) -> List[tuple[str, str]]:
    """
    Splits context into (subheading, subcontent) blocks.
    Prioritizes explicit title parts joined by '&' in section_name.
    """
    ctx = clean_raw_text(context)
    if not ctx:
        return [(section_name, "")]

    title_parts = [p.strip() for p in section_name.split("&") if p.strip()]
    headers: List[tuple[int, int, str]] = []

    # 1. Match title parts in context
    for tp in title_parts:
        pat = re.compile(r'(?:^|(?<=[.!?;,]|\s))(' + re.escape(tp) + r')\s*:\s*', re.IGNORECASE)
        m = pat.search(ctx)
        if m:
            headers.append((m.start(1), m.end(), m.group(1).strip()))

    # 2. If no multi-part title matches, search for clean punctuation-bounded headings
    if len(headers) <= 1:
        # Match capitalized phrases right before colon that follow punctuation or string start
        for m in re.finditer(r'(?:^|(?<=[.!?;,]))\s*([A-Z][A-Za-z0-9\s/–—\(\)-]{2,35}?)\s*:\s*', ctx):
            h_str = m.group(1).strip()
            word_count = len(h_str.split())
            if 1 <= word_count <= 5:
                if not any(hs <= m.start(1) <= he for hs, he, _ in headers):
                    headers.append((m.start(1), m.end(), h_str))

    headers.sort(key=lambda x: x[0])

    if not headers:
        return [(section_name, ctx)]

    blocks: List[tuple[str, str]] = []
    # If the first header doesn't start at 0, check if there was intro text
    first_start = headers[0][0]
    if first_start > 0:
        intro_text = ctx[:first_start].strip()
        if len(intro_text) > 10:
            blocks.append((section_name, intro_text))

    for i, (s, e, h_name) in enumerate(headers):
        next_s = headers[i + 1][0] if i + 1 < len(headers) else len(ctx)
        content = ctx[e:next_s].strip()
        blocks.append((h_name, content))

    return blocks

def extract_concepts_from_block(h_name: str, block: str) -> List[str]:
    """
    Extracts individual concepts from a sub-block of syllabus text.
    """
    if not block:
        norm_h = normalize_title(h_name)
        return [norm_h] if norm_h.lower() not in DISCARD_WORDS else []

    concepts = []
    
    # 1. Semicolons are prime concept separators
    if ";" in block:
        chunks = [c.strip() for c in block.split(";") if c.strip()]
    else:
        # 2. Sentences (. or ? followed by space)
        chunks = [c.strip() for c in re.split(r'(?:[.!?]\s+|\n+)', block) if c.strip()]

    for chunk in chunks:
        # If chunk contains dash/colon with compound items (e.g. 'Optimization methods - Adagrad, Adadelta...'),
        # keep it as a compound topic
        dash_match = re.search(r'[-–—]\s*([A-Za-z0-9])', chunk)
        if dash_match and len(chunk.split()) <= 18:
            norm = normalize_title(chunk)
            if len(norm) >= 3 and norm.lower() not in DISCARD_WORDS:
                concepts.append(norm)
            continue

        # Check comma separation
        comma_parts = [p.strip() for p in chunk.split(",") if p.strip()]
        avg_words = sum(len(p.split()) for p in comma_parts) / max(1, len(comma_parts))
        
        # If comma parts are short 1-word lists, keep as compound topic
        if len(comma_parts) > 1 and avg_words < 1.8:
            norm = normalize_title(chunk)
            if len(norm) >= 3 and norm.lower() not in DISCARD_WORDS:
                concepts.append(norm)
        else:
            for cp in comma_parts:
                norm = normalize_title(cp)
                if len(norm) >= 3 and norm.lower() not in DISCARD_WORDS:
                    concepts.append(norm)

    return concepts

def extract_course_topics(course: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extracts selectable topics for a course, maintaining module/unit and syllabus relationships.
    """
    course_code = course.get("code") or course.get("course_code")
    course_name = course.get("name") or course.get("course_name")
    raw_sections = course.get("topics", [])
    
    selectable_topics = []
    seen_names = set()
    topic_counter = 0

    for s_idx, section in enumerate(raw_sections):
        sec_name = section.get("name", f"Section {s_idx + 1}")
        module = section.get("module")
        unit = section.get("unit")
        raw_ctx = section.get("syllabus_context") or section.get("context", "")

        blocks = split_section_into_subheaders(sec_name, raw_ctx)
        
        section_concepts = []
        for h_name, block_content in blocks:
            extracted = extract_concepts_from_block(h_name, block_content)
            for cpt in extracted:
                if cpt and cpt.lower() not in seen_names:
                    seen_names.add(cpt.lower())
                    section_concepts.append((cpt, block_content))

        # Check if title itself contains major concept phrases not yet represented
        title_phrases = [normalize_title(p) for p in re.split(r'[,&]', sec_name) if p.strip()]
        for tp in title_phrases:
            if tp.lower() not in seen_names and len(tp) >= 4 and tp.lower() not in DISCARD_WORDS:
                # Add title concept at the start of section concepts
                seen_names.add(tp.lower())
                section_concepts.insert(0, (tp, raw_ctx))

        # Fallback if nothing extracted: preserve section name
        if not section_concepts:
            section_concepts.append((normalize_title(sec_name), raw_ctx))

        for cpt_name, block_ctx in section_concepts:
            topic_counter += 1
            t_id = f"{course_code}-s{s_idx + 1}-t{topic_counter}"
            concept_ctx = block_ctx if block_ctx else raw_ctx

            selectable_topics.append({
                "id": t_id,
                "course_code": course_code,
                "course_name": course_name,
                "name": cpt_name,
                "parent_section": sec_name,
                "module": module,
                "unit": unit,
                "concept_context": concept_ctx,
                "syllabus_context": raw_ctx
            })

    return selectable_topics

class TopicCurriculumService:
    def __init__(self, curriculum_path: Path = CURRICULUM_FILE):
        self.curriculum_path = curriculum_path
        self._raw_curriculum = None
        self._expanded_curriculum = None
        self._topic_index = {}
        self.load()

    def load(self):
        with open(self.curriculum_path, "r", encoding="utf-8") as f:
            self._raw_curriculum = json.load(f)

        expanded_courses = []
        self._topic_index.clear()

        for c in self._raw_curriculum.get("courses", []):
            code = c.get("code") or c.get("course_code")
            name = c.get("name") or c.get("course_name")
            
            extracted_topics = extract_course_topics(c)
            
            course_obj = {
                "code": code,
                "name": name,
                "year": c.get("year"),
                "semester": c.get("semester"),
                "prerequisite": c.get("prerequisite"),
                "sections": c.get("topics", []), # Original raw syllabus sections
                "topics": extracted_topics       # Individual selectable syllabus concepts
            }
            expanded_courses.append(course_obj)

            # Index by (code, topic_id) and (code, topic_name)
            for t in extracted_topics:
                self._topic_index[(code, t["id"])] = t
                self._topic_index[(code, t["name"].lower())] = t

        self._expanded_curriculum = {
            "program": self._raw_curriculum.get("program"),
            "regulation": self._raw_curriculum.get("regulation"),
            "institution": self._raw_curriculum.get("institution"),
            "courses": expanded_courses
        }

    def get_raw_curriculum(self) -> Dict[str, Any]:
        return self._raw_curriculum

    def get_expanded_curriculum(self) -> Dict[str, Any]:
        return self._expanded_curriculum

    def find_course_and_topic(
        self,
        course_code: str,
        topic_name: str,
        topic_id: Optional[str] = None
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Locates the course and selectable topic.
        Supports lookup by topic_id or topic_name.
        """
        for course in self._expanded_curriculum["courses"]:
            if course["code"] == course_code:
                # 1. Lookup by explicit topic_id
                if topic_id:
                    for t in course["topics"]:
                        if t["id"] == topic_id:
                            return course, t

                # 2. Lookup by exact topic name
                for t in course["topics"]:
                    if t["name"].lower() == topic_name.lower():
                        return course, t

                # 3. Lookup by substring
                for t in course["topics"]:
                    if topic_name.lower() in t["name"].lower() or t["name"].lower() in topic_name.lower():
                        return course, t

                # 4. Lookup by parent section name
                for s in course.get("sections", []):
                    if s.get("name", "").lower() == topic_name.lower():
                        for t in course["topics"]:
                            if t["parent_section"] == s["name"]:
                                return course, t

                if course["topics"]:
                    return course, course["topics"][0]

                raise ValueError(f"No selectable topics found for course {course_code}")

        raise ValueError(f"Course {course_code} not found")

# Singleton instance
curriculum_service = TopicCurriculumService()
