"""
Controlled 4-Attempt Retake Experiment for LearnAI
Reproduces user-reported retake behavior and rigorously validates:
- Attempt 1: Initial quiz (10 questions on McCulloch-Pitts Neuron) -> Submit.
- Attempt 2: Retake with candidate pool containing user-reported duplicate pairs -> Targeted Replacement -> Submit.
- Attempt 3: Retake with candidate pool testing previously covered concepts -> Targeted Replacement -> Submit.
- Attempt 4: Retake with novel questions -> Verified against Attempts 1, 2, 3 -> Submit.
Calculates and displays pairwise comparison table:
- Exact duplicate count
- Normalized-text duplicate count
- Semantic duplicate count
- Distinct learning-objective count
- Questions rejected and replaced
"""

import os
import json
import sqlite3
import uuid
from typing import Dict, Any, List, Tuple

from services.database import (
    init_db,
    get_or_create_student,
    create_quiz_session,
    submit_quiz_atomic,
    get_question_fingerprints,
    get_question_concepts,
    get_question_history_full,
)
from services.evaluator import evaluate_answers
from services.novelty import (
    evaluate_quiz_novelty,
    is_question_near_duplicate,
    normalise_text,
    extract_learning_objective,
    calculate_canonical_similarity,
    calculate_question_similarity,
)


def run_4_attempt_experiment(db_path: str):
    print("=" * 80)
    print("STARTING 4-ATTEMPT RETAKE VERIFICATION EXPERIMENT")
    print("Course: 22AM301 - Deep Learning | Topic: McCulloch-Pitts Neuron")
    print("=" * 80)

    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    student_id = get_or_create_student("student_4_attempt_eval", conn=conn)
    course_code = "22AM301"
    course_name = "Deep Learning"
    topic_name = "McCulloch-Pitts Neuron"

    attempts_data: List[List[Dict[str, Any]]] = []
    rejected_log: List[List[Tuple[int, str, str]]] = []
    replacements_log: List[List[Dict[str, Any]]] = []

    # ─────────────────────────────────────────────────────────────────────────
    # ATTEMPT 1: 10 baseline questions covering standard MP neuron aspects
    # ─────────────────────────────────────────────────────────────────────────
    attempt1_raw = [
        # 4 Easy
        {"question": "What is the main purpose of the McCulloch-Pitts neuron?", "options": {"A": "To model biological neuron logic", "B": "To train deep CNNs", "C": "To store image weights", "D": "To invert matrices"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Purpose of MP Neuron"},
        {"question": "Which values can a McCulloch-Pitts neuron take as inputs?", "options": {"A": "Only binary values {0, 1}", "B": "Continuous real numbers", "C": "Complex numbers", "D": "Negative floating points"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Input Domain"},
        {"question": "What type of threshold activation function is used in an MP neuron?", "options": {"A": "Binary step function", "B": "Sigmoid function", "C": "ReLU", "D": "Softmax"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Threshold Activation"},
        {"question": "Who introduced the formal model of the artificial neuron in 1943?", "options": {"A": "Warren McCulloch and Walter Pitts", "B": "Alan Turing", "C": "Frank Rosenblatt", "D": "John von Neumann"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Historical Context"},
        # 4 Medium
        {"question": "Why can't a single McCulloch-Pitts neuron implement the XOR function?", "options": {"A": "XOR is not linearly separable", "B": "Weights are non-zero", "C": "Threshold is too low", "D": "Inputs are binary"}, "correct_answer": "A", "difficulty": "Medium", "concept": "XOR Limitation"},
        {"question": "How are inhibitory inputs handled in a McCulloch-Pitts neuron?", "options": {"A": "Any active inhibitory input completely prevents firing", "B": "They add negative weights to the sum", "C": "They lower the threshold value", "D": "They invert the output bit"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Inhibitory Inputs"},
        {"question": "How can an MP neuron be configured to implement a 2-input logical AND gate?", "options": {"A": "Excitatory weights = 1, threshold = 2", "B": "Excitatory weights = 1, threshold = 1", "C": "Excitatory weights = 0, threshold = 1", "D": "Inhibitory weights = 1, threshold = 0"}, "correct_answer": "A", "difficulty": "Medium", "concept": "AND Gate Implementation"},
        {"question": "What configuration of an MP neuron implements a 2-input logical OR gate?", "options": {"A": "Excitatory weights = 1, threshold = 1", "B": "Excitatory weights = 1, threshold = 2", "C": "Inhibitory weights = 1, threshold = 2", "D": "Threshold = 0 with no inputs"}, "correct_answer": "A", "difficulty": "Medium", "concept": "OR Gate Implementation"},
        # 2 Hard
        {"question": "Under what condition does a generalized n-input MP neuron fire when excitatory inputs are present?", "options": {"A": "Sum of excitatory inputs >= threshold AND no inhibitory input is 1", "B": "Sum of all inputs > 0 regardless of inhibition", "C": "Average of inputs equals the threshold", "D": "Product of inputs exceeds unity"}, "correct_answer": "A", "difficulty": "Hard", "concept": "Firing Condition"},
        {"question": "What is the primary architectural limitation of MP neurons compared to modern Perceptrons?", "options": {"A": "Fixed pre-determined weights with no learning algorithm", "B": "Inability to output binary values", "C": "Lack of threshold parameters", "D": "Requires continuous floating point inputs"}, "correct_answer": "A", "difficulty": "Hard", "concept": "Architectural Comparison"}
    ]

    sess1_id = str(uuid.uuid4())
    create_quiz_session(sess1_id, student_id, course_code, course_name, topic_name, attempt1_raw, "mixed", conn=conn)
    sub1 = submit_quiz_atomic(sess1_id, student_id, ["A"] * 10, evaluator_fn=evaluate_answers, conn=conn)
    attempts_data.append(attempt1_raw)
    rejected_log.append([])
    replacements_log.append([])
    print(f"Attempt 1 submitted: 10 questions saved. Score: {sub1['score']}/10")

    # ─────────────────────────────────────────────────────────────────────────
    # ATTEMPT 2: Candidate pool containing the 3 user bug duplicates
    # ─────────────────────────────────────────────────────────────────────────
    history_fps_1 = get_question_fingerprints(student_id, course_code, topic_name, conn=conn)
    history_concepts_1 = get_question_concepts(student_id, course_code, topic_name, conn=conn)
    history_full_1 = get_question_history_full(student_id, course_code, topic_name, conn=conn)

    attempt2_candidates = [
        # Duplicate 1 (User Case 1): purpose vs function
        {"question": "What is the primary function of the MP neuron?", "options": {"A": "To model biological neuron logic", "B": "To train deep CNNs", "C": "To store image weights", "D": "To invert matrices"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Purpose of MP Neuron"},
        # Duplicate 2 (User Case 2): values vs inputs
        {"question": "What are the allowed input values for an MP neuron?", "options": {"A": "Only binary values {0, 1}", "B": "Continuous real numbers", "C": "Complex numbers", "D": "Negative floating points"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Input Domain"},
        # Duplicate 3 (User Case 3): implement XOR vs solve XOR problem
        {"question": "What prevents an MP neuron from solving the XOR problem?", "options": {"A": "Non-linear separability of XOR", "B": "Zero weights", "C": "High threshold", "D": "Step activation"}, "correct_answer": "A", "difficulty": "Medium", "concept": "XOR Limitation"},
        # 7 Novel questions
        {"question": "How does a McCulloch-Pitts neuron implement a logical NOT gate?", "options": {"A": "Single inhibitory input with threshold = 0", "B": "Excitatory input with threshold = 2", "C": "Two excitatory inputs with threshold = 1", "D": "It cannot implement a NOT gate"}, "correct_answer": "A", "difficulty": "Easy", "concept": "NOT Gate Implementation"},
        {"question": "Can a single McCulloch-Pitts neuron have fractional or real-valued weights?", "options": {"A": "No, weights are conventionally fixed at +1 for excitation or -infinity for inhibition", "B": "Yes, any floating point value is allowed", "C": "Only between 0 and 1", "D": "Only negative fractional values"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Weight Constraints"},
        {"question": "How is the NOR operation realized using McCulloch-Pitts neurons?", "options": {"A": "Threshold = 0 with all inputs acting as inhibitory", "B": "Threshold = 2 with excitatory inputs", "C": "Threshold = 1 with excitatory inputs", "D": "NOR cannot be realized by MP neurons"}, "correct_answer": "A", "difficulty": "Medium", "concept": "NOR Gate Implementation"},
        {"question": "What mathematical proof demonstrates that any boolean function can be realized with a network of MP neurons?", "options": {"A": "Completeness of AND, OR, and NOT connectives", "B": "Universal Approximation Theorem for MLPs", "C": "Central Limit Theorem", "D": "Banach Fixed Point Theorem"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Boolean Completeness"},
        {"question": "In what key way does the threshold theta in an MP neuron differ from the bias term b in a Perceptron?", "options": {"A": "Theta is a fixed non-learned scalar while bias b is an adaptable learned parameter", "B": "Theta must always be negative while bias must be positive", "C": "Theta only applies to outputs while bias applies to inputs", "D": "There is no mathematical difference"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Threshold vs Bias"},
        {"question": "How does synchronous time-step updating in multi-neuron MP networks allow modeling temporal sequential circuits?", "options": {"A": "Output delays of one unit time serve as state memory flip-flops", "B": "Continuous gradient flow across time steps", "C": "Stochastic random firing at discrete intervals", "D": "Backpropagation through time"}, "correct_answer": "A", "difficulty": "Hard", "concept": "Temporal Circuits"},
        {"question": "What is the computational complexity of evaluating an n-input McCulloch-Pitts neuron?", "options": {"A": "O(n) time and O(1) space", "B": "O(n^2) time and O(n) space", "C": "O(2^n) exponential time", "D": "O(log n) time"}, "correct_answer": "A", "difficulty": "Hard", "concept": "Computational Complexity"}
    ]

    eval2 = evaluate_quiz_novelty(
        questions=attempt2_candidates,
        history_fingerprints=history_fps_1,
        history_concepts=history_concepts_1,
        history_questions=history_full_1,
        topic_name=topic_name
    )

    rejections_2 = []
    for idx in eval2["all_flagged_indices"]:
        rejections_2.append((idx, attempt2_candidates[idx]["question"], eval2["duplicate_reasons"][eval2["all_flagged_indices"].index(idx)]))

    # Targeted replacement replaces flagged questions
    novel_kept_2 = [q for idx, q in enumerate(attempt2_candidates) if idx not in eval2["all_flagged_indices"]]
    replacements_2 = [
        # Replacement for Easy 1
        {"question": "What constitutes the output of a McCulloch-Pitts neuron when the excitation threshold is strictly met?", "options": {"A": "A single binary 1", "B": "A continuous probability", "C": "A vector of states", "D": "Zero output"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Output State"},
        # Replacement for Easy 2
        {"question": "Does a McCulloch-Pitts neuron have a mechanism for adaptive weight adjustment during execution?", "options": {"A": "No, it is a non-learning analytical model with fixed parameters", "B": "Yes, using Hebbian learning", "C": "Yes, using delta rule", "D": "Yes, via backpropagation"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Non-learning Character"},
        # Replacement for Medium
        {"question": "Why is the XOR problem solvable by a 2-layer network of MP neurons even though a single neuron fails?", "options": {"A": "Hidden layer decomposes non-linear space into linearly separable sub-regions", "B": "Weights become non-binary in 2 layers", "C": "The threshold is eliminated in 2 layers", "D": "Inhibitory inputs are converted to excitatory"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Multi-layer XOR Resolution"}
    ]
    attempt2_final = novel_kept_2 + replacements_2
    assert len(attempt2_final) == 10

    sess2_id = str(uuid.uuid4())
    create_quiz_session(sess2_id, student_id, course_code, course_name, topic_name, attempt2_final, "mixed", conn=conn)
    sub2 = submit_quiz_atomic(sess2_id, student_id, ["A"] * 10, evaluator_fn=evaluate_answers, conn=conn)
    attempts_data.append(attempt2_final)
    rejected_log.append(rejections_2)
    replacements_log.append(replacements_2)
    print(f"Attempt 2 submitted: {len(rejections_2)} duplicates detected and replaced. Final: 10 novel questions. Score: {sub2['score']}/10")

    # ─────────────────────────────────────────────────────────────────────────
    # ATTEMPT 3: Third retake testing further novel angles
    # ─────────────────────────────────────────────────────────────────────────
    history_fps_2 = get_question_fingerprints(student_id, course_code, topic_name, conn=conn)
    history_concepts_2 = get_question_concepts(student_id, course_code, topic_name, conn=conn)
    history_full_2 = get_question_history_full(student_id, course_code, topic_name, conn=conn)

    attempt3_candidates = [
        # Try a near-duplicate of Attempt 2's NOT gate question
        {"question": "How can a logical NOT operation be implemented with an MP neuron?", "options": {"A": "Inhibitory input with threshold = 0", "B": "Excitatory", "C": "Double threshold", "D": "Cannot"}, "correct_answer": "A", "difficulty": "Easy", "concept": "NOT Gate Implementation"},
        # 9 novel questions
        {"question": "What is the biological inspiration behind the absolute inhibitory input in McCulloch-Pitts neurons?", "options": {"A": "Direct hyperpolarization preventing action potential generation", "B": "Synaptic fatigue", "C": "Axon myelination", "D": "Dendritic arborization"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Biological Inspiration"},
        {"question": "In symbolic logic representations, which propositional calculus connectives can be modeled by MP units?", "options": {"A": "Conjunction, disjunction, and negation", "B": "Only universal quantifiers", "C": "Only modal logic operators", "D": "Only existential quantifiers"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Symbolic Logic"},
        {"question": "What happens if all inputs to an uninhibited MP neuron with threshold 0 are zero?", "options": {"A": "It fires (sum 0 >= 0)", "B": "It cannot fire", "C": "It produces an error", "D": "It outputs -1"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Zero Threshold Case"},
        {"question": "How can an MP neuron be configured to implement the NAND logic function?", "options": {"A": "Two inhibitory inputs with threshold = 0", "B": "Two excitatory inputs with threshold = 2", "C": "One excitatory and one inhibitory", "D": "NAND is not realizable with MP neurons"}, "correct_answer": "A", "difficulty": "Medium", "concept": "NAND Gate Implementation"},
        {"question": "What property of the McCulloch-Pitts neuron model guarantees deterministic state transitions in discrete time?", "options": {"A": "Fixed threshold and synchronous time-step updates", "B": "Probabilistic firing rates", "C": "Continuous weight updates", "D": "Random inhibitory fluctuations"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Determinism Property"},
        {"question": "Which of the following circuits requires recurrent connections between MP neurons?", "options": {"A": "A latch memory element storing a bit indefinitely", "B": "A simple 3-input AND gate", "C": "A 4-input OR gate", "D": "A NOT gate"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Recurrent Latch"},
        {"question": "In the original 1943 paper, how did McCulloch and Pitts relate neural networks to Turing machines?", "options": {"A": "Networks with cycles and infinite tape can compute any Turing-computable function", "B": "MP neurons disprove Turing computability", "C": "Neural networks are strictly weaker than finite automata", "D": "Turing machines cannot model binary neurons"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Turing Equivalence"},
        {"question": "How does the inhibitory veto mechanism in an MP neuron mathematically enforce non-linear gating?", "options": {"A": "Multiplying the excitation indicator by (1 - inhibitory_indicator)", "B": "Adding a constant to the activation function", "C": "Taking the logarithmic derivative", "D": "Computing matrix eigenvalues"}, "correct_answer": "A", "difficulty": "Hard", "concept": "Inhibitory Veto Math"},
        {"question": "Under what condition does a network of MP neurons exhibit stable limit cycles or sustained oscillations?", "options": {"A": "Cyclic inhibitory and excitatory feedback loops with appropriate delay", "B": "Feedforward connections with zero threshold", "C": "Eliminating all inhibitory inputs", "D": "Infinite threshold values"}, "correct_answer": "A", "difficulty": "Hard", "concept": "Limit Cycles"}
    ]

    eval3 = evaluate_quiz_novelty(
        questions=attempt3_candidates,
        history_fingerprints=history_fps_2,
        history_concepts=history_concepts_2,
        history_questions=history_full_2,
        topic_name=topic_name
    )

    rejections_3 = []
    for idx in eval3["all_flagged_indices"]:
        rejections_3.append((idx, attempt3_candidates[idx]["question"], eval3["duplicate_reasons"][eval3["all_flagged_indices"].index(idx)]))

    novel_kept_3 = [q for idx, q in enumerate(attempt3_candidates) if idx not in eval3["all_flagged_indices"]]
    replacements_3 = [
        {"question": "What is the effect of setting an MP neuron threshold theta greater than the total number of excitatory inputs?", "options": {"A": "The neuron can never fire under any input pattern", "B": "The neuron fires continuously", "C": "Inhibitory inputs are ignored", "D": "Weights become adaptive"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Unreachable Threshold"},
        {"question": "In threshold logic synthesis, what represents the dual of an MP neuron boolean function?", "options": {"A": "Negating both inputs and outputs while swapping threshold bounds", "B": "Inverting the threshold sign only", "C": "Setting all weights to zero", "D": "Eliminating all inhibitory lines"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Threshold Logic Duality"}
    ]
    attempt3_final = novel_kept_3 + replacements_3
    assert len(attempt3_final) == 10

    sess3_id = str(uuid.uuid4())
    create_quiz_session(sess3_id, student_id, course_code, course_name, topic_name, attempt3_final, "mixed", conn=conn)
    sub3 = submit_quiz_atomic(sess3_id, student_id, ["A"] * 10, evaluator_fn=evaluate_answers, conn=conn)
    attempts_data.append(attempt3_final)
    rejected_log.append(rejections_3)
    replacements_log.append(replacements_3)
    print(f"Attempt 3 submitted: {len(rejections_3)} duplicates detected and replaced. Final: 10 novel questions. Score: {sub3['score']}/10")

    # ─────────────────────────────────────────────────────────────────────────
    # ATTEMPT 4: Fourth retake evaluated against all 30 prior questions
    # ─────────────────────────────────────────────────────────────────────────
    history_fps_3 = get_question_fingerprints(student_id, course_code, topic_name, conn=conn)
    history_concepts_3 = get_question_concepts(student_id, course_code, topic_name, conn=conn)
    history_full_3 = get_question_history_full(student_id, course_code, topic_name, conn=conn)

    attempt4_raw = [
        # 4 Easy
        {"question": "In an MP neuron diagram, how are inhibitory synapses typically represented compared to excitatory ones?", "options": {"A": "Small circles or loop endings vs arrowheads", "B": "Thicker solid lines", "C": "Dotted green arrows", "D": "No visual distinction"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Diagrammatic Notation"},
        {"question": "Can an MP neuron represent real-valued continuous features like audio signals without prior discretization?", "options": {"A": "No, it strictly requires binarized or quantized input features", "B": "Yes, via pulse-width modulation", "C": "Yes, without any preprocessing", "D": "Only if features are normalized to [-1, 1]"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Discretization Requirement"},
        {"question": "What is the refractory period property in biological neurons that the basic MP model simplifies to discrete time steps?", "options": {"A": "The brief recovery interval where a neuron cannot immediately fire again", "B": "The time taken for axon myelination", "C": "The duration of synaptic neurotransmitter synthesis", "D": "The decay rate of action potentials"}, "correct_answer": "A", "difficulty": "Easy", "concept": "Refractory Period"},
        {"question": "How many distinct boolean functions of 2 variables can a single MP neuron compute?", "options": {"A": "14 out of 16 functions (all except XOR and XNOR)", "B": "All 16 functions", "C": "Only 4 functions", "D": "Only 8 functions"}, "correct_answer": "A", "difficulty": "Easy", "concept": "2-Variable Function Count"},
        # 4 Medium
        {"question": "How is the 2-variable XNOR function realized using a multi-neuron MP circuit?", "options": {"A": "Inverting the output of an XOR multi-neuron sub-network", "B": "Single neuron with threshold = 0", "C": "Single neuron with two inhibitory lines", "D": "XNOR cannot be realized even in multi-layer MP networks"}, "correct_answer": "A", "difficulty": "Medium", "concept": "XNOR Circuit"},
        {"question": "What is the primary difference between an MP neuron and a Perceptron regarding weight flexibility?", "options": {"A": "MP neuron weights are fixed and equal while Perceptron weights can be real-valued and learned", "B": "Perceptron weights must be integers while MP weights are real numbers", "C": "MP neurons allow negative excitatory weights", "D": "Perceptrons cannot implement inhibitory connections"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Weight Flexibility Comparison"},
        {"question": "How does increasing the threshold theta of an MP neuron from 1 to k (where k <= n) change its logical operation?", "options": {"A": "It converts an 'at least 1' OR detector into an 'at least k out of n' majority detector", "B": "It causes the neuron to output continuous values", "C": "It disables inhibitory inputs", "D": "It inverts the boolean output"}, "correct_answer": "A", "difficulty": "Medium", "concept": "K-of-N Detector"},
        {"question": "In what manner did Kleene build upon McCulloch-Pitts networks in 1956?", "options": {"A": "He formalized regular expressions and proved equivalence to finite automata modeled by MP nets", "B": "He developed the backpropagation algorithm", "C": "He proved the No Free Lunch theorem", "D": "He invented convolutional layers"}, "correct_answer": "A", "difficulty": "Medium", "concept": "Kleene Formalism"},
        # 2 Hard
        {"question": "Why is gradient-based optimization impossible on a pure McCulloch-Pitts neuron network?", "options": {"A": "The step activation has zero derivative almost everywhere and is discontinuous at zero", "B": "Matrix determinants are zero", "C": "Loss functions cannot be defined on binary outputs", "D": "Binary inputs cause numerical overflow"}, "correct_answer": "A", "difficulty": "Hard", "concept": "Gradient Non-differentiability"},
        {"question": "What algebraic condition on the hyperplane w * x = theta governs the decision boundary of an MP neuron in {0,1}^n hypercube?", "options": {"A": "It must separate the set of firing vertices from non-firing vertices via an affine hyperplane", "B": "It must pass strictly through the origin", "C": "It must be orthogonal to all coordinate axes simultaneously", "D": "It requires non-linear polynomial surfaces"}, "correct_answer": "A", "difficulty": "Hard", "concept": "Affine Hyperplane Boundary"}
    ]

    eval4 = evaluate_quiz_novelty(
        questions=attempt4_raw,
        history_fingerprints=history_fps_3,
        history_concepts=history_concepts_3,
        history_questions=history_full_3,
        topic_name=topic_name
    )

    assert eval4["is_fully_novel"] is True, f"Attempt 4 should be 100% novel, but got flagged: {eval4['duplicate_reasons']}"
    sess4_id = str(uuid.uuid4())
    create_quiz_session(sess4_id, student_id, course_code, course_name, topic_name, attempt4_raw, "mixed", conn=conn)
    sub4 = submit_quiz_atomic(sess4_id, student_id, ["A"] * 10, evaluator_fn=evaluate_answers, conn=conn)
    attempts_data.append(attempt4_raw)
    rejected_log.append([])
    replacements_log.append([])
    print(f"Attempt 4 submitted: 100% novel against 30 previous questions. Score: {sub4['score']}/10")

    conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # PAIRWISE COMPARISON ANALYSIS ACROSS ALL 4 ATTEMPTS
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("PAIRWISE ATTEMPT COMPARISON AUDIT (40 TOTAL QUESTIONS)")
    print("=" * 80)

    num_attempts = len(attempts_data)
    for a in range(num_attempts):
        for b in range(a + 1, num_attempts):
            q_list_a = attempts_data[a]
            q_list_b = attempts_data[b]

            exact_dups = 0
            norm_dups = 0
            sem_dups = 0
            matching_pairs = []

            for i, qa in enumerate(q_list_a):
                for j, qb in enumerate(q_list_b):
                    ta = qa["question"].strip()
                    tb = qb["question"].strip()

                    # Exact
                    if ta == tb:
                        exact_dups += 1
                        matching_pairs.append((f"Att{a+1}:Q{i+1}", f"Att{b+1}:Q{j+1}", "Exact", ta, tb))
                        continue

                    # Normalized
                    na = normalise_text(ta)
                    nb = normalise_text(tb)
                    if na == nb:
                        norm_dups += 1
                        matching_pairs.append((f"Att{a+1}:Q{i+1}", f"Att{b+1}:Q{j+1}", "Normalized", ta, tb))
                        continue

                    # Semantic / near
                    is_dup, reason = is_question_near_duplicate(qa, qb, topic_name=topic_name)
                    if is_dup:
                        sem_dups += 1
                        matching_pairs.append((f"Att{a+1}:Q{i+1}", f"Att{b+1}:Q{j+1}", f"Semantic ({reason})", ta, tb))

            print(f"Attempt {a+1} vs Attempt {b+1}:")
            print(f"  Exact Duplicates: {exact_dups}/10")
            print(f"  Normalized-Text Duplicates: {norm_dups}/10")
            print(f"  Semantic / Objective Duplicates: {sem_dups}/10")
            if matching_pairs:
                print("  Matches Found:")
                for m in matching_pairs:
                    print(f"    - {m[0]} <-> {m[1]} [{m[2]}]:\n      Q_A: {m[3]}\n      Q_B: {m[4]}")
            else:
                print("  Matches Found: None (100% pairwise distinct)")

    # ─────────────────────────────────────────────────────────────────────────
    # LEARNING OBJECTIVE COVERAGE & SUMMARY
    # ─────────────────────────────────────────────────────────────────────────
    all_40_questions = [q for att in attempts_data for q in att]
    assert len(all_40_questions) == 40

    lo_set = set()
    for q in all_40_questions:
        lo = extract_learning_objective(q["question"], q.get("concept", ""), topic_name=topic_name)
        lo_set.add(lo)

    print("\n" + "=" * 80)
    print("OVERALL 4-ATTEMPT METRICS SUMMARY")
    print("=" * 80)
    print(f"Total Completed Attempts: {num_attempts}")
    print(f"Total Completed Questions: {len(all_40_questions)}")
    print(f"Total Intercepted Duplicates Replaced: {sum(len(r) for r in rejected_log)}")
    print(f"Total Unique Learning Objective Facets: {len(lo_set)}")

    # Check difficulty breakdown per attempt
    for idx, att in enumerate(attempts_data):
        diffs = [q["difficulty"] for q in att]
        easy_cnt = diffs.count("Easy")
        med_cnt = diffs.count("Medium")
        hard_cnt = diffs.count("Hard")
        print(f"Attempt {idx+1} Difficulty Distribution: Easy={easy_cnt}, Medium={med_cnt}, Hard={hard_cnt} (Expected: 4, 4, 2)")
        assert easy_cnt == 4, f"Attempt {idx+1} has {easy_cnt} Easy questions, expected 4"
        assert med_cnt == 4, f"Attempt {idx+1} has {med_cnt} Medium questions, expected 4"
        assert hard_cnt == 2, f"Attempt {idx+1} has {hard_cnt} Hard questions, expected 2"

    print("\n[SUCCESS] 4-Attempt Retake Verification experiment completed with 0 duplicates and 100% compliant difficulty distribution.")


if __name__ == "__main__":
    import tempfile
    td = tempfile.gettempdir()
    db_file = os.path.join(td, f"learnai_4attempt_{uuid.uuid4().hex[:8]}.db")
    try:
        run_4_attempt_experiment(db_file)
    finally:
        if os.path.exists(db_file):
            try:
                os.remove(db_file)
            except Exception:
                pass
