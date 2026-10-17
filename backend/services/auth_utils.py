"""
auth_utils.py
=============
Server-side cryptographic token utilities for student identity binding.
Uses HMAC-SHA256 with constant-time verification and configurable expiration.
"""

import os
import hmac
import hashlib
import time
import base64
import logging
from typing import Optional, Tuple, Dict, Any
from fastapi import Request, HTTPException, Header

logger = logging.getLogger("learnai.auth")

DEFAULT_TOKEN_TTL_SECONDS = int(os.getenv("STUDENT_TOKEN_TTL_SECONDS", "86400"))  # 24 hours


def get_session_secret() -> bytes:
    """
    Retrieve server signing secret from environment.
    Falls back to a warning default if not configured.
    """
    secret = os.getenv("SESSION_SECRET_KEY") or os.getenv("LEARNAI_SECRET_KEY")
    if not secret:
        # Development fallback (logs warning once)
        secret = "learnai-default-dev-secret-key-change-in-production-32b!"
        logger.warning(
            "SESSION_SECRET_KEY is not set in environment; using development fallback. "
            "Set a strong random key in production!"
        )
    return secret.encode("utf-8")


def validate_student_id_format(student_id: Optional[str]) -> bool:
    """
    Validates that a student_id is a non-empty, safe alphanumeric/UUID string
    without control characters, newlines, or excessive length.
    """
    if not student_id or not isinstance(student_id, str):
        return False
    s = student_id.strip()
    if not s or len(s) > 100:
        return False
    # Safe character set: letters, numbers, hyphens, underscores, dots
    for ch in s:
        if not (ch.isalnum() or ch in "-_."):
            return False
    return True


def sign_student_identity(
    student_id: str,
    secret_key: Optional[bytes] = None,
    ttl_seconds: Optional[int] = None
) -> str:
    """
    Generates a cryptographically signed HMAC-SHA256 token binding student_id to an expiration timestamp.
    Format: <b64_student_id>.<timestamp>.<hmac_signature>
    """
    if not validate_student_id_format(student_id):
        raise ValueError(f"Invalid student_id format: '{student_id}'")

    secret = secret_key or get_session_secret()
    ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_TOKEN_TTL_SECONDS
    now_ts = int(time.time())
    exp_ts = now_ts + ttl

    clean_id = student_id.strip()
    b64_id = base64.urlsafe_b64encode(clean_id.encode("utf-8")).decode("utf-8").rstrip("=")
    payload = f"{b64_id}:{exp_ts}"

    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{b64_id}.{exp_ts}.{signature}"


def verify_student_identity(
    token: str,
    secret_key: Optional[bytes] = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Verifies token signature using constant-time comparison and checks expiration.
    Returns:
        (is_valid: bool, student_id: Optional[str], error_reason: Optional[str])
    """
    if not token or not isinstance(token, str):
        return False, None, "Token missing or malformed"

    parts = token.strip().split(".")
    if len(parts) != 3:
        return False, None, "Invalid token structure"

    b64_id, exp_str, signature = parts
    try:
        exp_ts = int(exp_str)
    except ValueError:
        return False, None, "Invalid token timestamp"

    # Decode student_id
    try:
        # Re-add padding
        pad = len(b64_id) % 4
        padded = b64_id + ("=" * (4 - pad) if pad else "")
        student_id = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
    except Exception:
        return False, None, "Invalid student_id encoding"

    if not validate_student_id_format(student_id):
        return False, None, "Invalid student_id content"

    # Constant-time signature verification
    secret = secret_key or get_session_secret()
    payload = f"{b64_id}:{exp_ts}"
    expected_sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        return False, None, "Signature mismatch"

    # Check expiration (with 60-second clock skew tolerance)
    now_ts = int(time.time())
    if now_ts > exp_ts + 60:
        return False, student_id, "Token expired"

    return True, student_id, None


def get_token_from_request(
    request: Request,
    authorization: Optional[str] = None
) -> Optional[str]:
    """
    Extracts Bearer token from Authorization header, X-Student-Token header, or query param 'token'.
    """
    auth_header = None
    if isinstance(authorization, str):
        auth_header = authorization
    elif request is not None and hasattr(request, "headers"):
        auth_header = request.headers.get("Authorization")

    if auth_header and isinstance(auth_header, str) and auth_header.strip():
        parts = auth_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        elif len(parts) == 1:
            return parts[0].strip()

    if request is not None and hasattr(request, "headers"):
        custom_header = request.headers.get("X-Student-Token")
        if custom_header and isinstance(custom_header, str) and custom_header.strip():
            return custom_header.strip()

    if request is not None and hasattr(request, "query_params"):
        query_token = request.query_params.get("token")
        if query_token and isinstance(query_token, str) and query_token.strip():
            return query_token.strip()

    return None


def get_authenticated_student_id(
    request: Request,
    authorization: Optional[str] = None
) -> str:
    """
    FastAPI dependency / utility that enforces server-verified HMAC student identity.
    Raises HTTP 401 if token is missing, invalid, expired, or tampered.
    Returns the authenticated student_id.
    """
    token = get_token_from_request(request, authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication token required. Header 'Authorization: Bearer <token>' is missing."
        )

    is_valid, student_id, err = verify_student_identity(token)
    if not is_valid:
        if err == "Token expired":
            raise HTTPException(
                status_code=401,
                detail="Authentication token expired. Please refresh your session."
            )
        raise HTTPException(
            status_code=401,
            detail=f"Invalid authentication token: {err}"
        )

    return student_id


def get_optional_authenticated_student_id(
    request: Request,
    authorization: Optional[str] = None
) -> Optional[str]:
    """
    Extracts authenticated student_id if token is present and valid.
    Raises HTTP 401 if token is present but invalid or expired.
    Returns None if no token was provided.
    """
    token = get_token_from_request(request, authorization)
    if not token:
        return None

    is_valid, student_id, err = verify_student_identity(token)
    if not is_valid:
        if err == "Token expired":
            raise HTTPException(
                status_code=401,
                detail="Authentication token expired. Please refresh your session."
            )
        raise HTTPException(
            status_code=401,
            detail=f"Invalid authentication token: {err}"
        )

    return student_id


def auth_header_for(student_id: str, ttl_seconds: Optional[int] = None) -> Dict[str, str]:
    """Helper for tests and client requests to generate Authorization header."""
    token = sign_student_identity(student_id, ttl_seconds=ttl_seconds)
    return {"Authorization": f"Bearer {token}"}
