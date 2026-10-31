-- =============================================================================
-- Rollback for Migration 001: Initial LearnAI MySQL Schema
-- Description: Safely drops created LearnAI MySQL tables in reverse dependency order.
-- WARNING: DESTRUCTIVE OPERATION. Executing this rollback permanently drops
-- all application tables and destroys all contained student, session, and quiz data.
-- Data CANNOT be recovered after execution unless restored from an independent backup.
-- =============================================================================

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS student_answers;
DROP TABLE IF EXISTS question_metadata;
DROP TABLE IF EXISTS quiz_attempts;
DROP TABLE IF EXISTS quiz_sessions;
DROP TABLE IF EXISTS topic_mastery;
DROP TABLE IF EXISTS students;

DELETE FROM schema_migrations WHERE version = '001_initial_mysql_schema' OR version = '001';

SET FOREIGN_KEY_CHECKS = 1;
