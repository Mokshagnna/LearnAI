"""
test_phase1_reliability.py
==========================
Automated unit tests for Phase 1: Quiz reliability and AI-provider fallback.
Uses mocked providers and responses to guarantee zero cost, zero quota usage, and deterministic execution.

Run with:
    python -m unittest backend.test_phase1_reliability -v
"""
import os
import sys
import time
import json
import unittest
from unittest.mock import MagicMock, patch

BACKEND_DIR = os.path.join(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.providers.base import (
    ProviderUnavailableError,
    ProviderConfigError,
    BaseAIProvider
)
from services.llm_service import (
    generate_quiz,
    generate_analysis,
    _get_providers,
    AIServiceUnavailableError,
    AIConfigError
)


def _make_valid_quiz_json(topic: str = "Test Topic", subject: str = "Test Subject"):
    """Creates a valid 10-question quiz JSON payload with genuinely distinct questions."""
    distinct_questions = [
        ("What is the primary definition and algebraic structure of this concept?", "A fundamental mathematical entity", "A scalar", "An undefined term", "A constant"),
        ("Which of the following theorems establishes the core property of this operation?", "The Fundamental Invariance Theorem", "Euclid's Lemma", "Bayes Rule", "Ohm's Law"),
        ("Under what specific condition is the inverse mathematically guaranteed to exist?", "When the determinant is strictly non-zero", "When trace is zero", "When all entries are prime", "Never"),
        ("How does increasing the input dimensionality affect computational complexity?", "It scales quadratically or cubically", "It is constant O(1)", "It decreases linearly", "No effect"),
        ("In practical applications, what is the standard method used to solve this system?", "Gaussian elimination with partial pivoting", "Random guessing", "Linear search", "Brute force enumeration"),
        ("What geometric interpretation corresponds to the kernel or null space?", "The subspace of vectors mapped to zero", "The span of all orthogonal planes", "The volume enclosed by vertices", "The centroid"),
        ("Which activation or transformation preserves linear independence?", "Non-singular linear mappings", "Strict zero projection", "Threshold truncation", "Constant addition"),
        ("How is the loss function minimized during optimization in this context?", "Gradient descent using backpropagated error gradients", "Direct matrix inversion", "Random permutation", "Analytical root-finding"),
        ("What regularization technique is commonly applied to prevent overfitting here?", "L2 weight decay regularization", "Removing training samples", "Increasing learning rate", "Zeroing out weights permanently"),
        ("When evaluating model convergence, which metric indicates optimal performance?", "Decreasing validation loss plateau", "Oscillating error magnitude", "Diverging weights", "Zero gradient variance across batches")
    ]
    questions = []
    for i, (q_text, opt_a, opt_b, opt_c, opt_d) in enumerate(distinct_questions):
        diff = "Easy" if i < 4 else ("Medium" if i < 8 else "Hard")
        questions.append({
            "question": f"{topic}: {q_text}",
            "options": {
                "A": opt_a,
                "B": opt_b,
                "C": opt_c,
                "D": opt_d
            },
            "correct_answer": "A",
            "difficulty": diff,
            "concept": f"{topic} Concept {i + 1}",
            "explanation": f"Explanation for question {i + 1}"
        })
    return json.dumps({
        "subject": subject,
        "topic": topic,
        "questions": questions
    })


class TestPhase1Reliability(unittest.TestCase):

    def test_provider_order_configuration(self):
        """Provider order strictly follows PROVIDER_ORDER env var."""
        with patch.dict(os.environ, {"PROVIDER_ORDER": "gemini,groq,nvidia"}):
            providers = _get_providers()
            names = [p.name for p in providers]
            self.assertEqual(names[:3], ["gemini", "groq", "nvidia"])

        with patch.dict(os.environ, {"PROVIDER_ORDER": "nvidia,gemini"}):
            providers = _get_providers()
            names = [p.name for p in providers]
            self.assertEqual(names[0], "nvidia")
            self.assertEqual(names[1], "gemini")

    @patch("services.llm_service._get_providers")
    def test_cross_provider_fallback_groq_to_nvidia(self, mock_get_providers):
        """When Groq encounters 429 rate limit, system seamlessly falls back to NVIDIA."""
        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
            "Groq rate limit reached", category="RATE_LIMIT_429"
        )

        mock_nvidia = MagicMock(spec=BaseAIProvider)
        mock_nvidia.name = "nvidia"
        mock_nvidia.is_configured.return_value = True
        mock_nvidia.generate_quiz_raw.return_value = _make_valid_quiz_json("Neural Networks")

        mock_get_providers.return_value = [mock_groq, mock_nvidia]

        with patch("services.llm_service.get_or_create_student", return_value="test_student"):
            quiz = generate_quiz(
                course_code="22AM301",
                course_name="Deep Learning",
                topic_name="Neural Networks",
                syllabus_context="Neural networks, perceptrons, activations",
                student_id="test_student",
                concepts=["Neural Networks Core Principles"]
            )

        self.assertEqual(quiz["provider_used"], "nvidia")
        self.assertEqual(len(quiz["questions"]), 10)
        self.assertEqual(mock_groq.generate_quiz_raw.call_count, 1)
        self.assertEqual(mock_nvidia.generate_quiz_raw.call_count, 1)

    @patch("services.llm_service._get_providers")
    def test_cross_provider_fallback_to_gemini(self, mock_get_providers):
        """When Groq and NVIDIA both fail with 503, Gemini successfully generates the quiz."""
        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError("503 Groq Down", category="SERVICE_UNAVAILABLE")

        mock_nvidia = MagicMock(spec=BaseAIProvider)
        mock_nvidia.name = "nvidia"
        mock_nvidia.is_configured.return_value = True
        mock_nvidia.generate_quiz_raw.side_effect = ProviderUnavailableError("503 NVIDIA Down", category="SERVICE_UNAVAILABLE")

        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True
        mock_gemini.generate_quiz_raw.return_value = _make_valid_quiz_json("Linear Algebra Concepts")

        mock_get_providers.return_value = [mock_groq, mock_nvidia, mock_gemini]

        with patch("services.llm_service.get_or_create_student", return_value="test_student"):
            quiz = generate_quiz(
                course_code="22MT103",
                course_name="Linear Algebra",
                topic_name="Linear Algebra Concepts",
                syllabus_context="Matrices, rank, inverse",
                student_id="test_student",
                concepts=["Linear Algebra Concepts Core Principles"]
            )

        self.assertEqual(quiz["provider_used"], "gemini")
        self.assertEqual(len(quiz["questions"]), 10)

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    @patch("services.llm_service._get_providers")
    def test_all_providers_auth_failure_raises_aiconfigerror(self, mock_get_providers, mock_load_demo):
        """When all configured providers fail due to authentication/invalid keys, AIConfigError is raised."""
        mock_p1 = MagicMock(spec=BaseAIProvider)
        mock_p1.name = "groq"
        mock_p1.is_configured.return_value = True
        mock_p1.generate_quiz_raw.side_effect = ProviderConfigError("Invalid API key", category="AUTH_CONFIG_ERROR")

        mock_p2 = MagicMock(spec=BaseAIProvider)
        mock_p2.name = "nvidia"
        mock_p2.is_configured.return_value = True
        mock_p2.generate_quiz_raw.side_effect = ProviderConfigError("Unauthorized key", category="AUTH_CONFIG_ERROR")

        mock_get_providers.return_value = [mock_p1, mock_p2]

        with patch("services.llm_service.get_or_create_student", return_value="test_student"):
            with self.assertRaises(AIConfigError):
                generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="Backpropagation",
                    syllabus_context="Backpropagation algorithm",
                    student_id="test_student"
                )

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    @patch("services.llm_service._get_providers")
    def test_all_providers_transient_failure_raises_aiserviceunavailable(self, mock_get_providers, mock_load_demo):
        """When all configured providers fail due to rate limits or 503s, AIServiceUnavailableError is raised."""
        mock_p1 = MagicMock(spec=BaseAIProvider)
        mock_p1.name = "groq"
        mock_p1.is_configured.return_value = True
        mock_p1.generate_quiz_raw.side_effect = ProviderUnavailableError("429 Rate Limit", category="RATE_LIMIT_429")

        mock_p2 = MagicMock(spec=BaseAIProvider)
        mock_p2.name = "nvidia"
        mock_p2.is_configured.return_value = True
        mock_p2.generate_quiz_raw.side_effect = ProviderUnavailableError("503 Service Unavailable", category="SERVICE_UNAVAILABLE")

        mock_get_providers.return_value = [mock_p1, mock_p2]

        with patch("services.llm_service.get_or_create_student", return_value="test_student"):
            with self.assertRaises(AIServiceUnavailableError) as ctx:
                generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="Convolutional Networks",
                    syllabus_context="CNN convolution layers",
                    student_id="test_student"
                )
            self.assertTrue(ctx.exception.retryable)

    @patch("services.llm_service._get_providers")
    def test_deadline_enforcement_skips_provider(self, mock_get_providers):
        """When remaining deadline is below threshold, subsequent provider attempts are aborted."""
        mock_p1 = MagicMock(spec=BaseAIProvider)
        mock_p1.name = "groq"
        mock_p1.is_configured.return_value = True
        mock_p1.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq timeout", category="SERVICE_UNAVAILABLE")

        mock_p2 = MagicMock(spec=BaseAIProvider)
        mock_p2.name = "nvidia"
        mock_p2.is_configured.return_value = True

        mock_get_providers.return_value = [mock_p1, mock_p2]

        # Artificially set OVERALL_DEADLINE_SECONDS to 0.5s so time_remaining immediately <= 2.5s
        with patch.dict(os.environ, {"OVERALL_DEADLINE_SECONDS": "0.1"}):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                with patch("services.llm_service._load_local_demo_questions", return_value=None):
                    with self.assertRaises(AIServiceUnavailableError):
                        generate_quiz(
                            course_code="22AM301",
                            course_name="Deep Learning",
                            topic_name="Perceptron",
                            syllabus_context="Perceptron rule",
                            student_id="test_student"
                        )

        # Because deadline expired, mock_p1 was skipped or interrupted and mock_p2 never called
        self.assertEqual(mock_p2.generate_quiz_raw.call_count, 0)

    @patch("services.llm_service._get_providers")
    def test_analysis_fallback_when_providers_fail(self, mock_get_providers):
        """generate_analysis returns structured fallback dictionary without crashing if providers fail."""
        mock_p = MagicMock(spec=BaseAIProvider)
        mock_p.name = "groq"
        mock_p.is_configured.return_value = True
        mock_p.generate_analysis_raw.side_effect = Exception("Analysis API connection failure")
        mock_get_providers.return_value = [mock_p]

        res = generate_analysis(
            subject="Deep Learning",
            topic="Sigmoidal Neuron",
            weak_concepts=["Activation Function", "Output Range"],
            score=4,
            total=10
        )

        self.assertIn("summary", res)
        self.assertIn("revision", res)
        self.assertIn("recommendation", res)
        self.assertIn("Activation Function", res["revision"])

    @patch("services.llm_service._get_providers")
    def test_gemini_skipped_when_3_to_4_seconds_remain_before_attempt(self, mock_get_providers):
        """
        Mocked test verifying deadline hard upper bound:
        When Groq takes 21.5s out of a 25.0s deadline and fails, leaving only 3.5s before Gemini attempt,
        Gemini is safely skipped because remaining time is below Gemini's safe execution threshold (4.0s).
        The request terminates cleanly under the 25.0s upper bound without blocking.
        """
        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True

        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True

        mock_get_providers.return_value = [mock_groq, mock_gemini]

        # Clock starts at 0.0, advances to 21.5s after Groq completes
        clock = {"time": 0.0}

        def fake_perf_counter():
            return clock["time"]

        def fake_groq_call(*args, **kwargs):
            clock["time"] = 21.5
            raise ProviderUnavailableError("Groq 503 error", category="SERVICE_UNAVAILABLE")

        mock_groq.generate_quiz_raw.side_effect = fake_groq_call

        with patch("services.llm_service.time.perf_counter", side_effect=fake_perf_counter):
            with patch.dict(os.environ, {"OVERALL_DEADLINE_SECONDS": "25.0", "MAX_PROVIDER_RETRIES": "0"}):
                with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                    with patch("services.llm_service._load_local_demo_questions", return_value=None):
                        with self.assertRaises(AIServiceUnavailableError) as ctx:
                            generate_quiz(
                                course_code="22AM301",
                                course_name="Deep Learning",
                                topic_name="Perceptron",
                                syllabus_context="Perceptron rule",
                                student_id="test_student"
                            )

        # Gemini was NOT called because only 3.5s remained (threshold is 4.0s)
        self.assertEqual(mock_gemini.generate_quiz_raw.call_count, 0)
        self.assertLessEqual(clock["time"], 25.0)

    def test_gemini_provider_client_timeout_not_forced_to_10s(self):
        """_get_client sets client timeout to requested timeout_sec (in ms) without forcing a 10s floor."""
        from services.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider()
        client = provider._get_client(timeout_sec=4.5, api_key="test_key")
        self.assertEqual(client._api_client._http_options.timeout, 4500)

    def test_gemini_provider_direct_call_under_4s_fails_fast(self):
        """Direct call to GeminiProvider with budget under 4.0s immediately raises ProviderUnavailableError."""
        from services.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider()
        start = time.perf_counter()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            with self.assertRaises(ProviderUnavailableError) as ctx:
                provider.generate_quiz_raw(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="Perceptron",
                    syllabus_context="Perceptron",
                    timeout_seconds=3.5
                )
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.5)
        self.assertEqual(ctx.exception.category, "TIMEOUT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
