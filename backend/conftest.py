"""
conftest.py
===========
Shared pytest configuration and fixtures for the Agentic-AI Notes to Quiz Generator backend test suite.
Provides automatic test-isolation safeguards for in-memory services.
"""
import pytest
from services.rate_limiter import reset_all_rate_limiters
from services.quota_manager import quota_manager


@pytest.fixture(autouse=True)
def isolate_rate_limiters_between_tests():
    """
    Ensure in-memory rate-limiter and AI provider quota state is cleanly isolated between individual tests,
    preventing spurious HTTP 429 errors or simulated provider cooldown leakage across tests.
    """
    reset_all_rate_limiters()
    quota_manager.reset_all()
    try:
        yield
    finally:
        reset_all_rate_limiters()
        quota_manager.reset_all()
