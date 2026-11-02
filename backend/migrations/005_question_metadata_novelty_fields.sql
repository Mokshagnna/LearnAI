-- =============================================================================
-- Migration 005: Question Metadata Novelty Fields for MySQL
-- Version: 005_question_metadata_novelty_fields
-- Description: Adds normalized_text, topic_id, and learning_objective columns
--   to question_metadata for cross-attempt novelty verification and duplicate
--   prevention during quiz retakes.
-- =============================================================================

ALTER TABLE question_metadata
    ADD COLUMN normalized_text TEXT,
    ADD COLUMN topic_id VARCHAR(100),
    ADD COLUMN learning_objective TEXT;

CREATE INDEX idx_qm_topic_id ON question_metadata (topic_id);
