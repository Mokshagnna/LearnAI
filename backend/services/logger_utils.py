"""
logger_utils.py
===============
Logging utilities for LearnAI:
- SecretRedactionFilter: Automatically strips API keys and sensitive tokens from log records
- Correlation ID tracking: Context variable to associate logs with request_id
"""
import re
import logging
from contextvars import ContextVar

# ContextVar for request-scoped correlation ID
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

SECRET_PATTERNS = [
    re.compile(r"gsk_[a-zA-Z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"nvapi-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"AIza[a-zA-Z0-9_\-]{30,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\.\-]{20,}", re.IGNORECASE),
    re.compile(r"api[-_]?key['\":\s=]+[a-zA-Z0-9_\-]{16,}", re.IGNORECASE)
]


class SecretRedactionFilter(logging.Filter):
    """
    Log filter that redacts API keys and secrets before messages are written.
    Also injects the active correlation ID into log records when available.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            clean_msg = record.msg
            for pattern in SECRET_PATTERNS:
                clean_msg = pattern.sub("[REDACTED_CREDENTIAL]", clean_msg)
            record.msg = clean_msg

        # Inject correlation ID if available
        req_id = request_id_var.get("")
        record.request_id = req_id if req_id else "-"
        return True


def sanitize_error_text(text: object) -> str:
    """
    Safely redacts known API keys, tokens, and authorization credentials
    from error strings, exception objects, and representations.
    """
    if text is None:
        return ""
    clean = str(text)
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("[REDACTED_CREDENTIAL]", clean)
    return clean


def setup_secure_logging():
    """Attaches SecretRedactionFilter to root and project loggers."""
    redaction_filter = SecretRedactionFilter()
    for name in ["learnai", "learnai.api", "learnai.orchestrator", "learnai.providers", "uvicorn"]:
        log = logging.getLogger(name)
        # Avoid duplicate filters
        if not any(isinstance(f, SecretRedactionFilter) for f in log.filters):
            log.addFilter(redaction_filter)

