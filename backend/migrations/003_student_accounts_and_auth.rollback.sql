-- =============================================================================
-- Rollback Migration 003: Student Accounts, Sessions, and Password Reset
-- =============================================================================

DROP TABLE IF EXISTS password_reset_tokens;
DROP TABLE IF EXISTS student_sessions;
DROP TABLE IF EXISTS student_accounts;
