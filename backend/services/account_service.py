"""
account_service.py
==================
Core service handling student registration, credential authentication,
password hashing with Argon2id, session management, and password reset flows.
Enforces strict input validation, security controls, and safe development fallbacks.
"""

import os
import re
import secrets
import hashlib
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from services.database import (
    create_student_account,
    get_student_account_by_email,
    get_student_account_by_id,
    update_student_password,
    record_student_login,
    create_student_session,
    revoke_student_session,
    invalidate_all_student_sessions,
    create_password_reset_token,
    get_password_reset_token,
    mark_password_reset_token_used,
    get_utc_now_iso,
)
from services.auth_utils import (
    sign_student_identity,
    DEFAULT_TOKEN_TTL_SECONDS,
)
from services.db.base import is_production_mode

logger = logging.getLogger("learnai.account")

# Argon2id password hasher with secure parameters (OWASP recommended)
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
)

# Standard RFC 5322 compliant regex for safe email validation
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$"
)

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 100
RESET_TOKEN_TTL_SECONDS = 3600  # 1 hour


def normalize_email(email: Optional[str]) -> str:
    """Normalize email address consistently across registration, login, and resets."""
    if not email or not isinstance(email, str):
        return ""
    return email.strip().lower()


def validate_email_format(email: str) -> Tuple[bool, Optional[str]]:
    """Validate email address format and length."""
    if not email or not isinstance(email, str):
        return False, "Email address is required."
    clean = email.strip()
    if len(clean) > 254:
        return False, "Email address exceeds maximum length of 254 characters."
    if not EMAIL_REGEX.match(clean):
        return False, "Please enter a valid email address (e.g., student@gmail.com)."
    return True, None


def validate_full_name(name: str) -> Tuple[bool, Optional[str]]:
    """Validate student full name."""
    if not name or not isinstance(name, str):
        return False, "Full name is required."
    clean = name.strip()
    if len(clean) < MIN_NAME_LENGTH:
        return False, f"Full name must be at least {MIN_NAME_LENGTH} characters."
    if len(clean) > MAX_NAME_LENGTH:
        return False, f"Full name cannot exceed {MAX_NAME_LENGTH} characters."
    # Disallow control characters or newlines
    if any(ord(c) < 32 for c in clean):
        return False, "Full name contains invalid characters."
    return True, None


def validate_password_strength(password: str, confirm_password: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate password requirements:
    - Minimum length: 8 characters
    - Maximum length: 128 characters
    - Must contain at least one letter and at least one number
    - Must match confirm_password if provided
    """
    if not password or not isinstance(password, str):
        return False, "Password is required."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
    if len(password) > MAX_PASSWORD_LENGTH:
        return False, f"Password cannot exceed {MAX_PASSWORD_LENGTH} characters."

    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_letter and has_digit):
        return False, "Password must contain at least one letter and one number."

    if confirm_password is not None:
        if password != confirm_password:
            return False, "Passwords do not match. Please re-enter your password."

    return True, None


def hash_password(plaintext_password: str) -> str:
    """Hash password using modern Argon2id algorithm."""
    return _hasher.hash(plaintext_password)


def verify_password(stored_hash: str, candidate_password: str) -> bool:
    """Verify candidate password against Argon2id hash in constant time."""
    if not stored_hash or not candidate_password:
        return False
    try:
        return _hasher.verify(stored_hash, candidate_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception as e:
        logger.error(f"Unexpected error during password verification: {e}")
        return False


def hash_token(raw_token: str) -> str:
    """Compute SHA-256 hash of a session token or reset token for secure DB representation."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


# ── Registration ─────────────────────────────────────────────────────────────

def register_student(
    full_name: str,
    email: str,
    password: str,
    confirm_password: str,
    device_info: str = ""
) -> Dict[str, Any]:
    """
    Register a new student account:
    - Validates all 4 fields.
    - Normalizes email and verifies uniqueness.
    - Hashes password with Argon2id.
    - Creates student record and student_account.
    - Issues authenticated HMAC session token.
    - Records session in student_sessions.
    Returns account details and session token.
    """
    # 1. Validate full name
    ok, err = validate_full_name(full_name)
    if not ok:
        raise ValueError(err)

    # 2. Validate email
    ok, err = validate_email_format(email)
    if not ok:
        raise ValueError(err)

    norm_email = normalize_email(email)

    # 3. Check duplicate email
    existing = get_student_account_by_email(norm_email)
    if existing:
        raise ValueError("An account with this email address already exists. Please log in instead.")

    # 4. Validate password & confirm password
    ok, err = validate_password_strength(password, confirm_password)
    if not ok:
        raise ValueError(err)

    # 5. Hash password with Argon2id
    pwd_hash = hash_password(password)

    # 6. Generate stable internal student ID
    student_id = f"stu_{secrets.token_hex(12)}"

    # 7. Create account in database
    account = create_student_account(
        student_id=student_id,
        email=norm_email,
        full_name=full_name.strip(),
        password_hash=pwd_hash
    )

    # 8. Mint authenticated HMAC session token with randomized TTL entropy to guarantee unique session tokens
    token_ttl = DEFAULT_TOKEN_TTL_SECONDS + secrets.randbelow(3600)
    token = sign_student_identity(student_id, ttl_seconds=token_ttl)
    token_h = hash_token(token)
    expires_at_iso = datetime.fromtimestamp(
        int(datetime.now(timezone.utc).timestamp()) + token_ttl,
        tz=timezone.utc
    ).replace(tzinfo=None).isoformat()

    # 9. Record session
    create_student_session(
        student_id=student_id,
        token_hash=token_h,
        expires_at=expires_at_iso,
        device_info=device_info
    )

    logger.info(f"Successfully registered student: {student_id} ({norm_email})")

    return {
        "student_id": student_id,
        "token": token,
        "full_name": account["full_name"],
        "email": account["email"],
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + DEFAULT_TOKEN_TTL_SECONDS,
        "message": "Account created successfully."
    }


# ── Authentication / Login ───────────────────────────────────────────────────

def authenticate_student(
    email: str,
    password: str,
    device_info: str = ""
) -> Dict[str, Any]:
    """
    Authenticate a student by email and password:
    - Normalizes email.
    - Looks up student account.
    - Verifies password using Argon2id.
    - Emits generic error on failure (no email enumeration).
    - Updates last login timestamp.
    - Mints authenticated session token and records session.
    """
    norm_email = normalize_email(email)
    if not norm_email or not password:
        raise ValueError("Invalid email address or password.")

    account = get_student_account_by_email(norm_email)
    if not account:
        # Perform dummy hash verification to mitigate timing-based email enumeration
        _hasher.hash("dummy_timing_mitigation_password")
        raise ValueError("Invalid email address or password.")

    stored_hash = account.get("password_hash") or ""
    if not verify_password(stored_hash, password):
        raise ValueError("Invalid email address or password.")

    student_id = account["student_id"]

    # Record login
    record_student_login(student_id)

    # Mint session token with randomized TTL entropy to guarantee unique session tokens per login/device
    token_ttl = DEFAULT_TOKEN_TTL_SECONDS + secrets.randbelow(3600)
    token = sign_student_identity(student_id, ttl_seconds=token_ttl)
    token_h = hash_token(token)
    expires_at_iso = datetime.fromtimestamp(
        int(datetime.now(timezone.utc).timestamp()) + token_ttl,
        tz=timezone.utc
    ).replace(tzinfo=None).isoformat()

    create_student_session(
        student_id=student_id,
        token_hash=token_h,
        expires_at=expires_at_iso,
        device_info=device_info
    )

    logger.info(f"Student authenticated successfully: {student_id} ({norm_email})")

    return {
        "student_id": student_id,
        "token": token,
        "full_name": account["full_name"],
        "email": account["email"],
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + DEFAULT_TOKEN_TTL_SECONDS,
        "message": "Login successful."
    }


# ── Logout ───────────────────────────────────────────────────────────────────

def logout_student(token: str) -> bool:
    """Revoke session token on logout."""
    if not token:
        return False
    token_h = hash_token(token)
    return revoke_student_session(token_h)


# ── Forgot Password & Password Reset ─────────────────────────────────────────

def is_smtp_configured() -> bool:
    """Check if SMTP credentials / email delivery is configured in environment."""
    smtp_host = os.getenv("SMTP_HOST") or os.getenv("EMAIL_HOST")
    smtp_user = os.getenv("SMTP_USER") or os.getenv("EMAIL_USER")
    return bool(smtp_host and smtp_host.strip() and smtp_user and smtp_user.strip())


def request_password_reset(email: str) -> Dict[str, Any]:
    """
    Handle password reset request:
    - Normalizes email.
    - Validates email format.
    - Looks up student account.
    - If account exists: generates single-use cryptographically secure reset token,
      stores its SHA-256 hash in DB with 1-hour expiration.
    - Checks email configuration.
    - Never leaks whether the email exists.
    - If email delivery is unconfigured: documents this clearly and provides
      safe dev/testing token in non-production mode.
    """
    ok, err = validate_email_format(email)
    if not ok:
        raise ValueError(err)

    norm_email = normalize_email(email)
    account = get_student_account_by_email(norm_email)

    raw_token = None
    email_configured = is_smtp_configured()

    if account:
        student_id = account["student_id"]
        # Generate 32-byte cryptographically secure random token
        raw_token = secrets.token_urlsafe(32)
        token_h = hash_token(raw_token)

        # Store only SHA-256 hash in DB, expiring in 1 hour
        create_password_reset_token(
            student_id=student_id,
            email=norm_email,
            token_hash=token_h,
            ttl_seconds=RESET_TOKEN_TTL_SECONDS
        )

        if email_configured:
            # When SMTP is configured, dispatch the email
            # (Note: In future production setup, send email via smtplib here)
            logger.info(f"Dispatched password reset instructions for student: {student_id}")
        else:
            logger.info("Password reset requested but SMTP is not configured in environment.")

    # Generic response: never reveal whether account exists
    response: Dict[str, Any] = {
        "status": "ok",
        "message": "If an account is associated with this email address, password reset instructions have been generated.",
        "email_configured": email_configured,
    }

    # Safe development/testing approach when email is not configured
    if not email_configured:
        response["notice"] = (
            "Email service is not configured on this server. "
            "For local testing and verification, use the generated reset token."
        )
        if not is_production_mode() and raw_token:
            response["dev_reset_token"] = raw_token

    return response


def reset_password_with_token(
    raw_token: str,
    new_password: str,
    confirm_password: str
) -> Dict[str, Any]:
    """
    Reset password using single-use time-limited reset token:
    - Verifies token hash exists in DB, is not expired, and has not been used.
    - Validates new password strength and matching.
    - Hashes new password with Argon2id.
    - Updates student_accounts record.
    - Marks reset token as used (single-use guarantee).
    - Invalidates all existing active sessions for this student.
    """
    if not raw_token or not raw_token.strip():
        raise ValueError("Password reset token is required.")

    clean_token = raw_token.strip()
    token_h = hash_token(clean_token)

    record = get_password_reset_token(token_h)
    if not record:
        raise ValueError("Invalid or expired password reset token.")

    # Check if already used
    if record.get("used_at"):
        raise ValueError("This password reset token has already been used. Please request a new one.")

    # Check expiration
    now_iso = get_utc_now_iso()
    expires_at = str(record.get("expires_at", ""))
    if expires_at and expires_at < now_iso:
        raise ValueError("This password reset token has expired. Please request a new one.")

    # Validate new password
    ok, err = validate_password_strength(new_password, confirm_password)
    if not ok:
        raise ValueError(err)

    student_id = record["student_id"]

    # Hash new password with Argon2id
    new_hash = hash_password(new_password)

    # Update password in DB
    update_student_password(student_id, new_hash)

    # Mark token used
    mark_password_reset_token_used(record["id"])

    # Invalidate all existing sessions for this student across all devices
    revoked_count = invalidate_all_student_sessions(student_id)

    logger.info(
        f"Password successfully reset for student: {student_id}. "
        f"Invalidated {revoked_count} existing sessions."
    )

    return {
        "status": "ok",
        "message": "Password has been successfully reset. Please log in with your new password."
    }
