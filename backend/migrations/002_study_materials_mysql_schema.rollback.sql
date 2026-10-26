-- =============================================================================
-- Rollback Migration 002: Drop Study Materials and Topics for MySQL / TiDB
-- Version: 002_study_materials_mysql_schema.rollback
-- =============================================================================

DROP TABLE IF EXISTS study_material_topics;
DROP TABLE IF EXISTS study_materials;
