"""
Enhanced Question and Concept Novelty Evaluation for LearnAI.

Maintains question fingerprints, concept tags, and learning objectives across
attempts to prevent both phrasing repetition and underlying concept repetition
during quiz retakes.
"""
import re
import logging
from typing import List, Tuple, Dict, Any, Set, Optional

logger = logging.getLogger("learnai.novelty")

TEXT_DUPLICATE_THRESHOLD = 0.55     # Jaccard on word trigrams/bigrams (lower = stricter)
CONCEPT_SIMILARITY_THRESHOLD = 0.50 # Jaccard on normalized concept tokens (lower = stricter)
HISTORY_QUESTION_LIMIT = 50         # Max recent questions retained in memory
HISTORY_CONCEPT_LIMIT = 30          # Max recent concepts retained in memory
MAX_EXCLUSION_PROMPT_QUESTIONS = 10 # Questions shown in LLM exclusion list
MAX_EXCLUSION_PROMPT_CONCEPTS = 12  # Concepts shown in LLM exclusion list

COMMON_STOPWORDS = {
    "a", "an", "the", "in", "on", "of", "for", "to", "at", "by", "with", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "which", "what", "how", "why", "when", "where", "who",
    "that", "this", "these", "those", "and", "or", "but", "if", "not", "as",
    "can", "could", "should", "would", "will", "shall", "into", "through", "about",
    "its", "it", "they", "them", "their", "take", "takes", "taken", "main", "primary",
    "one", "single", "following", "given", "statement", "true", "false", "correct",
    "incorrect", "best", "describes", "describe", "regarding", "respect", "using",
    "question", "questions", "attempt", "attempts", "quiz",
    "logical", "gate", "gates", "neuron", "neurons"
}

LOGIC_KEYWORDS = {
    "and", "or", "not", "xor", "nand", "nor", "xnor",
    "and_gate", "or_gate", "not_gate", "xor_gate", "nand_gate", "nor_gate", "xnor_gate"
}

# ── Domain Alias and Learning Objective Canonicalization ─────────────────────
ALIAS_MAP = {
    "and gate": "and_gate",
    "or gate": "or_gate",
    "not gate": "not_gate",
    "xor gate": "xor_gate",
    "nand gate": "nand_gate",
    "nor gate": "nor_gate",
    "xnor gate": "xnor_gate",
    "logical and": "and_gate",
    "logical or": "or_gate",
    "logical not": "not_gate",
    "logical xor": "xor_gate",
    "logical nand": "nand_gate",
    "logical nor": "nor_gate",
    "inhibitory input": "inhibition_facet",
    "inhibitory inputs": "inhibition_facet",
    "excitatory input": "excitation_facet",
    "excitatory inputs": "excitation_facet",
    "input values": "input_domain_facet",
    "allowed inputs": "input_domain_facet",
    "binary inputs": "input_domain_facet",
    "input domain": "input_domain_facet",
    "primary function": "purpose_facet",
    "function of": "purpose_facet",
    "main purpose": "purpose_facet",
    "mcculloch-pitts neuron": "mp_neuron",
    "mcculloch pitts neuron": "mp_neuron",
    "mcculloch-pitts neurons": "mp_neuron",
    "mcculloch pitts neurons": "mp_neuron",
    "mcculloch pitts": "mp_neuron",
    "mcculloch-pitts": "mp_neuron",
    "m-p": "mp_neuron",
    "m p": "mp_neuron",
    "mp neuron": "mp_neuron",
    "mp neurons": "mp_neuron",
    "artificial neural network": "ann",
    "artificial neural networks": "ann",
    "neural network": "ann",
    "neural networks": "ann",
    "convolutional neural network": "cnn",
    "convolutional neural networks": "cnn",
    "recurrent neural network": "rnn",
    "recurrent neural networks": "rnn",
    "exclusive or": "xor",
    "exclusive-or": "xor",
    "support vector machine": "svm",
    "support vector machines": "svm",
    "gradient descent": "gd",
    "stochastic gradient descent": "sgd",
    "mean squared error": "mse",
    "mean square error": "mse",
    "cross entropy": "cross_entropy",
    "cross-entropy": "cross_entropy",
    "backpropagation": "backprop",
    "back-propagation": "backprop",
    "back propagation": "backprop",
    "activation function": "activation_facet",
    "transfer function": "activation_facet",
    "loss function": "loss_facet",
    "cost function": "loss_facet",
    "objective function": "loss_facet",
}

SYNONYM_MAP = {
    "purpose": "purpose_facet",
    "role": "purpose_facet",
    "objective": "purpose_facet",
    "goal": "purpose_facet",
    "aim": "purpose_facet",
    "implement": "implement_facet",
    "implements": "implement_facet",
    "implementing": "implement_facet",
    "realize": "implement_facet",
    "realizes": "implement_facet",
    "realizing": "implement_facet",
    "realise": "implement_facet",
    "realises": "implement_facet",
    "realising": "implement_facet",
    "realizable": "implement_facet",
    "realisable": "implement_facet",
    "compute": "implement_facet",
    "computing": "implement_facet",
    "solve": "implement_facet",
    "solves": "implement_facet",
    "solving": "implement_facet",
    "values": "input_domain_facet",
    "value": "input_domain_facet",
    "accepted": "input_domain_facet",
    "domain": "input_domain_facet",
    "allowed": "input_domain_facet",
    "inhibition": "inhibition_facet",
    "inhibitory": "inhibition_facet",
    "excitation": "excitation_facet",
    "excitatory": "excitation_facet",
    "cannot": "limitation_facet",
    "cant": "limitation_facet",
    "unable": "limitation_facet",
    "prevent": "limitation_facet",
    "prevents": "limitation_facet",
    "prevented": "limitation_facet",
    "limitation": "limitation_facet",
    "limitations": "limitation_facet",
    "drawback": "limitation_facet",
    "drawbacks": "limitation_facet",
    "weakness": "limitation_facet",
    "weaknesses": "limitation_facet",
    "fail": "limitation_facet",
    "fails": "limitation_facet",
    "failure": "limitation_facet",
    "advantage": "advantage_facet",
    "benefit": "advantage_facet",
    "strength": "advantage_facet",
    "definition": "definition_facet",
    "define": "definition_facet",
    "meaning": "definition_facet",
    "characteristic": "property_facet",
    "property": "property_facet",
    "feature": "property_facet",
    "linear": "linear_facet",
    "linearly": "linear_facet",
    "separable": "separable_facet",
    "separability": "separable_facet",
    "chain rule": "chain_rule_facet",
    "weight update": "weight_update_facet",
    "update weights": "weight_update_facet",
    "weights updated": "weight_update_facet",
    "gradient calculation": "gradient_calc_facet",
    "calculating gradients": "gradient_calc_facet",
    "compute derivatives": "gradient_calc_facet",
}

def canonicalize_text(text: str, topic_name: str = "") -> str:
    """Normalize text and map domain aliases and synonyms to canonical tokens."""
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"['\"’]", "", t)
    t = re.sub(r"[^\w\s-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    # Map multi-word aliases first
    for alias, replacement in ALIAS_MAP.items():
        t = re.sub(rf"\b{re.escape(alias)}\b", replacement, t)

    for phrase, replacement in SYNONYM_MAP.items():
        if " " in phrase:
            t = re.sub(rf"\b{re.escape(phrase)}\b", replacement, t)

    words = t.split()
    mapped_words = []
    for w in words:
        clean_w = w.strip("-")
        mapped = SYNONYM_MAP.get(clean_w, clean_w)
        mapped_words.append(mapped)

    res = " ".join(mapped_words)
    if topic_name:
        topic_entity = None
        for alias, rep in ALIAS_MAP.items():
            if alias in topic_name.lower():
                topic_entity = rep
                break
        if topic_entity and topic_entity not in res:
            res = f"{res} {topic_entity}"

    return res

def extract_canonical_tokens(text: str, topic_name: str = "") -> Set[str]:
    """Extract canonical semantic tokens after alias and synonym normalization."""
    can = canonicalize_text(text, topic_name=topic_name)
    words = set(can.split())
    return {w for w in words if w not in COMMON_STOPWORDS and len(w) > 2}

def calculate_canonical_similarity(q1: str, q2: str, topic_name: str = "") -> float:
    """Calculates Jaccard overlap on canonicalized domain and facet tokens."""
    t1 = extract_canonical_tokens(q1, topic_name=topic_name)
    t2 = extract_canonical_tokens(q2, topic_name=topic_name)
    if not t1 or not t2:
        return 0.0
    if t1 == t2:
        return 1.0
    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return intersection / union if union else 0.0

def extract_learning_objective(question_text: str, concept_tag: str = "", topic_name: str = "") -> str:
    """
    Extracts a structured entity:facet learning objective tag.
    Identifies the core target entity and the cognitive facet being tested.
    """
    combined = f"{question_text} {concept_tag} {topic_name}"
    can_tokens = extract_canonical_tokens(combined, topic_name=topic_name)

    # 1. Identify Target Entity
    entity = "general_topic"
    for e in ["mp_neuron", "perceptron", "mlp", "ann", "cnn", "rnn", "lstm", "svm", "backprop", "xor", "loss_facet", "mse", "cross_entropy", "gd", "sgd"]:
        if e in can_tokens:
            entity = e
            break

    # 2. Specific logic gate facet
    for g in ["and_gate", "or_gate", "not_gate", "xor_gate", "nand_gate", "nor_gate", "xnor_gate"]:
        if g in can_tokens:
            return f"{entity}:{g}"

    # 3. Identify Target Facet
    facet = "concept_general"
    for f in ["purpose_facet", "implement_facet", "input_domain_facet", "inhibition_facet", "excitation_facet", "limitation_facet", "advantage_facet", "definition_facet", "chain_rule_facet", "weight_update_facet", "gradient_calc_facet"]:
        if f in can_tokens:
            facet = f
            break

    return f"{entity}:{facet}"


# ─────────────────────────────────────────────────────────────────────────────
# Text and Concept Normalization
# ─────────────────────────────────────────────────────────────────────────────

def normalise_text(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fingerprint(question_text: str) -> str:
    """Return a canonical fingerprint for a question string."""
    return normalise_text(question_text)


def extract_concept_keywords(concept_text: str) -> Set[str]:
    """Extract significant content keywords from a concept tag, preserving domain logic operators."""
    norm = normalise_text(concept_text)
    words = set(norm.split())
    keywords = {w for w in words if (w in LOGIC_KEYWORDS or (w not in COMMON_STOPWORDS and len(w) > 2))}
    return keywords if keywords else words


def _ngrams(text: str, n: int = 3) -> Set[str]:
    """Generate word n-grams from normalized text."""
    words = text.split()
    if len(words) < n:
        return {text} if text else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _jaccard(set_a: Set[str], set_b: Set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def calculate_question_similarity(q1: str, q2: str) -> float:
    """
    Computes text similarity using a blend of content keyword overlap,
    bigrams, and trigrams to catch both exact matches and paraphrased questions.
    Returns value between 0.0 and 1.0.
    """
    norm1 = normalise_text(q1)
    norm2 = normalise_text(q2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0

    kw1 = extract_concept_keywords(norm1)
    kw2 = extract_concept_keywords(norm2)
    j_kw = _jaccard(kw1, kw2)

    trigrams1 = _ngrams(norm1, 3)
    trigrams2 = _ngrams(norm2, 3)
    j_tri = _jaccard(trigrams1, trigrams2)

    bigrams1 = _ngrams(norm1, 2)
    bigrams2 = _ngrams(norm2, 2)
    j_bi = _jaccard(bigrams1, bigrams2)

    can_sim = calculate_canonical_similarity(q1, q2)
    # Blend: strong keyword overlap, n-gram match, or canonical alias/synonym overlap
    return max(j_tri, j_bi * 0.9, j_kw * 0.85, can_sim)


def calculate_concept_similarity(concept1: str, concept2: str) -> float:
    """
    Computes semantic similarity between two concept tags using a combination of
    Jaccard keyword similarity and overlap coefficient (useful when one concept is concisely named).
    """
    norm1 = normalise_text(concept1)
    norm2 = normalise_text(concept2)
    if norm1 == norm2 and norm1:
        return 1.0

    kw1 = extract_concept_keywords(concept1)
    kw2 = extract_concept_keywords(concept2)
    if not kw1 or not kw2:
        return 0.0

    jaccard = _jaccard(kw1, kw2)
    min_size = min(len(kw1), len(kw2))
    overlap_coeff = len(kw1 & kw2) / min_size if min_size else 0.0

    return max(jaccard, overlap_coeff)


# ─────────────────────────────────────────────────────────────────────────────
# Within-Quiz Duplicate Detection & Option Verification
# ─────────────────────────────────────────────────────────────────────────────

def are_options_duplicate(opts1: Any, opts2: Any) -> Tuple[bool, float]:
    """Checks if two questions share essentially the same 4 options, even if permuted."""
    if not isinstance(opts1, dict) or not isinstance(opts2, dict):
        return False, 0.0
    s1 = {normalise_text(str(v)) for v in opts1.values() if v and len(normalise_text(str(v))) > 0}
    s2 = {normalise_text(str(v)) for v in opts2.values() if v and len(normalise_text(str(v))) > 0}
    if not s1 or not s2:
        return False, 0.0
    overlap = len(s1 & s2) / max(len(s1), len(s2))
    return (overlap >= 0.75, overlap)


def is_question_near_duplicate(q1: Dict[str, Any], q2: Dict[str, Any], topic_name: str = "") -> Tuple[bool, str]:
    """
    Determines if q2 is an exact, near-duplicate, or equivalent learning objective duplicate of q1.
    Checks:
      1. Exact wording match (Level 1)
      2. Artificial variation patterns: '(Application Variation)', 'Variation'
      3. High textual / phrasing similarity (Level 2)
      4. Canonical alias & synonym similarity (Level 2)
      5. Option set permutation with shared question topic
      6. Same correct answer text with moderate question overlap
      7. Equivalent learning objective (Level 3): tests identical target entity and cognitive facet
    """
    t1 = str(q1.get("question", "")).strip()
    t2 = str(q2.get("question", "")).strip()
    if not t1 or not t2:
        return False, ""

    norm1 = normalise_text(t1)
    norm2 = normalise_text(t2)

    # 1. Exact match (Level 1)
    if norm1 == norm2:
        return True, f"Exact identical question wording: '{t2[:60]}...'"

    # 2. Artificial variations (e.g. "(Application Variation)")
    if "variation" in norm1 or "variation" in norm2:
        base1 = re.sub(r'\(?\b(?:application\s+)?variation\b\)?', '', norm1).strip()
        base2 = re.sub(r'\(?\b(?:application\s+)?variation\b\)?', '', norm2).strip()
        if base1 == base2 or (base1 and base2 and calculate_question_similarity(base1, base2) >= 0.70):
            return True, f"Artificial variation of existing question: '{t2[:60]}...'"

    # Safeguard against false positive collisions on distinct technical concepts
    # When questions test mutually exclusive domain facets or operators,
    # they are NOT duplicates even if they share high template/stem similarity:
    can_tokens1 = extract_canonical_tokens(f"{t1} {q1.get('concept', '')}", topic_name=topic_name)
    can_tokens2 = extract_canonical_tokens(f"{t2} {q2.get('concept', '')}", topic_name=topic_name)

    # A. Distinct logic gates (AND vs OR vs NOT vs XOR vs NAND vs NOR vs XNOR)
    gates = {"and_gate", "or_gate", "not_gate", "xor_gate", "nand_gate", "nor_gate", "xnor_gate"}
    g1 = can_tokens1 & gates
    g2 = can_tokens2 & gates
    if g1 and g2 and g1 != g2:
        return False, ""

    # B. Distinct input mechanisms (excitatory vs inhibitory)
    in_types = {"excitation_facet", "inhibition_facet"}
    it1 = can_tokens1 & in_types
    it2 = can_tokens2 & in_types
    if it1 and it2 and it1 != it2:
        return False, ""

    # C. Distinct function types (activation function vs loss function)
    fn_types = {"activation_facet", "loss_facet"}
    ft1 = can_tokens1 & fn_types
    ft2 = can_tokens2 & fn_types
    if ft1 and ft2 and ft1 != ft2:
        return False, ""

    # D. Distinct backpropagation mechanisms (chain rule vs weight updates vs gradient calculation)
    bp_types = {"chain_rule_facet", "weight_update_facet", "gradient_calc_facet"}
    bt1 = can_tokens1 & bp_types
    bt2 = can_tokens2 & bp_types
    if bt1 and bt2 and bt1 != bt2:
        return False, ""

    # 3. Canonical alias & synonym similarity (Level 2)
    can_sim = calculate_canonical_similarity(t1, t2, topic_name=topic_name)
    if can_sim >= 0.55:
        return True, f"Near-duplicate wording / paraphrasing ({can_sim:.2f}) with question: '{t1[:60]}...'"

    # 4. High textual / phrasing similarity (Level 2)
    sim = calculate_question_similarity(t1, t2)
    if sim >= 0.50:
        return True, f"High phrasing similarity ({sim:.2f}) with question: '{t1[:60]}...'"

    # 5. Option set duplication (permuted options)
    opts_dup, opt_ratio = are_options_duplicate(q1.get("options"), q2.get("options"))
    if opts_dup and (sim >= 0.28 or can_sim >= 0.28):
        return True, f"Identical answer option set ({opt_ratio:.0%} match) with question: '{t1[:60]}...'"

    # 6. Same correct answer text with moderate question overlap
    c1 = str(q1.get("correct_answer", "")).upper()
    c2 = str(q2.get("correct_answer", "")).upper()
    ans_val1 = normalise_text(str(q1.get("options", {}).get(c1, "")))
    ans_val2 = normalise_text(str(q2.get("options", {}).get(c2, "")))
    if ans_val1 and ans_val2 and ans_val1 == ans_val2 and len(ans_val1.split()) >= 2 and (sim >= 0.38 or can_sim >= 0.38):
        return True, f"Tests identical target answer fact ('{ans_val1[:40]}') with similar stem ({max(sim, can_sim):.2f})"

    # 7. Equivalent learning objective (Level 3): same target entity and cognitive facet with semantic overlap
    lo1 = extract_learning_objective(t1, str(q1.get("concept", "")), topic_name=topic_name)
    lo2 = extract_learning_objective(t2, str(q2.get("concept", "")), topic_name=topic_name)
    if lo1 and lo2 and not lo1.endswith(":concept_general") and not lo1.startswith("general_topic:") and lo1 == lo2:
        if can_sim >= 0.25 or sim >= 0.25:
            return True, f"Tests equivalent learning objective ('{lo1}') with question: '{t1[:60]}...'"

    return False, ""


def detect_within_quiz_duplicates(questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Scans all questions within a single quiz to find exact and near-duplicates.
    Returns unique questions, flagged duplicate indices, duplicate pairs, and reasons.
    """
    duplicate_indices = set()
    duplicate_pairs = []
    reasons = []

    for j in range(len(questions)):
        if j in duplicate_indices:
            continue
        for i in range(j):
            if i in duplicate_indices:
                continue
            is_dup, reason = is_question_near_duplicate(questions[i], questions[j])
            if is_dup:
                duplicate_indices.add(j)
                duplicate_pairs.append((i, j, reason))
                reasons.append(f"Q{j + 1} duplicates Q{i + 1}: {reason}")
                break

    unique_questions = [q for idx, q in enumerate(questions) if idx not in duplicate_indices]
    return {
        "has_duplicates": len(duplicate_indices) > 0,
        "duplicate_indices": sorted(list(duplicate_indices)),
        "duplicate_pairs": duplicate_pairs,
        "unique_questions": unique_questions,
        "duplicate_reasons": reasons
    }


GENERIC_DOMAIN_WORDS = {
    "linear", "concept", "concepts", "introduction", "basic", "basics",
    "definition", "definitions", "application", "applications", "method", "methods",
    "type", "types", "system", "systems", "example", "examples", "properties",
    "overview", "problem", "problems", "theory", "process", "rule", "rules",
    "function", "functions", "model", "models", "structure", "structures",
    "analysis", "technique", "techniques", "simple", "general", "standard",
    "different", "following", "given", "common", "important", "order", "value", "values",
    "data", "database", "databases", "query", "queries",
    "layer", "layers", "activation", "activations", "gradient", "gradients",
    "output", "outputs", "input", "inputs", "parameter", "parameters", "weight", "weights"
}


def is_question_relevant_to_topic(
    question: Dict[str, Any],
    topic_name: str,
    concepts: Optional[List[str]] = None,
    syllabus_context: Optional[str] = None,
    course_code: str = "",
    competing_topics: Optional[List[Any]] = None
) -> Tuple[bool, str]:
    """
    Authoritative topic isolation validator.
    Verifies that:
      1. Strips untrusted student notes from syllabus_context so passive reference never dilutes topic boundaries.
      2. Requires distinctive keyword or concept overlap with the selected topic.
      3. Verifies that the question does NOT primarily test competing or sibling syllabus topics
         from the same course or uploaded notes document.
    """
    q_text = str(question.get("question", "")).strip()
    if not q_text:
        return False, "Empty question statement"

    # Extract target domain vocabulary (excluding untrusted student notes blocks)
    vocab_sources = [topic_name]
    if concepts:
        vocab_sources.extend(concepts)
    if syllabus_context:
        clean_ctx = re.sub(
            r"<student_notes_untrusted>.*?</student_notes_untrusted>",
            "",
            syllabus_context,
            flags=re.DOTALL | re.IGNORECASE
        ).strip()
        if clean_ctx:
            vocab_sources.append(clean_ctx)

    target_text = " ".join(vocab_sources)
    target_norm = normalise_text(target_text)
    target_keywords = extract_concept_keywords(target_norm)

    if len(target_keywords) < 3:
        words = set(target_norm.split())
        target_keywords = {w for w in words if w not in COMMON_STOPWORDS and len(w) >= 2}

    # Target concepts set for direct concept matching
    target_concepts_lower = {str(c).strip().lower() for c in (concepts or []) if str(c).strip()}

    # If course_code is provided, enrich target_keywords and target_concepts with curriculum concepts for target topic
    if course_code:
        try:
            from services.curriculum_service import curriculum_service
            crs = curriculum_service.get_course(course_code)
            if crs:
                target_clean = re.sub(r'[^a-z0-9]', '', topic_name.lower()).replace('losss', 'loss')
                for ct in crs.get("topics", []):
                    ct_clean = re.sub(r'[^a-z0-9]', '', ct.get("name", "").lower()).replace('losss', 'loss')
                    if ct_clean == target_clean or (len(target_clean) >= 4 and target_clean in ct_clean) or (len(ct_clean) >= 4 and ct_clean in target_clean):
                        for c in ct.get("concepts", []):
                            target_concepts_lower.add(str(c).strip().lower())
                            for kw in extract_concept_keywords(normalise_text(str(c))):
                                target_keywords.add(kw)
        except Exception:
            pass

    # Question keywords (text + concept + explanation + options)
    q_concept_raw = str(question.get("concept", "")).strip()
    q_explanation_raw = str(question.get("explanation", "")).strip()
    q_full = q_text + " " + q_concept_raw + " " + q_explanation_raw
    if isinstance(question.get("options"), dict):
        for opt_val in question.get("options", {}).values():
            q_full += " " + str(opt_val)

    q_norm = normalise_text(q_full)
    q_keywords = extract_concept_keywords(q_norm)

    # 1. Target Overlap check
    shared = target_keywords & q_keywords
    distinctive = shared - GENERIC_DOMAIN_WORDS

    stem_matches = []
    for kw in target_keywords:
        if kw not in GENERIC_DOMAIN_WORDS and len(kw) >= 4 and any((qw.startswith(kw) or kw.startswith(qw)) for qw in q_keywords if len(qw) >= 4):
            stem_matches.append(kw)

    has_target_relevance = (len(distinctive) >= 1) or (len(shared) >= 2) or (len(stem_matches) >= 1)
    if not has_target_relevance:
        return False, f"Question has no relevance to topic '{topic_name}' or its syllabus concepts"

    # 2. Competing Topics Isolation Check
    competing_specs: List[Dict[str, Any]] = []
    seen_competing_names = {topic_name.strip().lower()}

    if competing_topics is not None:
        for ct in competing_topics:
            if isinstance(ct, str) and ct.strip() and ct.strip().lower() not in seen_competing_names:
                seen_competing_names.add(ct.strip().lower())
                competing_specs.append({"name": ct.strip(), "concepts": []})
            elif isinstance(ct, dict) and ct.get("name") and ct["name"].strip().lower() not in seen_competing_names:
                seen_competing_names.add(ct["name"].strip().lower())
                competing_specs.append(ct)
            elif isinstance(ct, dict) and ct.get("topic_name") and ct["topic_name"].strip().lower() not in seen_competing_names:
                seen_competing_names.add(ct["topic_name"].strip().lower())
                competing_specs.append({"name": ct["topic_name"].strip(), "concepts": ct.get("matched_concepts", [])})
    course_wide_kws: Set[str] = set()
    if course_code:
        try:
            from services.curriculum_service import curriculum_service
            from collections import Counter
            crs = curriculum_service.get_course(course_code)
            if crs:
                course_kws = extract_concept_keywords(normalise_text(crs.get("name", "")))
                kw_counts = Counter()
                for ct in crs.get("topics", []):
                    vocab = [ct.get("name", "")] + [str(c) for c in ct.get("concepts", [])]
                    for kw in extract_concept_keywords(normalise_text(" ".join(vocab))):
                        kw_counts[kw] += 1
                course_wide_kws = {kw for kw, cnt in kw_counts.items() if cnt >= 2} | course_kws

                target_clean = re.sub(r'[^a-z0-9]', '', topic_name.lower()).replace('losss', 'loss')
                for ct in crs.get("topics", []):
                    ct_name = ct.get("name", "").strip()
                    ct_clean = re.sub(r'[^a-z0-9]', '', ct_name.lower()).replace('losss', 'loss')
                    if ct_name and ct_name.lower() not in seen_competing_names:
                        if ct_clean == target_clean or (len(target_clean) >= 4 and target_clean in ct_clean) or (len(ct_clean) >= 4 and ct_clean in target_clean):
                            continue
                        seen_competing_names.add(ct_name.lower())
                        competing_specs.append(ct)
        except Exception:
            pass

    # Check whether question is dominated by any competing topic
    for comp in competing_specs:
        c_name = comp.get("name") or comp.get("topic_name") or ""
        c_concepts = comp.get("concepts", []) or []

        c_vocab = [c_name] + [str(c) for c in c_concepts]
        c_norm = normalise_text(" ".join(c_vocab))
        c_kw = extract_concept_keywords(c_norm)

        # Competing keywords exclusive of target topic keywords, generic domain words, and course-wide terms
        comp_exclusive = ((c_kw - target_keywords) - GENERIC_DOMAIN_WORDS) - course_wide_kws
        if not comp_exclusive:
            continue

        comp_overlap = comp_exclusive & q_keywords

        # Check declared concept belonging exclusively to competing topic
        c_concepts_lower = {str(c).strip().lower() for c in c_concepts if str(c).strip()}
        c_concept_match = (
            q_concept_raw.lower() in c_concepts_lower or
            q_concept_raw.lower() == c_name.lower()
        ) and (q_concept_raw.lower() not in target_concepts_lower and q_concept_raw.lower() != topic_name.lower())

        if c_concept_match and len(comp_overlap) >= 1 and len(distinctive) == 0:
            return False, f"Question concept '{q_concept_raw}' belongs to competing topic '{c_name}'"

        if len(comp_overlap) >= 2 and len(comp_overlap) > len(distinctive):
            topic_clean = topic_name.strip().lower()
            q_text_clean = q_text.lower()
            if topic_clean in q_text_clean or any(c in q_text_clean for c in target_concepts_lower if len(c) >= 5):
                if len(comp_overlap) >= 3 and len(comp_overlap) >= 2 * len(distinctive):
                    return False, f"Question primarily tests competing topic '{c_name}' (matched keywords: {list(comp_overlap)[:4]}) instead of selected topic '{topic_name}'"
            else:
                return False, f"Question primarily tests competing topic '{c_name}' (matched keywords: {list(comp_overlap)[:4]}) instead of selected topic '{topic_name}'"

    if len(distinctive) >= 1:
        return True, f"Distinctive relevant keywords found: {list(distinctive)[:4]}"
    if len(shared) >= 2:
        return True, f"Relevant keywords found: {list(shared)[:4]}"
    if stem_matches:
        return True, f"Stem match with keyword '{stem_matches[0]}'"

    return False, f"Question has no relevance to topic '{topic_name}' or its syllabus concepts"


def normalize_question_concept(
    q: Dict[str, Any],
    topic_name: str,
    concepts: Optional[List[str]] = None
) -> str:
    """
    Normalizes the concept field of a question to align with genuine syllabus concepts.
    Maps vague or missing concepts to the most appropriate syllabus concept.
    """
    raw_c = str(q.get("concept", "")).strip()
    PLACEHOLDERS = {"concept name", "concept", "general", "none", "n/a", "null", "undefined", ""}

    if concepts:
        clean_concepts = [c.strip() for c in concepts if c and c.strip()]
        if clean_concepts:
            # 1. Direct match with a syllabus concept
            for sc in clean_concepts:
                if normalise_text(sc) == normalise_text(raw_c):
                    return sc

            # 2. Similarity match with raw concept
            if raw_c.lower() not in PLACEHOLDERS:
                best_sc = None
                best_sim = 0.0
                for sc in clean_concepts:
                    sim = calculate_concept_similarity(raw_c, sc)
                    if sim > best_sim:
                        best_sim = sim
                        best_sc = sc
                if best_sim >= 0.40 and best_sc:
                    return best_sc

            # 3. Keyword and semantic match with question text
            q_text = str(q.get("question", ""))
            q_norm = normalise_text(q_text)
            q_kw = extract_concept_keywords(q_norm)

            best_sc = None
            max_score = 0.0
            for sc in clean_concepts:
                sc_norm = normalise_text(sc)
                sc_kw = extract_concept_keywords(sc_norm)
                score = 0.0
                # Direct phrase match in question text
                if sc_norm in q_norm:
                    score += 10.0
                # Distinctive keyword overlap
                distinctive_overlap = (sc_kw & q_kw) - GENERIC_DOMAIN_WORDS
                score += len(distinctive_overlap) * 3.0
                # Generic keyword overlap
                generic_overlap = (sc_kw & q_kw) & GENERIC_DOMAIN_WORDS
                score += len(generic_overlap) * 1.0
                # Stem matching
                for skw in sc_kw:
                    if skw not in GENERIC_DOMAIN_WORDS and len(skw) >= 4:
                        if any(skw in qk or qk in skw for qk in q_kw if len(qk) >= 4):
                            score += 1.5
                if score > max_score:
                    max_score = score
                    best_sc = sc
            if max_score > 0.0 and best_sc:
                return best_sc

            # If single concept in topic, use it
            if len(clean_concepts) == 1:
                return clean_concepts[0]

    # Fallback: if raw_c is meaningful and not a placeholder or course name, keep it
    if raw_c and raw_c.lower() not in PLACEHOLDERS and len(raw_c) > 2:
        return raw_c

    return topic_name


# ─────────────────────────────────────────────────────────────────────────────
# History Extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_fingerprints_from_history(topic_history: List[Dict[str, Any]]) -> List[str]:
    """Pull question texts from recent quiz_attempts and return fingerprints."""
    fps: List[str] = []
    for attempt in topic_history:
        quiz_data = attempt.get("quiz_data", {})
        questions = quiz_data.get("questions", []) if isinstance(quiz_data, dict) else []
        for q in questions:
            q_text = q.get("question", "") if isinstance(q, dict) else ""
            if q_text:
                fp = fingerprint(q_text)
                if fp not in fps:
                    fps.append(fp)
            if len(fps) >= HISTORY_QUESTION_LIMIT:
                return fps
    return fps


def extract_concepts_from_history(topic_history: List[Dict[str, Any]]) -> List[str]:
    """Pull concept tags from recent quiz_attempts, preserving uniqueness."""
    concepts: List[str] = []
    for attempt in topic_history:
        quiz_data = attempt.get("quiz_data", {})
        questions = quiz_data.get("questions", []) if isinstance(quiz_data, dict) else []
        for q in questions:
            c_text = q.get("concept", "") if isinstance(q, dict) else ""
            if c_text:
                c_clean = c_text.strip()
                if c_clean and c_clean not in concepts:
                    concepts.append(c_clean)
            if len(concepts) >= HISTORY_CONCEPT_LIMIT:
                return concepts
    return concepts


# ─────────────────────────────────────────────────────────────────────────────
# Novelty Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_quiz_novelty(
    questions: List[Dict[str, Any]],
    history_fingerprints: List[str],
    history_concepts: List[str],
    history_questions: Optional[List[Dict[str, Any]]] = None,
    topic_name: str = ""
) -> Dict[str, Any]:
    """
    Evaluates whether a generated quiz contains duplicates across three levels:
      - Level 1: Exact duplicate detection (normalized text / fingerprint match).
      - Level 2: Near-duplicate detection (paraphrased wording, canonical alias/synonyms).
      - Level 3: Concept and learning-objective detection (identical target entity and facet).

    Returns:
        dict:
            is_fully_novel: bool
            text_duplicate_indices: List[int]
            concept_duplicate_indices: List[int]
            all_flagged_indices: List[int]
            duplicate_reasons: List[str]
            novel_count: int
    """
    if not history_fingerprints and not history_concepts and not history_questions:
        return {
            "is_fully_novel": True,
            "text_duplicate_indices": [],
            "concept_duplicate_indices": [],
            "all_flagged_indices": [],
            "duplicate_reasons": [],
            "novel_count": len(questions)
        }

    text_dups = []
    concept_dups = []
    reasons = []

    # Precompute normalized fingerprints for Level 1 exact matching
    norm_hist_fps = {normalise_text(fp) for fp in history_fingerprints if fp}

    for idx, q in enumerate(questions):
        q_text = str(q.get("question", "")).strip()
        q_concept = str(q.get("concept", "")).strip()
        q_fp = normalise_text(q_text)
        is_dup = False

        # Level 1: Exact duplicate match
        if q_fp in norm_hist_fps:
            text_dups.append(idx)
            reasons.append(f"[Level 1 Exact Duplicate] Q{idx + 1} matches previous attempt wording: '{q_text[:55]}...'")
            is_dup = True

        # Level 2 & 3: Match against detailed history questions if available
        if not is_dup and history_questions:
            for prev_q in history_questions:
                dup_found, dup_msg = is_question_near_duplicate(prev_q, q, topic_name=topic_name)
                if dup_found:
                    if "learning objective" in dup_msg.lower():
                        concept_dups.append(idx)
                        reasons.append(f"[Level 3 Learning Objective] Q{idx + 1} {dup_msg}")
                    else:
                        text_dups.append(idx)
                        reasons.append(f"[Level 2 Near-Duplicate] Q{idx + 1} {dup_msg}")
                    is_dup = True
                    break

        # Level 2 fallback: High phrasing similarity against raw historical fingerprints
        if not is_dup and history_fingerprints:
            for hist_fp in history_fingerprints:
                sim = calculate_question_similarity(q_text, hist_fp)
                can_sim = calculate_canonical_similarity(q_text, hist_fp, topic_name=topic_name)
                max_s = max(sim, can_sim)
                if max_s >= TEXT_DUPLICATE_THRESHOLD:
                    text_dups.append(idx)
                    reasons.append(f"[Level 2 Near-Duplicate] Q{idx + 1} phrasing similarity ({max_s:.2f}) with previous question: '{hist_fp[:50]}...'")
                    is_dup = True
                    break

        # Level 3 fallback: Concept tracking against history concepts
        if not is_dup and history_concepts:
            for hist_concept in history_concepts:
                c_sim = calculate_concept_similarity(q_concept, hist_concept)
                if c_sim >= 0.85:  # Very high concept match on concise tags
                    concept_dups.append(idx)
                    reasons.append(f"[Level 3 Concept Overlap] Q{idx + 1} tests already-covered concept: '{hist_concept}'")
                    is_dup = True
                    break

    all_flagged = sorted(list(set(text_dups + concept_dups)))
    is_fully_novel = len(all_flagged) == 0

    return {
        "is_fully_novel": is_fully_novel,
        "text_duplicate_indices": sorted(list(set(text_dups))),
        "concept_duplicate_indices": sorted(list(set(concept_dups))),
        "all_flagged_indices": all_flagged,
        "duplicate_reasons": reasons,
        "novel_count": len(questions) - len(all_flagged)
    }


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Building
# ─────────────────────────────────────────────────────────────────────────────

def build_exclusion_prompt_block(
    history_fingerprints: List[str],
    history_concepts: List[str],
    attempt_id: str,
    attempt_number: int = 1
) -> str:
    """
    Constructs an explicit anti-repetition block for the LLM prompt.
    Supplies both previous question phrasing and already-tested concepts.
    """
    if not history_fingerprints and not history_concepts:
        return (
            f"Quiz Attempt ID: {attempt_id} (First Attempt)\n"
            "Generate 10 fresh, high-quality multiple choice questions covering foundational and advanced aspects of this topic.\n"
        )

    parts = [f"Quiz Attempt ID: {attempt_id} (Retake Attempt #{attempt_number})\n"]
    parts.append("CRITICAL NOVELTY AND CONCEPT DIVERSITY REQUIREMENTS:")
    parts.append(
        "The student has already attempted a quiz on this topic. Repeating questions, identical answers, "
        "or testing the exact same sub-concepts will degrade the learning experience.\n"
    )

    if history_concepts:
        sample_concepts = history_concepts[:MAX_EXCLUSION_PROMPT_CONCEPTS]
        concept_list = ", ".join(f"'{c}'" for c in sample_concepts)
        parts.append(
            f"1. PREVIOUSLY COVERED CONCEPTS:\n"
            f"   [{concept_list}]\n"
            f"   Test syllabus concepts through FRESH angles, new numerical values, edge cases, "
            f"   practical scenarios, and applied problems rather than repeating previous questions.\n"
        )

    if history_fingerprints:
        sample_questions = history_fingerprints[:MAX_EXCLUSION_PROMPT_QUESTIONS]
        q_list = "\n".join(f"   - {fp}" for fp in sample_questions)
        parts.append(
            f"2. PREVIOUS QUESTION PHRASINGS (DO NOT REPEAT OR PARAPHRASE):\n"
            f"{q_list}\n"
        )

    parts.append(
        "3. RULES FOR FRESH QUESTIONS:\n"
        "   - You MUST generate and return all 10 questions in the 'questions' array (count must be exactly 10).\n"
        "   - Every question must test a distinct learning objective or scenario.\n"
        "   - Do not ask standard definition questions if already tested.\n"
        "   - Use applied problem statements (e.g. given specific input values, weights, or conditions, what is the output?).\n"
        "   - Ensure all 4 options are distinct, plausible, and only one is correct.\n"
    )

    return "\n".join(parts)
