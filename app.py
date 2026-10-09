"""FoundIT - Campus Lost and Found System.

Authentication is backed by MySQL. Public account creation is available only
for Student and Faculty / Staff accounts. Administrator accounts must be
inserted directly into the database.
"""

import os
import re
import secrets
import sys
from datetime import datetime
from functools import wraps

import mysql.connector
from mysql.connector import Error, IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from flask import Flask, jsonify, redirect, render_template, request, session, url_for


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ========================= BACKEND DEFENSE MAP =========================
# 1. Authentication: identifies the account and verifies its password hash.
# 2. Authorization: role_required() protects Student, Faculty/Staff, and Admin routes.
# 3. Validation: server-side checks reject invalid or incomplete data.
# 4. Database: MySQL stores accounts, reports, claims, messages, notifications,
#    categories, and audit logs so important data survives browser/session changes.
# 5. Ownership: report/message/claim queries include the current account where needed.
# 6. Auditability: important administrator actions are written to audit_logs.
# 7. Error handling: database details stay in the Flask console while users receive
#    understandable messages.
# ======================================================================


def load_env_file(path=None):
    """Load FOUNDIT_* settings from a local .env file (KEY=VALUE per line).

    Real environment variables always win, so run.bat / PowerShell settings keep
    working. This means MySQL credentials no longer depend on how the app was
    launched (IDE run button, double-click, terminal, ...).
    """
    path = path or os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and not os.environ.get(key):
                os.environ[key] = value


def load_secret_key():
    """Use FOUNDIT_SECRET_KEY, else a key stored once in instance/secret_key.

    A random key generated on every start would log everyone out on each
    restart and break multi-process servers.
    """
    configured = os.environ.get("FOUNDIT_SECRET_KEY")
    if configured:
        return configured

    key_path = os.path.join(BASE_DIR, "instance", "secret_key")
    try:
        if os.path.isfile(key_path):
            with open(key_path, encoding="utf-8") as handle:
                stored = handle.read().strip()
            if stored:
                return stored
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        generated = secrets.token_hex(32)
        with open(key_path, "w", encoding="utf-8") as handle:
            handle.write(generated)
        return generated
    except OSError:
        return secrets.token_hex(32)


load_env_file()

# Flask application setup. Configuration is loaded before routes are registered so every
# request uses the same session and database settings.
app = Flask(__name__)
app.secret_key = load_secret_key()

# Lost-item photo uploads are kept in MySQL as MEDIUMBLOB data. Keep uploads small.
# Profile pictures are smaller than report photos and are stored per account.
ALLOWED_PROFILE_PICTURE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PROFILE_PICTURE_BYTES = 2 * 1024 * 1024

EMAIL_NAME_PART = r"[a-z][a-z0-9_-]*"
EMAIL_PATTERNS = {
    "student": re.compile(
        rf"^{EMAIL_NAME_PART}\.{EMAIL_NAME_PART}\.(\d{{6}})\.tc@umindanao\.edu\.ph$",
        re.IGNORECASE,
    ),
    "faculty_staff": re.compile(
        rf"^{EMAIL_NAME_PART}\.{EMAIL_NAME_PART}\.(\d{{4}})\.tc@umindanao\.edu\.ph$",
        re.IGNORECASE,
    ),
    "admin": re.compile(
        rf"^{EMAIL_NAME_PART}\.{EMAIL_NAME_PART}\.(\d{{4}})\.tc@umindanao\.edu\.ph$",
        re.IGNORECASE,
    ),
}

ROLE_INFO = {
    "student": ("student_dashboard", "Student"),
    "faculty_staff": ("faculty_dashboard", "Faculty / Staff"),
    "admin": ("admin_dashboard", "Administrator"),
}

CREATE_ROLES = {"student", "faculty_staff"}

# Authentication data is intentionally separated by account type. These table
# names are application constants, never values supplied by the client.
ACCOUNT_TABLES = {
    "student": "students",
    "faculty_staff": "faculty_staff",
    "admin": "admins",
}


# ---------------------------------------------------------------- Account queries
# These functions read the three separate account tables.
# Keeping account types separate makes the Student, Faculty/Staff, and Admin
# rules easier to enforce and explain during the project defense.
def find_account(email, account_id):
    """Find a login account in the correct account-type table."""
    if len(account_id) == 6:
        return fetch_one(
            """
            SELECT account_id, 'student' AS role, first_name, last_name, email,
                   password_hash, status
            FROM students
            WHERE email = %s AND account_id = %s
            """,
            (email, account_id),
        )

    account = fetch_one(
        """
        SELECT account_id, 'faculty_staff' AS role, first_name, last_name, email,
               password_hash, status
        FROM faculty_staff
        WHERE email = %s AND account_id = %s
        """,
        (email, account_id),
    )
    if account:
        return account

    return fetch_one(
        """
        SELECT account_id, 'admin' AS role, first_name, last_name, email,
               password_hash, status
        FROM admins
        WHERE email = %s AND account_id = %s
        """,
        (email, account_id),
    )


def find_existing_account(account_id, email):
    """Check all three account tables for an existing ID or school email."""
    return fetch_one(
        """
        SELECT account_id, email, 'student' AS role
        FROM students
        WHERE account_id = %s OR email = %s

        UNION ALL

        SELECT account_id, email, 'faculty_staff' AS role
        FROM faculty_staff
        WHERE account_id = %s OR email = %s

        UNION ALL

        SELECT account_id, email, 'admin' AS role
        FROM admins
        WHERE account_id = %s OR email = %s

        LIMIT 1
        """,
        (account_id, email, account_id, email, account_id, email),
    )


# -------------------------------------------------------------------- Database
# All database access is centralized here. Routes call these helpers instead of
# opening connections in many unrelated places. This keeps SQL handling
# consistent and makes connection cleanup easier to audit.
def db_settings():
    """Read the MySQL connection settings (environment variables or .env)."""
    try:
        port = int(os.environ.get("FOUNDIT_DB_PORT") or 3306)
    except ValueError:
        port = 3306
    return {
        "host": os.environ.get("FOUNDIT_DB_HOST") or "127.0.0.1",
        "port": port,
        "user": os.environ.get("FOUNDIT_DB_USER") or "root",
        "password": os.environ.get("FOUNDIT_DB_PASSWORD", "hhyan.sql@01"),
        "database": os.environ.get("FOUNDIT_DB_NAME") or "foundit",
    }


def db_connection():
    """Create a new MySQL connection using the configured settings."""
    return mysql.connector.connect(
        charset="utf8mb4",
        # Without a timeout a wrong host/port makes every page hang.
        connection_timeout=5,
        **db_settings(),
    )


def describe_db_error(exc):
    """Translate a MySQL error into (http_status, actionable_message).

    Full technical details stay in the Flask console; the browser only gets a
    short explanation of what to fix.
    """
    settings = db_settings()
    errno = getattr(exc, "errno", None)

    if errno in {1045, 1698}:
        return 503, (
            f"MySQL rejected the login for user '{settings['user']}'. "
            "Set the correct FOUNDIT_DB_USER and FOUNDIT_DB_PASSWORD in the .env file, then restart the app."
        )
    if errno in {2002, 2003, 2005, 2013, 2055} or (
        errno is None and isinstance(exc, (OSError, mysql.connector.errors.InterfaceError))
    ):
        return 503, (
            f"Cannot reach MySQL at {settings['host']}:{settings['port']}. "
            "Make sure the MySQL server is running and FOUNDIT_DB_HOST / FOUNDIT_DB_PORT are correct."
        )
    if errno == 1049:
        return 503, (
            f"The database '{settings['database']}' does not exist. "
            "Run database/foundit.sql in MySQL Workbench first."
        )
    if errno == 1044:
        # MySQL reports a non-existent database as "access denied" to non-admin users.
        return 503, (
            f"The database '{settings['database']}' does not exist, or user '{settings['user']}' "
            "is not allowed to use it. Run database/foundit.sql and check FOUNDIT_DB_NAME / FOUNDIT_DB_USER."
        )
    if errno in {1146, 1054, 1136}:
        return 500, (
            "The database tables are missing or outdated. "
            "Run database/foundit.sql (see Troubleshooting in the README)."
        )
    if errno == 3819:
        return 400, "The database rejected the data because it breaks a table rule (check the ID and school email)."
    if errno in {1406, 1264, 1265}:
        return 400, "One of the values is too long or invalid for the database."
    if errno == 1452:
        return 409, (
            "The report could not be saved because the logged-in account is not present "
            "in the clean database. Log out, create/login with an account from this database, and try again."
        )
    if errno == 1451:
        return 409, "This record is linked to other data, so the database did not allow the change."
    return 500, "A database operation failed. Check the Flask console for the exact MySQL error."


def fetch_one(query, params=()):
    # Fetch one row and always close the cursor/connection.
    connection = db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(query, params)
        return cursor.fetchone()
    finally:
        cursor.close()
        connection.close()


def fetch_all(query, params=()):
    # Fetch multiple rows; values stay parameterized instead of being concatenated into SQL.
    connection = db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


def execute_write(query, params=()):
    # Write data in one transaction. A failed write is rolled back.
    connection = db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(query, params)
        connection.commit()
        return cursor.rowcount
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


# ------------------------------------------------------------- Profile pictures
# Profile pictures are account-owned personalization data. They are stored in
# the same account table as the user so a picture follows the account across
# browsers and sessions without using localStorage.
PROFILE_FIELDS = {
    "profile_picture": "MEDIUMBLOB NULL AFTER updated_at",
    "profile_picture_mime_type": "VARCHAR(50) NULL AFTER profile_picture",
}

def ensure_profile_fields():
    """Add profile-picture columns to older Student/Faculty/Admin databases."""
    connection = db_connection()
    cursor = connection.cursor()
    try:
        for table_name in ACCOUNT_TABLES.values():
            for column_name, definition in PROFILE_FIELDS.items():
                cursor.execute(
                    """
                    SELECT COUNT(*) AS column_count
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = %s
                      AND column_name = %s
                    """,
                    (table_name, column_name),
                )
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def current_account_table():
    """Return the trusted account table for the signed-in role."""
    user = session.get("user")
    if not user or user.get("role") not in ACCOUNT_TABLES:
        return None
    return ACCOUNT_TABLES[user["role"]]



# --------------------------------------------------------- Report photo storage
# Report photos are kept separately from the report record so one report can
# hold up to five images without duplicating the report's other fields. The
# legacy photo_* columns remain as the primary-photo compatibility layer for
# older records and existing API consumers.
REPORT_PHOTO_TABLES_SQL = {
    "lost": """
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    "found": """
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
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
}


def ensure_report_photo_tables():
    """Create multi-photo storage tables for new and existing installations."""
    connection = db_connection()
    cursor = connection.cursor()
    try:
        for statement in REPORT_PHOTO_TABLES_SQL.values():
            cursor.execute(statement)
        for table_name in ("lost_item_reports", "found_item_reports"):
            cursor.execute(
                """
                SELECT COLUMN_TYPE
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = %s
                  AND column_name = 'photo_data'
                """,
                (table_name,),
            )
            row = cursor.fetchone()
            if row and str(row[0]).lower() == "mediumblob":
                cursor.execute(f"ALTER TABLE {table_name} MODIFY COLUMN photo_data LONGBLOB NULL")
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def prepare_report_photos(files, field_name="photo"):
    """Validate up to five report images and return normalized binary records."""
    uploaded = [file for file in files if file and file.filename]
    if len(uploaded) > 5:
        raise ValueError("You can upload a maximum of 5 images per report.")

    signatures = {
        "image/jpeg": lambda data: data[:3] == b"\xff\xd8\xff",
        "image/png": lambda data: data[:8] == b"\x89PNG\r\n\x1a\n",
        "image/webp": lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }
    photos = []
    for file in uploaded:
        data = file.read()
        if not data:
            raise ValueError("One of the selected images is empty.")

        detected_type = None
        for mime_type, check in signatures.items():
            if check(data):
                detected_type = mime_type
                break
        if detected_type is None:
            raise ValueError(f"{file.filename} is not a valid PNG, JPG, JPEG, or WEBP image.")

        filename = secure_filename(file.filename)[:255] or f"{field_name}-photo"
        photos.append({
            "data": data,
            "filename": filename,
            "mime_type": detected_type,
        })
    return photos


def store_report_photos(cursor, report_type, report_id, photos):
    """Store all selected images and mirror the first image into legacy columns."""
    if not photos:
        return
    table = "lost_item_photos" if report_type == "lost" else "found_item_photos"
    id_column = "report_id" if report_type == "lost" else "found_report_id"
    for sort_order, photo in enumerate(photos, start=1):
        cursor.execute(
            f"""
            INSERT INTO {table} ({id_column}, sort_order, photo_data, photo_filename, photo_mime_type)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (report_id, sort_order, photo["data"], photo["filename"], photo["mime_type"]),
        )


def report_photo_urls(report_type, report_id, has_photo=False):
    """Return all stored photo URLs, falling back to the legacy primary photo."""
    table = "lost_item_photos" if report_type == "lost" else "found_item_photos"
    id_column = "report_id" if report_type == "lost" else "found_report_id"
    endpoint = "lost_report_extra_photo" if report_type == "lost" else "found_report_extra_photo"
    rows = fetch_all(
        f"SELECT photo_id FROM {table} WHERE {id_column} = %s ORDER BY sort_order, photo_id",
        (report_id,),
    )
    urls = [url_for(endpoint, photo_id=row["photo_id"], **({"report_id": report_id} if report_type == "lost" else {"found_report_id": report_id})) for row in rows]
    if not urls and has_photo:
        primary_endpoint = "lost_report_photo" if report_type == "lost" else "found_report_photo"
        kwargs = {"report_id": report_id} if report_type == "lost" else {"found_report_id": report_id}
        urls.append(url_for(primary_endpoint, **kwargs))
    return urls

# ------------------------------------------------------------- Categories
# Categories are master data: administrators maintain the list in MySQL,
# while report forms only display active records. This removes the old
# hardcoded category list from the application logic.
CATEGORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS categories (
    category_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(50) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (category_id),
    UNIQUE KEY uq_categories_name (name),
    KEY idx_categories_active_order (is_active, sort_order, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def ensure_category_table():
    """Create the category master table when the database has not created it yet."""
    connection = db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(CATEGORY_TABLE_SQL)
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def category_validation_error(category):
    # Important defense point: the browser is not trusted. The server checks
    # the submitted category against the active database master list.
    """Return a validation tuple when a category is missing or inactive."""
    category = str(category or "").strip()
    if not category:
        return "category", "Select an item category."
    if len(category) > 50:
        return "category", "Category must not exceed 50 characters."
    try:
        row = fetch_one(
            """
            SELECT category_id, name
            FROM categories
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
              AND is_active = TRUE
            LIMIT 1
            """,
            (category,),
        )
    except Error:
        return "category", "The category list is temporarily unavailable. Please try again."
    if not row:
        return "category", "Select a valid active item category."
    return None


# ------------------------------------------------------------- Messages
# Messages are stored in MySQL instead of browser localStorage. The database
# therefore becomes the source of truth and messages stay available after
# logout, browser changes, or application restarts.
MESSAGE_TABLE_SQL = """
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

def ensure_message_table():
    """Create the message table when the clean schema has not created it yet."""
    connection = db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(MESSAGE_TABLE_SQL)
        connection.commit()
    finally:
        cursor.close()
        connection.close()


# ------------------------------------------------------------- Post-age tracking
# The public feed already shows the report's Posted date (created_at).
# These fields only remember whether the seven-day reminder has already been sent.
POST_AGE_COLUMNS_SQL = {
    "one_week_notified_at": "TIMESTAMP NULL DEFAULT NULL AFTER updated_at",
}


def ensure_post_age_fields():
    """Add one-week reminder tracking fields to older report databases safely."""
    connection = db_connection()
    cursor = connection.cursor()
    try:
        for table_name in ("lost_item_reports", "found_item_reports"):
            for column_name, definition in POST_AGE_COLUMNS_SQL.items():
                cursor.execute(
                    """
                    SELECT COUNT(*) AS column_count
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = %s
                      AND column_name = %s
                    """,
                    (table_name, column_name),
                )
                if cursor.fetchone()[0] == 0:
                    cursor.execute(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                    )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def run_one_week_post_notifications():
    """Notify active Faculty / Staff once when an unresolved post reaches seven days."""
    ensure_post_age_fields()

    connection = db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        # Lock candidate rows so two Faculty / Staff polling requests cannot
        # send the same seven-day reminder at the same time.
        cursor.execute(
            """
            SELECT report_id AS item_id, 'lost' AS item_type, item_name, last_seen AS location,
                   created_at
            FROM lost_item_reports
            WHERE status = 'verified'
              AND one_week_notified_at IS NULL
              AND created_at <= CURRENT_TIMESTAMP - INTERVAL 7 DAY
            FOR UPDATE
            """
        )
        lost_items = cursor.fetchall()

        cursor.execute(
            """
            SELECT found_report_id AS item_id, 'found' AS item_type, item_name, found_location AS location,
                   created_at
            FROM found_item_reports
            WHERE status = 'verified'
              AND one_week_notified_at IS NULL
              AND created_at <= CURRENT_TIMESTAMP - INTERVAL 7 DAY
            FOR UPDATE
            """
        )
        found_items = cursor.fetchall()

        items = lost_items + found_items
        if not items:
            connection.commit()
            return 0

        # Capture the active Faculty / Staff recipients inside the same
        # transaction. Each account receives its own isolated notification row.
        cursor.execute("SELECT account_id FROM faculty_staff WHERE status = 'active'")
        recipients = [str(row["account_id"]) for row in cursor.fetchall()]
        if not recipients:
            connection.commit()
            return 0

        created_count = 0
        for item in items:
            item_label = "Lost item" if item["item_type"] == "lost" else "Found item"
            title = f"{item_label} posted for 1 week"
            message = (
                f"{item_label} “{item['item_name']}” has been posted on FoundIT for 1 week "
                f"and is still unresolved. Location: {item['location']}. "
                f"Posted: {item['created_at'].strftime('%B %d, %Y').replace(' 0', ' ') if hasattr(item['created_at'], 'strftime') else item['created_at']}."
            )

            # The lost-report relationship can be linked directly. Found-item
            # notifications use the item ID in the message because the current
            # notification schema intentionally links reports to lost reports.
            related_report_id = item["item_id"] if item["item_type"] == "lost" else None
            cursor.executemany(
                """
                INSERT INTO notifications (
                    recipient_role, recipient_account_id, notification_type, status,
                    title, message, related_report_id
                ) VALUES ('faculty_staff', %s, 'item-aged-one-week', 'warning', %s, %s, %s)
                """,
                [(recipient, title, message, related_report_id) for recipient in recipients],
            )
            created_count += len(recipients)

            if item["item_type"] == "lost":
                cursor.execute(
                    """
                    UPDATE lost_item_reports
                    SET one_week_notified_at = CURRENT_TIMESTAMP
                    WHERE report_id = %s AND one_week_notified_at IS NULL
                    """,
                    (item["item_id"],),
                )
            else:
                cursor.execute(
                    """
                    UPDATE found_item_reports
                    SET one_week_notified_at = CURRENT_TIMESTAMP
                    WHERE found_report_id = %s AND one_week_notified_at IS NULL
                    """,
                    (item["item_id"],),
                )

        connection.commit()
        return created_count
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


# ------------------------------------------------------------- Notifications
# Every notification belongs to one exact role + account ID. This prevents
# Student A from seeing Student B's notifications.
NOTIFICATION_TABLE_SQL = """
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
    CONSTRAINT fk_notifications_report
        FOREIGN KEY (related_report_id) REFERENCES lost_item_reports(report_id)
        ON UPDATE CASCADE ON DELETE SET NULL,
    CONSTRAINT fk_notifications_claim
        FOREIGN KEY (related_claim_id) REFERENCES claim_requests(claim_id)
        ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

def ensure_notification_table():
    """Create notification support tables and repair one legacy column if needed."""
    ensure_message_table()
    connection = db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(NOTIFICATION_TABLE_SQL)
        cursor.execute(
            """
            SELECT COUNT(*) AS column_count
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'notifications'
              AND column_name = 'related_message_id'
            """
        )
        column_exists = cursor.fetchone()[0] > 0
        if not column_exists:
            cursor.execute(
                "ALTER TABLE notifications ADD COLUMN related_message_id BIGINT UNSIGNED NULL AFTER related_claim_id"
            )
            cursor.execute(
                "ALTER TABLE notifications ADD KEY idx_notifications_message (related_message_id)"
            )
            cursor.execute(
                "ALTER TABLE notifications ADD CONSTRAINT fk_notifications_message "
                "FOREIGN KEY (related_message_id) REFERENCES messages(message_id) "
                "ON UPDATE CASCADE ON DELETE SET NULL"
            )
        # Legacy versions stored an admin's own actions as notifications.
        # Those are never valid user-facing admin notifications.
        cursor.execute(
            "DELETE FROM notifications WHERE recipient_role = 'admin' AND notification_type = 'admin-action'"
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def create_notification(recipient_role, recipient_account_id, title, message, *,
                        notification_type="system", status="info",
                        related_report_id=None, related_claim_id=None):
    """Create one notification for exactly one account."""
    # The recipient is explicit, so notification ownership is enforced at the database query level.
    ensure_notification_table()
    return execute_write(
        """
        INSERT INTO notifications (
            recipient_role, recipient_account_id, notification_type, status,
            title, message, related_report_id, related_claim_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (recipient_role, str(recipient_account_id), notification_type, status,
         title, message, related_report_id, related_claim_id),
    )


def notify_active_role(role, title, message, *, notification_type="system", status="info",
                       related_report_id=None, related_claim_id=None):
    """Create independent notification records for every active account in a role."""
    ensure_notification_table()
    table = ACCOUNT_TABLES[role]
    accounts = fetch_all(
        f"SELECT account_id FROM {table} WHERE status = 'active'"
    )
    if not accounts:
        return 0

    connection = db_connection()
    cursor = connection.cursor()
    try:
        cursor.executemany(
            """
            INSERT INTO notifications (
                recipient_role, recipient_account_id, notification_type, status,
                title, message, related_report_id, related_claim_id
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            [
                (role, str(row["account_id"]), notification_type, status, title, message,
                 related_report_id, related_claim_id)
                for row in accounts
            ],
        )
        connection.commit()
        return cursor.rowcount
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def serialize_notification(row):
    return {
        "id": row["notification_id"],
        "type": row["notification_type"],
        "status": row["status"],
        "title": row["title"],
        "message": row["message"],
        "read": row["read_at"] is not None,
        "createdAt": row["created_at"].isoformat(),
        "readAt": row["read_at"].isoformat() if row["read_at"] else None,
        "relatedReportId": row["related_report_id"],
        "relatedClaimId": row["related_claim_id"],
        "relatedMessageId": row.get("related_message_id"),
    }


# ---------------------------------------------------------------- Audit Log
# Audit records answer the defense question: "Who changed what, and when?"
# They are written server-side so the browser cannot simply edit its own log.
AUDIT_TABLE_SQL = """
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

def ensure_audit_table():
    connection = db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(AUDIT_TABLE_SQL)
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def create_audit_log(action, target_type, target_id, details):
    """Record a server-side administrative action in MySQL."""
    user = session.get("user")
    if not user or user.get("role") != "admin":
        raise ValueError("Only an authenticated administrator can create audit entries.")
    ensure_audit_table()
    return execute_write(
        """
        INSERT INTO audit_logs (admin_account_id, admin_name, admin_email, action, target_type, target_id, details)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        """,
        (str(user["account_id"]), user["name"], user["email"],
         str(action).strip(), str(target_type).strip(), str(target_id).strip(), str(details).strip()),
    )


# ---------------------------------------------------------------- Validation
# Input validation is performed on the server even when the frontend also
# validates fields. Frontend validation improves usability; backend validation
# protects the database and business rules.
def json_body():
    """Return the JSON request body as a dict (never None or a list)."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def normalize_email(email):
    return str(email or "").strip().lower()


def valid_password(password):
    password = str(password or "")
    return len(password) >= 8 and re.search(r"[^A-Za-z0-9]", password) is not None


def parse_school_email(email, expected_role=None):
    """Return the expected registration role and numeric ID from the email."""
    email = normalize_email(email)

    if expected_role:
        pattern = EMAIL_PATTERNS.get(expected_role)
        match = pattern.fullmatch(email) if pattern else None
        return (expected_role, match.group(1)) if match else (None, None)

    # Login only needs to know the numeric ID. The database is the authority
    # for the account's actual role, so a 4-digit email can belong to either
    # Faculty / Staff or an Administrator.
    student_match = EMAIL_PATTERNS["student"].fullmatch(email)
    if student_match:
        return "student", student_match.group(1)

    four_digit_match = re.fullmatch(
        rf"{EMAIL_NAME_PART}\.{EMAIL_NAME_PART}\.(\d{{4}})\.tc@umindanao\.edu\.ph",
        email,
        re.IGNORECASE,
    )
    if four_digit_match:
        return None, four_digit_match.group(1)

    return None, None


def validate_registration(data):
    role = str(data.get("role", "")).strip().lower()
    school_id = str(data.get("school_id", "")).strip()
    first_name = str(data.get("first_name", "")).strip()
    last_name = str(data.get("last_name", "")).strip()
    email = normalize_email(data.get("email", ""))
    password = str(data.get("password", ""))
    confirm_password = str(data.get("confirm_password", ""))

    if role not in CREATE_ROLES:
        return "role", "Only Student and Faculty / Staff accounts can be created here."

    required_digits = 6 if role == "student" else 4
    if not re.fullmatch(rf"\d{{{required_digits}}}", school_id):
        label = "Student ID" if role == "student" else "Faculty / Staff ID"
        return "school_id", f"{label} must contain exactly {required_digits} digits."

    email_role, email_id = parse_school_email(email, role)
    if not email_role:
        example = "firstname.lastname.123456.tc@umindanao.edu.ph" if role == "student" else "firstname.lastname.1234.tc@umindanao.edu.ph"
        return "email", f"Use your school email in this format: {example}"

    if email_id != school_id:
        return "email", "The ID in the school email must exactly match the account ID."

    if not first_name:
        return "first_name", "Enter your first name."

    if not last_name:
        return "last_name", "Enter your last name."

    if not valid_password(password):
        return "password", "Password must contain at least 8 characters and at least one symbol."

    if password != confirm_password:
        return "confirm_password", "Passwords do not match."

    return None


def account_view(account):
    """Convert a database account row into the session-safe user object."""
    role = account["role"]
    endpoint, label = ROLE_INFO[role]
    first = account["first_name"]
    last = account["last_name"]

    return {
        "account_id": account["account_id"],
        "role": role,
        "role_label": label,
        "email": account["email"],
        "name": f"{first} {last}",
        "initials": (first[:1] + last[:1]).upper(),
        "status": account["status"],
        "dashboard": endpoint,
    }


def database_error_response(exc=None):
    """Return an actionable database error without exposing raw SQL details."""
    exc = exc or sys.exc_info()[1]
    app.logger.error("Database operation failed: %s", exc, exc_info=True)
    status, message = describe_db_error(exc)
    return jsonify(message=message), status


REQUIRED_SCHEMA = {
    "students": {
        "account_id", "first_name", "last_name", "email", "password_hash",
        "status", "created_at", "updated_at", "profile_picture", "profile_picture_mime_type",
    },
    "faculty_staff": {
        "account_id", "first_name", "last_name", "email", "password_hash",
        "status", "created_at", "updated_at", "profile_picture", "profile_picture_mime_type",
    },
    "admins": {
        "account_id", "first_name", "last_name", "email", "password_hash",
        "status", "created_at", "updated_at", "profile_picture", "profile_picture_mime_type",
    },
    "categories": {"category_id", "name", "is_active", "sort_order", "created_at", "updated_at"},
    "lost_item_reports": {
        "report_id", "student_account_id", "faculty_staff_account_id",
        "item_name", "category", "description", "photo_data", "photo_filename",
        "photo_mime_type", "last_seen", "date_lost", "status",
        "reviewed_by_faculty_staff_id", "reviewed_at", "one_week_notified_at", "created_at", "updated_at",
    },
    "lost_item_photos": {"photo_id", "report_id", "sort_order", "photo_data", "photo_filename", "photo_mime_type", "created_at"},
    "found_item_photos": {"photo_id", "found_report_id", "sort_order", "photo_data", "photo_filename", "photo_mime_type", "created_at"},
    "found_item_reports": {
        "found_report_id", "faculty_staff_account_id", "item_name", "category",
        "description", "photo_data", "photo_filename", "photo_mime_type",
        "found_location", "date_found", "status", "one_week_notified_at", "created_at", "updated_at",
    },
    "claim_requests": {
        "claim_id", "found_report_id", "student_account_id", "faculty_staff_account_id",
        "claim_message", "status", "reviewed_by_faculty_staff_id", "reviewed_at",
        "returned_by_faculty_staff_id", "returned_at", "created_at", "updated_at",
    },
    "claim_receipts": {
        "receipt_id", "receipt_no", "receipt_year", "receipt_seq", "claim_id",
        "issued_by_faculty_staff_id", "issued_at",
    },
    "notifications": {
        "notification_id", "recipient_role", "recipient_account_id", "notification_type",
        "status", "title", "message", "related_report_id", "related_claim_id",
        "read_at", "created_at",
    },
    "audit_logs": {
        "audit_id", "admin_account_id", "admin_name", "admin_email", "action",
        "target_type", "target_id", "details", "created_at",
    },
}


def check_database():
    """Return a list of human-readable problems (empty list = database is OK)."""
    try:
        columns = fetch_all(
            """
            SELECT table_name AS tbl, column_name AS col
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
            """
        )
    except Error as exc:
        return [describe_db_error(exc)[1]]

    found = {}
    for row in columns:
        found.setdefault(str(row["tbl"]).lower(), set()).add(str(row["col"]).lower())

    problems = []
    for table, required in REQUIRED_SCHEMA.items():
        if table not in found:
            problems.append(f"Table '{table}' is missing. Run database/foundit.sql.")
            continue
        missing = sorted(required - found[table])
        if missing:
            problems.append(
                f"Table '{table}' is outdated (missing columns: {', '.join(missing)}). "
                "It was probably created by an older script. Back up your data, then re-create it with database/foundit.sql."
            )
    return problems


# ---------------------------------------------------------------- Authorization
# Authentication identifies the user; authorization decides whether that user
# is allowed to call a specific route. The decorators below provide that second layer.
def wants_json():
    """API calls expect JSON, never an HTML redirect to the login page."""
    return request.path.startswith("/api/")


def not_signed_in():
    if wants_json():
        return jsonify(message="Your session has expired. Please log in again."), 401
    return redirect(url_for("login"))


def wrong_role(user):
    if wants_json():
        return jsonify(message="Your account is not allowed to use this feature."), 403
    return redirect(url_for(ROLE_INFO[user["role"]][0]))


def role_required(*roles):
    """Allow a dashboard/API only to signed-in users with an allowed role."""
    allowed_roles = set(roles)

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = session.get("user")
            if not user:
                return not_signed_in()

            if user["role"] not in allowed_roles:
                return wrong_role(user)

            return view(*args, **kwargs)

        return wrapper

    return decorator


# ------------------------------------------------------------------- Profile API
@app.get("/api/profile")
@role_required("student", "faculty_staff", "admin")
def get_profile():
    """Return the signed-in account's safe profile details."""
    table = current_account_table()
    user = session["user"]
    try:
        account = fetch_one(
            f"""
            SELECT account_id, first_name, last_name, email, status, created_at,
                   profile_picture IS NOT NULL AS has_profile_picture
            FROM {table}
            WHERE account_id = %s
            """,
            (str(user["account_id"]),),
        )
    except Error:
        return database_error_response()
    if not account:
        return jsonify(message="Your account could not be found."), 404

    role = user["role"]
    return jsonify(profile={
        "accountId": str(account["account_id"]),
        "fullName": f"{account['first_name']} {account['last_name']}",
        "firstName": account["first_name"],
        "lastName": account["last_name"],
        "email": account["email"],
        "role": ROLE_INFO[role][1],
        "status": account["status"].title(),
        "createdAt": account["created_at"].isoformat() if account["created_at"] else None,
        "hasProfilePicture": bool(account["has_profile_picture"]),
    })


@app.get("/api/profile/photo")
@role_required("student", "faculty_staff", "admin")
def get_profile_photo():
    """Return only the current user's profile picture; never expose another account's image."""
    table = current_account_table()
    user = session["user"]
    try:
        account = fetch_one(
            f"SELECT profile_picture, profile_picture_mime_type FROM {table} WHERE account_id = %s",
            (str(user["account_id"]),),
        )
    except Error:
        return database_error_response()
    if not account or not account["profile_picture"]:
        return jsonify(message="No profile picture has been uploaded."), 404

    from flask import Response
    response = Response(account["profile_picture"], mimetype=account["profile_picture_mime_type"] or "image/jpeg")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/profile/photo")
@role_required("student", "faculty_staff", "admin")
def upload_profile_photo():
    """Replace the signed-in user's profile picture with a validated image."""
    uploaded = request.files.get("profile_picture")
    if not uploaded or not uploaded.filename:
        return jsonify(message="Choose a profile picture first."), 400

    mime_type = (uploaded.mimetype or "").lower().strip()
    if mime_type not in ALLOWED_PROFILE_PICTURE_TYPES:
        return jsonify(message="Profile picture must be a JPG, PNG, or WEBP image."), 400

    data = uploaded.read(MAX_PROFILE_PICTURE_BYTES + 1)
    if not data:
        return jsonify(message="The selected profile picture is empty."), 400
    if len(data) > MAX_PROFILE_PICTURE_BYTES:
        return jsonify(message="Profile picture must not exceed 2 MB."), 413

    # Basic file-signature validation prevents a renamed non-image file from
    # being stored just because its browser MIME type claimed to be an image.
    signatures = {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not signatures.get(mime_type, False):
        return jsonify(message="The uploaded file does not appear to be a valid image."), 400

    table = current_account_table()
    user = session["user"]
    try:
        connection = db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""UPDATE {table}
                SET profile_picture = %s, profile_picture_mime_type = %s
                WHERE account_id = %s""",
            (data, mime_type, str(user["account_id"])),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return jsonify(message="Your account could not be updated."), 404
        connection.commit()
    except Error:
        try:
            connection.rollback()
        except Exception:
            pass
        return database_error_response()
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass

    return jsonify(message="Profile picture updated successfully.", profilePictureUrl=url_for("get_profile_photo")), 200


# Publicly reusable inside the signed-in dashboards: forms call this endpoint
# to load the current active category master list.
# ------------------------------------------------------------------- Category API
# Public report forms only need active categories. Admin routes below manage the full list.
@app.get("/api/categories")
@role_required("student", "faculty_staff", "admin")
def get_categories():
    """Return the active category master list used by report forms."""
    try:
        categories = fetch_all(
            """
            SELECT category_id, name, sort_order
            FROM categories
            WHERE is_active = TRUE
            ORDER BY sort_order ASC, name ASC, category_id ASC
            """
        )
    except Error:
        return database_error_response()
    return jsonify(
        categories=[
            {"id": row["category_id"], "name": row["name"], "sortOrder": row["sort_order"]}
            for row in categories
        ]
    )


@app.get("/api/admin/categories")
@role_required("admin")
def admin_categories():
    """Return all categories so administrators can maintain the master list."""
    try:
        categories = fetch_all(
            """
            SELECT category_id, name, is_active, sort_order, created_at, updated_at
            FROM categories
            ORDER BY sort_order ASC, name ASC, category_id ASC
            """
        )
    except Error:
        return database_error_response()
    return jsonify(
        categories=[
            {
                "id": row["category_id"],
                "name": row["name"],
                "isActive": bool(row["is_active"]),
                "sortOrder": row["sort_order"],
                "createdAt": row["created_at"].isoformat(),
                "updatedAt": row["updated_at"].isoformat(),
            }
            for row in categories
        ]
    )


# Admin-only category maintenance: create a new master-list record.
@app.post("/api/admin/categories")
@role_required("admin")
def admin_create_category():
    data = json_body()
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify(message="Category name is required."), 400
    if len(name) > 50:
        return jsonify(message="Category name must not exceed 50 characters."), 400

    try:
        duplicate = fetch_one(
            "SELECT category_id FROM categories WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s)) LIMIT 1",
            (name,),
        )
        if duplicate:
            return jsonify(message="That category already exists."), 409
        sort_order = int(data.get("sortOrder", 0) or 0)
        if sort_order < 0:
            raise ValueError
        connection = db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO categories (name, is_active, sort_order) VALUES (%s, TRUE, %s)",
            (name, sort_order),
        )
        category_id = cursor.lastrowid
        connection.commit()
    except ValueError:
        if "connection" in locals(): connection.rollback()
        return jsonify(message="Sort order must be a non-negative whole number."), 400
    except IntegrityError:
        if "connection" in locals(): connection.rollback()
        return jsonify(message="That category already exists."), 409
    except Error:
        if "connection" in locals(): connection.rollback()
        return database_error_response()
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals(): connection.close()

    create_audit_log("CREATE CATEGORY", "category", category_id, f"Created category: {name}.")
    return jsonify(message="Category created.", category={"id": category_id, "name": name, "isActive": True, "sortOrder": sort_order}), 201


# Admin-only category maintenance: rename, reorder, or activate/deactivate.
@app.patch("/api/admin/categories/<int:category_id>")
@role_required("admin")
def admin_update_category(category_id):
    data = json_body()
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify(message="Category name is required."), 400
    if len(name) > 50:
        return jsonify(message="Category name must not exceed 50 characters."), 400

    is_active = data.get("isActive")
    if not isinstance(is_active, bool):
        return jsonify(message="Category status must be active or inactive."), 400
    try:
        sort_order = int(data.get("sortOrder", 0) or 0)
    except (TypeError, ValueError):
        return jsonify(message="Sort order must be a non-negative whole number."), 400
    if sort_order < 0:
        return jsonify(message="Sort order must be a non-negative whole number."), 400

    try:
        category = fetch_one(
            "SELECT category_id, name, is_active, sort_order FROM categories WHERE category_id=%s",
            (category_id,),
        )
        if not category:
            return jsonify(message="Category not found."), 404
        duplicate = fetch_one(
            """
            SELECT category_id FROM categories
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s)) AND category_id <> %s
            LIMIT 1
            """,
            (name, category_id),
        )
        if duplicate:
            return jsonify(message="That category name is already in use."), 409
        execute_write(
            """
            UPDATE categories
            SET name=%s, is_active=%s, sort_order=%s
            WHERE category_id=%s
            """,
            (name, is_active, sort_order, category_id),
        )
    except Error:
        return database_error_response()

    action = "ACTIVATE CATEGORY" if is_active and not category["is_active"] else "DEACTIVATE CATEGORY" if not is_active and category["is_active"] else "EDIT CATEGORY"
    create_audit_log(action, "category", category_id, f"Category changed from “{category['name']}” to “{name}”; status: {'active' if is_active else 'inactive'}.")
    return jsonify(message="Category updated.")


# Returns only messages addressed to the currently signed-in account.
# ---------------------------------------------------------------- Messaging API
# Messages are database records. Every read/delete/reply operation is checked against
# the signed-in recipient so the browser cannot access another account's messages.
@app.get("/api/messages")
@role_required("student", "faculty_staff", "admin")
def get_messages():
    try:
        ensure_message_table()
        rows = fetch_all(
            """
            SELECT m.message_id, m.sender_role, m.sender_account_id, m.recipient_role,
                   m.recipient_account_id, m.recipient_email, m.subject, m.body,
                   m.read_at, m.created_at,
                   COALESCE(s.first_name, f.first_name, a.first_name) AS sender_first_name,
                   COALESCE(s.last_name, f.last_name, a.last_name) AS sender_last_name,
                   COALESCE(s.email, f.email, a.email) AS sender_email
            FROM messages m
            LEFT JOIN students s
              ON m.sender_role = 'student' AND s.account_id = m.sender_account_id
            LEFT JOIN faculty_staff f
              ON m.sender_role = 'faculty_staff' AND f.account_id = m.sender_account_id
            LEFT JOIN admins a
              ON m.sender_role = 'admin' AND a.account_id = m.sender_account_id
            WHERE m.recipient_role = %s
              AND m.recipient_account_id = %s
            ORDER BY m.created_at DESC, m.message_id DESC
            LIMIT 500
            """,
            (session["user"]["role"], str(session["user"]["account_id"])),
        )
    except Error:
        return database_error_response()

    return jsonify(messages=[
        {
            "id": row["message_id"],
            "senderRole": ROLE_INFO[row["sender_role"]][1],
            "senderName": " ".join(filter(None, [row["sender_first_name"], row["sender_last_name"]])) or "User",
            "senderEmail": row["sender_email"],
            "subject": row["subject"],
            "body": row["body"],
            "createdAt": row["created_at"].isoformat(),
            "read": row["read_at"] is not None,
            "readAt": row["read_at"].isoformat() if row["read_at"] else None,
        } for row in rows
    ])


# Direct compose is intentionally limited to Faculty/Staff and Admin.
@app.post("/api/messages")
@role_required("faculty_staff", "admin")
def send_message():
    data = json_body()
    recipient_email = normalize_email(data.get("recipient_email"))
    subject = str(data.get("subject", "")).strip()
    body = str(data.get("body", "")).strip()

    if not recipient_email or not re.fullmatch(
        rf"{EMAIL_NAME_PART}\.{EMAIL_NAME_PART}\.(?:\d{{4}}|\d{{6}})\.tc@umindanao\.edu\.ph",
        recipient_email, re.IGNORECASE
    ):
        return jsonify(message="Enter a valid UMindanao school email address."), 400
    if not subject or len(subject) > 160:
        return jsonify(message="Subject is required and must not exceed 160 characters."), 400
    if not body or len(body) > 3000:
        return jsonify(message="Message is required and must not exceed 3,000 characters."), 400

    try:
        recipients = fetch_all(
            """
            SELECT account_id, 'student' AS role, first_name, last_name, email, status
            FROM students WHERE LOWER(email) = %s
            UNION ALL
            SELECT account_id, 'faculty_staff' AS role, first_name, last_name, email, status
            FROM faculty_staff WHERE LOWER(email) = %s
            UNION ALL
            SELECT account_id, 'admin' AS role, first_name, last_name, email, status
            FROM admins WHERE LOWER(email) = %s
            """,
            (recipient_email, recipient_email, recipient_email),
        )
        recipients = [row for row in recipients if row["status"] == "active"]
        if not recipients:
            return jsonify(message="No active account was found for that email address."), 404
        if len(recipients) > 1:
            return jsonify(message="That email is linked to more than one account. Contact an administrator."), 409

        recipient = recipients[0]
        sender = session["user"]
        if recipient["role"] == sender["role"] and str(recipient["account_id"]) == str(sender["account_id"]):
            return jsonify(message="You cannot send a message to your own account."), 409

        ensure_notification_table()
        connection = db_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO messages (sender_role, sender_account_id, recipient_role,
                                      recipient_account_id, recipient_email, subject, body)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (sender["role"], str(sender["account_id"]), recipient["role"],
                 str(recipient["account_id"]), recipient["email"], subject, body),
            )
            message_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO notifications (recipient_role, recipient_account_id,
                    notification_type, status, title, message, related_message_id)
                VALUES (%s,%s,'message','info',%s,%s,%s)
                """,
                (recipient["role"], str(recipient["account_id"]),
                 f"New message from {sender['name']}",
                 f"You received a new message: “{subject}”.", message_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
    except Error:
        return database_error_response()

    if sender["role"] == "admin":
        try:
            create_audit_log(
                "SEND MESSAGE", "message", message_id,
                f"Sent a message to {recipient['email']}: {subject}.",
            )
        except Error:
            # The message was already committed successfully. Keep the user-facing
            # send operation successful even if audit persistence has a temporary issue.
            app.logger.exception("Could not record admin message audit entry.")

    return jsonify(message="Message sent successfully."), 201


# Reply is available to every role, but the original sender is resolved
# from the database instead of trusting a recipient ID supplied by the browser.
@app.post("/api/messages/<int:message_id>/reply")
@role_required("student", "faculty_staff", "admin")
def reply_to_message(message_id):
    """Reply directly to the user who sent this message. The recipient is resolved server-side."""
    data = json_body()
    body = str(data.get("body", "")).strip()
    if not body or len(body) > 3000:
        return jsonify(message="Reply is required and must not exceed 3,000 characters."), 400

    try:
        ensure_notification_table()
        rows = fetch_all(
            """
            SELECT m.message_id, m.subject,
                   m.sender_role, m.sender_account_id,
                   COALESCE(s.email, f.email, a.email) AS sender_email,
                   COALESCE(s.first_name, f.first_name, a.first_name) AS sender_first_name,
                   COALESCE(s.last_name, f.last_name, a.last_name) AS sender_last_name,
                   COALESCE(s.status, f.status, a.status) AS sender_status
            FROM messages m
            LEFT JOIN students s
              ON m.sender_role = 'student' AND s.account_id = m.sender_account_id
            LEFT JOIN faculty_staff f
              ON m.sender_role = 'faculty_staff' AND f.account_id = m.sender_account_id
            LEFT JOIN admins a
              ON m.sender_role = 'admin' AND a.account_id = m.sender_account_id
            WHERE m.message_id = %s
              AND m.recipient_role = %s
              AND m.recipient_account_id = %s
            LIMIT 1
            """,
            (message_id, session["user"]["role"], str(session["user"]["account_id"])),
        )
        if not rows:
            return jsonify(message="Message not found."), 404

        original = rows[0]
        if original["sender_role"] == session["user"]["role"] and str(original["sender_account_id"]) == str(session["user"]["account_id"]):
            return jsonify(message="You cannot reply to your own message."), 409
        if original["sender_status"] != "active":
            return jsonify(message="That user is no longer active and cannot receive a reply."), 409

        sender = session["user"]
        original_subject = original["subject"] or "No subject"
        subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
        connection = db_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO messages (sender_role, sender_account_id, recipient_role,
                                      recipient_account_id, recipient_email, subject, body)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (sender["role"], str(sender["account_id"]), original["sender_role"],
                 str(original["sender_account_id"]), original["sender_email"], subject, body),
            )
            reply_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO notifications (recipient_role, recipient_account_id,
                    notification_type, status, title, message, related_message_id)
                VALUES (%s,%s,'message','info',%s,%s,%s)
                """,
                (original["sender_role"], str(original["sender_account_id"]),
                 f"New reply from {sender['name']}",
                 f"You received a reply to “{original_subject}”.", reply_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
    except Error:
        return database_error_response()

    return jsonify(message="Reply sent successfully.", message_id=reply_id), 201


@app.patch("/api/messages/<int:message_id>/read")
@role_required("student", "faculty_staff", "admin")
def mark_message_read(message_id):
    try:
        ensure_notification_table()
        connection = db_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE messages SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
                WHERE message_id = %s AND recipient_role = %s AND recipient_account_id = %s
                """,
                (message_id, session["user"]["role"], str(session["user"]["account_id"])),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return jsonify(message="Message not found."), 404
            cursor.execute(
                """
                UPDATE notifications
                SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
                WHERE related_message_id = %s
                  AND recipient_role = %s
                  AND recipient_account_id = %s
                  AND read_at IS NULL
                """,
                (message_id, session["user"]["role"], str(session["user"]["account_id"])),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
    except Error:
        return database_error_response()
    return jsonify(message="Message marked as read.")


@app.delete("/api/messages/<int:message_id>")
@role_required("student", "faculty_staff", "admin")
def delete_message(message_id):
    try:
        ensure_message_table()
        deleted = execute_write(
            """DELETE FROM messages
               WHERE message_id = %s AND recipient_role = %s AND recipient_account_id = %s""",
            (message_id, session["user"]["role"], str(session["user"]["account_id"])),
        )
    except Error:
        return database_error_response()
    if not deleted:
        return jsonify(message="Message not found."), 404
    return jsonify(message="Message deleted.")


# ---------------------------------------------------------- Notification API
# Notifications are always filtered by the current role + account ID.
@app.get("/api/notifications")
@role_required("student", "faculty_staff", "admin")
def get_notifications():
    try:
        ensure_notification_table()
        if session["user"]["role"] == "faculty_staff":
            # Notification polling is our lightweight scheduler: no separate
            # background service is required for this school-project deployment.
            run_one_week_post_notifications()
        rows = fetch_all(
            """
            SELECT notification_id, notification_type, status, title, message,
                   related_report_id, related_claim_id, related_message_id, read_at, created_at
            FROM notifications
            WHERE recipient_role = %s
              AND recipient_account_id = %s
              AND NOT (recipient_role = 'admin' AND notification_type = 'admin-action')
            ORDER BY created_at DESC, notification_id DESC
            LIMIT 500
            """,
            (session["user"]["role"], str(session["user"]["account_id"])),
        )
    except Error:
        return database_error_response()
    return jsonify(notifications=[serialize_notification(row) for row in rows])


@app.patch("/api/notifications/read-all")
@role_required("student", "faculty_staff", "admin")
def mark_notifications_read():
    try:
        ensure_notification_table()
        execute_write(
            """
            UPDATE notifications
            SET read_at = CURRENT_TIMESTAMP
            WHERE recipient_role = %s
              AND recipient_account_id = %s
              AND read_at IS NULL
            """,
            (session["user"]["role"], str(session["user"]["account_id"])),
        )
    except Error:
        return database_error_response()
    return jsonify(message="Notifications marked as read.")


@app.post("/api/notifications/self")
@role_required("student", "faculty_staff", "admin")
def create_self_notification():
    """Allow non-admin dashboards to record a notification for themselves only.

    Administrators never receive notifications for their own administrative
    actions. Admin notifications are generated server-side only for reports
    submitted by Students or Faculty / Staff.
    """
    if session["user"]["role"] == "admin":
        return jsonify(message="Administrators cannot create notifications for their own activity."), 403
    data = json_body()
    title = str(data.get("title", "")).strip()
    message = str(data.get("message", "")).strip()
    status = str(data.get("status", "info")).strip().lower() or "info"
    if not title or not message:
        return jsonify(message="Notification title and message are required."), 400
    if len(title) > 255 or len(message) > 2000:
        return jsonify(message="Notification title or message is too long."), 400
    try:
        create_notification(
            session["user"]["role"], session["user"]["account_id"], title, message,
            status=status, notification_type="admin-action" if session["user"]["role"] == "admin" else "system",
        )
    except Error:
        return database_error_response()
    return jsonify(message="Notification created."), 201


def lost_report_user_required(view):
    """Allow lost-report CRUD only for signed-in Students or Faculty / Staff."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = session.get("user")
        if not user:
            return not_signed_in()
        if user["role"] not in {"student", "faculty_staff"}:
            return wrong_role(user)
        return view(*args, **kwargs)
    return wrapper


# --------------------------------------------------------- Authentication / Pages
# Authentication flow: validate input -> find the correct account -> verify the
# password hash -> check account status -> store only safe session information.
# Login and account-creation routes establish the session used by role_required().
@app.get("/")
@app.get("/login")
def login():
    return render_template("auth/login.html")


# Login never accepts a role from the browser. The account table determines
# whether the 4-digit account is Faculty/Staff or Administrator.
@app.post("/login")
def login_submit():
    data = json_body()
    email = normalize_email(data.get("email"))
    password = str(data.get("password", ""))

    role, email_id = parse_school_email(email)
    # A 4-digit school email can belong to either Faculty / Staff or Admin.
    # parse_school_email() therefore returns role=None for valid 4-digit emails.
    # Only the parsed ID being missing means the email format is invalid.
    if email_id is None:
        return jsonify(field="email", message="Enter a valid UMindanao school email."), 400

    if not password:
        return jsonify(field="password", message="Enter your password."), 400

    try:
        account = find_account(email, email_id)
    except Error:
        return database_error_response()

    if not account:
        return jsonify(field="email", message="No account was found for this school email and ID."), 401

    if not check_password_hash(account["password_hash"], password):
        return jsonify(field="password", message="Incorrect password."), 401

    if account["status"] == "pending":
        return jsonify(
            field="email",
            message="Your Faculty / Staff account is pending. An administrator must activate it before you can log in.",
        ), 403

    if account["status"] == "suspended":
        return jsonify(field="email", message="This account is currently suspended."), 403

    session.clear()
    session["user"] = account_view(account)
    return jsonify(redirect=url_for(ROLE_INFO[account["role"]][0]))


@app.get("/create-account")
def create_account():
    return render_template("auth/create-account.html")


@app.post("/create-account")
def create_account_submit():
    data = json_body()
    validation_error = validate_registration(data)

    if validation_error:
        field, message = validation_error
        return jsonify(field=field, message=message), 400

    role = str(data["role"]).strip().lower()
    school_id = str(data["school_id"]).strip()
    first_name = str(data["first_name"]).strip()
    last_name = str(data["last_name"]).strip()
    email = normalize_email(data["email"])
    password = str(data["password"])

    status = "active" if role == "student" else "pending"
    password_hash = generate_password_hash(password)

    try:
        existing = find_existing_account(school_id, email)
        if existing:
            if existing["email"] == email:
                return jsonify(
                    field="email",
                    message="An account with this school email already exists.",
                ), 409
            return jsonify(
                field="school_id",
                message="An account with this ID already exists.",
            ), 409

        table = ACCOUNT_TABLES[role]
        execute_write(
            f"""
            INSERT INTO {table}
                (account_id, first_name, last_name, email, password_hash, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (school_id, first_name, last_name, email, password_hash, status),
        )
    except IntegrityError as exc:
        # MySQL duplicate-key errors are returned as a clean validation message.
        if getattr(exc, "errno", None) in {1062}:
            return jsonify(
                field="email",
                message="An account with this ID or school email already exists.",
            ), 409
        return database_error_response()
    except Error:
        return database_error_response()

    if role == "faculty_staff":
        message = "Account created successfully. Your Faculty / Staff account is pending admin activation."
    else:
        message = "Account created successfully. You can now log in."

    return jsonify(message=message, redirect=url_for("login")), 201


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------- Admin APIs
# The Admin area is responsible for oversight: accounts, audit history, items,
# categories, and communications. Each route is protected with @role_required("admin").
@app.get("/api/admin/audit")
@role_required("admin")
def admin_audit():
    try:
        ensure_audit_table()
        rows = fetch_all(
            """
            SELECT audit_id, admin_account_id, admin_name, admin_email, action,
                   target_type, target_id, details, created_at
            FROM audit_logs
            ORDER BY created_at DESC, audit_id DESC
            LIMIT 1000
            """
        )
    except Error:
        return database_error_response()

    return jsonify(audit=[
        {
            "id": row["audit_id"],
            "timestamp": row["created_at"].isoformat(),
            "adminId": row["admin_account_id"],
            "adminName": row["admin_name"],
            "adminEmail": row["admin_email"],
            "action": row["action"],
            "targetType": "item" if row["target_type"] in {"lost", "found"} else row["target_type"],
            "targetId": row["target_id"],
            "details": row["details"],
        } for row in rows
    ])


# ------------------------------------------------------------ Admin accounts API
# Administrators can activate/suspend Student and Faculty/Staff accounts.
# The Admin account itself is not managed through this public status endpoint.
@app.get("/api/admin/accounts")
@role_required("admin")
def admin_accounts():
    try:
        accounts = fetch_all(
            """
            SELECT account_id, 'student' AS role, first_name, last_name, email,
                   status, created_at, updated_at
            FROM students

            UNION ALL

            SELECT account_id, 'faculty_staff' AS role, first_name, last_name, email,
                   status, created_at, updated_at
            FROM faculty_staff

            ORDER BY created_at DESC
            """
        )
    except Error:
        return database_error_response()

    result = []
    for account in accounts:
        view = account_view(account)
        result.append(
            {
                "id": view["account_id"],
                "school_id": view["account_id"],
                "name": view["name"],
                "email": view["email"],
                "role": view["role_label"],
                "role_code": view["role"],
                "status": view["status"],
                "createdAt": account["created_at"].isoformat(),
                "updatedAt": account["updated_at"].isoformat(),
            }
        )

    return jsonify(accounts=result)


@app.patch("/api/admin/accounts/<role>/<school_id>/status")
@role_required("admin")
def update_account_status(role, school_id):
    data = json_body()
    status = str(data.get("status", "")).strip().lower()

    if role not in {"student", "faculty_staff"}:
        return jsonify(message="Only Student and Faculty / Staff accounts can be managed here."), 400

    expected_digits = 6 if role == "student" else 4
    if not re.fullmatch(rf"\d{{{expected_digits}}}", str(school_id)):
        return jsonify(message="Invalid account ID for the selected account type."), 400

    if status not in {"active", "suspended"}:
        return jsonify(message="Account status must be active or suspended."), 400

    table = ACCOUNT_TABLES[role]

    try:
        account = fetch_one(
            f"""
            SELECT account_id, first_name, last_name, email, status
            FROM {table}
            WHERE account_id = %s
            """,
            (school_id,),
        )
        if not account:
            return jsonify(message="Account not found."), 404

        execute_write(
            f"UPDATE {table} SET status = %s WHERE account_id = %s",
            (status, school_id),
        )
        create_audit_log(
            "ACTIVATE ACCOUNT" if status == "active" else "SUSPEND ACCOUNT",
            "account", school_id,
            f"Changed {account['first_name']} {account['last_name']} ({account['email']}) from {account['status']} to {status}.",
        )
    except Error:
        return database_error_response()

    return jsonify(
        message=f"{account['first_name']} {account['last_name']}'s account is now {status}.",
        status=status,
    )



# ------------------------------------------------------------- Found reports / claims
# Claims follow a simple lifecycle: pending -> accepted -> returned, or
# pending -> rejected. The return fields record the staff member and time
# when the physical handover was completed, which keeps the database aligned
# with the real-world Lost and Found process.
CLAIM_RETURN_COLUMNS_SQL = {
    "returned_by_faculty_staff_id": "VARCHAR(4) NULL",
    "returned_at": "TIMESTAMP NULL DEFAULT NULL",
}

def ensure_claim_return_fields():
    """Add return-tracking fields to older databases without deleting claim data."""
    connection = db_connection()
    cursor = connection.cursor()
    try:
        for column_name, definition in CLAIM_RETURN_COLUMNS_SQL.items():
            cursor.execute(
                """
                SELECT COUNT(*) AS column_count
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'claim_requests'
                  AND column_name = %s
                """,
                (column_name,),
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    f"ALTER TABLE claim_requests ADD COLUMN {column_name} {definition}"
                )

        # Existing installations may still have the original three-state enum.
        # Expanding it is safe because all existing values remain valid.
        cursor.execute(
            """
            SELECT COLUMN_TYPE
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'claim_requests'
              AND column_name = 'status'
            """
        )
        row = cursor.fetchone()
        column_type = str(row[0]).lower() if row else ""
        if "'returned'" not in column_type:
            cursor.execute(
                """
                ALTER TABLE claim_requests
                MODIFY COLUMN status ENUM('pending','accepted','rejected','returned')
                NOT NULL DEFAULT 'pending'
                """
            )

        # A returned found item must leave the public claiming pool. Existing
        # verified records stay verified until a real return is recorded.
        cursor.execute(
            """
            SELECT COLUMN_TYPE
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = 'found_item_reports'
              AND column_name = 'status'
            """
        )
        found_status_row = cursor.fetchone()
        found_status_type = str(found_status_row[0]).lower() if found_status_row else ""
        if "'returned'" not in found_status_type:
            cursor.execute(
                """
                ALTER TABLE found_item_reports
                MODIFY COLUMN status ENUM('verified','returned')
                NOT NULL DEFAULT 'verified'
                """
            )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    ensure_claim_receipts_table()

# ------------------------------------------------------------ Return receipts
# Every returned claim gets exactly one row in claim_receipts. The receipt number
# (RCP-YYYY-000001) is generated once, stored, and never recalculated.
CLAIM_RECEIPTS_TABLE_SQL = """
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
)
"""
_receipt_table_ready = False


def format_receipt_no(year, sequence):
    return f"RCP-{int(year)}-{int(sequence):06d}"


def issue_claim_receipt(cursor, claim_id, staff_account_id, year=None, issued_at=None):
    """Create the receipt row for a claim and return its receipt number.

    Must run inside the caller's transaction. The next sequence for the year is
    read with FOR UPDATE; the UNIQUE (receipt_year, receipt_seq) key is the final
    safety net, so a rare race is retried instead of producing a duplicate.
    """
    cursor.execute("SELECT receipt_no FROM claim_receipts WHERE claim_id=%s", (claim_id,))
    existing = cursor.fetchone()
    if existing:
        return existing["receipt_no"] if isinstance(existing, dict) else existing[0]

    if year is None:
        cursor.execute("SELECT YEAR(CURRENT_TIMESTAMP) AS y")
        row = cursor.fetchone()
        year = row["y"] if isinstance(row, dict) else row[0]

    for _attempt in range(5):
        cursor.execute(
            "SELECT COALESCE(MAX(receipt_seq), 0) + 1 AS next_seq FROM claim_receipts WHERE receipt_year=%s FOR UPDATE",
            (year,),
        )
        row = cursor.fetchone()
        sequence = row["next_seq"] if isinstance(row, dict) else row[0]
        receipt_no = format_receipt_no(year, sequence)
        try:
            if issued_at is None:
                cursor.execute(
                    "INSERT INTO claim_receipts (receipt_no, receipt_year, receipt_seq, claim_id, issued_by_faculty_staff_id) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (receipt_no, year, sequence, claim_id, staff_account_id),
                )
            else:
                cursor.execute(
                    "INSERT INTO claim_receipts (receipt_no, receipt_year, receipt_seq, claim_id, issued_by_faculty_staff_id, issued_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (receipt_no, year, sequence, claim_id, staff_account_id, issued_at),
                )
            return receipt_no
        except IntegrityError:
            continue
    raise Error("Could not generate a unique receipt number. Please try again.")


def ensure_claim_receipts_table():
    """Create claim_receipts if needed and give already-returned claims a receipt."""
    global _receipt_table_ready
    if _receipt_table_ready:
        return
    connection = db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(CLAIM_RECEIPTS_TABLE_SQL)
        connection.commit()

        # Backfill: older returned claims receive numbers in the order they were returned.
        cursor.execute(
            """
            SELECT c.claim_id, c.returned_by_faculty_staff_id AS staff_id,
                   COALESCE(c.returned_at, c.updated_at, c.created_at) AS done_at
            FROM claim_requests c
            LEFT JOIN claim_receipts rc ON rc.claim_id = c.claim_id
            WHERE c.status = 'returned' AND rc.receipt_id IS NULL
            ORDER BY done_at, c.claim_id
            """
        )
        for old in cursor.fetchall():
            issue_claim_receipt(cursor, old["claim_id"], old["staff_id"],
                                year=old["done_at"].year, issued_at=old["done_at"])
        connection.commit()
        _receipt_table_ready = True
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


# Found-item reports come from Faculty/Staff and are automatically verified.
# Claims are separate records so one found item can have a controlled claim workflow.

def validate_found_report_form(data):
    item_name = str(data.get("item_name", "")).strip()
    category = str(data.get("category", "")).strip()
    description = str(data.get("description", "")).strip()
    found_location = str(data.get("found_location", "")).strip()
    date_found = str(data.get("date_found", "")).strip()

    if not item_name:
        return "item_name", "Enter the item name."
    if len(item_name) > 100:
        return "item_name", "Item name must not exceed 100 characters."
    category_error = category_validation_error(category)
    if category_error:
        return category_error
    if not description:
        return "description", "Enter a description of the found item."
    if len(description) > 1000:
        return "description", "Description must not exceed 1,000 characters."
    if not found_location:
        return "found_location", "Enter where the item was found."
    if len(found_location) > 150:
        return "found_location", "Found location must not exceed 150 characters."
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_found):
        return "date_found", "Enter a valid found date."
    try:
        datetime.strptime(date_found, "%Y-%m-%d")
    except ValueError:
        return "date_found", "Enter a real calendar date."
    return None


def read_found_report(found_report_id):
    return fetch_one(
        """
        SELECT
            r.found_report_id, r.faculty_staff_account_id, r.item_name,
            r.category, r.description, r.photo_data IS NOT NULL AS has_photo,
            r.found_location, r.date_found, r.status, r.created_at, r.updated_at,
            CONCAT(f.first_name, ' ', f.last_name) AS reporter_name
        FROM found_item_reports AS r
        INNER JOIN faculty_staff AS f ON f.account_id = r.faculty_staff_account_id
        WHERE r.found_report_id = %s
        """,
        (found_report_id,),
    )


def serialize_found_report(report):
    return {
        "id": report["found_report_id"],
        "item_name": report["item_name"],
        "category": report["category"],
        "description": report["description"],
        "found_location": report["found_location"],
        "date_found": report["date_found"].isoformat(),
        "status": report["status"],
        "reporter_id": report["faculty_staff_account_id"],
        "reporter_role": "faculty_staff",
        "reporter_name": report.get("reporter_name"),
        "photo_url": url_for("found_report_photo", found_report_id=report["found_report_id"]) if report["has_photo"] else None,
        "photo_urls": report_photo_urls("found", report["found_report_id"], bool(report["has_photo"])),
        "createdAt": report["created_at"].isoformat(),
        "updatedAt": report["updated_at"].isoformat(),
    }


# ---------------------------------------------------------- Found-item workflow
# Faculty / Staff found reports are verified immediately and can enter the public feed.
@app.post("/api/faculty/found-reports")
@role_required("faculty_staff")
def create_found_report():
    data = request.form
    validation_error = validate_found_report_form(data)
    if validation_error:
        field, message = validation_error
        return jsonify(field=field, message=message), 400

    try:
        photos = prepare_report_photos(request.files.getlist("photo"))
    except ValueError as exc:
        return jsonify(field="photo", message=str(exc)), 400

    photo = photos[0] if photos else None
    photo_data = photo["data"] if photo else None
    photo_filename = photo["filename"] if photo else None
    photo_mime_type = photo["mime_type"] if photo else None

    user = session["user"]
    try:
        if not report_owner_is_active(user):
            session.clear()
            return jsonify(message="Your account is no longer active. Please log in again."), 401
        connection = db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO found_item_reports (
                faculty_staff_account_id, item_name, category, description,
                photo_data, photo_filename, photo_mime_type, found_location,
                date_found, status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'verified')
            """,
            (user["account_id"], data["item_name"].strip(), data["category"].strip(),
             data["description"].strip(), photo_data, photo_filename, photo_mime_type,
             data["found_location"].strip(), data["date_found"].strip()),
        )
        report_id = cursor.lastrowid
        store_report_photos(cursor, "found", report_id, photos)
        connection.commit()
    except IntegrityError:
        if "connection" in locals(): connection.rollback()
        return database_error_response()
    except Error:
        if "connection" in locals(): connection.rollback()
        return database_error_response()
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals(): connection.close()

    try:
        notify_active_role(
            "admin",
            "New Faculty / Staff found-item report",
            f"{user.get('name', 'A Faculty / Staff user')} reported “{data['item_name'].strip()}” as a found item. The report was automatically verified and is public.",
            notification_type="new-found-report",
            status="approved",
        )
    except Error:
        app.logger.exception("Could not create Admin notifications for Faculty / Staff found report %s", report_id)

    return jsonify(report=serialize_found_report(read_found_report(report_id))), 201


@app.get("/api/faculty/found-reports")
@role_required("faculty_staff")
def faculty_found_reports():
    try:
        reports = fetch_all(
            """
            SELECT r.found_report_id, r.faculty_staff_account_id, r.item_name,
                   r.category, r.description, r.photo_data IS NOT NULL AS has_photo,
                   r.found_location, r.date_found, r.status, r.created_at, r.updated_at,
                   CONCAT(f.first_name, ' ', f.last_name) AS reporter_name
            FROM found_item_reports r
            JOIN faculty_staff f ON f.account_id = r.faculty_staff_account_id
            WHERE r.faculty_staff_account_id = %s
            ORDER BY r.created_at DESC
            """, (session["user"]["account_id"],)
        )
    except Error:
        return database_error_response()
    return jsonify(reports=[serialize_found_report(r) for r in reports])


@app.get("/api/public/found-reports")
def public_found_reports():
    if not session.get("user"):
        return jsonify(message="Authentication required."), 401
    try:
        reports = fetch_all(
            """
            SELECT r.found_report_id, r.faculty_staff_account_id, r.item_name,
                   r.category, r.description, r.photo_data IS NOT NULL AS has_photo,
                   r.found_location, r.date_found, r.status, r.created_at, r.updated_at,
                   CONCAT(f.first_name, ' ', f.last_name) AS reporter_name
            FROM found_item_reports r
            JOIN faculty_staff f ON f.account_id = r.faculty_staff_account_id
            WHERE r.status = 'verified'
            ORDER BY r.created_at DESC LIMIT 50
            """
        )
        total = fetch_one("SELECT COUNT(*) AS total FROM found_item_reports WHERE status = 'verified'")
    except Error:
        return database_error_response()
    user = session.get("user") or {}
    requester_col = "student_account_id" if user.get("role") == "student" else "faculty_staff_account_id"
    try:
        my_claims = fetch_all(
            f"SELECT found_report_id, status FROM claim_requests WHERE {requester_col}=%s",
            (user.get("account_id"),),
        ) if user.get("account_id") else []
    except Error:
        return database_error_response()
    my_claim_status = {str(row["found_report_id"]): row["status"] for row in my_claims}
    serialized = []
    for row in reports:
        item = serialize_found_report(row)
        item["claim_status"] = my_claim_status.get(str(item["id"]))
        serialized.append(item)
    return jsonify(reports=serialized, count=int(total["total"] if total else 0))


@app.get("/api/public/recent-posts")
def public_recent_posts():
    if not session.get("user"):
        return jsonify(message="Authentication required."), 401
    try:
        lost = fetch_all(
            """
            SELECT r.report_id, r.student_account_id, r.faculty_staff_account_id,
                   r.item_name, r.category, r.description, r.photo_data IS NOT NULL AS has_photo,
                   r.last_seen, r.date_lost, r.status, r.created_at, r.updated_at,
                   r.reviewed_at,
                   CASE WHEN r.student_account_id IS NOT NULL THEN CONCAT(s.first_name,' ',s.last_name)
                        ELSE CONCAT(f.first_name,' ',f.last_name) END AS reporter_name,
                   CONCAT(rv.first_name,' ',rv.last_name) AS reviewed_by_name
            FROM lost_item_reports r
            LEFT JOIN students s ON s.account_id=r.student_account_id
            LEFT JOIN faculty_staff f ON f.account_id=r.faculty_staff_account_id
            LEFT JOIN faculty_staff rv ON rv.account_id=r.reviewed_by_faculty_staff_id
            WHERE r.status='verified' ORDER BY r.created_at DESC LIMIT 50
            """
        )
        found = fetch_all(
            """
            SELECT r.found_report_id, r.faculty_staff_account_id, r.item_name,
                   r.category, r.description, r.photo_data IS NOT NULL AS has_photo,
                   r.found_location, r.date_found, r.status, r.created_at, r.updated_at,
                   CONCAT(f.first_name,' ',f.last_name) AS reporter_name
            FROM found_item_reports r JOIN faculty_staff f ON f.account_id=r.faculty_staff_account_id
            WHERE r.status='verified' ORDER BY r.created_at DESC LIMIT 50
            """
        )
    except Error:
        return database_error_response()
    items = []
    for r in lost:
        item = serialize_lost_report(r)
        item["post_type"] = "lost"
        items.append(item)

    # Tell the signed-in user whether they have ever submitted a claim for
    # each public Found Item. This is used only to lock the claim form after
    # the first request; it does not expose another user's claim data.
    user = session.get("user") or {}
    requester_col = "student_account_id" if user.get("role") == "student" else "faculty_staff_account_id"
    try:
        my_claims = fetch_all(
            f"SELECT found_report_id, status FROM claim_requests WHERE {requester_col}=%s",
            (user.get("account_id"),),
        ) if user.get("account_id") else []
    except Error:
        return database_error_response()
    my_claim_status = {str(row["found_report_id"]): row["status"] for row in my_claims}

    for r in found:
        item = serialize_found_report(r)
        item["post_type"] = "found"
        item["claim_status"] = my_claim_status.get(str(item["id"]))
        items.append(item)
    items.sort(key=lambda x: x.get("createdAt") or "", reverse=True)
    lost_total = fetch_one("SELECT COUNT(*) AS total FROM lost_item_reports WHERE status='verified'")
    found_total = fetch_one("SELECT COUNT(*) AS total FROM found_item_reports WHERE status='verified'")
    return jsonify(reports=items[:50], lostCount=int(lost_total["total"] if lost_total else 0), foundCount=int(found_total["total"] if found_total else 0), count=min(50, len(items)))



def _report_photo_access(report, user):
    if report["status"] == "verified":
        return True
    if user.get("role") == "admin":
        return True
    if user.get("role") == "faculty_staff" and report.get("faculty_staff_account_id") == user.get("account_id"):
        return True
    if user.get("role") == "student" and report.get("student_account_id") == user.get("account_id"):
        return True
    return user.get("role") == "faculty_staff"


@app.get("/api/lost-reports/<int:report_id>/photos/<int:photo_id>")
def lost_report_extra_photo(photo_id, report_id):
    user = session.get("user")
    if not user:
        return jsonify(message="Authentication required."), 401
    try:
        row = fetch_one(
            """
            SELECT p.photo_data, p.photo_mime_type, r.status, r.student_account_id, r.faculty_staff_account_id
            FROM lost_item_photos p JOIN lost_item_reports r ON r.report_id=p.report_id
            WHERE p.photo_id=%s AND p.report_id=%s
            """,
            (photo_id, report_id),
        )
    except Error:
        return database_error_response()
    if not row or not _report_photo_access(row, user):
        return jsonify(message="Photo not found."), 404
    return app.response_class(row["photo_data"], mimetype=row["photo_mime_type"])


@app.get("/api/found-reports/<int:found_report_id>/photos/<int:photo_id>")
def found_report_extra_photo(photo_id, found_report_id):
    user = session.get("user")
    if not user:
        return jsonify(message="Authentication required."), 401
    try:
        row = fetch_one(
            """
            SELECT p.photo_data, p.photo_mime_type, r.status, r.faculty_staff_account_id
            FROM found_item_photos p JOIN found_item_reports r ON r.found_report_id=p.found_report_id
            WHERE p.photo_id=%s AND p.found_report_id=%s
            """,
            (photo_id, found_report_id),
        )
    except Error:
        return database_error_response()
    if not row or not _report_photo_access(row, user):
        return jsonify(message="Photo not found."), 404
    return app.response_class(row["photo_data"], mimetype=row["photo_mime_type"])


@app.get("/api/found-reports/<int:found_report_id>/photo")
def found_report_photo(found_report_id):
    if not session.get("user"):
        return jsonify(message="Authentication required."), 401
    try:
        report = fetch_one("SELECT photo_data, photo_mime_type, status FROM found_item_reports WHERE found_report_id=%s", (found_report_id,))
    except Error:
        return database_error_response()
    if not report or report["photo_data"] is None or report["status"] != "verified":
        return jsonify(message="Photo not found."), 404
    return app.response_class(report["photo_data"], mimetype=report["photo_mime_type"])



# ---------------------------------------------------------------- Admin item management
ADMIN_LOST_STATUSES = {"pending", "verified", "rejected"}
ADMIN_FOUND_STATUSES = {"verified", "returned"}


def serialize_admin_item(row, item_type):
    """Return one database-backed item in the shape used by Admin Manage Items."""
    if item_type == "lost":
        owner_role = "student" if row["student_account_id"] else "faculty_staff"
        owner_id = row["student_account_id"] or row["faculty_staff_account_id"]
        return {
            "id": row["report_id"],
            "item_name": row["item_name"],
            "type": "lost",
            "category": row["category"],
            "description": row["description"],
            "location": row["last_seen"],
            "last_seen": row["last_seen"],
            "date_lost": row["date_lost"].isoformat(),
            "date": row["date_lost"].isoformat(),
            "status": row["status"],
            "reporter_id": owner_id,
            "reporter_role": owner_role,
            "reporter_name": row.get("reporter_name"),
            "createdAt": row["created_at"].isoformat(),
            "updatedAt": row["updated_at"].isoformat(),
            "source": "student" if owner_role == "student" else "faculty",
        }

    return {
        "id": row["found_report_id"],
        "item_name": row["item_name"],
        "type": "found",
        "category": row["category"],
        "description": row["description"],
        "location": row["found_location"],
        "found_location": row["found_location"],
        "date_found": row["date_found"].isoformat(),
        "date": row["date_found"].isoformat(),
        "status": row["status"],
        "reporter_id": row["faculty_staff_account_id"],
        "reporter_role": "faculty_staff",
        "reporter_name": row.get("reporter_name"),
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
        "source": "faculty",
    }


@app.get("/api/admin/items")
@role_required("admin")
def admin_items():
    """Return every Lost and Found report from the MySQL database."""
    try:
        lost = fetch_all(
            """
            SELECT r.report_id, r.student_account_id, r.faculty_staff_account_id,
                   r.item_name, r.category, r.description, r.last_seen, r.date_lost,
                   r.status, r.created_at, r.updated_at,
                   CASE
                       WHEN r.student_account_id IS NOT NULL THEN CONCAT(s.first_name, ' ', s.last_name)
                       ELSE CONCAT(f.first_name, ' ', f.last_name)
                   END AS reporter_name
            FROM lost_item_reports r
            LEFT JOIN students s ON s.account_id = r.student_account_id
            LEFT JOIN faculty_staff f ON f.account_id = r.faculty_staff_account_id
            ORDER BY r.created_at DESC, r.report_id DESC
            """
        )
        found = fetch_all(
            """
            SELECT r.found_report_id, r.faculty_staff_account_id,
                   r.item_name, r.category, r.description, r.found_location,
                   r.date_found, r.status, r.created_at, r.updated_at,
                   CONCAT(f.first_name, ' ', f.last_name) AS reporter_name
            FROM found_item_reports r
            LEFT JOIN faculty_staff f ON f.account_id = r.faculty_staff_account_id
            ORDER BY r.created_at DESC, r.found_report_id DESC
            """
        )
    except Error:
        return database_error_response()

    items = [serialize_admin_item(row, "lost") for row in lost]
    items.extend(serialize_admin_item(row, "found") for row in found)
    items.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    return jsonify(items=items)


@app.patch("/api/admin/items/<item_type>/<int:item_id>")
@role_required("admin")
def admin_update_item(item_type, item_id):
    """Edit a Lost or Found report directly from the Admin Manage Items panel."""
    item_type = str(item_type).strip().lower()
    if item_type not in {"lost", "found"}:
        return jsonify(message="Item type must be lost or found."), 400

    data = json_body()
    item_name = str(data.get("item_name", "")).strip()
    category = str(data.get("category", "")).strip()
    description = str(data.get("description", "")).strip()
    location = str(data.get("location", "")).strip()
    date_value = str(data.get("date", "")).strip()
    status = str(data.get("status", "")).strip().lower()

    max_item_name = 100
    max_category = 50
    max_description = 1000
    max_location = 150
    if not item_name or len(item_name) > max_item_name:
        return jsonify(message=f"Item name is required and must not exceed {max_item_name} characters."), 400
    category_error = category_validation_error(category)
    if category_error:
        return jsonify(field=category_error[0], message=category_error[1]), 400
    if not description or len(description) > max_description:
        return jsonify(message=f"Description is required and must not exceed {max_description} characters."), 400
    if not location or len(location) > max_location:
        return jsonify(message=f"Location is required and must not exceed {max_location} characters."), 400
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
        return jsonify(message="Enter a valid date."), 400
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return jsonify(message="Enter a real calendar date."), 400

    allowed_statuses = ADMIN_FOUND_STATUSES if item_type == "found" else ADMIN_LOST_STATUSES
    if status not in allowed_statuses:
        allowed = ", ".join(sorted(allowed_statuses))
        return jsonify(message=f"Invalid {item_type} status. Allowed status: {allowed}."), 400

    table_id = "report_id" if item_type == "lost" else "found_report_id"
    location_column = "last_seen" if item_type == "lost" else "found_location"
    date_column = "date_lost" if item_type == "lost" else "date_found"
    table = "lost_item_reports" if item_type == "lost" else "found_item_reports"

    try:
        updated = execute_write(
            f"""
            UPDATE {table}
            SET item_name = %s,
                category = %s,
                description = %s,
                {location_column} = %s,
                {date_column} = %s,
                status = %s
            WHERE {table_id} = %s
            """,
            (item_name, category, description, location, date_value, status, item_id),
        )
        if not updated:
            return jsonify(message="Item report not found."), 404

        if item_type == "lost":
            report = read_lost_report(item_id)
            serialized = serialize_admin_item(report, "lost") if report else None
        else:
            report = read_found_report(item_id)
            serialized = serialize_admin_item(report, "found") if report else None

        create_audit_log(
            "EDIT ITEM", "item", item_id,
            f"Updated {item_type} item details: {item_name}.",
        )
    except Error:
        return database_error_response()

    return jsonify(item=serialized)


@app.delete("/api/admin/items/<item_type>/<int:item_id>")
@role_required("admin")
def admin_delete_item(item_type, item_id):
    """Delete a Lost/Found report. Found reports also remove their dependent claims."""
    item_type = str(item_type).strip().lower()
    if item_type not in {"lost", "found"}:
        return jsonify(message="Item type must be lost or found."), 400

    table_id = "report_id" if item_type == "lost" else "found_report_id"
    table = "lost_item_reports" if item_type == "lost" else "found_item_reports"

    try:
        connection = db_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                f"SELECT {table_id}, item_name FROM {table} WHERE {table_id} = %s",
                (item_id,),
            )
            report = cursor.fetchone()
            if not report:
                return jsonify(message="Item report not found."), 404

            # claim_requests intentionally uses ON DELETE RESTRICT. An admin
            # deleting a Found Item must first remove its dependent claim rows.
            # Their notification rows remain valid because related_claim_id is
            # nullable and uses ON DELETE SET NULL.
            if item_type == "found":
                cursor.execute(
                    "DELETE FROM claim_requests WHERE found_report_id = %s",
                    (item_id,),
                )

            cursor.execute(
                f"DELETE FROM {table} WHERE {table_id} = %s",
                (item_id,),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return jsonify(message="Item report could not be deleted."), 409
            connection.commit()
            item_name = report["item_name"]
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

        create_audit_log(
            "DELETE ITEM", "item", item_id,
            f"Deleted {item_type} item report: {item_name}.",
        )
    except Error:
        return database_error_response()

    return jsonify(message=f"{item_type.title()} item report '{item_name}' deleted successfully.")


def serialize_claim_request(row):
    return {
        "id": row["claim_id"], "found_report_id": row["found_report_id"],
        "item_name": row["item_name"], "category": row["category"],
        "found_location": row["found_location"], "claim_message": row["claim_message"],
        "status": row["status"], "createdAt": row["created_at"].isoformat(),
        "reviewedAt": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        "returnedAt": row.get("returned_at").isoformat() if row.get("returned_at") else None,
        "returnedBy": row.get("returned_by_name"),
        "receiptNo": row.get("receipt_no"),
        "requester_id": row["student_account_id"] or row["faculty_staff_account_id"],
        "requester_role": "student" if row["student_account_id"] else "faculty_staff",
        "requester_name": row.get("requester_name"),
        "reviewedBy": row.get("reviewed_by_name"),
        "photo_url": url_for("found_report_photo", found_report_id=row["found_report_id"]) if row.get("has_photo") else None,
    }


# --------------------------------------------------------------- Claim workflow
# Claims prove ownership of a public found item and are reviewed by Faculty / Staff.
@app.post("/api/found-reports/<int:found_report_id>/claims")
@role_required("student", "faculty_staff")
def create_claim_request(found_report_id):
    # Claim ownership is checked against the signed-in account and the current
    # claim state. A rejected claim may be submitted again; pending/accepted claims may not.
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    if len(message) < 20:
        return jsonify(message="Please provide at least 20 characters explaining why you believe you are the owner."), 400
    if len(message) > 2000:
        return jsonify(message="Your claim message must not exceed 2,000 characters."), 400
    user = session["user"]
    requester_col = "student_account_id" if user["role"] == "student" else "faculty_staff_account_id"
    try:
        found = fetch_one("SELECT found_report_id, status, faculty_staff_account_id FROM found_item_reports WHERE found_report_id=%s", (found_report_id,))
        if not found or found["status"] != "verified":
            return jsonify(message="This found item is no longer available for claiming."), 404
        if user["role"] == "faculty_staff" and str(found["faculty_staff_account_id"]) == str(user["account_id"]):
            return jsonify(message="You cannot submit a claim request for your own found-item report."), 409
        existing = fetch_one(
            f"SELECT claim_id, status FROM claim_requests WHERE found_report_id=%s AND {requester_col}=%s LIMIT 1",
            (found_report_id, user["account_id"])
        )
        connection = db_connection(); cursor = connection.cursor()
        if existing:
            if existing["status"] in ("pending", "accepted", "returned"):
                return jsonify(
                    message="You already requested a claim for this item.",
                    code="CLAIM_ALREADY_REQUESTED",
                    status=existing["status"],
                ), 409
            # A rejected claim may be submitted again. Reuse the existing
            # claim record so the current database uniqueness rules remain
            # valid, but reset the review state and replace the proof message.
            cursor.execute(
                f"UPDATE claim_requests SET claim_message=%s, status='pending', reviewed_by_faculty_staff_id=NULL, reviewed_at=NULL, returned_by_faculty_staff_id=NULL, returned_at=NULL WHERE claim_id=%s",
                (message, existing["claim_id"])
            )
            claim_id = existing["claim_id"]
        else:
            cursor.execute(
                f"INSERT INTO claim_requests (found_report_id, {requester_col}, claim_message) VALUES (%s,%s,%s)",
                (found_report_id, user["account_id"], message)
            )
            claim_id = cursor.lastrowid
        connection.commit()
    except IntegrityError:
        if "connection" in locals(): connection.rollback()
        return database_error_response()
    except Error:
        if "connection" in locals(): connection.rollback()
        return database_error_response()
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals(): connection.close()

    try:
        requester_name = user.get("name", "A user")
        found_item = fetch_one("SELECT item_name FROM found_item_reports WHERE found_report_id=%s", (found_report_id,))
        item_name = found_item["item_name"] if found_item else "the found item"
        notify_active_role(
            "faculty_staff",
            "New claim request pending review",
            f"{requester_name} submitted a claim request for “{item_name}”. Please review the claim.",
            notification_type="pending-claim",
            status="pending",
            related_claim_id=claim_id,
        )
    except Error:
        app.logger.exception("Could not create Faculty / Staff notifications for claim %s", claim_id)

    return jsonify(message="Claim request submitted for Faculty / Staff review.", claim_id=claim_id), 201


def claim_query(where="", params=()):
    return fetch_all(
        f"""
        SELECT c.claim_id, c.found_report_id, c.student_account_id, c.faculty_staff_account_id,
               c.claim_message, c.status, c.reviewed_at, c.returned_at,
               c.returned_by_faculty_staff_id, c.created_at,
               r.item_name, r.category, r.found_location, r.date_found, r.description,
               (r.photo_data IS NOT NULL) AS has_photo,
               CASE WHEN c.student_account_id IS NOT NULL THEN CONCAT(s.first_name,' ',s.last_name)
                    ELSE CONCAT(f.first_name,' ',f.last_name) END AS requester_name,
               CONCAT(rv.first_name,' ',rv.last_name) AS reviewed_by_name,
               CONCAT(ret.first_name,' ',ret.last_name) AS returned_by_name,
               rc.receipt_no
        FROM claim_requests c
        JOIN found_item_reports r ON r.found_report_id=c.found_report_id
        LEFT JOIN claim_receipts rc ON rc.claim_id=c.claim_id
        LEFT JOIN students s ON s.account_id=c.student_account_id
        LEFT JOIN faculty_staff f ON f.account_id=c.faculty_staff_account_id
        LEFT JOIN faculty_staff rv ON rv.account_id=c.reviewed_by_faculty_staff_id
        LEFT JOIN faculty_staff ret ON ret.account_id=c.returned_by_faculty_staff_id
        {where} ORDER BY c.created_at DESC
        """, params
    )


@app.get("/api/faculty/claims")
@role_required("faculty_staff")
def faculty_claims():
    """Return all claim states needed by the Faculty / Staff claim workspace."""
    try:
        ensure_claim_return_fields()
        claims = claim_query("WHERE c.status IN ('pending','accepted','returned')")
    except Error:
        return database_error_response()
    serialized = [serialize_claim_request(c) for c in claims]
    pending = [claim for claim in serialized if claim["status"] == "pending"]
    approved = [claim for claim in serialized if claim["status"] == "accepted"]
    returned = [claim for claim in serialized if claim["status"] == "returned"]
    return jsonify(
        claims=pending,
        approved_claims=approved,
        returned_claims=returned,
        count=len(pending),
    )


@app.patch("/api/faculty/claims/<int:claim_id>/review")
@role_required("faculty_staff")
def review_claim(claim_id):
    ensure_claim_return_fields()
    data = request.get_json(silent=True) or {}
    decision = data.get("status")
    if decision not in {"accepted", "rejected"}:
        return jsonify(message="Claim decision must be accepted or rejected."), 400
    try:
        connection = db_connection(); cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT claim_id, status, student_account_id, faculty_staff_account_id,
                   found_report_id,
                   (SELECT item_name FROM found_item_reports WHERE found_report_id = claim_requests.found_report_id) AS item_name
            FROM claim_requests
            WHERE claim_id=%s
            """,
            (claim_id,),
        )
        claim = cursor.fetchone()
        if not claim:
            return jsonify(message="Claim request not found."), 404
        if claim["status"] != "pending":
            return jsonify(message="This claim has already been reviewed."), 409
        cursor.execute(
            "UPDATE claim_requests SET status=%s, reviewed_by_faculty_staff_id=%s, reviewed_at=CURRENT_TIMESTAMP, returned_by_faculty_staff_id=NULL, returned_at=NULL WHERE claim_id=%s AND status='pending'",
            (decision, session["user"]["account_id"], claim_id)
        )
        connection.commit()
    except Error:
        if "connection" in locals(): connection.rollback()
        return database_error_response()
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals(): connection.close()

    try:
        # The staff-side pending notification is actionable only while the claim is pending.
        # Remove every staff copy after review so no reviewer receives a stale claim alert.
        execute_write(
            """
            DELETE FROM notifications
            WHERE notification_type = 'pending-claim'
              AND related_claim_id = %s
              AND recipient_role = 'faculty_staff'
            """,
            (claim_id,),
        )
    except Error:
        app.logger.exception("Could not clear staff pending-claim notifications for claim %s", claim_id)

    requester_role = "student" if claim["student_account_id"] else "faculty_staff"
    requester_id = claim["student_account_id"] or claim["faculty_staff_account_id"]
    try:
        accepted = decision == "accepted"
        create_notification(
            requester_role,
            requester_id,
            f"Claim request {decision}",
            f"Your claim request for “{claim['item_name'] or 'the found item'}” was {'accepted' if accepted else 'rejected'} by Faculty / Staff.",
            notification_type="claim-result",
            status="approved" if accepted else "rejected",
            related_claim_id=claim_id,
        )
    except Error:
        app.logger.exception("Could not create claim result notification for claim %s", claim_id)

    return jsonify(message=f"Claim request {decision} successfully.")


@app.patch("/api/faculty/claims/<int:claim_id>/return")
@role_required("faculty_staff")
def return_claim(claim_id):
    """Mark an accepted claim as physically returned and record the processor."""
    ensure_claim_return_fields()
    user = session["user"]
    try:
        connection = db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT c.claim_id, c.status, c.student_account_id, c.faculty_staff_account_id,
                   c.found_report_id, c.claim_message, c.reviewed_at,
                   r.item_name, r.category, r.found_location,
                   CASE WHEN c.student_account_id IS NOT NULL THEN CONCAT(s.first_name,' ',s.last_name)
                        ELSE CONCAT(f.first_name,' ',f.last_name) END AS requester_name
            FROM claim_requests c
            JOIN found_item_reports r ON r.found_report_id = c.found_report_id
            LEFT JOIN students s ON s.account_id = c.student_account_id
            LEFT JOIN faculty_staff f ON f.account_id = c.faculty_staff_account_id
            WHERE c.claim_id = %s
            """,
            (claim_id,),
        )
        claim = cursor.fetchone()
        if not claim:
            return jsonify(message="Claim not found."), 404
        if claim["status"] != "accepted":
            return jsonify(message="Only an approved claim can be marked as returned."), 409

        cursor.execute(
            """
            SELECT claim_id, student_account_id, faculty_staff_account_id
            FROM claim_requests
            WHERE found_report_id=%s AND claim_id<>%s AND status='pending'
            """,
            (claim["found_report_id"], claim_id),
        )
        closed_claimants = cursor.fetchall()

        cursor.execute(
            """
            UPDATE claim_requests
            SET status='returned', returned_by_faculty_staff_id=%s, returned_at=CURRENT_TIMESTAMP
            WHERE claim_id=%s AND status='accepted'
            """,
            (user["account_id"], claim_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return jsonify(message="This claim was already returned or is no longer eligible."), 409

        # Issue the official receipt in the same transaction as the return.
        receipt_no = issue_claim_receipt(cursor, claim_id, user["account_id"])

        # Once the item is physically returned, it must no longer appear as
        # publicly claimable. Any other pending requests for the same item are
        # closed in the same transaction so no second claimant can be approved.
        cursor.execute(
            """
            UPDATE found_item_reports
            SET status='returned'
            WHERE found_report_id=%s AND status='verified'
            """,
            (claim["found_report_id"],),
        )
        cursor.execute(
            """
            UPDATE claim_requests
            SET status='rejected', reviewed_by_faculty_staff_id=%s, reviewed_at=CURRENT_TIMESTAMP
            WHERE found_report_id=%s AND claim_id<>%s AND status='pending'
            """,
            (user["account_id"], claim["found_report_id"], claim_id),
        )
        connection.commit()
    except Error:
        if "connection" in locals():
            connection.rollback()
        return database_error_response()
    finally:
        if "cursor" in locals(): cursor.close()
        if "connection" in locals(): connection.close()

    requester_role = "student" if claim["student_account_id"] else "faculty_staff"
    requester_id = claim["student_account_id"] or claim["faculty_staff_account_id"]
    try:
        create_notification(
            requester_role,
            requester_id,
            "Claimed item returned",
            f"Your approved claim for “{claim['item_name'] or 'the found item'}” has been marked as returned by Faculty / Staff. Receipt No.: {receipt_no}.",
            notification_type="claim-returned",
            status="approved",
            related_claim_id=claim_id,
        )
    except Error:
        app.logger.exception("Could not create claim-return notification for claim %s", claim_id)

    for closed_claim in closed_claimants:
        try:
            execute_write(
                """
                DELETE FROM notifications
                WHERE notification_type='pending-claim'
                  AND related_claim_id=%s
                  AND recipient_role='faculty_staff'
                """,
                (closed_claim["claim_id"],),
            )
        except Error:
            app.logger.exception("Could not clear pending notification for closed claim %s", closed_claim["claim_id"])

        closed_role = "student" if closed_claim["student_account_id"] else "faculty_staff"
        closed_id = closed_claim["student_account_id"] or closed_claim["faculty_staff_account_id"]
        try:
            create_notification(
                closed_role,
                closed_id,
                "Claim request closed",
                f"Your claim request for “{claim['item_name'] or 'the found item'}” was closed because the item has already been returned to a verified owner.",
                notification_type="claim-closed",
                status="rejected",
                related_claim_id=closed_claim["claim_id"],
            )
        except Error:
            app.logger.exception("Could not notify closed claimant %s", closed_claim["claim_id"])

    return jsonify(message="Claim marked as returned successfully.", claim_id=claim_id, receipt_no=receipt_no)


@app.get("/api/faculty/claims/<int:claim_id>/receipt")
@role_required("faculty_staff")
def claim_return_receipt(claim_id):
    """Return the server-side receipt data for a claim already marked returned."""
    ensure_claim_return_fields()
    try:
        row = fetch_one(
            """
            SELECT c.claim_id, c.status, c.claim_message, c.reviewed_at, c.returned_at,
                   r.found_report_id, r.item_name, r.category, r.description,
                   r.found_location, r.date_found,
                   c.student_account_id, c.faculty_staff_account_id,
                   CASE WHEN c.student_account_id IS NOT NULL THEN CONCAT(s.first_name,' ',s.last_name)
                        ELSE CONCAT(f.first_name,' ',f.last_name) END AS requester_name,
                   CASE WHEN c.student_account_id IS NOT NULL THEN s.email ELSE f.email END AS requester_email,
                   CONCAT(rv.first_name,' ',rv.last_name) AS reviewed_by_name,
                   CONCAT(ret.first_name,' ',ret.last_name) AS returned_by_name,
                   rc.receipt_no, rc.issued_at AS receipt_issued_at
            FROM claim_requests c
            JOIN found_item_reports r ON r.found_report_id = c.found_report_id
            LEFT JOIN claim_receipts rc ON rc.claim_id = c.claim_id
            LEFT JOIN students s ON s.account_id = c.student_account_id
            LEFT JOIN faculty_staff f ON f.account_id = c.faculty_staff_account_id
            LEFT JOIN faculty_staff rv ON rv.account_id = c.reviewed_by_faculty_staff_id
            LEFT JOIN faculty_staff ret ON ret.account_id = c.returned_by_faculty_staff_id
            WHERE c.claim_id = %s
            """,
            (claim_id,),
        )
        if not row:
            return jsonify(message="Claim not found."), 404
        if row["status"] != "returned":
            return jsonify(message="A receipt is available only after the claim has been returned."), 409
        return jsonify(receipt={
            "receipt_no": row["receipt_no"],
            "claim_id": row["claim_id"],
            "found_report_id": row["found_report_id"],
            "item_name": row["item_name"],
            "category": row["category"],
            "description": row["description"],
            "found_location": row["found_location"],
            "date_found": row["date_found"].isoformat() if row["date_found"] else None,
            "claim_message": row["claim_message"],
            "requester_id": row["student_account_id"] or row["faculty_staff_account_id"],
            "requester_role": "student" if row["student_account_id"] else "faculty_staff",
            "requester_name": row["requester_name"],
            "requester_email": row["requester_email"],
            "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
            "reviewed_by": row["reviewed_by_name"],
            "returned_at": row["returned_at"].isoformat() if row["returned_at"] else None,
            "returned_by": row["returned_by_name"],
        })
    except Error:
        return database_error_response()


# -------------------------------------------------------------- Lost reports
def lost_report_owner_columns(user):
    """Return the correct reporter column/value for the signed-in account."""
    if user["role"] == "student":
        return "student_account_id", user["account_id"]
    if user["role"] == "faculty_staff":
        return "faculty_staff_account_id", user["account_id"]
    return None, None


def report_owner_is_active(user):
    """Confirm the session owner still exists and is active in MySQL."""
    table = ACCOUNT_TABLES.get(user.get("role"))
    if not table:
        return False

    account = fetch_one(
        f"SELECT status FROM {table} WHERE account_id = %s",
        (user.get("account_id"),),
    )
    return bool(account and account["status"] == "active")


def serialize_lost_report(report):
    """Convert a database lost-report row into the frontend response shape."""
    owner_role = "student" if report["student_account_id"] else "faculty_staff"
    owner_id = report["student_account_id"] or report["faculty_staff_account_id"]
    return {
        "id": report["report_id"],
        "item_name": report["item_name"],
        "category": report["category"],
        "description": report["description"],
        "last_seen": report["last_seen"],
        "date_lost": report["date_lost"].isoformat(),
        "status": report["status"],
        "reporter_id": owner_id,
        "reporter_role": owner_role,
        "reporter_name": report.get("reporter_name"),
        "photo_url": (
            url_for("lost_report_photo", report_id=report["report_id"])
            if report["has_photo"]
            else None
        ),
        "photo_urls": report_photo_urls("lost", report["report_id"], bool(report["has_photo"])),
        "createdAt": report["created_at"].isoformat(),
        "updatedAt": report["updated_at"].isoformat(),
        "reviewedAt": report["reviewed_at"].isoformat() if report["reviewed_at"] else None,
        "reviewedBy": report.get("reviewed_by_name"),
    }


def validate_lost_report_form(data):
    """Validate all required lost-item report fields."""
    item_name = str(data.get("item_name", "")).strip()
    category = str(data.get("category", "")).strip()
    description = str(data.get("description", "")).strip()
    last_seen = str(data.get("last_seen", "")).strip()
    date_lost = str(data.get("date_lost", "")).strip()

    if not item_name:
        return "item_name", "Enter the item name."
    if len(item_name) > 100:
        return "item_name", "Item name must not exceed 100 characters."

    category_error = category_validation_error(category)
    if category_error:
        return category_error

    if not description:
        return "description", "Enter a description of the lost item."
    if len(description) > 1000:
        return "description", "Description must not exceed 1,000 characters."

    if not last_seen:
        return "last_seen", "Enter where you last saw the item."
    if len(last_seen) > 150:
        return "last_seen", "Last seen location must not exceed 150 characters."

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_lost):
        return "date_lost", "Enter a valid lost date."

    try:
        datetime.strptime(date_lost, "%Y-%m-%d")
    except ValueError:
        return "date_lost", "Enter a real calendar date."

    return None


def read_lost_report(report_id):
    return fetch_one(
        """
        SELECT
            r.report_id,
            r.student_account_id,
            r.faculty_staff_account_id,
            r.item_name,
            r.category,
            r.description,
            r.photo_data IS NOT NULL AS has_photo,
            r.last_seen,
            r.date_lost,
            r.status,
            r.created_at,
            r.updated_at,
            r.reviewed_at,
            CASE
                WHEN r.student_account_id IS NOT NULL
                    THEN CONCAT(s.first_name, ' ', s.last_name)
                WHEN r.faculty_staff_account_id IS NOT NULL
                    THEN CONCAT(f.first_name, ' ', f.last_name)
            END AS reporter_name,
            CASE
                WHEN r.reviewed_by_faculty_staff_id IS NOT NULL
                    THEN CONCAT(rv.first_name, ' ', rv.last_name)
            END AS reviewed_by_name
        FROM lost_item_reports AS r
        LEFT JOIN students AS s
            ON s.account_id = r.student_account_id
        LEFT JOIN faculty_staff AS f
            ON f.account_id = r.faculty_staff_account_id
        LEFT JOIN faculty_staff AS rv
            ON rv.account_id = r.reviewed_by_faculty_staff_id
        WHERE r.report_id = %s
        """,
        (report_id,),
    )


# ----------------------------------------------------------- Lost-item workflow
# Student reports begin as pending; Faculty / Staff reports use the separate automatic-verify route.
@app.get("/api/lost-reports")
@role_required("student")
def student_lost_reports():
    try:
        reports = fetch_all(
            """
            SELECT
                r.report_id,
                r.student_account_id,
                r.faculty_staff_account_id,
                r.item_name,
                r.category,
                r.description,
                r.photo_data IS NOT NULL AS has_photo,
                r.last_seen,
                r.date_lost,
                r.status,
                r.created_at,
                r.updated_at,
                r.reviewed_at,
                CONCAT(s.first_name, ' ', s.last_name) AS reporter_name,
                CONCAT(rv.first_name, ' ', rv.last_name) AS reviewed_by_name
            FROM lost_item_reports AS r
            INNER JOIN students AS s
                ON s.account_id = r.student_account_id
            LEFT JOIN faculty_staff AS rv
                ON rv.account_id = r.reviewed_by_faculty_staff_id
            WHERE r.student_account_id = %s
            ORDER BY r.created_at DESC
            """,
            (session["user"]["account_id"],),
        )
    except Error:
        return database_error_response()

    return jsonify(reports=[serialize_lost_report(report) for report in reports])


@app.get("/api/faculty/lost-reports")
@role_required("faculty_staff")
def faculty_lost_reports():
    try:
        reports = fetch_all(
            """
            SELECT
                r.report_id,
                r.student_account_id,
                r.faculty_staff_account_id,
                r.item_name,
                r.category,
                r.description,
                r.photo_data IS NOT NULL AS has_photo,
                r.last_seen,
                r.date_lost,
                r.status,
                r.created_at,
                r.updated_at,
                r.reviewed_at,
                CASE
                    WHEN r.student_account_id IS NOT NULL
                        THEN CONCAT(s.first_name, ' ', s.last_name)
                    WHEN r.faculty_staff_account_id IS NOT NULL
                        THEN CONCAT(f.first_name, ' ', f.last_name)
                END AS reporter_name,
                CONCAT(rv.first_name, ' ', rv.last_name) AS reviewed_by_name
            FROM lost_item_reports AS r
            LEFT JOIN students AS s
                ON s.account_id = r.student_account_id
            LEFT JOIN faculty_staff AS f
                ON f.account_id = r.faculty_staff_account_id
            LEFT JOIN faculty_staff AS rv
                ON rv.account_id = r.reviewed_by_faculty_staff_id
            ORDER BY
                CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END,
                r.created_at DESC
            """
        )
    except Error:
        return database_error_response()

    return jsonify(reports=[serialize_lost_report(report) for report in reports])


@app.post("/api/lost-reports")
@role_required("student")
def create_lost_report():
    data = request.form
    validation_error = validate_lost_report_form(data)
    if validation_error:
        field, message = validation_error
        app.logger.warning("Lost report validation failed: %s - %s", field, message)
        return jsonify(field=field, message=message), 400

    try:
        photos = prepare_report_photos(request.files.getlist("photo"))
    except ValueError as exc:
        return jsonify(field="photo", message=str(exc)), 400

    photo = photos[0] if photos else None
    photo_data = photo["data"] if photo else None
    photo_filename = photo["filename"] if photo else None
    photo_mime_type = photo["mime_type"] if photo else None

    user = session["user"]

    try:
        if not report_owner_is_active(user):
            session.clear()
            return jsonify(message="Your account is no longer active. Please log in again."), 401

        connection = db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO lost_item_reports (
                student_account_id,
                item_name,
                category,
                description,
                photo_data,
                photo_filename,
                photo_mime_type,
                last_seen,
                date_lost
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user["account_id"],
                data["item_name"].strip(),
                data["category"].strip(),
                data["description"].strip(),
                photo_data,
                photo_filename,
                photo_mime_type,
                data["last_seen"].strip(),
                data["date_lost"].strip(),
            ),
        )
        report_id = cursor.lastrowid
        store_report_photos(cursor, "lost", report_id, photos)
        connection.commit()
    except IntegrityError:
        if "connection" in locals():
            connection.rollback()
        return database_error_response()
    except Error:
        if "connection" in locals():
            connection.rollback()
        return database_error_response()
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals():
            connection.close()

    try:
        notify_active_role(
            "faculty_staff",
            "New lost-item report pending review",
            f"A student submitted “{data['item_name'].strip()}”. Please review this report.",
            notification_type="pending-report",
            status="pending",
            related_report_id=report_id,
        )
    except Error:
        app.logger.exception("Could not create Faculty / Staff notifications for lost report %s", report_id)

    try:
        notify_active_role(
            "admin",
            "New student lost-item report",
            f"A student submitted “{data['item_name'].strip()}”. The report is awaiting Faculty / Staff verification.",
            notification_type="new-lost-report",
            status="pending",
            related_report_id=report_id,
        )
    except Error:
        app.logger.exception("Could not create Admin notifications for student lost report %s", report_id)

    report = read_lost_report(report_id)
    return jsonify(report=serialize_lost_report(report)), 201


@app.post("/api/faculty/lost-reports")
@role_required("faculty_staff")
def create_faculty_lost_report():
    data = request.form
    validation_error = validate_lost_report_form(data)
    if validation_error:
        field, message = validation_error
        app.logger.warning("Lost report validation failed: %s - %s", field, message)
        return jsonify(field=field, message=message), 400

    try:
        photos = prepare_report_photos(request.files.getlist("photo"))
    except ValueError as exc:
        return jsonify(field="photo", message=str(exc)), 400

    photo = photos[0] if photos else None
    photo_data = photo["data"] if photo else None
    photo_filename = photo["filename"] if photo else None
    photo_mime_type = photo["mime_type"] if photo else None

    user = session["user"]

    try:
        if not report_owner_is_active(user):
            session.clear()
            return jsonify(message="Your account is no longer active. Please log in again."), 401

        connection = db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO lost_item_reports (
                faculty_staff_account_id,
                item_name,
                category,
                description,
                photo_data,
                photo_filename,
                photo_mime_type,
                last_seen,
                date_lost,
                status,
                reviewed_by_faculty_staff_id,
                reviewed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'verified', %s, CURRENT_TIMESTAMP)
            """,
            (
                user["account_id"],
                data["item_name"].strip(),
                data["category"].strip(),
                data["description"].strip(),
                photo_data,
                photo_filename,
                photo_mime_type,
                data["last_seen"].strip(),
                data["date_lost"].strip(),
                user["account_id"],
            ),
        )
        report_id = cursor.lastrowid
        store_report_photos(cursor, "lost", report_id, photos)
        connection.commit()
    except IntegrityError:
        if "connection" in locals():
            connection.rollback()
        return database_error_response()
    except Error:
        if "connection" in locals():
            connection.rollback()
        return database_error_response()
    finally:
        if "cursor" in locals():
            cursor.close()
        if "connection" in locals():
            connection.close()

    try:
        notify_active_role(
            "admin",
            "New Faculty / Staff lost-item report",
            f"{user.get('name', 'A Faculty / Staff user')} submitted “{data['item_name'].strip()}”. This report was automatically verified and is public.",
            notification_type="new-lost-report",
            status="approved",
            related_report_id=report_id,
        )
    except Error:
        app.logger.exception("Could not create Admin notifications for Faculty / Staff lost report %s", report_id)

    report = read_lost_report(report_id)
    return jsonify(report=serialize_lost_report(report)), 201


@app.get("/api/public/lost-reports")
def public_lost_reports():
    """Return verified student lost-item reports for the shared public board.

    A report becomes public only after Faculty / Staff verification. Pending and
    rejected reports are intentionally excluded.
    """
    user = session.get("user")
    if not user:
        return jsonify(message="Authentication required."), 401

    try:
        reports = fetch_all(
            """
            SELECT
                r.report_id,
                r.student_account_id,
                r.faculty_staff_account_id,
                r.item_name,
                r.category,
                r.description,
                r.photo_data IS NOT NULL AS has_photo,
                r.last_seen,
                r.date_lost,
                r.status,
                r.created_at,
                r.updated_at,
                r.reviewed_at,
                CASE
                    WHEN r.student_account_id IS NOT NULL
                        THEN CONCAT(s.first_name, ' ', s.last_name)
                    WHEN r.faculty_staff_account_id IS NOT NULL
                        THEN CONCAT(f.first_name, ' ', f.last_name)
                END AS reporter_name,
                CONCAT(rv.first_name, ' ', rv.last_name) AS reviewed_by_name
            FROM lost_item_reports AS r
            LEFT JOIN students AS s
                ON s.account_id = r.student_account_id
            LEFT JOIN faculty_staff AS f
                ON f.account_id = r.faculty_staff_account_id
            LEFT JOIN faculty_staff AS rv
                ON rv.account_id = r.reviewed_by_faculty_staff_id
            WHERE r.status = 'verified'
            ORDER BY r.created_at DESC
            LIMIT 50
            """
        )
        total_row = fetch_one(
            "SELECT COUNT(*) AS total FROM lost_item_reports WHERE status = 'verified'"
        )
    except Error:
        return database_error_response()

    return jsonify(
        reports=[serialize_lost_report(report) for report in reports],
        count=int(total_row["total"] if total_row else 0),
    )


@app.get("/api/lost-reports/<int:report_id>/photo")
def lost_report_photo(report_id):
    user = session.get("user")
    if not user:
        return jsonify(message="Authentication required."), 401

    try:
        report = fetch_one(
            """
            SELECT
                student_account_id,
                faculty_staff_account_id,
                status,
                photo_data,
                photo_mime_type
            FROM lost_item_reports
            WHERE report_id = %s
            """,
            (report_id,),
        )
    except Error:
        return database_error_response()

    if not report or report["photo_data"] is None:
        return jsonify(message="Photo not found."), 404

    # Verified reports are public to authenticated users. Pending/rejected
    # report photos remain private to the reviewing workflow.
    if report["status"] != "verified":
        is_owner = (
            (user["role"] == "student" and report["student_account_id"] == user["account_id"])
            or (
                user["role"] == "faculty_staff"
                and report["faculty_staff_account_id"] == user["account_id"]
            )
        )
        if user["role"] != "faculty_staff" and not is_owner:
            return jsonify(message="You are not allowed to view this photo."), 403

    if user["role"] not in {"student", "faculty_staff", "admin"}:
        return jsonify(message="You are not allowed to view this photo."), 403

    return app.response_class(report["photo_data"], mimetype=report["photo_mime_type"])


@app.patch("/api/lost-reports/<int:report_id>")
@lost_report_user_required
def update_lost_report(report_id):
    data = request.form
    validation_error = validate_lost_report_form(data)
    if validation_error:
        field, message = validation_error
        return jsonify(field=field, message=message), 400

    try:
        if not report_owner_is_active(session["user"]):
            session.clear()
            return jsonify(message="Your account is no longer active. Please log in again."), 401

        owner_column, owner_id = lost_report_owner_columns(session["user"])
        if not owner_column:
            return jsonify(message="This account cannot manage lost-item reports."), 403

        report = fetch_one(
            f"""
            SELECT report_id, status
            FROM lost_item_reports
            WHERE report_id = %s AND {owner_column} = %s
            """,
            (report_id, owner_id),
        )
        if not report:
            return jsonify(message="Lost-item report not found."), 404
        if report["status"] != "pending":
            return jsonify(message="Only pending reports can be edited."), 409

        try:
            photos = prepare_report_photos(request.files.getlist("photo"))
        except ValueError as exc:
            return jsonify(field="photo", message=str(exc)), 400
        replace_photo = bool(photos)
        photo = photos[0] if photos else None
        photo_data = photo["data"] if photo else None
        photo_filename = photo["filename"] if photo else None
        photo_mime_type = photo["mime_type"] if photo else None

        if replace_photo:
            query = f"""
                UPDATE lost_item_reports
                SET item_name = %s,
                    category = %s,
                    description = %s,
                    photo_data = %s,
                    photo_filename = %s,
                    photo_mime_type = %s,
                    last_seen = %s,
                    date_lost = %s
                WHERE report_id = %s AND {owner_column} = %s AND status = 'pending'
            """
            params = (
                data["item_name"].strip(), data["category"].strip(),
                data["description"].strip(), photo_data, photo_filename,
                photo_mime_type, data["last_seen"].strip(), data["date_lost"].strip(),
                report_id, session["user"]["account_id"],
            )
        else:
            query = f"""
                UPDATE lost_item_reports
                SET item_name = %s,
                    category = %s,
                    description = %s,
                    last_seen = %s,
                    date_lost = %s
                WHERE report_id = %s AND {owner_column} = %s AND status = 'pending'
            """
            params = (
                data["item_name"].strip(), data["category"].strip(),
                data["description"].strip(), data["last_seen"].strip(),
                data["date_lost"].strip(), report_id, session["user"]["account_id"],
            )

        try:
            updated = execute_write(query, params)
        except Error:
            return database_error_response()

        if not updated:
            return jsonify(message="Only pending reports can be edited."), 409

        if replace_photo:
            try:
                connection = db_connection()
                cursor = connection.cursor()
                cursor.execute("DELETE FROM lost_item_photos WHERE report_id = %s", (report_id,))
                store_report_photos(cursor, "lost", report_id, photos)
                connection.commit()
            except Error:
                if "connection" in locals(): connection.rollback()
                return database_error_response()
            finally:
                if "cursor" in locals(): cursor.close()
                if "connection" in locals(): connection.close()
    except Error:
        return database_error_response()

    report = read_lost_report(report_id)
    return jsonify(report=serialize_lost_report(report))


@app.delete("/api/lost-reports/<int:report_id>")
@lost_report_user_required
def delete_lost_report(report_id):
    try:
        if not report_owner_is_active(session["user"]):
            session.clear()
            return jsonify(message="Your account is no longer active. Please log in again."), 401

        owner_column, owner_id = lost_report_owner_columns(session["user"])
        if not owner_column:
            return jsonify(message="This account cannot manage lost-item reports."), 403

        deleted = execute_write(
            f"""
            DELETE FROM lost_item_reports
            WHERE report_id = %s
              AND {owner_column} = %s
              AND status = 'pending'
            """,
            (report_id, owner_id),
        )
    except Error:
        return database_error_response()

    if not deleted:
        return jsonify(message="Only your pending reports can be deleted."), 409

    return jsonify(message="Lost-item report deleted successfully.")


@app.patch("/api/faculty/lost-reports/<int:report_id>/review")
@role_required("faculty_staff")
def review_lost_report(report_id):
    data = json_body()
    decision = str(data.get("status", "")).strip().lower()

    if decision not in {"verified", "rejected"}:
        return jsonify(message="Review status must be verified or rejected."), 400

    try:
        if not report_owner_is_active(session["user"]):
            session.clear()
            return jsonify(message="Your account is no longer active. Please log in again."), 401

        report = fetch_one(
            """
            SELECT report_id, status, student_account_id, faculty_staff_account_id, item_name
            FROM lost_item_reports
            WHERE report_id = %s
            """,
            (report_id,),
        )
        if not report:
            return jsonify(message="Lost-item report not found."), 404
        if report["status"] != "pending":
            return jsonify(message="This report has already been reviewed."), 409

        updated = execute_write(
            """
            UPDATE lost_item_reports
            SET status = %s,
                reviewed_by_faculty_staff_id = %s,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE report_id = %s
              AND status = 'pending'
            """,
            (decision, session["user"]["account_id"], report_id),
        )
    except Error:
        return database_error_response()

    if not updated:
        return jsonify(message="This report has already been reviewed."), 409

    try:
        # Resolve every Faculty / Staff copy of the actionable pending alert.
        # Keep it in their notification history, but never leave it unread.
        accepted = decision == "verified"
        execute_write(
            """
            UPDATE notifications
            SET read_at = COALESCE(read_at, CURRENT_TIMESTAMP),
                status = %s,
                title = %s,
                message = %s
            WHERE notification_type = 'pending-report'
              AND related_report_id = %s
              AND recipient_role = 'faculty_staff'
            """,
            (
                "approved" if accepted else "rejected",
                "Lost-item report verified" if accepted else "Lost-item report rejected",
                (
                    f"“{report['item_name']}” was verified and is now available on the public Recently Posted board."
                    if accepted
                    else f"“{report['item_name']}” was rejected and will not be published on the public Recently Posted board."
                ),
                report_id,
            ),
        )
    except Error:
        app.logger.exception("Could not resolve staff report notifications for report %s", report_id)

    try:
        accepted = decision == "verified"
        execute_write(
            """
            UPDATE notifications
            SET status = %s,
                title = %s,
                message = %s
            WHERE recipient_role = 'admin'
              AND notification_type = 'new-lost-report'
              AND related_report_id = %s
            """,
            (
                "approved" if accepted else "rejected",
                "Student lost-item report verified" if accepted else "Student lost-item report rejected",
                (
                    f"Student report “{report['item_name']}” was verified by Faculty / Staff and is now public."
                    if accepted
                    else f"Student report “{report['item_name']}” was rejected by Faculty / Staff and is not public."
                ),
                report_id,
            ),
        )
    except Error:
        app.logger.exception("Could not resolve Admin lost-report notifications for report %s", report_id)

    try:
        if report["student_account_id"]:
            accepted = decision == "verified"
            create_notification(
                "student",
                report["student_account_id"],
                f"Lost item report {'verified' if accepted else 'rejected'}",
                f"Your reported item “{report['item_name']}” was {'verified' if accepted else 'rejected'} by Faculty / Staff.",
                notification_type="lost-report-result",
                status="approved" if accepted else "rejected",
                related_report_id=report_id,
            )
    except Error:
        app.logger.exception("Could not create report result notification for report %s", report_id)

    return jsonify(report=serialize_lost_report(read_lost_report(report_id)))


# -------------------------------------------------------- Errors / Health / Pages
# These handlers keep technical database or upload errors from becoming confusing browser responses.
@app.errorhandler(413)
def upload_too_large(_error):
    return jsonify(field="photo", message="The upload is too large. A photo must not exceed 5 MB."), 413


@app.errorhandler(500)
def unexpected_error(error):
    """Keep JSON endpoints returning JSON instead of an HTML error page."""
    if wants_json() or request.method != "GET":
        return jsonify(message="Unexpected server error. Check the Flask console for details."), 500
    return error.get_response()


# Simple diagnostic endpoint used during setup and defense demonstrations.
# It verifies that the expected database schema is available.
@app.get("/health")
def health():
    """Open http://127.0.0.1:5000/health to see whether the database is ready."""
    problems = check_database()
    if problems:
        return jsonify(database="error", problems=problems), 503
    return jsonify(database="ok")


# -------------------------------------------------------------------- Dashboards
@app.after_request
def disable_caching_for_dashboards(response):
    """Stop the browser from showing a dashboard or account data from cache after logout."""
    if request.endpoint in {
        "student_dashboard",
        "faculty_dashboard",
        "admin_dashboard",
        "admin_accounts",
    } or (wants_json() and response.mimetype == "application/json"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/student")
@role_required("student")
def student_dashboard():
    stats = {"lost": 0, "found": 0}
    return render_template("student/dashboard.html", user=session["user"], stats=stats)


@app.get("/faculty")
@role_required("faculty_staff")
def faculty_dashboard():
    stats = {"lost": 0, "found": 0, "pending_reports": 0, "pending_claims": 0}
    return render_template("faculty/dashboard.html", user=session["user"], stats=stats)


@app.get("/admin")
@role_required("admin")
def admin_dashboard():
    return render_template("admin/dashboard.html", user=session["user"])


def print_startup_report():
    # Startup initializes small supporting tables when needed, then checks the
    # expected schema. This makes database setup problems visible immediately.
    settings = db_settings()
    print("-" * 64)
    print(
        f"FoundIT database: {settings['user']}@{settings['host']}:{settings['port']}"
        f"/{settings['database']}"
    )
    try:
        ensure_profile_fields()
        ensure_report_photo_tables()
        ensure_category_table()
        ensure_claim_return_fields()
        ensure_post_age_fields()
        ensure_notification_table()
        ensure_audit_table()
    except Error as exc:
        app.logger.warning("Could not initialize notifications/audit tables yet: %s", exc)
    problems = check_database()
    if problems:
        print("DATABASE PROBLEM - account creation and login will not work yet:")
        for problem in problems:
            print(f"  * {problem}")
    else:
        print("Database check passed: all required tables are present.")
    print("-" * 64)


if __name__ == "__main__":
    print_startup_report()
    app.run(host="127.0.0.1", port=5000, debug=False)
