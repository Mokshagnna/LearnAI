-- =============================================================================
-- Migration 001: Initial LearnAI Schema for Supabase PostgreSQL
-- Version: 001
-- Description: Creates core LearnAI tables, constraints, foreign keys, and indexes.
-- =============================================================================

-- 0. Schema Migration Tracking Table
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 1. Students Table
CREATE TABLE IF NOT EXISTS students (
    id VARCHAR(100) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    last_active TIMESTAMPTZ NOT NULL
);

-- 2. Quiz Sessions Table
CREATE TABLE IF NOT EXISTS quiz_sessions (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_code VARCHAR(50) NOT NULL,
    course_name VARCHAR(250) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'in_progress',
    questions_snapshot JSONB NOT NULL,
    recommended_difficulty VARCHAR(50) DEFAULT 'mixed',
    adaptive_metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    submitted_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quiz_sessions_expires ON quiz_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_quiz_sessions_student_status ON quiz_sessions(student_id, status);

-- 3. Quiz Attempts Table
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_code VARCHAR(50) NOT NULL,
    course_name VARCHAR(250) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    quiz_data JSONB NOT NULL,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    percentage DOUBLE PRECISION NOT NULL,
    status VARCHAR(50) NOT NULL,
    difficulty_accuracy JSONB NOT NULL,
    concept_accuracy JSONB NOT NULL,
    weak_concepts JSONB NOT NULL,
    strong_concepts JSONB NOT NULL,
    recommended_difficulty VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL,
    session_id VARCHAR(100) REFERENCES quiz_sessions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_quiz_attempts_student ON quiz_attempts(student_id);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_course_topic ON quiz_attempts(course_code, topic_name);

-- 4. Question Metadata Table
CREATE TABLE IF NOT EXISTS question_metadata (
    id VARCHAR(100) PRIMARY KEY,
    attempt_id VARCHAR(100) NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
    student_id VARCHAR(100) NOT NULL,
    course_code VARCHAR(50) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    question_index INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    question_fingerprint VARCHAR(255) NOT NULL,
    concept VARCHAR(250) NOT NULL,
    difficulty VARCHAR(50) NOT NULL,
    question_type VARCHAR(50) DEFAULT 'mcq',
    correct_answer VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_question_metadata_fingerprint ON question_metadata(question_fingerprint);
CREATE INDEX IF NOT EXISTS idx_question_metadata_student_topic ON question_metadata(student_id, course_code, topic_name);

-- 5. Student Answers Table
CREATE TABLE IF NOT EXISTS student_answers (
    id VARCHAR(100) PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL REFERENCES quiz_sessions(id) ON DELETE CASCADE,
    attempt_id VARCHAR(100) NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
    student_id VARCHAR(100) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    question_index INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    selected_answer VARCHAR(50) NOT NULL,
    correct_answer VARCHAR(50) NOT NULL,
    is_correct INTEGER NOT NULL,
    difficulty VARCHAR(50) NOT NULL,
    concept VARCHAR(250) NOT NULL,
    explanation TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_student_answers_session_index UNIQUE (session_id, question_index)
);

CREATE INDEX IF NOT EXISTS idx_student_answers_session ON student_answers(session_id);
CREATE INDEX IF NOT EXISTS idx_student_answers_attempt ON student_answers(attempt_id);
CREATE INDEX IF NOT EXISTS idx_student_answers_student_concept ON student_answers(student_id, concept);

-- 6. Topic Mastery Table
CREATE TABLE IF NOT EXISTS topic_mastery (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    course_code VARCHAR(50) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    total_questions INTEGER NOT NULL DEFAULT 0,
    total_correct INTEGER NOT NULL DEFAULT 0,
    mastery_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    current_level VARCHAR(50) NOT NULL DEFAULT 'mixed',
    weak_concepts JSONB NOT NULL DEFAULT '[]'::jsonb,
    strong_concepts JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_practiced_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_topic_mastery_student_course_topic UNIQUE (student_id, course_code, topic_name)
);

CREATE INDEX IF NOT EXISTS idx_topic_mastery_lookup ON topic_mastery(student_id, course_code, topic_name);
