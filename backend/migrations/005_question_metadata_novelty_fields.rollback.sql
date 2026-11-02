-- =============================================================================
-- Migration 005 Rollback: Revert Question Metadata Novelty Fields
-- Version: 005_question_metadata_novelty_fields.rollback
-- =============================================================================

DROP INDEX idx_qm_topic_id ON question_metadata;

ALTER TABLE question_metadata
    DROP COLUMN normalized_text,
    DROP COLUMN topic_id,
    DROP COLUMN learning_objective;
