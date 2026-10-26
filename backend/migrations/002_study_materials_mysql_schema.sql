-- =============================================================================
-- Migration 002: Study Materials and Extracted Topics for MySQL / TiDB
-- Version: 002_study_materials_mysql_schema
-- Description: Creates persistent study_materials and study_material_topics tables.
-- =============================================================================

-- 1. Study Materials Table
CREATE TABLE IF NOT EXISTS study_materials (
    id VARCHAR(100) PRIMARY KEY,
    student_id VARCHAR(100) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(50) NOT NULL,
    file_size BIGINT NOT NULL DEFAULT 0,
    extracted_text_preview TEXT,
    extracted_text MEDIUMTEXT,
    content_hash VARCHAR(128),
    notes_session_id VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    CONSTRAINT fk_study_materials_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    INDEX idx_study_materials_student_status (student_id, status),
    INDEX idx_study_materials_student_hash (student_id, content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Study Material Topics Table
CREATE TABLE IF NOT EXISTS study_material_topics (
    id VARCHAR(100) PRIMARY KEY,
    material_id VARCHAR(100) NOT NULL,
    student_id VARCHAR(100) NOT NULL,
    course_code VARCHAR(50) NOT NULL,
    course_name VARCHAR(250) NOT NULL,
    topic_id VARCHAR(100) NOT NULL,
    topic_name VARCHAR(250) NOT NULL,
    confidence DOUBLE DEFAULT 1.0,
    matched_concepts JSON,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL,
    CONSTRAINT uq_study_material_topics UNIQUE (material_id, course_code, topic_name),
    CONSTRAINT fk_study_material_topics_material FOREIGN KEY (material_id) REFERENCES study_materials(id) ON DELETE CASCADE,
    CONSTRAINT fk_study_material_topics_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    INDEX idx_study_material_topics_material (material_id),
    INDEX idx_study_material_topics_student_topic (student_id, course_code, topic_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
