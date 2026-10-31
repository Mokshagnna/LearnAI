-- =============================================================================
-- Migration 003: Student Accounts, Sessions, and Password Reset for MySQL / TiDB
-- Version: 003_student_accounts_and_auth
-- Description: Creates student_accounts, student_sessions, and password_reset_tokens tables.
-- =============================================================================

-- 1. Student Accounts Table
CREATE TABLE IF NOT EXISTS student_accounts (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    email_verified TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    last_login_at DATETIME(6),
    sessions_invalidated_at DATETIME(6),
    CONSTRAINT fk_student_accounts_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    INDEX idx_student_accounts_email (email),
    INDEX idx_student_accounts_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Student Sessions Table (Cross-device, Logout, and Revocation Tracking)
CREATE TABLE IF NOT EXISTS student_sessions (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    created_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    is_revoked TINYINT(1) NOT NULL DEFAULT 0,
    device_info VARCHAR(255) DEFAULT '',
    CONSTRAINT fk_student_sessions_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    INDEX idx_student_sessions_token_hash (token_hash),
    INDEX idx_student_sessions_student_revoked (student_id, is_revoked)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Password Reset Tokens Table (Cryptographically Secure Single-Use Tokens)
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    created_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    used_at DATETIME(6),
    CONSTRAINT fk_pwd_reset_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    INDEX idx_pwd_reset_token_hash (token_hash),
    INDEX idx_pwd_reset_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
