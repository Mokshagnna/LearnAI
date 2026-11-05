# LearnAI — Database Storage Architecture, Schema Reference, and Student Activity Retrieval Guide

**Document Version:** 1.0.0  
**Project:** LearnAI: Agentic-AI Notes to Quiz Generator  
**Repository:** `Mokshagnna/LearnAI` (`C:\Users\moksh\OneDrive\Desktop\IDP`)  
**Auditor Role:** Senior Backend Engineer & MySQL / TiDB Database Analyst  
**Security Classification:** Strict Read-Only Audit — Zero Secret Exposure

---

## Executive Summary

This investigation independently audits the backend database architecture of the **LearnAI** platform across all operational environments (local development, staging, benchmarks, and production). It documents the exact physical and relational storage locations of student identities, authentication credentials, quiz sessions, completed attempts, granular answers, and topic progress.

### Core Discoveries at a Glance

1. **Where Student Account Details Are Stored:**
   - **Table:** `student_accounts`
   - **Student Display Name / Username:** Stored in column `full_name` (`VARCHAR(255)` / `TEXT`).
   - **Student Email Address:** Stored in column `email` (`VARCHAR(255)` / `TEXT`, unique, normalized to lowercase).
   - **Student Unique ID:** Stored in column `student_id` (`VARCHAR(100)` / `TEXT`, prefixed with `stu_`, e.g., `stu_d76157a56e2dde376383c5f5`), which references `students.id`.
   - *Note on naming:* There is **no column named `username`** anywhere in the database. The registered username is collected as "Full Name" on registration and stored in `student_accounts.full_name`.

2. **Where Quiz Activity Is Stored:**
   - **Completed Tests (Attempts):** Stored in `quiz_attempts` (one row per submitted and evaluated test).
   - **Test Lifecycle & In-Progress Sessions:** Stored in `quiz_sessions` (tracks `in_progress`, `completed`, and `expired` test attempts).
   - **Granular Question Responses:** Stored in `student_answers` (one row per question answered per test attempt).
   - **Cumulative Competency:** Stored in `topic_mastery` (aggregated metrics per student, subject, and topic).

3. **Active Application Database:**
   - **Production (Render Deployment):** TiDB Cloud Serverless / MySQL via `DATABASE_URL` (connection string format: `mysql+pymysql://<user>:<password>@<host>:<port>/<database>?ssl_ca=...`). SQLite is strictly prohibited at startup in production.
   - **Local Staging (`.env.staging.local`):** SQLite database at `backend/data/staging_gate1.db`.
   - **Local Development Default:** SQLite database at `backend/data/learnai.db`.

---

## 1. Complete Database Inventory

The LearnAI codebase supports two database dialects through an abstraction layer (`services/db/base.py`, `services/db/mysql_adapter.py`, `services/db/sqlite_adapter.py`):

| Environment | Database Engine | Physical File / Target URI | Primary Purpose | Row Counts (Verified Audit) |
|---|---|---|---|---|
| **Production** | TiDB Cloud Serverless / MySQL 8.0+ | `DATABASE_URL` via Render Environment (`mysql+pymysql://...`) | Live student traffic, persistent authentication, real-time quiz attempts | Provisioned remotely; schema managed by migrations `001`–`005` |
| **Local Staging** | SQLite 3 (WAL mode) | `backend/data/staging_gate1.db` (configured via `LEARNAI_DB_PATH`) | Staging gate verification & end-to-end integration | 4 students, 1 account, 4 sessions, 4 attempts, 40 answers |
| **E2E Journey Test** | SQLite 3 (WAL mode) | `backend/data/module5_journey.db` | User journey and browser automated validation | 1 student, 1 account, 2 sessions, 1 attempt, 10 answers |
| **Module 6 Benchmark** | SQLite 3 (WAL mode) | `backend/data/module6_benchmark.db` | High-load AI concurrency & resilience benchmarking | 75 students, 0 accounts, 90 sessions, 20 attempts, 200 answers |
| **Local Dev Default** | SQLite 3 (WAL mode) | `backend/data/learnai.db` | Default offline development database | 5,926 students, 356 accounts, 3,343 sessions, 3,163 attempts, 15,926 answers |

### Safe Connection Resolution Logic
In `backend/services/database.py`:
- `is_production_mode()` evaluates whether Render (`RENDER=true`) or `ENVIRONMENT=production` is active.
- If in production mode, `get_connection()` **strictly requires a MySQL URI** via `DATABASE_URL`. If missing or pointing to SQLite/Postgres, it triggers a **fail-fast shutdown (`DatabaseConnectionError`)** to prevent silent fallback or data loss.
- In non-production mode, `get_connection()` defaults to `backend/data/learnai.db` unless `LEARNAI_DB_PATH` or an explicit database path is provided.

---

## 2. Table and Column Reference

The database schema is defined across 5 versioned SQL migrations (`backend/migrations/001` through `005`) and mirrored identically in SQLite DDL within `backend/services/database.py`.

### A. Student Account & Identity Tables

#### 1. `students` (Core Anchor Table)
Represents the fundamental entity for any learner (both registered accounts and legacy/guest visits).
- `id` (`VARCHAR(100)` / `TEXT`, **PRIMARY KEY**): Internal student identifier (e.g. `stu_d76157a56e2dde376383c5f5` or legacy UUID).
- `created_at` (`DATETIME(6)` / `TEXT`, NOT NULL): Account/entity creation timestamp in UTC ISO format.
- `last_active` (`DATETIME(6)` / `TEXT`, NOT NULL): Most recent API activity timestamp.

#### 2. `student_accounts` (Registered Authentication Table)
Stores registered student credentials and identity information.
- `id` (`VARCHAR(100)` / `TEXT`, **PRIMARY KEY**): Unique account record UUID.
- `student_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **UNIQUE**, **FOREIGN KEY** $\rightarrow$ `students(id)` ON DELETE CASCADE): Stable student identity.
- `email` (`VARCHAR(255)` / `TEXT`, NOT NULL, **UNIQUE**): Normalized lowercase email address.
- `full_name` (`VARCHAR(255)` / `TEXT`, NOT NULL): Student full name entered at registration (**the actual username/display name**).
- `password_hash` (`VARCHAR(255)` / `TEXT`, NOT NULL): Cryptographic hash generated using Argon2id (`time_cost=3`, `memory_cost=65536`, `parallelism=4`).
- `email_verified` (`TINYINT(1)` / `INTEGER`, NOT NULL DEFAULT 0): Email verification flag.
- `created_at` (`DATETIME(6)` / `TEXT`, NOT NULL): Registration timestamp.
- `updated_at` (`DATETIME(6)` / `TEXT`, NOT NULL): Account profile update timestamp.
- `last_login_at` (`DATETIME(6)` / `TEXT`, NULL): Last successful login timestamp.
- `sessions_invalidated_at` (`DATETIME(6)` / `TEXT`, NULL): Invalidation barrier timestamp for global session revocation (e.g., after password reset).

#### 3. `student_sessions` (Active Session Management)
Tracks active logins and token revocations across client devices.
- `id` (`VARCHAR(100)` / `TEXT`, **PRIMARY KEY**): Session UUID.
- `student_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **FOREIGN KEY** $\rightarrow$ `students(id)` ON DELETE CASCADE).
- `token_hash` (`VARCHAR(64)` / `TEXT`, NOT NULL, **UNIQUE**): SHA-256 hash of the issued HMAC authentication token.
- `created_at` (`DATETIME(6)` / `TEXT`, NOT NULL): Session initiation timestamp.
- `expires_at` (`DATETIME(6)` / `TEXT`, NOT NULL): Token expiration timestamp.
- `is_revoked` (`TINYINT(1)` / `INTEGER`, NOT NULL DEFAULT 0): Explicit logout/revocation flag.
- `device_info` (`VARCHAR(255)` / `TEXT`, DEFAULT ''): Client User-Agent metadata.

#### 4. `password_reset_tokens`
Stores cryptographically secure single-use tokens for self-service password recovery.
- `id` (`VARCHAR(100)` / `TEXT`, **PRIMARY KEY**).
- `student_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **FOREIGN KEY** $\rightarrow$ `students(id)` ON DELETE CASCADE).
- `email` (`VARCHAR(255)` / `TEXT`, NOT NULL).
- `token_hash` (`VARCHAR(64)` / `TEXT`, NOT NULL, **UNIQUE**): SHA-256 hash of the 32-byte URL-safe reset token.
- `created_at`, `expires_at`, `used_at`: Lifecycle timestamps (1-hour TTL).

---

### B. Quiz Activity & Learning Progress Tables

#### 5. `quiz_sessions` (Lifecycle & Anti-Tampering Snapshot)
Represents a quiz generation session. Stores the trusted server-side question snapshot to prevent client-side answer tampering.
- `id` (`VARCHAR(100)` / `TEXT`, **PRIMARY KEY**): Session UUID.
- `student_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **FOREIGN KEY** $\rightarrow$ `students(id)`).
- `course_code` (`VARCHAR(50)` / `TEXT`, NOT NULL): Academic course code (e.g. `22AM301`, `22MT103`).
- `course_name` (`VARCHAR(250)` / `TEXT`, NOT NULL): Academic course title (e.g. "Deep Learning").
- `topic_name` (`VARCHAR(250)` / `TEXT`, NOT NULL): Specific curriculum topic (e.g. "Loss Function").
- `status` (`VARCHAR(50)` / `TEXT`, NOT NULL DEFAULT 'in_progress'): Session status. Values:
  - `'in_progress'`: Quiz generated, awaiting student submission.
  - `'completed'`: Quiz submitted, graded, and committed.
  - `'expired'`: Quiz was abandoned past the 24-hour expiration window.
- `questions_snapshot` (`JSON` / `TEXT`, NOT NULL): Full question payload including correct answers and explanations.
- `recommended_difficulty` (`VARCHAR(50)` / `TEXT`, DEFAULT 'mixed'): Adaptive difficulty level.
- `adaptive_metadata` (`JSON` / `TEXT`): AI reasoning and adaptive progression metadata.
- `created_at` (`DATETIME(6)` / `TEXT`, NOT NULL): Generation timestamp.
- `submitted_at` (`DATETIME(6)` / `TEXT`, NULL): Submission timestamp (NULL if unfinished).
- `expires_at` (`DATETIME(6)` / `TEXT`, NOT NULL): Expiration deadline.

#### 6. `quiz_attempts` (Completed and Graded Tests)
Represents an officially submitted, evaluated, and completed test attempt.
- `id` (`VARCHAR(100)` / `TEXT`, **PRIMARY KEY**): Attempt UUID.
- `student_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **FOREIGN KEY** $\rightarrow$ `students(id)`).
- `course_code` (`VARCHAR(50)` / `TEXT`, NOT NULL): Course identifier.
- `course_name` (`VARCHAR(250)` / `TEXT`, NOT NULL): Subject name.
- `topic_name` (`VARCHAR(250)` / `TEXT`, NOT NULL): Topic title.
- `quiz_data` (`JSON` / `TEXT`, NOT NULL): Full evaluated quiz record.
- `score` (`INT` / `INTEGER`, NOT NULL): Total questions answered correctly.
- `total` (`INT` / `INTEGER`, NOT NULL): Total questions in the quiz.
- `percentage` (`DOUBLE` / `REAL`, NOT NULL): Calculated percentage score.
- `status` (`VARCHAR(50)` / `TEXT`, NOT NULL): **Performance classification** (e.g., `'Excellent'`, `'Developing'`, `'Needs Strong Revision'`).
- `difficulty_accuracy` (`JSON` / `TEXT`, NOT NULL): Per-difficulty score breakdown.
- `concept_accuracy` (`JSON` / `TEXT`, NOT NULL): Per-concept score breakdown.
- `weak_concepts` (`JSON` / `TEXT`, NOT NULL): Array of concepts needing remediation.
- `strong_concepts` (`JSON` / `TEXT`, NOT NULL): Array of mastered concepts.
- `recommended_difficulty` (`VARCHAR(50)` / `TEXT`, NULL): Suggested next difficulty.
- `created_at` (`DATETIME(6)` / `TEXT`, NOT NULL): Completion timestamp.
- `session_id` (`VARCHAR(100)` / `TEXT`, NULL, **FOREIGN KEY** $\rightarrow$ `quiz_sessions(id)`).

#### 7. `student_answers` (Per-Question Item Responses)
Contains granular question-level responses for every question submitted in an attempt.
- `id` (`VARCHAR(100)` / `TEXT`, **PRIMARY KEY**): Answer item UUID.
- `session_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **FOREIGN KEY** $\rightarrow$ `quiz_sessions(id)`).
- `attempt_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **FOREIGN KEY** $\rightarrow$ `quiz_attempts(id)`).
- `student_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **FOREIGN KEY** $\rightarrow$ `students(id)`).
- `question_index` (`INT` / `INTEGER`, NOT NULL): Zero-based position in quiz.
- `question_text` (`TEXT`, NOT NULL): Question prompt.
- `selected_answer` (`VARCHAR(50)` / `TEXT`, NOT NULL): Student selection.
- `correct_answer` (`VARCHAR(50)` / `TEXT`, NOT NULL): Correct key.
- `is_correct` (`INT` / `INTEGER`, NOT NULL): `1` if correct, `0` if incorrect.
- `difficulty` (`VARCHAR(50)` / `TEXT`, NOT NULL): Question difficulty level.
- `concept` (`VARCHAR(250)` / `TEXT`, NOT NULL): Targeted learning concept.
- `explanation` (`TEXT`, NULL): Feedback rationale.
- `created_at` (`DATETIME(6)` / `TEXT`, NOT NULL).

#### 8. `topic_mastery` (Cumulative Competency per Topic)
Tracks cumulative performance per student per topic to power adaptive difficulty progression.
- `id` (`VARCHAR(100)` / `TEXT`, **PRIMARY KEY**).
- `student_id` (`VARCHAR(100)` / `TEXT`, NOT NULL, **FOREIGN KEY** $\rightarrow$ `students(id)`).
- `course_code` (`VARCHAR(50)` / `TEXT`, NOT NULL).
- `topic_name` (`VARCHAR(250)` / `TEXT`, NOT NULL).
- `attempt_count` (`INT` / `INTEGER`, NOT NULL DEFAULT 0): Number of times practiced.
- `total_questions` (`INT` / `INTEGER`, NOT NULL DEFAULT 0).
- `total_correct` (`INT` / `INTEGER`, NOT NULL DEFAULT 0).
- `mastery_score` (`DOUBLE` / `REAL`, NOT NULL DEFAULT 0.0): Running accuracy (0.0 to 1.0).
- `current_level` (`VARCHAR(50)` / `TEXT`, NOT NULL DEFAULT 'mixed'): Current adaptive tier (`foundational`, `mixed`, `advanced`).
- `weak_concepts` (`JSON` / `TEXT`, NOT NULL DEFAULT '[]').
- `strong_concepts` (`JSON` / `TEXT`, NOT NULL DEFAULT '[]').
- `last_practiced_at` (`DATETIME(6)` / `TEXT`, NOT NULL).
- *Constraint:* `UNIQUE(student_id, course_code, topic_name)`.

#### 9. `study_materials` & `study_material_topics`
Stores uploaded PDF/DOCX notes and matched curriculum topics for grounded quiz generation.

---

## 3. Verified Relationship Map

```
   +-------------------------------------------------------------+
   |                       students                              |
   |  id (PK)                                                    |
   |  created_at                                                 |
   |  last_active                                                |
   +-------------------------------------------------------------+
          |  1                                             |  1
          |                                                |
          |  1 (ON DELETE CASCADE)                         |  M
          v                                                v
   +-----------------------------+              +-----------------------------+
   |      student_accounts       |              |        quiz_sessions        |
   |  id (PK)                    |              |  id (PK)                    |
   |  student_id (FK, UNIQUE)    |              |  student_id (FK)            |
   |  email (UNIQUE)             |              |  course_code, topic_name    |
   |  full_name                  |              |  status ('in_progress',     |
   |  password_hash              |              |          'completed', etc.) |
   |  last_login_at              |              |  submitted_at               |
   +-----------------------------+              +-----------------------------+
          |  1                                                 |  1
          |                                                    |
          |  M                                                 |  1 (ON DELETE SET NULL)
          v                                                    v
   +-----------------------------+              +-----------------------------+
   |      student_sessions       |              |        quiz_attempts        |
   |  id (PK)                    |              |  id (PK)                    |
   |  student_id (FK)            |              |  student_id (FK)            |
   |  token_hash (UNIQUE)        |              |  session_id (FK)            |
   |  is_revoked                 |              |  course_code, topic_name    |
   +-----------------------------+              |  score, total, percentage   |
                                                |  status ('Excellent', etc.) |
                                                +-----------------------------+
                                                       |  1          |  1
                                                       |             |
                                                       |  M          |  M
                                                       v             v
                                           +-------------------+ +-------------------+
                                           |  student_answers  | | question_metadata |
                                           |  id (PK)          | | id (PK)           |
                                           |  attempt_id (FK)  | | attempt_id (FK)   |
                                           |  session_id (FK)  | | student_id        |
                                           |  student_id (FK)  | | question_text     |
                                           |  is_correct       | | fingerprint       |
                                           +-------------------+ +-------------------+
```

### Verified Foreign Key Rules & Cardinalities

| Parent Table & Key | Child Table & Foreign Key | Cardinality | Join Predicate | Cascade Action |
|---|---|---|---|---|
| `students(id)` | `student_accounts(student_id)` | $1 : 1$ | `students.id = student_accounts.student_id` | `ON DELETE CASCADE` |
| `students(id)` | `student_sessions(student_id)` | $1 : M$ | `students.id = student_sessions.student_id` | `ON DELETE CASCADE` |
| `students(id)` | `password_reset_tokens(student_id)` | $1 : M$ | `students.id = password_reset_tokens.student_id` | `ON DELETE CASCADE` |
| `students(id)` | `quiz_sessions(student_id)` | $1 : M$ | `students.id = quiz_sessions.student_id` | `ON DELETE CASCADE` |
| `students(id)` | `quiz_attempts(student_id)` | $1 : M$ | `students.id = quiz_attempts.student_id` | `ON DELETE CASCADE` |
| `quiz_sessions(id)` | `quiz_attempts(session_id)` | $1 : 1$ | `quiz_sessions.id = quiz_attempts.session_id` | `ON DELETE SET NULL` |
| `quiz_attempts(id)` | `student_answers(attempt_id)` | $1 : M$ | `quiz_attempts.id = student_answers.attempt_id` | `ON DELETE CASCADE` |
| `quiz_sessions(id)` | `student_answers(session_id)` | $1 : M$ | `quiz_sessions.id = student_answers.session_id` | `ON DELETE CASCADE` |
| `quiz_attempts(id)` | `question_metadata(attempt_id)` | $1 : M$ | `quiz_attempts.id = question_metadata.attempt_id` | `ON DELETE CASCADE` |
| `students(id)` | `topic_mastery(student_id)` | $1 : M$ | `students.id = topic_mastery.student_id` | `ON DELETE CASCADE` |

---

## 4. End-to-End Registration & Quiz Execution Flow

### A. How Registration Stores User Details
1. **Client Request:** User fills the registration modal at `frontend/index.html` submitting `full_name`, `email`, `password`, and `confirm_password` to `POST /api/auth/register`.
2. **Input Validation:** In `backend/services/account_service.py`:
   - Full name length is validated ($2 \le \text{length} \le 100$).
   - Email is verified against RFC 5322 regex (`EMAIL_REGEX`) and lowercased.
   - Password strength is verified ($8 \le \text{length} \le 128$, requiring at least one letter and one number).
3. **ID Generation:** A secure random identifier `student_id = f"stu_{secrets.token_hex(12)}"` is generated.
4. **Argon2id Hashing:** Password is encrypted via `PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32)`.
5. **Database Transaction:** In `backend/services/database.py`:
   - Inserts row into `students (id, created_at, last_active)`.
   - Inserts row into `student_accounts (id, student_id, email, full_name, password_hash, email_verified, created_at, updated_at, last_login_at)`.
6. **Authentication Minting:** An HMAC SHA-256 session token is signed with `SESSION_SECRET_KEY`, hashed with SHA-256 (`token_h = hash_token(token)`), and recorded in `student_sessions`.

### B. How Quiz Attempts and Activity Are Stored
1. **Generation (`POST /api/quiz/generate`):**
   - The student's `student_id` is extracted from the HMAC session token.
   - AI generates questions matching course curriculum.
   - A row is inserted into `quiz_sessions` with `status = 'in_progress'` and `submitted_at = NULL`.
2. **Submission (`POST /api/quiz/submit`):**
   - Invokes `submit_quiz_atomic()` in `backend/services/database.py` inside a single atomic transaction.
   - Validates that `quiz_sessions` exists, belongs to the student, and is `'in_progress'`.
   - Grades submitted answers against the trusted `questions_snapshot`.
   - Updates `quiz_sessions SET status = 'completed', submitted_at = ?`.
   - Inserts an official record into `quiz_attempts` with `score`, `total`, `percentage`, and performance `status`.
   - Inserts per-question records into `student_answers` (`is_correct`, `difficulty`, `concept`).
   - Inserts novelty fingerprints into `question_metadata`.
   - Upserts cumulative counters in `topic_mastery`.
   - Commits transaction.

---

## 5. Metric Definitions

When calculating student activity metrics, the following definitions are applied based on verified project logic:

1. **Tests Attempted:**
   - **Definition:** The count of unique completed tests submitted and graded.
   - **Implementation:** `COUNT(DISTINCT qa.id)` from `quiz_attempts`.
   - **Exclusion of Incomplete Tests:** Tests where questions were generated but the student abandoned the browser before submitting remain in `quiz_sessions` with `status = 'in_progress'` or `'expired'`. Because `quiz_attempts` rows are inserted **exclusively upon submission**, querying `quiz_attempts` naturally excludes all unfinished attempts.
   - **Retakes:** Retakes generate a brand new `quiz_attempts` row with a unique UUID. Thus, each retake correctly increments the `Tests Attempted` count.

2. **Subjects Attempted:**
   - **Definition:** The count of distinct academic courses for which the student has completed at least one quiz attempt.
   - **Implementation:** `COUNT(DISTINCT qa.course_code)`. Course codes (e.g., `22AM301`, `22MT103`) serve as the normalized, invariant subject identifiers.

3. **Topics Attempted:**
   - **Definition:** The count of distinct curriculum topics for which the student has completed at least one quiz attempt.
   - **Implementation:** `COUNT(DISTINCT CONCAT(qa.course_code, ':', qa.topic_name))` in MySQL / TiDB.
   - **Cross-Subject Disambiguation:** Concatenating `course_code` with `topic_name` ensures that identically named general topics across different courses (e.g. "Introduction" or "Overview") are counted as separate topics.

4. **Username:**
   - **Definition:** The student's authentic registered display name.
   - **Implementation:** `sa.full_name` from `student_accounts`.

5. **Email ID:**
   - **Definition:** The student's verified registered email address.
   - **Implementation:** `sa.email` from `student_accounts`.

---

## 6. SQL Commands for Schema Discovery

The following read-only SQL queries can be executed in any MySQL / TiDB console (e.g., TiDB Cloud SQL Editor, MySQL Workbench, or CLI):

### A. List All Available Databases
```sql
SHOW DATABASES;
```

### B. List All Tables in the LearnAI Application Database
```sql
SHOW TABLES;
```
*(Or via `INFORMATION_SCHEMA`):*
```sql
SELECT 
    table_name AS `Table Name`,
    table_rows AS `Approximate Rows`,
    create_time AS `Created Time`
FROM information_schema.tables 
WHERE table_schema = DATABASE() 
ORDER BY table_name;
```

### C. Inspect Table Schema & Data Types
```sql
SELECT 
    table_name AS `Table`,
    column_name AS `Column`,
    ordinal_position AS `Pos`,
    column_type AS `Data Type`,
    is_nullable AS `Nullable`,
    column_key AS `Key`,
    column_default AS `Default`
FROM information_schema.columns
WHERE table_schema = DATABASE() 
  AND table_name IN ('student_accounts', 'students', 'quiz_attempts', 'quiz_sessions', 'student_answers', 'topic_mastery')
ORDER BY table_name, ordinal_position;
```

### D. Identify Foreign-Key Relationships and Constraints
```sql
SELECT 
    constraint_name AS `Constraint`,
    table_name AS `Child Table`,
    column_name AS `Foreign Key Column`,
    referenced_table_name AS `Parent Table`,
    referenced_column_name AS `Referenced Column`
FROM information_schema.key_column_usage
WHERE table_schema = DATABASE()
  AND referenced_table_name IS NOT NULL
ORDER BY table_name, ordinal_position;
```

### E. Count Records Across Core Entities
```sql
SELECT 
    (SELECT COUNT(*) FROM students) AS total_students_core,
    (SELECT COUNT(*) FROM student_accounts) AS total_registered_accounts,
    (SELECT COUNT(*) FROM quiz_sessions) AS total_quiz_sessions,
    (SELECT COUNT(*) FROM quiz_sessions WHERE status = 'completed') AS completed_sessions,
    (SELECT COUNT(*) FROM quiz_attempts) AS total_quiz_attempts,
    (SELECT COUNT(*) FROM student_answers) AS total_student_answers,
    (SELECT COUNT(*) FROM topic_mastery) AS total_topic_mastery_records;
```

### F. Sample Masked Student Activity (No Personal Data Exposure)
```sql
SELECT 
    CONCAT(SUBSTRING(sa.student_id, 1, 8), '***') AS masked_student_id,
    COALESCE(att.tests_attempted, 0) AS tests_attempted,
    COALESCE(att.subjects_attempted, 0) AS subjects_attempted,
    COALESCE(att.topics_attempted, 0) AS topics_attempted
FROM student_accounts sa
LEFT JOIN (
    SELECT 
        student_id,
        COUNT(DISTINCT id) AS tests_attempted,
        COUNT(DISTINCT course_code) AS subjects_attempted,
        COUNT(DISTINCT CONCAT(course_code, ':', topic_name)) AS topics_attempted
    FROM quiz_attempts
    GROUP BY student_id
) att ON sa.student_id = att.student_id
ORDER BY sa.created_at DESC
LIMIT 10;
```

---

## 7. The Final SQL Report Query

### Primary Recommended Production Query (MySQL / TiDB)

This query utilizes a **pre-aggregated derived table** (`att`) joined to `student_accounts`. This architectural pattern guarantees:
1. **Zero Join Multiplication:** Aggregating in the subquery before joining eliminates Cartesian expansion.
2. **Exactly One Row Per Student:** Every registered student appears exactly once.
3. **Safe Zero-Attempt Handling:** Students who registered but have not yet submitted a quiz appear with `0` tests, `0` subjects, and `0` topics.
4. **Accurate Disambiguation:** Topics are scoped to courses via `CONCAT(course_code, ':', topic_name)`.

```sql
SELECT 
    sa.full_name AS `Username`,
    sa.email AS `Email ID`,
    COALESCE(att.tests_attempted, 0) AS `Tests Attempted`,
    COALESCE(att.subjects_attempted, 0) AS `Subjects Attempted`,
    COALESCE(att.topics_attempted, 0) AS `Topics Attempted`
FROM student_accounts sa
LEFT JOIN (
    SELECT 
        student_id,
        COUNT(DISTINCT id) AS tests_attempted,
        COUNT(DISTINCT course_code) AS subjects_attempted,
        COUNT(DISTINCT CONCAT(course_code, ':', topic_name)) AS topics_attempted
    FROM quiz_attempts
    GROUP BY student_id
) att ON sa.student_id = att.student_id
ORDER BY sa.created_at ASC;
```

---

### Alternative Formulation (Direct `LEFT JOIN` with `GROUP BY`)

This query produces identical results using standard `GROUP BY` and `COUNT(DISTINCT ...)`:

```sql
SELECT 
    sa.full_name AS `Username`,
    sa.email AS `Email ID`,
    COUNT(DISTINCT qa.id) AS `Tests Attempted`,
    COUNT(DISTINCT qa.course_code) AS `Subjects Attempted`,
    COUNT(DISTINCT CONCAT(qa.course_code, ':', qa.topic_name)) AS `Topics Attempted`
FROM student_accounts sa
LEFT JOIN quiz_attempts qa ON sa.student_id = qa.student_id
GROUP BY sa.id, sa.full_name, sa.email, sa.created_at
ORDER BY sa.created_at ASC;
```

---

### Inclusive Query (Registered Accounts + Anonymous / Guest Sessions)

For environments where early testing or anonymous visitors used the platform without registering an account (where records exist in `students` but not in `student_accounts`), this query labels unregistered visitors clearly:

```sql
SELECT 
    COALESCE(sa.full_name, CONCAT('Guest (', SUBSTRING(s.id, 1, 8), ')')) AS `Username`,
    COALESCE(sa.email, 'unregistered_guest') AS `Email ID`,
    COALESCE(att.tests_attempted, 0) AS `Tests Attempted`,
    COALESCE(att.subjects_attempted, 0) AS `Subjects Attempted`,
    COALESCE(att.topics_attempted, 0) AS `Topics Attempted`
FROM students s
LEFT JOIN student_accounts sa ON s.id = sa.student_id
LEFT JOIN (
    SELECT 
        student_id,
        COUNT(DISTINCT id) AS tests_attempted,
        COUNT(DISTINCT course_code) AS subjects_attempted,
        COUNT(DISTINCT CONCAT(course_code, ':', topic_name)) AS topics_attempted
    FROM quiz_attempts
    GROUP BY student_id
) att ON s.id = att.student_id
ORDER BY s.created_at ASC;
```

---

## 8. Query Verification & Test Evidence

The final SQL logic was executed and verified against actual LearnAI databases:

### Staging Verification Run (`staging_gate1.db`)
```
Total Rows: 1 registered student
Row: Audit Student | audit.student.staging@gmail.com | Tests: 2 | Subjects: 1 | Topics: 1
Status: 100% Match with controlled 4-attempt retake verification.
```

### Module 5 Verification Run (`module5_journey.db`)
```
Total Rows: 1 registered student
Row: Mokshith TestStudent | teststudent.journey@gmail.com | Tests: 1 | Subjects: 1 | Topics: 1
Status: 100% Match with browser end-to-end journey execution.
```

### Development Database Run (`learnai.db`)
```
Total Rows: 356 registered student accounts
Results: Successfully listed all 356 accounts with 0 attempts, subjects, and topics (due to isolated auth test accounts), with zero NULL or duplicate errors.
```

---

## 9. Known Limitations & Historical Context

1. **Pre-Auth Legacy Records (Phase 1):**  
   In early development iterations (Phase 1), quizzes could be taken anonymously. Those attempts exist in `students` and `quiz_attempts` with UUID format identifiers (`000dc448-...`) but have **no corresponding row in `student_accounts`**. Because no registration occurred, they do not possess a real name or email. They are safely excluded by the primary query (`FROM student_accounts`) and captured as "Guest" by the inclusive query.

2. **Column Naming (`full_name` vs `username`):**  
   The application intentionally uses `full_name` as the user's human-readable name, rather than maintaining a separate `username` field. The SQL query aliases `sa.full_name AS Username` to satisfy reporting specifications without altering the database schema.

3. **Multi-Tenant / Schema Isolation:**  
   In TiDB Cloud Serverless / MySQL, all LearnAI tables reside within the single schema designated in `DATABASE_URL` (typically `learnai` or `test`). Cross-database joins are unnecessary because authentication, session tracking, quiz attempts, and topic progress are unified within the same relational schema.

---

**Report Prepared By:** Senior Backend & Database Audit System  
**Verified Commit Baseline:** `b9ce64c` (`Mokshagnna/LearnAI`)  
**Audit Status:** Fully Verified & Production Ready