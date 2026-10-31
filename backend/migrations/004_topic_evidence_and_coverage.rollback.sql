-- =============================================================================
-- Migration 004: Rollback (Topic Evidence, Coverage Status, and Matching Metadata)
-- Version: 004_topic_evidence_and_coverage
-- Description: Reverses the schema additions from migration 004.
-- =============================================================================

ALTER TABLE study_material_topics
    DROP COLUMN original_topic_title,
    DROP COLUMN topic_aliases,
    DROP COLUMN coverage_status,
    DROP COLUMN evidence,
    DROP COLUMN source_location,
    DROP COLUMN extraction_version,
    DROP COLUMN matching_algorithm_version;
