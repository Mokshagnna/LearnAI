-- =============================================================================
-- Migration 001: Initial LearnAI Schema for MySQL
-- Version: 001_initial_mysql_schema
-- Description: Creates core LearnAI tables, constraints, foreign keys, and indexes.
-- =============================================================================

-- 0. Schema Migration Tracking Table
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) DEFAULT '',
    applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 1. Students Table
CREATE TABLE IF NOT EXISTS students (
    id VARCHAR(100) PRIMARY KEY,
    created_at DATETIME(6) NOT NULL,
    last_active DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Quiz Sessions Table
CREATE TABLE IF NOT EXISTS quiz_sessions (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL,
    course_code VARCHAR(50) NOT NULL,
    course_name VARCHAR(250) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'in_progress',
    questions_snapshot JSON NOT NULL,
    recommended_difficulty VARCHAR(50) DEFAULT 'mixed',
    adaptive_metadata JSON,
    created_at DATETIME(6) NOT NULL,
    submitted_at DATETIME(6),
    expires_at DATETIME(6) NOT NULL,
    CONSTRAINT fk_quiz_sessions_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    INDEX idx_quiz_sessions_expires (expires_at),
    INDEX idx_quiz_sessions_student_status (student_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Quiz Attempts Table
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL,
    course_code VARCHAR(50) NOT NULL,
    course_name VARCHAR(250) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    quiz_data JSON NOT NULL,
    score INT NOT NULL,
    total INT NOT NULL,
    percentage DOUBLE NOT NULL,
    status VARCHAR(50) NOT NULL,
    difficulty_accuracy JSON NOT NULL,
    concept_accuracy JSON NOT NULL,
    weak_concepts JSON NOT NULL,
    strong_concepts JSON NOT NULL,
    recommended_difficulty VARCHAR(50),
    created_at DATETIME(6) NOT NULL,
    session_id VARCHAR(100),
    CONSTRAINT fk_quiz_attempts_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    CONSTRAINT fk_quiz_attempts_session FOREIGN KEY (session_id) REFERENCES quiz_sessions(id) ON DELETE SET NULL,
    INDEX idx_quiz_attempts_student (student_id),
    INDEX idx_quiz_attempts_course_topic (course_code, topic_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Question Metadata Table
CREATE TABLE IF NOT EXISTS question_metadata (
    id VARCHAR(100) PRIMARY KEY,
    attempt_id VARCHAR(100) NOT NULL,
    student_id VARCHAR(100) NOT NULL,
    course_code VARCHAR(50) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    question_index INT NOT NULL,
    question_text TEXT NOT NULL,
    question_fingerprint TEXT NOT NULL,
    concept VARCHAR(250) NOT NULL,
    difficulty VARCHAR(50) NOT NULL,
    question_type VARCHAR(50) DEFAULT 'mcq',
    correct_answer VARCHAR(50) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    CONSTRAINT fk_question_metadata_attempt FOREIGN KEY (attempt_id) REFERENCES quiz_attempts(id) ON DELETE CASCADE,
    INDEX idx_question_metadata_fingerprint (question_fingerprint(255)),
    INDEX idx_question_metadata_student_topic (student_id, course_code, topic_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. Student Answers Table
CREATE TABLE IF NOT EXISTS student_answers (
    id VARCHAR(100) PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL,
    attempt_id VARCHAR(100) NOT NULL,
    student_id VARCHAR(100) NOT NULL,
    question_index INT NOT NULL,
    question_text TEXT NOT NULL,
    selected_answer VARCHAR(50) NOT NULL,
    correct_answer VARCHAR(50) NOT NULL,
    is_correct INT NOT NULL,
    difficulty VARCHAR(50) NOT NULL,
    concept VARCHAR(250) NOT NULL,
    explanation TEXT,
    created_at DATETIME(6) NOT NULL,
    CONSTRAINT fk_student_answers_session FOREIGN KEY (session_id) REFERENCES quiz_sessions(id) ON DELETE CASCADE,
    CONSTRAINT fk_student_answers_attempt FOREIGN KEY (attempt_id) REFERENCES quiz_attempts(id) ON DELETE CASCADE,
    CONSTRAINT fk_student_answers_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE KEY uq_student_answers_session_index (session_id, question_index),
    INDEX idx_student_answers_session (session_id),
    INDEX idx_student_answers_attempt (attempt_id),
    INDEX idx_student_answers_student_concept (student_id, concept)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6. Topic Mastery Table
CREATE TABLE IF NOT EXISTS topic_mastery (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL,
    course_code VARCHAR(50) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    total_questions INT NOT NULL DEFAULT 0,
    total_correct INT NOT NULL DEFAULT 0,
    mastery_score DOUBLE NOT NULL DEFAULT 0.0,
    current_level VARCHAR(50) NOT NULL DEFAULT 'mixed',
    weak_concepts JSON NOT NULL,
    strong_concepts JSON NOT NULL,
    last_practiced_at DATETIME(6) NOT NULL,
    CONSTRAINT fk_topic_mastery_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE KEY uq_topic_mastery_student_course_topic (student_id, course_code, topic_name),
    INDEX idx_topic_mastery_lookup (student_id, course_code, topic_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
