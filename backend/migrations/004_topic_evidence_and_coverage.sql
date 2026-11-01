-- =============================================================================
-- Migration 004: Topic Evidence, Coverage Status, and Matching Metadata
-- Version: 004_topic_evidence_and_coverage
-- Description: Extends study_material_topics with coverage classification,
--   source-location evidence, original topic title, aliases, and versioning
--   fields so that extracted topic coverage is auditable and never presented
--   as a raw similarity percentage.
-- =============================================================================

ALTER TABLE study_material_topics
    ADD COLUMN original_topic_title TEXT,
    ADD COLUMN topic_aliases TEXT,
    ADD COLUMN coverage_status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
    ADD COLUMN evidence TEXT,
    ADD COLUMN source_location TEXT,
    ADD COLUMN extraction_version TEXT,
    ADD COLUMN matching_algorithm_version TEXT;
