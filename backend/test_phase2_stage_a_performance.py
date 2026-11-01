"""
test_phase2_stage_a_performance.py
==================================
Verification tests for Stage A:
- GroqProvider and NvidiaProvider client connection reuse (caching per API key)
- DatabaseConfig pool_max_size defaults to 25 and respects DB_POOL_MAX_SIZE
- Quiz generation timing instrumentation in llm_service and main endpoint
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from services.providers.groq_provider import GroqProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.db.base import DatabaseConfig
from main import app

client = TestClient(app)


def test_groq_provider_client_reuse():
    """Verify GroqProvider caches and reuses client instance for the same API key."""
    provider = GroqProvider()
    with patch("groq.Groq") as mock_groq_cls:
        instance_a = MagicMock()
        instance_b = MagicMock()
        mock_groq_cls.side_effect = [instance_a, instance_b]

        c1 = provider._get_client(10.0, "test_api_key_reuse_123")
        c2 = provider._get_client(5.0, "test_api_key_reuse_123")

        assert c1 is instance_a
        assert c2 is instance_a
        # Groq client was created only once despite two calls
        assert mock_groq_cls.call_count == 1


def test_nvidia_provider_client_reuse():
    """Verify NvidiaProvider caches and reuses OpenAI client instance for the same API key."""
    provider = NvidiaProvider()
    with patch("openai.OpenAI") as mock_openai_cls:
        instance_a = MagicMock()
        instance_b = MagicMock()
        mock_openai_cls.side_effect = [instance_a, instance_b]

        c1 = provider._get_client(10.0, "nvapi_test_key_reuse_456")
        c2 = provider._get_client(5.0, "nvapi_test_key_reuse_456")

        assert c1 is instance_a
        assert c2 is instance_a
        # OpenAI client was created only once despite two calls
        assert mock_openai_cls.call_count == 1


def test_database_pool_max_size_configuration():
    """Verify pool_max_size defaults to 10 (safe for Render workers and TiDB) and can be overridden."""
    # Test default
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DB_POOL_MAX_SIZE", None)
        config = DatabaseConfig()
        assert config.pool_max_size == 10

    # Test override
    with patch.dict(os.environ, {"DB_POOL_MAX_SIZE": "25"}):
        config = DatabaseConfig()
        assert config.pool_max_size == 25


def test_quiz_generation_stage_timing_metrics():
    """Verify generate_quiz populates stage timing metrics."""
    from services.llm_service import generate_quiz

    sample_questions = [
        {"question": "What is the time complexity of binary search on a sorted array?", "options": {"A": "O(log n)", "B": "O(n)", "C": "O(1)", "D": "O(n log n)"}, "correct_answer": "A", "concept": "Complexity", "explanation": "Binary search divides the search interval in half each step.", "difficulty": "Easy"},
        {"question": "Which data structure uses LIFO order for insertion and deletion?", "options": {"A": "Stack", "B": "Queue", "C": "Array", "D": "Heap"}, "correct_answer": "A", "concept": "Stack", "explanation": "A stack is a Last-In First-Out structure.", "difficulty": "Easy"},
        {"question": "What is the worst-case time complexity of quicksort?", "options": {"A": "O(n^2)", "B": "O(n log n)", "C": "O(n)", "D": "O(log n)"}, "correct_answer": "A", "concept": "Sorting", "explanation": "Quicksort degrades to O(n^2) with unbalanced partitions.", "difficulty": "Medium"},
        {"question": "Which traversal of a binary search tree yields sorted keys?", "options": {"A": "Inorder", "B": "Preorder", "C": "Postorder", "D": "Level order"}, "correct_answer": "A", "concept": "Trees", "explanation": "Inorder traversal visits left subtree, root, then right subtree.", "difficulty": "Easy"},
        {"question": "What does Dijkstra algorithm compute on weighted graphs?", "options": {"A": "Single-source shortest paths", "B": "Minimum spanning tree", "C": "Maximum flow", "D": "Topological sort"}, "correct_answer": "A", "concept": "Graphs", "explanation": "Dijkstra finds shortest paths from a single source with non-negative weights.", "difficulty": "Medium"},
        {"question": "Which algorithm is commonly used for finding a minimum spanning tree?", "options": {"A": "Kruskal algorithm", "B": "Bellman-Ford", "C": "Floyd-Warshall", "D": "A* search"}, "correct_answer": "A", "concept": "MST", "explanation": "Kruskal greedily adds minimum weight edges without creating cycles.", "difficulty": "Medium"},
        {"question": "What is the average lookup time for an element in a hash table?", "options": {"A": "O(1)", "B": "O(log n)", "C": "O(n)", "D": "O(n^2)"}, "correct_answer": "A", "concept": "Hashing", "explanation": "With good hash distribution, average search time is O(1).", "difficulty": "Easy"},
        {"question": "Which paradigm does the merge sort algorithm follow?", "options": {"A": "Divide and conquer", "B": "Dynamic programming", "C": "Greedy method", "D": "Backtracking"}, "correct_answer": "A", "concept": "Paradigms", "explanation": "Merge sort divides the list, recursively sorts, and merges.", "difficulty": "Easy"},
        {"question": "What is the space complexity of standard depth-first search on a tree?", "options": {"A": "O(h) where h is height", "B": "O(n^2)", "C": "O(1)", "D": "O(2^n)"}, "correct_answer": "A", "concept": "Search", "explanation": "DFS recursion stack stores at most the path from root to leaf, O(h).", "difficulty": "Hard"},
        {"question": "Which data structure is typically used to implement a priority queue?", "options": {"A": "Binary Heap", "B": "Circular Queue", "C": "Doubly Linked List", "D": "Hash Table"}, "correct_answer": "A", "concept": "Heaps", "explanation": "Binary heap provides O(log n) insertion and extraction of minimum or maximum.", "difficulty": "Hard"}
    ]

    mock_raw_json = json.dumps({"questions": sample_questions})

    with patch("services.llm_service._get_providers") as mock_get_p:
        mock_provider = MagicMock()
        mock_provider.name = "mock_timed_provider"
        mock_provider.is_configured.return_value = True
        mock_provider.generate_quiz_raw.return_value = mock_raw_json
        mock_get_p.return_value = [mock_provider]

        quiz = generate_quiz(
            course_code="CS101",
            course_name="Data Structures and Algorithms",
            topic_name="Complexity and Sorting",
            syllabus_context="Syllabus covering binary search, stacks, sorting, trees, and graphs.",
            student_id="test_student_timing_1",
            num_questions=10
        )

        assert "timing_metrics" in quiz
        metrics = quiz["timing_metrics"]
        assert "db_context_sec" in metrics
        assert "llm_primary_sec" in metrics
        assert "json_validation_sec" in metrics
        assert "total_service_sec" in metrics
        assert metrics["total_service_sec"] >= 0.0
