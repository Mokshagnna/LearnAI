#!/usr/bin/env python3
"""
cleanup_orphan_student.py
=========================
Safety-first inspection and candidate orphan-student cleanup utility for LearnAI.
Designed strictly for disposable TiDB Cloud Serverless / MySQL test databases.

SAFETY ARCHITECTURE & DELETION REFUSAL:
---------------------------------------
1. Deletion Path Unavailable:
   The LearnAI schema uses foreign keys with ON DELETE CASCADE across five dependent tables:
   - quiz_sessions
   - quiz_attempts
   - question_metadata (via attempts and student reference)
   - student_answers
   - topic_mastery

   Under TiDB Serverless default Snapshot Isolation (REPEATABLE READ), point-locks on
   the parent row (SELECT ... FOR UPDATE) acquire locks ONLY on students, NOT child tables.
   Concurrent child row insertions cannot be prevented at the database query level.
   If a concurrent insert occurs while cleanup is active, a subsequent DELETE on students
   triggers server-side ON DELETE CASCADE, silently and irreversibly destroying child records.

   Furthermore, candidate attribution rests on circumstantial correlation (creation timestamp,
   single student, zero child records) and is not proven by test runner logs.

   Because the application architecture does not currently provide an independently verified
   operational isolation mechanism (freezing all application servers, workers, and test runners),
   the live DELETION PATH IS EXPLICITLY UNAVAILABLE.

2. Read-Only Inspection Mode:
   The utility operates in read-only inspection mode. It validates target parameters,
   ensures strict TLS configuration, validates candidate UUID formatting, and audits
   preconditions without mutating the database.

3. Complete Secret & Identifier Privacy:
   Database URLs, usernames, passwords, hostnames, and candidate UUIDs are NEVER logged,
   printed to stdout/stderr, or included in error messages. Driver exceptions are mapped
   to fixed static categories without formatting raw exception text or tracebacks.
"""

import os
import sys
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.db.mysql_adapter import MySQLAdapter
from scripts.migrate_sqlite_to_mysql import (
    is_production_target,
    is_tidb_cloud_gateway_hostname,
    is_verified_mysql_test_target,
)

HEX_CHARS = frozenset("0123456789abcdefABCDEF")
ALLOWED_QUERY_KEYS = frozenset(["ssl_mode", "ssl_ca"])
DEPENDENT_TABLES = (
    "quiz_sessions",
    "quiz_attempts",
    "question_metadata",
    "student_answers",
    "topic_mastery",
)
REQUIRED_CONFIRMATION_PHRASE = "DELETE VERIFIED CANDIDATE"


class ErrorCategory(str, Enum):
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    ATTRIBUTION_UNVERIFIED = "ATTRIBUTION_UNVERIFIED"
    OPERATIONAL_ISOLATION_UNAVAILABLE = "OPERATIONAL_ISOLATION_UNAVAILABLE"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    QUERY_FAILED = "QUERY_FAILED"
    COMMIT_UNCERTAIN = "COMMIT_UNCERTAIN"
    POST_COMMIT_UNVERIFIED = "POST_COMMIT_UNVERIFIED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# ── 1. Strict Validation Primitives ──────────────────────────────────────────

def validate_percent_encoding(raw: str) -> None:
    """
    Validate that all percent escapes in a string conform strictly to RFC 3986 (%HH).
    Rejects bare %, incomplete sequences (%2), and non-hex characters (%GG).
    Raises ValueError with a fixed generic error message without echoing the input.
    """
    if not raw or "%" not in raw:
        return
    idx = 0
    length = len(raw)
    while idx < length:
        if raw[idx] == "%":
            if idx + 2 >= length:
                raise ValueError("Invalid percent-encoding in database URL.")
            c1, c2 = raw[idx + 1], raw[idx + 2]
            if c1 not in HEX_CHARS or c2 not in HEX_CHARS:
                raise ValueError("Invalid percent-encoding in database URL.")
            idx += 3
        else:
            idx += 1


def parse_canonical_query_params(query: str) -> Dict[str, str]:
    """
    Parse query parameters strictly enforcing canonical RFC 3986 format.
    - Prohibits empty segments (&&, leading/trailing &).
    - Prohibits percent-encoded parameter keys (%73sl_mode).
    - Prohibits duplicate parameter keys.
    - Permits strictly allowed keys (ssl_mode, ssl_ca).
    - Validates percent-encoding on both keys and values.
    """
    if not query:
        return {}
    if query.startswith("&") or query.endswith("&") or "&&" in query:
        raise ValueError("Malformed query string: invalid separators.")
    pairs = query.split("&")
    seen_keys = set()
    result: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError("Malformed query parameter: missing key-value assignment.")
        raw_key, raw_val = pair.split("=", 1)
        validate_percent_encoding(raw_key)
        validate_percent_encoding(raw_val)
        if "%" in raw_key:
            raise ValueError("Percent-encoded query parameter keys are not permitted.")
        if raw_key in seen_keys:
            raise ValueError("Duplicate query parameter.")
        if raw_key not in ALLOWED_QUERY_KEYS:
            raise ValueError("Disallowed query parameter.")
        seen_keys.add(raw_key)
        result[raw_key] = raw_val
    return result


def validate_candidate_uuid(raw_id: Optional[str]) -> Tuple[bool, str, Optional[uuid.UUID]]:
    """
    Strictly validate that candidate ID is a valid RFC 4122 Version 4 UUID.
    Does NOT use uuid.UUID(raw_id, version=4) because that silently mutates bits.
    Instead, parses raw UUID and explicitly asserts parsed.version == 4 and RFC_4122 variant.
    Never outputs the candidate ID in error messages or logs.
    """
    if not raw_id or not isinstance(raw_id, str):
        return False, "Candidate identifier is required.", None
    trimmed = raw_id.strip()
    if len(trimmed) != 36:
        return False, "Invalid candidate identifier format: length mismatch.", None
    parts = trimmed.split("-")
    if len(parts) != 5 or [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False, "Invalid candidate identifier format: hyphen structure mismatch.", None
    try:
        parsed = uuid.UUID(trimmed)
    except Exception:
        return False, "Invalid candidate identifier format: malformed hexadecimal string.", None

    if parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        return False, "Invalid candidate identifier format: RFC 4122 version 4 required.", None

    return True, "Candidate identifier format valid.", parsed


def parse_strict_count(row: Any) -> int:
    """
    Parse a single-column COUNT(*) result row strictly.
    - Requires non-empty tuple or list of length exactly 1.
    - Requires integer value.
    - Strictly rejects booleans (True/False are subclasses of int in Python).
    - Strictly rejects floats, strings, negative values, and None.
    """
    if row is None or not isinstance(row, (tuple, list)):
        raise ValueError("Unexpected query result shape: expected single row tuple.")
    if len(row) != 1:
        raise ValueError("Unexpected query result shape: expected exactly one column.")
    val = row[0]
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError("Unexpected count type: non-boolean integer required.")
    if val < 0:
        raise ValueError("Negative count value encountered.")
    return val


def categorize_driver_exception(exc: Exception) -> Tuple[ErrorCategory, str]:
    """
    Map driver and runtime exceptions to fixed categories and static messages.
    Does NOT evaluate str(exc), repr(exc), or chained exception objects to prevent secret leakage.
    """
    exc_type = type(exc).__name__
    if "OperationalError" in exc_type or "InterfaceError" in exc_type:
        return ErrorCategory.CONNECTION_FAILED, "Database connection or network communication failed."
    if "ProgrammingError" in exc_type or "DataError" in exc_type:
        return ErrorCategory.QUERY_FAILED, "Database query execution failed."
    if "IntegrityError" in exc_type:
        return ErrorCategory.PRECONDITION_FAILED, "Database constraint violation occurred."
    return ErrorCategory.INTERNAL_ERROR, "An internal driver or database error occurred."


# ── 2. Target Validation & Preflight ─────────────────────────────────────────

def validate_cleanup_target_url(url: Optional[str]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Strict preflight validation of cleanup target database URL.
    - Must be a non-empty string using mysql:// or mysql+pymysql://.
    - Passes is_verified_mysql_test_target() and rejects is_production_target().
    - Hostname must strictly match TiDB Cloud AWS Serverless gateway hostname pattern.
    - Port must be locked to 4000.
    - Database name must be 'learnai_test'.
    - Validates percent-encoding across the entire URL.
    - Validates canonical query string (only ssl_mode and ssl_ca allowed).
    - Requires ssl_mode=VERIFY_IDENTITY.
    - Requires existing readable CA certificate file.
    - Verifies adapter parameters agree exactly with validated components.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return False, "Target database URL is not configured or empty.", {}

    trimmed = url.strip()

    try:
        validate_percent_encoding(trimmed)
    except ValueError:
        return False, "Invalid percent-encoding in database URL.", {}

    try:
        parsed = urlparse(trimmed)
    except Exception:
        return False, "Failed to parse target database URL.", {}

    if parsed.scheme not in ("mysql", "mysql+pymysql"):
        return False, "Invalid database URL scheme: must be 'mysql' or 'mysql+pymysql'.", {}

    if is_production_target(trimmed):
        return False, "Target URL rejected: flagged by production target indicators.", {}

    if not is_verified_mysql_test_target(trimmed):
        return False, (
            "Target URL rejected: not a verified disposable test target. "
            "Requires CONFIRM_DISPOSABLE_TEST_TARGET=true and approved test database name."
        ), {}

    hostname = (parsed.hostname or "").lower()
    if not is_tidb_cloud_gateway_hostname(hostname):
        return False, "Target host rejected: must be an approved TiDB Cloud Serverless gateway host.", {}

    if parsed.port != 4000:
        return False, "Target port rejected: TiDB Cloud Serverless target must use port 4000.", {}

    db_name = (parsed.path.lstrip("/") or "").lower()
    if db_name != "learnai_test":
        return False, "Target database rejected: only 'learnai_test' is permitted for candidate cleanup.", {}

    try:
        query_params = parse_canonical_query_params(parsed.query)
    except ValueError as e:
        return False, f"Target query string rejected: {e}", {}

    ssl_mode_val = query_params.get("ssl_mode", "").upper()
    if ssl_mode_val != "VERIFY_IDENTITY":
        return False, "Target rejected: ssl_mode must be strictly VERIFY_IDENTITY.", {}

    raw_ca_path = query_params.get("ssl_ca", "")
    if not raw_ca_path:
        return False, "Target rejected: ssl_ca parameter is required.", {}

    decoded_ca_path = unquote(raw_ca_path)
    ca_path_obj = Path(decoded_ca_path)
    if not ca_path_obj.is_file():
        return False, "Target rejected: specified CA certificate file does not exist.", {}

    # Verify agreement with MySQLAdapter parsed connection parameters
    try:
        adapter_params = MySQLAdapter.parse_connection_params(trimmed)
    except Exception:
        return False, "Target rejected: adapter failed to parse connection parameters.", {}

    if adapter_params.get("host") != hostname:
        return False, "Target rejected: adapter host mismatch.", {}
    if adapter_params.get("port") != 4000:
        return False, "Target rejected: adapter port mismatch.", {}
    if adapter_params.get("database") != "learnai_test":
        return False, "Target rejected: adapter database mismatch.", {}
    if adapter_params.get("autocommit") is not False:
        return False, "Target rejected: adapter must enforce autocommit=False.", {}

    ssl_ctx = adapter_params.get("ssl")
    if not hasattr(ssl_ctx, "check_hostname") or not ssl_ctx.check_hostname:
        return False, "Target rejected: adapter SSLContext must enable check_hostname.", {}
    if getattr(ssl_ctx, "verify_mode", None) != 2:  # ssl.CERT_REQUIRED == 2
        return False, "Target rejected: adapter SSLContext must enforce CERT_REQUIRED.", {}

    # Verify adapter decoded password matches parsed URL password without disclosing either
    parsed_password = unquote(parsed.password or "")
    if adapter_params.get("password") != parsed_password:
        return False, "Target rejected: adapter password parameter mismatch.", {}

    return True, "Preflight target validation successful.", adapter_params


# ── 3. Operational Isolation & Candidate Attribution Guards ─────────────────

def check_candidate_attribution() -> Tuple[bool, str]:
    """
    Check whether candidate record attribution has been independently proven.
    A candidate's existence, creation timestamp, student count of 1, or 0 dependencies
    are circumstantial and do NOT constitute proof of failed-test origin.
    Returns False because candidate attribution remains unproven.
    """
    return (
        False,
        "Candidate attribution is UNVERIFIED. Circumstantial attributes (timestamp, single student, "
        "zero child rows) do not prove origin from a failed test runner. Independent proof is absent."
    )


def check_operational_isolation() -> Tuple[bool, str]:
    """
    Check whether operational isolation is actively established and verified.
    Because the application architecture does not provide an automated lock or freeze
    across all application servers, background workers, and external test runners,
    operational isolation cannot be verified at runtime.
    Returns False to prevent race conditions with ON DELETE CASCADE.
    """
    return (
        False,
        "Operational isolation is UNAVAILABLE. The application architecture cannot guarantee that "
        "concurrent requests or workers are stopped. Because the schema defines ON DELETE CASCADE, "
        "concurrent child insertions cannot be prevented at the query level."
    )


# ── 4. Inspection & Deletion Handlers ────────────────────────────────────────

def inspect_candidate(
    target_url: Optional[str],
    candidate_id: Optional[str],
) -> Dict[str, Any]:
    """
    Read-only offline inspection handler.
    Validates target URL and candidate UUID syntax, audits attribution and isolation guards.
    Does NOT connect to the database or verify candidate existence in live tables.
    Never executes mutating SQL.
    """
    summary: Dict[str, Any] = {
        "status": "OFFLINE_PREFLIGHT_PASS",
        "mode": "OFFLINE PREFLIGHT ONLY",
        "target_valid": False,
        "candidate_id_valid": False,
        "live_db_verification": "SKIPPED",
        "candidate_record_existence": "NOT VERIFIED",
        "attribution_verified": False,
        "operational_isolation_verified": False,
        "deletion_allowed": False,
        "error_category": None,
        "message": "",
    }

    # 1. Target URL validation
    valid_target, target_msg, conn_params = validate_cleanup_target_url(target_url)
    if not valid_target:
        summary["status"] = "OFFLINE_PREFLIGHT_FAIL"
        summary["error_category"] = ErrorCategory.PREFLIGHT_FAILED.value
        summary["message"] = target_msg
        return summary
    summary["target_valid"] = True

    # 2. Candidate UUID validation
    valid_uuid, uuid_msg, parsed_uuid = validate_candidate_uuid(candidate_id)
    if not valid_uuid:
        summary["status"] = "OFFLINE_PREFLIGHT_FAIL"
        summary["error_category"] = ErrorCategory.PREFLIGHT_FAILED.value
        summary["message"] = uuid_msg
        return summary
    summary["candidate_id_valid"] = True

    # 3. Attribution & Operational Isolation checks
    attr_ok, attr_msg = check_candidate_attribution()
    summary["attribution_verified"] = attr_ok

    iso_ok, iso_msg = check_operational_isolation()
    summary["operational_isolation_verified"] = iso_ok

    summary["deletion_allowed"] = False
    summary["message"] = (
        "Offline preflight complete. Target URL configuration and candidate UUID syntax are valid. "
        "Live database verification was skipped (offline mode). "
        "Candidate record existence and dependencies in the target database remain unverified. "
        "Deletion is disallowed because live deletion is disabled in this utility."
    )
    return summary


def execute_cleanup(
    target_url: Optional[str] = None,
    candidate_id: Optional[str] = None,
) -> int:
    """
    Execution entrypoint.
    Unconditionally REFUSES deletion.
    Documents the architectural blocker: ON DELETE CASCADE under Snapshot Isolation creates
    an unmitigated race condition, and candidate attribution remains unproven.
    """
    print("=" * 60)
    print("LearnAI -- Candidate Orphan Student Cleanup Utility")
    print("=" * 60)
    print("[REFUSED] Live deletion is explicitly UNAVAILABLE.")
    print("Architectural Blockers:")
    print("  1. Operational Isolation: The current architecture has no mechanism to prove")
    print("     that all application instances, workers, and test runners are inactive.")
    print("     Foreign keys with ON DELETE CASCADE under TiDB Snapshot Isolation create")
    print("     an unmitigated risk of silent child record destruction upon parent deletion.")
    print("  2. Candidate Attribution: Circumstantial evidence (timestamp, count=1, zero children)")
    print("     does not prove origin. Deletion requires independent proof and operator approval.")
    print("=" * 60)
    return 1


# ── 5. CLI Entrypoint ────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    """
    Main CLI entrypoint.
    Performs offline preflight inspection or unconditionally refuses execution.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Safety-first candidate orphan student offline preflight inspection utility for LearnAI."
    )
    parser.add_argument(
        "--target-url",
        default=os.getenv("TEST_MYSQL_DATABASE_URL"),
        help="Target database URL (defaults to TEST_MYSQL_DATABASE_URL).",
    )
    parser.add_argument(
        "--candidate-id",
        required=True,
        help="Explicit candidate student UUID (RFC 4122 v4).",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        default=True,
        help="Run read-only offline preflight inspection (default).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Request cleanup execution (explicitly refused).",
    )

    args = parser.parse_args(argv)

    if args.execute:
        return execute_cleanup(target_url=args.target_url, candidate_id=args.candidate_id)

    # Offline preflight inspection path
    summary = inspect_candidate(target_url=args.target_url, candidate_id=args.candidate_id)
    print("=" * 60)
    print("LearnAI -- Candidate Orphan Student Offline Preflight")
    print("=" * 60)
    print(f"Status:                         {summary['status']}")
    print("Mode:                           OFFLINE PREFLIGHT ONLY")
    print(f"Target Configuration Valid:     {summary['target_valid']}")
    print(f"Candidate Identifier Valid:     {summary['candidate_id_valid']}")
    print("Live Database Verification:     SKIPPED")
    print("Candidate Record Existence:     NOT VERIFIED")
    print("Deletion Allowed:               NO")
    print(f"Detail:                         {summary['message']}")
    print("=" * 60)

    return 0 if summary["status"] == "OFFLINE_PREFLIGHT_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
