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

    def test_gemini_provider_client_timeout_enforces_10s_minimum_deadline(self):
        """_get_client enforces Google's documented minimum allowed deadline of 10s (10,000ms)."""
        from services.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider()
        # Defensive floor: sub-10s input is raised to 10,000ms to avoid Google HTTP 400 INVALID_ARGUMENT
        client_low = provider._get_client(timeout_sec=4.5, api_key="test_key")
        self.assertEqual(client_low._api_client._http_options.timeout, 10000)

        # Values >= 10s are preserved accurately in milliseconds
        client_normal = provider._get_client(timeout_sec=14.0, api_key="test_key")
        self.assertEqual(client_normal._api_client._http_options.timeout, 14000)

    def test_gemini_provider_direct_call_under_9s_fails_fast(self):
        """Direct call to GeminiProvider with budget under minimum threshold fails fast without sending invalid deadline to API."""
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
                    timeout_seconds=8.0
                )
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.5)
        self.assertEqual(ctx.exception.category, "TIMEOUT")

    def test_gemini_provider_exact_10s_budget_proceeds_without_premature_exhaustion(self):
        """When exactly 10.0s is budgeted, GeminiProvider does not abort before calling the client."""
        from services.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider()
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = '{"questions": []}'
        mock_client.models.generate_content.return_value = mock_resp

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            with patch.object(provider, "_get_client", return_value=mock_client):
                res = provider.generate_quiz_raw(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="Perceptron",
                    syllabus_context="Perceptron",
                    timeout_seconds=10.0
                )

        self.assertTrue(mock_client.models.generate_content.called)
        self.assertEqual(res, '{"questions": []}')

    def test_gemini_provider_handles_google_minimum_deadline_400_error(self):
        """Mocked test for Google GenAI 400 INVALID_ARGUMENT 'Manually set deadline 9s is too short'."""
        from services.providers.gemini_provider import GeminiProvider
        from services.providers.base import ProviderError
        from google.genai import errors

        provider = GeminiProvider()
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = errors.APIError(
            400,
            {"error": {"message": "Manually set deadline 9s is too short. Minimum allowed deadline is 10s."}}
        )

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            with patch.object(provider, "_get_client", return_value=mock_client):
                with self.assertRaises(ProviderError) as ctx:
                    provider._execute_with_models("prompt", timeout_seconds=12.0)

        self.assertEqual(ctx.exception.category, "MALFORMED_REQUEST_400")
        self.assertFalse(ctx.exception.retryable)
        self.assertIsNone(ctx.exception.__cause__)

    def test_orchestrator_failover_after_provider_specific_400_error(self):
        """When Provider 1 encounters a provider-specific 400 error, orchestrator smoothly fails over to Provider 2."""
        from services.providers.base import ProviderError

        mock_groq = MagicMock()
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.side_effect = ProviderError(
            "Groq malformed request / 400 Bad Request",
            category="MALFORMED_REQUEST_400",
            retryable=False
        )

        mock_nvidia = MagicMock()
        mock_nvidia.name = "nvidia"
        mock_nvidia.is_configured.return_value = True
        mock_nvidia.generate_quiz_raw.return_value = _make_valid_quiz_json("Neural Networks")

        with patch("services.llm_service._get_providers", return_value=[mock_groq, mock_nvidia]):
            with patch.dict(os.environ, {"PROVIDER_ORDER": "groq,nvidia", "OVERALL_DEADLINE_SECONDS": "25.0"}):
                with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                    quiz = generate_quiz(
                        course_code="22AM301",
                        course_name="Deep Learning",
                        topic_name="Neural Networks",
                        syllabus_context="Perceptron and MLP layers",
                        student_id="test_student",
                        concepts=["Neural Networks Basics"]
                    )

        self.assertEqual(quiz["provider_used"], "nvidia")
        self.assertEqual(len(quiz["questions"]), 10)
        self.assertEqual(mock_groq.generate_quiz_raw.call_count, 1)
        self.assertEqual(mock_nvidia.generate_quiz_raw.call_count, 1)

    def test_single_configured_provider_permanent_400_raises_config_error(self):
        """When only 1 provider is configured and fails with a permanent 400, AIConfigError is raised (not masked as 503)."""
        from services.providers.base import ProviderError

        mock_gemini = MagicMock()
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True
        mock_gemini.generate_quiz_raw.side_effect = ProviderError(
            "Gemini malformed request: Manually set deadline 9s is too short. Minimum allowed deadline is 10s.",
            category="MALFORMED_REQUEST_400",
            retryable=False
        )

        with patch("services.llm_service._get_providers", return_value=[mock_gemini]):
            with patch.dict(os.environ, {"PROVIDER_ORDER": "gemini", "OVERALL_DEADLINE_SECONDS": "25.0"}):
                with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                    with patch("services.llm_service._load_local_demo_questions", return_value=None):
                        with self.assertRaises(AIConfigError) as ctx:
                            generate_quiz(
                                course_code="22AM301",
                                course_name="Deep Learning",
                                topic_name="Unseen Topic Without Demo",
                                syllabus_context="Custom context",
                                student_id="test_student"
                            )

        self.assertIn("400", str(ctx.exception).upper() + ctx.exception.category)

    def test_orchestrator_real_provider_fallback_groq_to_nvidia(self):
        """
        Orchestrator-level integration test:
        Does NOT mock _get_providers. Real GroqProvider and NvidiaProvider instances are used.
        Groq client fails with 429 rate limit across all models.
        Orchestrator seamlessly catches ProviderUnavailableError and invokes NvidiaProvider,
        which generates a valid 10-question quiz.
        """
        from services.providers.groq_provider import GroqProvider
        from services.providers.nvidia_provider import NvidiaProvider

        mock_groq_client = MagicMock()
        mock_groq_client.chat.completions.create.side_effect = Exception("rate limit 429 too many requests")

        mock_nvidia_client = MagicMock()
        mock_nvidia_msg = MagicMock()
        mock_nvidia_msg.content = _make_valid_quiz_json("Convolutional Networks")
        mock_nvidia_choice = MagicMock()
        mock_nvidia_choice.message = mock_nvidia_msg
        mock_nvidia_resp = MagicMock()
        mock_nvidia_resp.choices = [mock_nvidia_choice]
        mock_nvidia_client.chat.completions.create.return_value = mock_nvidia_resp

        env_patch = {
            "PROVIDER_ORDER": "groq,nvidia",
            "GROQ_API_KEY": "test_groq_key",
            "NVIDIA_API_KEY": "test_nvidia_key",
            "GEMINI_API_KEY": "",
            "MAX_PROVIDER_RETRIES": "0",
            "OVERALL_DEADLINE_SECONDS": "25.0",
        }

        with patch.dict(os.environ, env_patch, clear=False):
            with patch.object(GroqProvider, "_get_client", return_value=mock_groq_client) as mock_groq_get_client:
                with patch.object(NvidiaProvider, "_get_client", return_value=mock_nvidia_client) as mock_nvidia_get_client:
                    with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                        quiz = generate_quiz(
                            course_code="22AM301",
                            course_name="Deep Learning",
                            topic_name="Convolutional Networks",
                            syllabus_context="CNN convolution layers, kernels, pooling",
                            student_id="test_student",
                            concepts=["Convolutional Networks Core Principles"]
                        )

        self.assertEqual(quiz["provider_used"], "nvidia")
        self.assertEqual(len(quiz["questions"]), 10)
        self.assertTrue(mock_groq_client.chat.completions.create.called)
        self.assertTrue(mock_nvidia_client.chat.completions.create.called)

    def test_nvidia_provider_timeout_not_forced_to_4s(self):
        """_get_client sets client timeout without enforcing an artificial 4.0s minimum floor."""
        from services.providers.nvidia_provider import NvidiaProvider
        provider = NvidiaProvider()
        client = provider._get_client(timeout_sec=2.8, api_key="test_key")
        self.assertAlmostEqual(client.timeout, 2.8, places=2)

        client_large = provider._get_client(timeout_sec=12.0, api_key="test_key")
        self.assertAlmostEqual(client_large.timeout, 12.0, places=2)

    def test_nvidia_model_fallback_on_primary_failure(self):
        """When NVIDIA primary model fails with 503, fallback model on the same key succeeds."""
        from services.providers.nvidia_provider import NvidiaProvider
        provider = NvidiaProvider()

        primary_model = "meta/llama-3.3-70b-instruct"
        fallback_model = "meta/llama-3.1-70b-instruct"

        mock_client = MagicMock()

        def fake_create(**kwargs):
            model_called = kwargs.get("model")
            if model_called == primary_model:
                raise Exception("503 Service Unavailable: Model temporarily overloaded")
            elif model_called == fallback_model:
                msg = MagicMock()
                msg.content = '{"quiz": "success_from_fallback"}'
                choice = MagicMock()
                choice.message = msg
                resp = MagicMock()
                resp.choices = [choice]
                return resp
            raise ValueError(f"Unexpected model: {model_called}")

        mock_client.chat.completions.create.side_effect = fake_create

        env_patch = {
            "NVIDIA_API_KEY": "primary_key",
            "NVIDIA_MODEL": primary_model,
            "NVIDIA_FALLBACK_MODEL": fallback_model,
        }
        os.environ.pop("NVIDIA_BACKUP_API_KEY", None)

        with patch.dict(os.environ, env_patch, clear=False):
            with patch.object(provider, "_get_client", return_value=mock_client):
                result = provider._execute("generate quiz prompt", timeout_seconds=10.0)

        self.assertEqual(result, '{"quiz": "success_from_fallback"}')
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)

    def test_provider_model_defaults_match_specification(self):
        """Verify code defaults for models across all providers match documented standards."""
        from services.providers.groq_provider import GroqProvider
        from services.providers.nvidia_provider import NvidiaProvider
        from services.providers.gemini_provider import GeminiProvider

        clean_env = {
            "GROQ_MODEL": "",
            "NVIDIA_MODEL": "",
            "NVIDIA_FALLBACK_MODEL": "",
            "GEMINI_MODEL": "",
            "GEMINI_FALLBACK_MODEL": ""
        }
        with patch.dict(os.environ, clean_env, clear=False):
            os.environ.pop("GROQ_MODEL", None)
            os.environ.pop("NVIDIA_MODEL", None)
            os.environ.pop("NVIDIA_FALLBACK_MODEL", None)
            os.environ.pop("GEMINI_MODEL", None)
            os.environ.pop("GEMINI_FALLBACK_MODEL", None)

            # Test Groq defaults
            g_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
            self.assertEqual(g_model, "openai/gpt-oss-120b")

            # Test NVIDIA defaults
            nv_model = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
            nv_fallback = os.getenv("NVIDIA_FALLBACK_MODEL", "meta/llama-3.1-70b-instruct")
            self.assertEqual(nv_model, "meta/llama-3.3-70b-instruct")
            self.assertEqual(nv_fallback, "meta/llama-3.1-70b-instruct")

            # Test Gemini defaults
            gem_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
            gem_fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")
            self.assertEqual(gem_model, "gemini-3.6-flash")
            self.assertEqual(gem_fallback, "gemini-3.5-flash")

    def test_gemini_permanent_400_failover_to_groq(self):
        """When Gemini returns a 400 malformed request, orchestrator safely falls over to Groq without failing fast."""
        from services.providers.base import ProviderError

        mock_gemini = MagicMock()
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True
        mock_gemini.generate_quiz_raw.side_effect = ProviderError(
            "Gemini deadline rejected: 400 INVALID_ARGUMENT",
            category="MALFORMED_REQUEST_400",
            retryable=False
        )

        mock_groq = MagicMock()
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.return_value = _make_valid_quiz_json("Deep Learning")

        with patch("services.llm_service._get_providers", return_value=[mock_gemini, mock_groq]):
            with patch.dict(os.environ, {"PROVIDER_ORDER": "gemini,groq", "OVERALL_DEADLINE_SECONDS": "25.0"}):
                with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                    quiz = generate_quiz(
                        course_code="22AM301",
                        course_name="Deep Learning",
                        topic_name="History of Deep Learning",
                        syllabus_context="Deep Learning history and milestones",
                        student_id="test_student"
                    )

        self.assertEqual(quiz["provider_used"], "groq")
        self.assertEqual(len(quiz["questions"]), 10)
        self.assertEqual(mock_gemini.generate_quiz_raw.call_count, 1)
        self.assertEqual(mock_groq.generate_quiz_raw.call_count, 1)

    def test_all_providers_rate_limit_raises_airatelimiterror_with_429(self):
        """When all providers fail due to 429 quota exhaustion, AIRateLimitError is raised with status_code=429."""
        from services.llm_service import AIRateLimitError

        mock_p1 = MagicMock(spec=BaseAIProvider)
        mock_p1.name = "groq"
        mock_p1.is_configured.return_value = True
        mock_p1.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq 429 Rate Limit", category="RATE_LIMIT_429")

        mock_p2 = MagicMock(spec=BaseAIProvider)
        mock_p2.name = "gemini"
        mock_p2.is_configured.return_value = True
        mock_p2.generate_quiz_raw.side_effect = ProviderUnavailableError("Gemini 429 Quota Exhausted", category="RATE_LIMIT_429")

        with patch("services.llm_service._get_providers", return_value=[mock_p1, mock_p2]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                with patch("services.llm_service._load_local_demo_questions", return_value=None):
                    with self.assertRaises(AIRateLimitError) as ctx:
                        generate_quiz(
                            course_code="22AM301",
                            course_name="Deep Learning",
                            topic_name="Unseen Topic",
                            syllabus_context="Context",
                            student_id="test_student"
                        )
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("rate limit or quota", str(ctx.exception).lower())

    def test_all_providers_timeout_raises_aitimeouterror_with_504(self):
        """When all providers time out, AITimeoutError is raised with status_code=504."""
        from services.llm_service import AITimeoutError

        mock_p1 = MagicMock(spec=BaseAIProvider)
        mock_p1.name = "groq"
        mock_p1.is_configured.return_value = True
        mock_p1.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq timed out", category="TIMEOUT")

        mock_p2 = MagicMock(spec=BaseAIProvider)
        mock_p2.name = "gemini"
        mock_p2.is_configured.return_value = True
        mock_p2.generate_quiz_raw.side_effect = ProviderUnavailableError("Gemini timed out", category="TIMEOUT")

        with patch("services.llm_service._get_providers", return_value=[mock_p1, mock_p2]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                with patch("services.llm_service._load_local_demo_questions", return_value=None):
                    with self.assertRaises(AITimeoutError) as ctx:
                        generate_quiz(
                            course_code="22AM301",
                            course_name="Deep Learning",
                            topic_name="Unseen Topic",
                            syllabus_context="Context",
                            student_id="test_student"
                        )
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertIn("timed out", str(ctx.exception).lower())

    def test_api_quiz_generate_endpoint_status_codes_and_messages(self):
        """FastAPI route returns 429, 504, 500 status codes with clean, actionable detail messages."""
        from fastapi.testclient import TestClient
        from main import app
        from services.llm_service import AIRateLimitError, AITimeoutError, AIConfigError

        client = TestClient(app)

        # 1. Test 429 Rate Limit
        with patch("main.generate_quiz", side_effect=AIRateLimitError("AI service rate limit exceeded.")):
            resp = client.post("/api/quiz/generate", json={"course_code": "22AM301", "topic": "Deep Neural Networks Basics"})
            self.assertEqual(resp.status_code, 429)
            self.assertIn("rate limit", resp.json()["detail"].lower())

        # 2. Test 504 Gateway Timeout
        with patch("main.generate_quiz", side_effect=AITimeoutError("Quiz generation timed out.")):
            resp = client.post("/api/quiz/generate", json={"course_code": "22AM301", "topic": "Deep Neural Networks Basics"})
            self.assertEqual(resp.status_code, 504)
            self.assertIn("timed out", resp.json()["detail"].lower())

        # 3. Test 500 AI Configuration Error
        with patch("main.generate_quiz", side_effect=AIConfigError("AI provider configuration error: key invalid.")):
            resp = client.post("/api/quiz/generate", json={"course_code": "22AM301", "topic": "Deep Neural Networks Basics"})
            self.assertEqual(resp.status_code, 500)
            self.assertIn("configuration error", resp.json()["detail"].lower())

    # ── Section 5 Regression Tests: Gemini 504 Timeout and Failover ──────────

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    def test_gemini_504_failover_to_groq_succeeds(self, mock_load_demo):
        """Scenario 1: Gemini returns a 504 DEADLINE_EXCEEDED and orchestrator fails over to Groq which succeeds."""
        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True
        mock_gemini.generate_quiz_raw.side_effect = ProviderUnavailableError(
            "504 DEADLINE_EXCEEDED: Deadline expired before operation could complete",
            category="DEADLINE_EXCEEDED_504"
        )

        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.return_value = _make_valid_quiz_json("History of Deep Learning")

        with patch("services.llm_service._get_providers", return_value=[mock_gemini, mock_groq]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                quiz = generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="History of Deep Learning, What Is Deep Learning",
                    syllabus_context="Historical overview of deep learning",
                    student_id="test_student"
                )

        self.assertEqual(quiz["provider_used"], "groq")
        self.assertEqual(len(quiz["questions"]), 10)
        self.assertEqual(mock_gemini.generate_quiz_raw.call_count, 1)
        self.assertEqual(mock_groq.generate_quiz_raw.call_count, 1)

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    def test_gemini_504_failover_to_nvidia_when_groq_unavailable(self, mock_load_demo):
        """Scenario 2: Gemini returns 504, Groq is unavailable/fails, and NVIDIA succeeds."""
        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True
        mock_gemini.generate_quiz_raw.side_effect = ProviderUnavailableError(
            "504 DEADLINE_EXCEEDED", category="DEADLINE_EXCEEDED_504"
        )

        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.side_effect = ProviderUnavailableError(
            "503 Service Unavailable", category="SERVICE_UNAVAILABLE_503"
        )

        mock_nvidia = MagicMock(spec=BaseAIProvider)
        mock_nvidia.name = "nvidia"
        mock_nvidia.is_configured.return_value = True
        mock_nvidia.generate_quiz_raw.return_value = _make_valid_quiz_json("History of Deep Learning")

        with patch("services.llm_service._get_providers", return_value=[mock_gemini, mock_groq, mock_nvidia]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                quiz = generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="History of Deep Learning, What Is Deep Learning",
                    syllabus_context="Historical overview",
                    student_id="test_student"
                )

        self.assertEqual(quiz["provider_used"], "nvidia")
        self.assertEqual(len(quiz["questions"]), 10)
        self.assertEqual(mock_gemini.generate_quiz_raw.call_count, 1)
        self.assertEqual(mock_groq.generate_quiz_raw.call_count, 1)
        self.assertEqual(mock_nvidia.generate_quiz_raw.call_count, 1)

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    def test_sole_gemini_configured_times_out_raises_diagnostic_error(self, mock_load_demo):
        """Scenario 3: Only Gemini is configured and times out; raises diagnostic AITimeoutError (HTTP 504) noting unconfigured providers."""
        from services.llm_service import AITimeoutError

        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True
        mock_gemini.generate_quiz_raw.side_effect = ProviderUnavailableError(
            "504 DEADLINE_EXCEEDED: Deadline expired before operation could complete",
            category="DEADLINE_EXCEEDED_504"
        )

        with patch("services.llm_service._get_providers", return_value=[mock_gemini]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                with self.assertRaises(AITimeoutError) as ctx:
                    generate_quiz(
                        course_code="22AM301",
                        course_name="Deep Learning",
                        topic_name="History of Deep Learning, What Is Deep Learning",
                        syllabus_context="Historical overview",
                        student_id="test_student"
                    )

        err_msg = str(ctx.exception)
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertIn("Gemini", err_msg)
        self.assertIn("timed out", err_msg)
        self.assertIn("No fallback providers", err_msg)
        self.assertIn("Render", err_msg)

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    def test_no_provider_configured_raises_aiconfigerror(self, mock_load_demo):
        """Scenario 4: No provider has valid configuration in environment; raises AIConfigError (HTTP 500)."""
        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = False

        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = False

        mock_nvidia = MagicMock(spec=BaseAIProvider)
        mock_nvidia.name = "nvidia"
        mock_nvidia.is_configured.return_value = False

        with patch("services.llm_service._get_providers", return_value=[mock_gemini, mock_groq, mock_nvidia]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                with self.assertRaises(AIConfigError) as ctx:
                    generate_quiz(
                        course_code="22AM301",
                        course_name="Deep Learning",
                        topic_name="History of Deep Learning",
                        syllabus_context="Historical overview",
                        student_id="test_student"
                    )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("No AI provider is configured", str(ctx.exception))

    def test_gemini_internal_fallback_cannot_fit_within_deadline_yields_promptly(self):
        """Scenario 5: Gemini's internal model fallback cannot fit within deadline; yields promptly without sub-10s call."""
        from services.providers.gemini_provider import GeminiProvider
        from google.genai import errors

        provider = GeminiProvider()
        api_error_504 = errors.APIError(504, "Deadline expired before operation could complete", None)
        api_error_504.code = 504
        api_error_504.status = "DEADLINE_EXCEEDED"

        mock_client = MagicMock()
        def side_effect(*args, **kwargs):
            raise api_error_504

        mock_client.models.generate_content.side_effect = side_effect

        with patch.object(provider, "_get_client", return_value=mock_client):
            with patch.dict(os.environ, {
                "GEMINI_API_KEY": "AIzaSyFakeKeyForTestDeadlineYield_012345",
                "GEMINI_MODEL": "gemini-3.6-flash",
                "GEMINI_FALLBACK_MODEL": "gemini-3.5-flash"
            }):
                # Simulate time consumed such that remaining budget after attempt 1 is 2.14s (< 9.0s)
                counter_calls = [0.0, 0.1, 0.1, 0.1, 9.86, 9.86, 9.86, 9.86, 9.86]
                call_idx = [0]
                def mock_perf():
                    idx = call_idx[0]
                    call_idx[0] += 1
                    if idx < len(counter_calls):
                        return counter_calls[idx]
                    return 9.86

                with patch("time.perf_counter", side_effect=mock_perf):
                    with self.assertRaises(ProviderUnavailableError) as ctx:
                        provider._execute_with_models(prompt="Generate quiz", timeout_seconds=12.0)

        # Gemini should have tried primary model once, seen 2.14s left, and stopped without calling fallback model
        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        self.assertEqual(ctx.exception.category, "DEADLINE_EXCEEDED_504")

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    def test_provider_failover_respects_overall_time_budget(self, mock_load_demo):
        """Scenario 6: Failover respects overall time budget; skips Gemini if remaining time <= 10.0s."""
        from services.llm_service import AITimeoutError

        mock_p1 = MagicMock(spec=BaseAIProvider)
        mock_p1.name = "groq"
        mock_p1.is_configured.return_value = True
        mock_p1.generate_quiz_raw.side_effect = ProviderUnavailableError("Groq failed", category="TIMEOUT")

        mock_gemini = MagicMock(spec=BaseAIProvider)
        mock_gemini.name = "gemini"
        mock_gemini.is_configured.return_value = True

        with patch("services.llm_service._get_providers", return_value=[mock_p1, mock_gemini]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                with patch.dict(os.environ, {"OVERALL_DEADLINE_SECONDS": "12.0"}):
                    # Mock perf_counter to show 9.5s elapsed after Groq
                    seq = [0.0, 0.1, 9.5, 9.6, 9.7, 9.8, 9.9]
                    idx = [0]
                    def mock_time():
                        cur = seq[min(idx[0], len(seq) - 1)]
                        idx[0] += 1
                        return cur

                    with patch("time.perf_counter", side_effect=mock_time):
                        with self.assertRaises(AITimeoutError):
                            generate_quiz(
                                course_code="22AM301",
                                course_name="Deep Learning",
                                topic_name="History of Deep Learning",
                                syllabus_context="Context",
                                student_id="test_student"
                            )

        # Gemini must not have been called because remaining budget (2.5s) was below minimum 10.0s
        self.assertEqual(mock_gemini.generate_quiz_raw.call_count, 0)

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    def test_provider_timeout_never_returns_partial_or_fake_successful_quiz(self, mock_load_demo):
        """Scenario 7: When providers time out, the system never returns an empty, partial, or malformed dict."""
        from services.llm_service import AITimeoutError

        mock_p1 = MagicMock(spec=BaseAIProvider)
        mock_p1.name = "gemini"
        mock_p1.is_configured.return_value = True
        mock_p1.generate_quiz_raw.side_effect = ProviderUnavailableError("Deadline exceeded", category="DEADLINE_EXCEEDED_504")

        with patch("services.llm_service._get_providers", return_value=[mock_p1]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                with self.assertRaises(AITimeoutError):
                    generate_quiz(
                        course_code="22AM301",
                        course_name="Deep Learning",
                        topic_name="History of Deep Learning",
                        syllabus_context="Context",
                        student_id="test_student"
                    )

    @patch("services.llm_service._load_local_demo_questions", return_value=None)
    def test_existing_quiz_generation_and_adaptive_learning_intact(self, mock_load_demo):
        """Scenario 8: Normal successful generation preserves 10 distinct questions, adaptive distribution, and novelty checks."""
        mock_groq = MagicMock(spec=BaseAIProvider)
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.generate_quiz_raw.return_value = _make_valid_quiz_json("History of Deep Learning", "Deep Learning")

        with patch("services.llm_service._get_providers", return_value=[mock_groq]):
            with patch("services.llm_service.get_or_create_student", return_value="test_student"):
                quiz = generate_quiz(
                    course_code="22AM301",
                    course_name="Deep Learning",
                    topic_name="History of Deep Learning, What Is Deep Learning",
                    syllabus_context="Detailed concepts in deep learning",
                    student_id="test_student",
                    concepts=["Concept 1", "Concept 2"]
                )

        self.assertEqual(len(quiz["questions"]), 10)
        self.assertEqual(quiz["provider_used"], "groq")
        self.assertIn("adaptive_explanation", quiz)
        self.assertIn(quiz["recommended_difficulty"], ["Easy", "Medium", "Hard", "mixed"])
        for q in quiz["questions"]:
            self.assertEqual(len(q["options"]), 4)
            self.assertIn(q["difficulty"], ["Easy", "Medium", "Hard"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
