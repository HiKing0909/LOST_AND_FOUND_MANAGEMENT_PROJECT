-- FoundIT database — canonical clean-install schema
--
-- Purpose: create the complete database used by the current application.
-- WARNING: this script intentionally drops the existing foundit database first.
-- Back up existing data before running it.
--
-- The categories table is master data maintained by administrators.

-- FoundIT CLEAN DATABASE
-- Defense idea: this file is the single source of truth for the database schema.
-- The Flask application validates the schema at runtime, while this script
-- creates the complete clean structure used for testing/deployment.
-- MySQL 8.0.16+
--
-- WARNING: This is a clean-install script. It intentionally removes the
-- existing `foundit` database so no old tables/migrations can interfere.
-- BACK UP ANY DATA YOU WANT TO KEEP BEFORE RUNNING THIS FILE.

DROP DATABASE IF EXISTS foundi;
CREATE DATABASE foundi; CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE foundit;

-- Students use 6-digit IDs. Account data is intentionally separated from
-- Faculty/Staff and Administrator accounts so role-specific rules stay clear.
CREATE TABLE IF NOT EXISTS students (
    account_id VARCHAR(6) NOT NULL,
    first_name VARCHAR(80) NOT NULL,
    last_name VARCHAR(80) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    status ENUM('active', 'suspended') NOT NULL DEFAULT 'active',

    email_account_id VARCHAR(6)
        GENERATED ALWAYS AS (
            SUBSTRING_INDEX(
                SUBSTRING_INDEX(LOWER(email), '.tc@', 1),
                '.',
                -1
            )
        ) STORED,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    profile_picture MEDIUMBLOB NULL,
    profile_picture_mime_type VARCHAR(50) NULL,

    PRIMARY KEY (account_id),
    UNIQUE KEY uq_students_email (email),

    CONSTRAINT chk_students_id
        CHECK (account_id REGEXP '^[0-9]{6}$'),

    CONSTRAINT chk_students_email
        CHECK (
            email REGEXP
                '^[a-z][a-z0-9_-]*\\.[a-z][a-z0-9_-]*\\.[0-9]{6}\\.tc@umindanao\\.edu\\.ph$'
        ),

    CONSTRAINT chk_students_email_id
        CHECK (email_account_id = account_id),

    CONSTRAINT chk_students_name
        CHECK (
            CHAR_LENGTH(TRIM(first_name)) > 0
            AND CHAR_LENGTH(TRIM(last_name)) > 0
        )
);

-- Faculty/Staff use 4-digit IDs and start as pending until an Admin activates them.
CREATE TABLE IF NOT EXISTS faculty_staff (
    account_id VARCHAR(4) NOT NULL,
    first_name VARCHAR(80) NOT NULL,
    last_name VARCHAR(80) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    status ENUM('pending', 'active', 'suspended') NOT NULL DEFAULT 'pending',

    email_account_id VARCHAR(4)
        GENERATED ALWAYS AS (
            SUBSTRING_INDEX(
                SUBSTRING_INDEX(LOWER(email), '.tc@', 1),
                '.',
                -1
            )
        ) STORED,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    profile_picture MEDIUMBLOB NULL,
    profile_picture_mime_type VARCHAR(50) NULL,

    PRIMARY KEY (account_id),
    UNIQUE KEY uq_faculty_staff_email (email),

    CONSTRAINT chk_faculty_staff_id
        CHECK (account_id REGEXP '^[0-9]{4}$'),

    CONSTRAINT chk_faculty_staff_email
        CHECK (
            email REGEXP
                '^[a-z][a-z0-9_-]*\\.[a-z][a-z0-9_-]*\\.[0-9]{4}\\.tc@umindanao\\.edu\\.ph$'
        ),

    CONSTRAINT chk_faculty_staff_email_id
        CHECK (email_account_id = account_id),

    CONSTRAINT chk_faculty_staff_name
        CHECK (
            CHAR_LENGTH(TRIM(first_name)) > 0
            AND CHAR_LENGTH(TRIM(last_name)) > 0
        )
);
DELIMITER $$
CREATE TRIGGER trg_faculty_staff_pending_insert
BEFORE INSERT ON faculty_staff
FOR EACH ROW
BEGIN
    SET NEW.status = 'pending';
END$$
DELIMITER ;

-- Administrators also use 4-digit IDs, but public registration is disabled.
-- Admin accounts are inserted directly by the database administrator.
CREATE TABLE IF NOT EXISTS admins (
    account_id VARCHAR(4) NOT NULL,
    first_name VARCHAR(80) NOT NULL,
    last_name VARCHAR(80) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    status ENUM('active', 'suspended') NOT NULL DEFAULT 'active',

    email_account_id VARCHAR(4)
        GENERATED ALWAYS AS (
            SUBSTRING_INDEX(
                SUBSTRING_INDEX(LOWER(email), '.tc@', 1),
                '.',
                -1
            )
        ) STORED,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    profile_picture MEDIUMBLOB NULL,
    profile_picture_mime_type VARCHAR(50) NULL,

    PRIMARY KEY (account_id),
    UNIQUE KEY uq_admins_email (email),

    CONSTRAINT chk_admins_id
        CHECK (account_id REGEXP '^[0-9]{4}$'),

    CONSTRAINT chk_admins_email
        CHECK (
            email REGEXP
                '^[a-z][a-z0-9_-]*\\.[a-z][a-z0-9_-]*\\.[0-9]{4}\\.tc@umindanao\\.edu\\.ph$'
        ),

    CONSTRAINT chk_admins_email_id
        CHECK (email_account_id = account_id),

    CONSTRAINT chk_admins_name
        CHECK (
            CHAR_LENGTH(TRIM(first_name)) > 0
            AND CHAR_LENGTH(TRIM(last_name)) > 0
        )
);


-- ---------------------------------------------------------------------------
-- Item categories
-- The category master list is database-backed so administrators can add,
-- rename, reorder, activate, or deactivate categories without changing code.
-- Historical reports keep their category text so renaming a category does not
-- rewrite old report records.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS categories (
    category_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(50) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (category_id),
    UNIQUE KEY uq_categories_name (name),
    KEY idx_categories_active_order (is_active, sort_order, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO categories (name, is_active, sort_order) VALUES
    ('Electronics', TRUE, 1),
    ('Documents & IDs', TRUE, 2),
    ('Wallet & Money', TRUE, 3),
    ('Keys', TRUE, 4),
    ('Clothing', TRUE, 5),
    ('Bags', TRUE, 6),
    ('School Supplies', TRUE, 7),
    ('Accessories', TRUE, 8),
    ('Books', TRUE, 9),
    ('Other', TRUE, 10)
ON DUPLICATE KEY UPDATE
    name = VALUES(name);


-- ---------------------------------------------------------------------------
-- Lost item reports
-- One report belongs to exactly one Student OR one Faculty / Staff account.
-- Photos are stored in MySQL so the report remains available across browsers.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lost_item_reports (
    report_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

    student_account_id VARCHAR(6) NULL,
    faculty_staff_account_id VARCHAR(4) NULL,

    item_name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    description VARCHAR(1000) NOT NULL,

    photo_data LONGBLOB NULL,
    photo_filename VARCHAR(255) NULL,
    photo_mime_type VARCHAR(50) NULL,

    last_seen VARCHAR(150) NOT NULL,
    date_lost DATE NOT NULL,

    status ENUM('pending', 'verified', 'rejected')
        NOT NULL DEFAULT 'pending',

    reviewed_by_faculty_staff_id VARCHAR(4) NULL,
    reviewed_at TIMESTAMP NULL DEFAULT NULL,
    returned_by_faculty_staff_id VARCHAR(4) NULL,
    returned_at TIMESTAMP NULL DEFAULT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    one_week_notified_at TIMESTAMP NULL DEFAULT NULL,

    PRIMARY KEY (report_id),

    KEY idx_lost_reports_student (student_account_id),
    KEY idx_lost_reports_faculty_staff (faculty_staff_account_id),
    KEY idx_lost_reports_status_created (status, created_at),
    KEY idx_lost_reports_one_week (status, created_at, one_week_notified_at),

    CONSTRAINT fk_lost_reports_student
        FOREIGN KEY (student_account_id)
        REFERENCES students (account_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_lost_reports_faculty_staff
        FOREIGN KEY (faculty_staff_account_id)
        REFERENCES faculty_staff (account_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,

    CONSTRAINT fk_lost_reports_reviewer
        FOREIGN KEY (reviewed_by_faculty_staff_id)
        REFERENCES faculty_staff (account_id)
        ON UPDATE CASCADE
        ON DELETE SET NULL,

    CONSTRAINT chk_lost_reports_single_owner
        CHECK (
            (student_account_id IS NOT NULL AND faculty_staff_account_id IS NULL)
            OR
            (student_account_id IS NULL AND faculty_staff_account_id IS NOT NULL)
        ),

    CONSTRAINT chk_lost_reports_item_name
        CHECK (CHAR_LENGTH(TRIM(item_name)) BETWEEN 1 AND 100),

    CONSTRAINT chk_lost_reports_category
        CHECK (CHAR_LENGTH(TRIM(category)) BETWEEN 1 AND 50),

    CONSTRAINT chk_lost_reports_description
        CHECK (CHAR_LENGTH(TRIM(description)) BETWEEN 1 AND 1000),

    CONSTRAINT chk_lost_reports_last_seen
        CHECK (CHAR_LENGTH(TRIM(last_seen)) BETWEEN 1 AND 150),

    CONSTRAINT chk_lost_reports_photo_metadata
        CHECK (
            (photo_data IS NULL AND photo_filename IS NULL AND photo_mime_type IS NULL)
            OR
            (photo_data IS NOT NULL AND photo_filename IS NOT NULL AND photo_mime_type IS NOT NULL)
        ),

    CONSTRAINT chk_lost_reports_photo_type
        CHECK (
            photo_mime_type IS NULL
            OR photo_mime_type IN ('image/jpeg', 'image/png', 'image/webp')
        )
);

-- Useful indexes are already included in the table definitions.
-- The application checks the schema at startup and at /health.


-- ---------------------------------------------------------------------------
-- Found item reports
-- Faculty / Staff found-item reports are automatically verified and public.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS found_item_reports (
    found_report_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    faculty_staff_account_id VARCHAR(4) NOT NULL,
    item_name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    description VARCHAR(1000) NOT NULL,
    photo_data LONGBLOB NULL,
    photo_filename VARCHAR(255) NULL,
    photo_mime_type VARCHAR(50) NULL,
    found_location VARCHAR(150) NOT NULL,
    date_found DATE NOT NULL,
    status ENUM('verified', 'returned') NOT NULL DEFAULT 'verified',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    one_week_notified_at TIMESTAMP NULL DEFAULT NULL,
    PRIMARY KEY (found_report_id),
    KEY idx_found_reports_staff (faculty_staff_account_id),
    KEY idx_found_reports_created (created_at),
    KEY idx_found_reports_one_week (status, created_at, one_week_notified_at),
    CONSTRAINT fk_found_reports_staff FOREIGN KEY (faculty_staff_account_id)
        REFERENCES faculty_staff (account_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_found_reports_photo_metadata CHECK (
        (photo_data IS NULL AND photo_filename IS NULL AND photo_mime_type IS NULL)
        OR (photo_data IS NOT NULL AND photo_filename IS NOT NULL AND photo_mime_type IS NOT NULL)
    ),
    CONSTRAINT chk_found_reports_photo_type CHECK (
        photo_mime_type IS NULL OR photo_mime_type IN ('image/jpeg', 'image/png', 'image/webp')
    )
);

-- ---------------------------------------------------------------------------
-- Report photo collections
-- Up to five images may be attached to each report. LONGBLOB intentionally
-- leaves file size uncapped at the FoundIT application layer; the practical
-- storage limit is the database/server capacity.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lost_item_photos (
    photo_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    report_id BIGINT UNSIGNED NOT NULL,
    sort_order TINYINT UNSIGNED NOT NULL,
    photo_data LONGBLOB NOT NULL,
    photo_filename VARCHAR(255) NOT NULL,
    photo_mime_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (photo_id),
    UNIQUE KEY uq_lost_report_photo_order (report_id, sort_order),
    KEY idx_lost_report_photos_report (report_id, sort_order),
    CONSTRAINT fk_lost_report_photos_report
        FOREIGN KEY (report_id) REFERENCES lost_item_reports(report_id)
        ON DELETE CASCADE ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS found_item_photos (
    photo_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    found_report_id BIGINT UNSIGNED NOT NULL,
    sort_order TINYINT UNSIGNED NOT NULL,
    photo_data LONGBLOB NOT NULL,
    photo_filename VARCHAR(255) NOT NULL,
    photo_mime_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (photo_id),
    UNIQUE KEY uq_found_report_photo_order (found_report_id, sort_order),
    KEY idx_found_report_photos_report (found_report_id, sort_order),
    CONSTRAINT fk_found_report_photos_report
        FOREIGN KEY (found_report_id) REFERENCES found_item_reports(found_report_id)
        ON DELETE CASCADE ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------------
-- Claim requests
-- Any authenticated Student or Faculty / Staff user may request a claim.
-- Faculty / Staff review every request before it becomes accepted/rejected.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS claim_requests (
    claim_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    found_report_id BIGINT UNSIGNED NOT NULL,
    student_account_id VARCHAR(6) NULL,
    faculty_staff_account_id VARCHAR(4) NULL,
    claim_message VARCHAR(2000) NOT NULL,
    status ENUM('pending', 'accepted', 'rejected', 'returned') NOT NULL DEFAULT 'pending',
    reviewed_by_faculty_staff_id VARCHAR(4) NULL,
    reviewed_at TIMESTAMP NULL DEFAULT NULL,
    returned_by_faculty_staff_id VARCHAR(4) NULL,
    returned_at TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (claim_id),
    KEY idx_claim_requests_found (found_report_id),
    KEY idx_claim_requests_status_created (status, created_at),
    KEY idx_claim_requests_student (student_account_id),
    KEY idx_claim_requests_staff (faculty_staff_account_id),
    KEY idx_claim_requests_returned (status, returned_at),
    UNIQUE KEY uq_claim_requests_found_student (found_report_id, student_account_id),
    UNIQUE KEY uq_claim_requests_found_staff (found_report_id, faculty_staff_account_id),
    CONSTRAINT fk_claim_requests_found FOREIGN KEY (found_report_id)
        REFERENCES found_item_reports (found_report_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_claim_requests_student FOREIGN KEY (student_account_id)
        REFERENCES students (account_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_claim_requests_staff FOREIGN KEY (faculty_staff_account_id)
        REFERENCES faculty_staff (account_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_claim_requests_reviewer FOREIGN KEY (reviewed_by_faculty_staff_id)
        REFERENCES faculty_staff (account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_claim_requests_returner FOREIGN KEY (returned_by_faculty_staff_id)
        REFERENCES faculty_staff (account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT chk_claim_requests_single_requester CHECK (
        (student_account_id IS NOT NULL AND faculty_staff_account_id IS NULL)
        OR (student_account_id IS NULL AND faculty_staff_account_id IS NOT NULL)
    ),
    CONSTRAINT chk_claim_requests_message CHECK (CHAR_LENGTH(TRIM(claim_message)) BETWEEN 20 AND 2000)
);

-- ---------------------------------------------------------------------------
-- Return receipts
-- One official receipt is issued when an approved claim is marked as returned.
-- receipt_no looks like RCP-2026-000001: RCP + year + a sequence that restarts
-- every year. It is stored here (never recalculated) so a printed receipt can
-- always be matched to its database record.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS claim_receipts (
    receipt_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    receipt_no VARCHAR(20) NOT NULL,
    receipt_year SMALLINT UNSIGNED NOT NULL,
    receipt_seq INT UNSIGNED NOT NULL,
    claim_id BIGINT UNSIGNED NOT NULL,
    issued_by_faculty_staff_id VARCHAR(4) NULL,
    issued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (receipt_id),
    UNIQUE KEY uq_claim_receipts_no (receipt_no),
    UNIQUE KEY uq_claim_receipts_year_seq (receipt_year, receipt_seq),
    UNIQUE KEY uq_claim_receipts_claim (claim_id),
    CONSTRAINT fk_claim_receipts_claim FOREIGN KEY (claim_id)
        REFERENCES claim_requests (claim_id) ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_claim_receipts_issuer FOREIGN KEY (issued_by_faculty_staff_id)
        REFERENCES faculty_staff (account_id) ON UPDATE CASCADE ON DELETE SET NULL
);

-- ---------------------------------------------------------------------------
-- Direct messages
-- Faculty / Staff and Administrators can send to any active account by school
-- email. The recipient role is resolved by the server and is not entered by
-- the sender.
-- ---------------------------------------------------------------------------
-- Communication is persistent: messages live in MySQL rather than browser storage.
CREATE TABLE IF NOT EXISTS messages (
    message_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sender_role ENUM('student', 'faculty_staff', 'admin') NOT NULL,
    sender_account_id VARCHAR(6) NOT NULL,
    recipient_role ENUM('student', 'faculty_staff', 'admin') NOT NULL,
    recipient_account_id VARCHAR(6) NOT NULL,
    recipient_email VARCHAR(255) NOT NULL,
    subject VARCHAR(160) NOT NULL,
    body VARCHAR(3000) NOT NULL,
    read_at TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id),
    KEY idx_messages_recipient (recipient_role, recipient_account_id, created_at),
    KEY idx_messages_unread (recipient_role, recipient_account_id, read_at, created_at),
    KEY idx_messages_recipient_email (recipient_email)
);

-- ---------------------------------------------------------------------------
-- Per-account notifications
-- Each row belongs to exactly one signed-in account. Broadcast events create
-- independent rows for each eligible recipient so unread/read state is never
-- shared between users or roles.
-- ---------------------------------------------------------------------------

-- Admin accounts never receive notifications for their own activity.
-- Admin notifications are created only for Student lost reports and Faculty / Staff
-- lost/found reports.
-- Notifications are isolated by recipient role + account ID.
CREATE TABLE IF NOT EXISTS notifications (
    notification_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    recipient_role ENUM('student', 'faculty_staff', 'admin') NOT NULL,
    recipient_account_id VARCHAR(6) NOT NULL,
    notification_type VARCHAR(50) NOT NULL DEFAULT 'system',
    status VARCHAR(20) NOT NULL DEFAULT 'info',
    title VARCHAR(255) NOT NULL,
    message VARCHAR(2000) NOT NULL,
    related_report_id BIGINT UNSIGNED NULL,
    related_claim_id BIGINT UNSIGNED NULL,
    related_message_id BIGINT UNSIGNED NULL,
    read_at TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (notification_id),
    KEY idx_notifications_recipient (recipient_role, recipient_account_id, created_at),
    KEY idx_notifications_unread (recipient_role, recipient_account_id, read_at, created_at),
    KEY idx_notifications_report (related_report_id),
    KEY idx_notifications_claim (related_claim_id),
    KEY idx_notifications_message (related_message_id),
    CONSTRAINT fk_notifications_message FOREIGN KEY (related_message_id)
        REFERENCES messages (message_id) ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_notifications_report FOREIGN KEY (related_report_id)
        REFERENCES lost_item_reports (report_id) ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_notifications_claim FOREIGN KEY (related_claim_id)
        REFERENCES claim_requests (claim_id) ON UPDATE CASCADE ON DELETE SET NULL
);

-- ---------------------------------------------------------------------------
-- Admin audit log
-- Administrative actions are stored in MySQL, not browser localStorage.
-- ---------------------------------------------------------------------------
-- Audit logs provide accountability for administrator actions.
CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    admin_account_id VARCHAR(4) NOT NULL,
    admin_name VARCHAR(161) NOT NULL,
    admin_email VARCHAR(255) NOT NULL,
    action VARCHAR(80) NOT NULL,
    target_type VARCHAR(50) NOT NULL,
    target_id VARCHAR(100) NOT NULL,
    details VARCHAR(2000) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (audit_id),
    KEY idx_audit_created (created_at, audit_id),
    KEY idx_audit_admin (admin_account_id, created_at),
    KEY idx_audit_target (target_type, target_id, created_at)
);

