-- =============================================================================
-- Rollback for Migration 001: Initial LearnAI Schema
-- Description: Safely drops created LearnAI tables in reverse dependency order.
-- WARNING: DESTRUCTIVE OPERATION. Executing this rollback permanently drops
-- all application tables and destroys all contained student, session, and quiz data.
-- Data CANNOT be recovered after execution unless restored from an independent backup.
-- =============================================================================

DROP TABLE IF EXISTS student_answers CASCADE;
DROP TABLE IF EXISTS question_metadata CASCADE;
DROP TABLE IF EXISTS quiz_attempts CASCADE;
DROP TABLE IF EXISTS quiz_sessions CASCADE;
DROP TABLE IF EXISTS topic_mastery CASCADE;
DROP TABLE IF EXISTS students CASCADE;
DELETE FROM schema_migrations WHERE version = '001';
