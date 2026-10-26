"""
conftest.py
===========
Shared pytest configuration and fixtures for the LearnAI backend test suite.
Provides automatic test-isolation safeguards for in-memory services.
"""
import pytest
from services.rate_limiter import reset_all_rate_limiters


@pytest.fixture(autouse=True)
def isolate_rate_limiters_between_tests():
    """
    Ensure in-memory rate-limiter state is cleanly isolated between individual tests,
    preventing spurious HTTP 429 errors when multiple tests execute within 60 seconds
    under TestClient's shared 'testclient' IP.
    """
    reset_all_rate_limiters()
    try:
        yield
    finally:
        reset_all_rate_limiters()
