# FoundIT — Campus Lost and Found System

FoundIT is a Flask + MySQL web application for managing campus lost-and-found reports, verification, found-item claims, notifications, messaging, and administrator records.

The project is designed around three account roles:

- **Student** — reports lost items and receives report/claim/message updates.
- **Faculty / Staff** — reports lost/found items, verifies student lost reports, and reviews claims.
- **Administrator** — manages accounts, items, categories, audit records, and system communication.

---

## 1. Project Structure

```text
FoundIT_V6/
├── app.py                         # Flask backend and API routes
├── requirements.txt               # Python dependencies
├── run.bat                        # Windows setup/run helper
├── .env.example                   # MySQL configuration template
├── generate_admin_hash.py         # Generates a secure admin password hash
├── database/
│   ├── foundit.sql                # Canonical database schema + seed categories
│   └── admin_insert_example.sql   # Example administrator INSERT
├── templates/                     # Jinja/HTML pages and shared partials
├── static/                        # CSS, JavaScript, images, and fonts
└── STUDY_GUIDE.md      # Simple explanations for project defense
```

There are no old migration/repair README files in this version. **`database/foundit.sql` is the single clean-install database script.**

---

## 2. Main System Flow

```text
User Login
    ↓
Role-based Dashboard
    ↓
┌──────────────────────────────────────────────────────┐
│ Student                                             │
│   Report Lost Item → Pending → Faculty/Staff Review │
│                     → Verified / Rejected           │
│                                                      │
│ Faculty/Staff                                       │
│   Report Lost → Automatically Verified              │
│   Report Found → Automatically Verified             │
│   Verify Student Reports → Review Claims            │
│                                                      │
│ Administrator                                       │
│   Manage Accounts → Items → Categories → Audit Log  │
└──────────────────────────────────────────────────────┘
    ↓
MySQL Database
```

Important system data is stored in MySQL rather than relying on browser storage. This allows data to survive logout, browser changes, and application restarts.

---

## 3. Requirements

Install these before running FoundIT:

- Python 3.10+ recommended
- MySQL 8.0+
- MySQL Workbench (recommended for database setup)
- A modern web browser

Python packages are listed in `requirements.txt`.

---

## 4. Database Setup

### Important

`database/foundit.sql` intentionally contains:

```sql
DROP DATABASE IF EXISTS foundit;
```

This creates a clean database but **deletes the existing `foundit` database**. Back up important data before running it.

### Steps

1. Open MySQL Workbench.
2. Open `database/foundit.sql`.
3. Run the entire script.
4. Confirm that the `foundit` database contains the required tables.

The database includes tables for:

- `students`
- `faculty_staff`
- `admins`
- `categories`
- `lost_item_reports`
- `found_item_reports`
- `claim_requests` — claim lifecycle and return tracking
- `messages`
- `notifications`
- `audit_logs`

### Category database

Item categories are master data stored in the `categories` table. They are **not hardcoded into the report form**.

- Report forms load active categories from `GET /api/categories`.
- The backend validates submitted categories against active database records.
- Administrators can add, rename, reorder, activate, or deactivate categories.
- Deactivation is preferred to deletion so historical data is not unnecessarily destroyed.
- Category changes are recorded in the audit log.

---

## 5. Configure MySQL

Copy `.env.example` to `.env` and set your local MySQL settings:

```text
FOUNDIT_DB_HOST=127.0.0.1
FOUNDIT_DB_PORT=3306
FOUNDIT_DB_USER=root
FOUNDIT_DB_PASSWORD=YOUR_MYSQL_PASSWORD
FOUNDIT_DB_NAME=foundit
```

If your local MySQL account has no password, leave `FOUNDIT_DB_PASSWORD` empty.

You may also configure `FOUNDIT_SECRET_KEY` for the Flask session. If it is not supplied, the development setup creates a persistent key under the ignored `instance/` directory.

**Never commit `.env`, `instance/secret_key`, database passwords, or real administrator credentials.**

---

## 6. Install and Run

### Windows — easiest method

Run:

```text
run.bat
```

The script creates `.venv` if necessary, installs the requirements, and starts Flask.

### Manual method

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:5000/
```

---

## 7. Database Health Check

Open:

```text
http://127.0.0.1:5000/health
```

A healthy database connection returns JSON similar to:

```json
{"database":"ok"}
```

If the health check fails, check the Flask terminal and verify the `.env` settings and MySQL service.

---

## 8. Account Rules

### Student

- Account ID: exactly 6 digits.
- Can register through the website.
- Can submit lost-item reports.

### Faculty / Staff

- Account ID: exactly 4 digits.
- Can register through the website.
- New accounts start as `pending` and require administrator activation.
- Lost-item reports are automatically verified.
- Found-item reports are automatically verified and can enter the public feed.

### Administrator

- Account ID: exactly 4 digits.
- Administrator accounts are inserted directly into MySQL.
- Administrators manage accounts, items, categories, audit records, and communication.

### Creating an administrator

Generate a password hash locally:

```text
python generate_admin_hash.py
```

Copy the generated hash into `database/admin_insert_example.sql`, replace the example values as needed, and run that SQL against the clean `foundit` database.

The script stores only a Werkzeug password hash; it does not store the administrator's plain-text password.

---

## 9. Lost and Found Workflows

### Student lost item

```text
Student submits report
        ↓
Pending
        ↓
Faculty / Staff reviews
      ↙   ↘
Verified  Rejected
   ↓
Public Recently Posted
```

### Faculty / Staff lost item

Faculty / Staff reports are automatically verified and do not enter the student verification queue.

### Faculty / Staff found item

Found items reported by Faculty / Staff are automatically verified and become eligible for the public Found Items feed.

### Claim workflow

```text
Public Found Item
       ↓
User submits proof/explanation
       ↓
Pending Claim
     ↙       ↘
Accepted    Rejected
   ↓            ↓
Approved       User may submit again
   ↓
Returned to Owner
   ↓
Printable Receipt
```

Claim states are stored in MySQL as `pending`, `accepted`, `rejected`, or `returned`.
An **accepted** claim means Faculty / Staff approved the claimant; it does not mean the physical item has already been handed over.
When the item is physically returned, the backend records `returned_at` and `returned_by_faculty_staff_id`, changes the claim to `returned`, and changes the found item to `returned` so it leaves the public claiming pool.
A printable return receipt is generated from server-side database data.
The backend prevents a user from having multiple active claim attempts for the same found item.

---

## 10. One-week unresolved-item reminder

The public Recently Posted cards already show each report's `Posted` date, which comes from the report `created_at` timestamp. FoundIT uses that same database timestamp for the seven-day rule.

- Only active/verified lost and found posts are checked.
- When `created_at` reaches seven days in the past, Faculty / Staff receive a reminder notification.
- Each report is reminded only once using `one_week_notified_at`.
- Returned found items are excluded because their status is `returned`.
- The check runs during Faculty / Staff notification polling, so the school-project deployment does not require a separate background worker.
- The database update and notification inserts are performed in one transaction to prevent duplicate reminders during simultaneous polling requests.

### Defense explanation

> "The system already records when a report was posted, so we reuse `created_at` instead of creating another date field. After seven days, the backend checks whether the item is still active and whether a reminder has already been sent. This gives Faculty / Staff a one-time reminder for unresolved public posts without changing the existing Recently Posted interface."

## 10. Messaging and Notifications

Messages are stored in the MySQL `messages` table. A recipient can open a message and reply to the original sender.

Important rules:

- The recipient is resolved by the backend from the supplied email.
- A user cannot message or reply to themselves.
- Reply recipients are resolved from the original message in the database rather than trusted from the browser.
- Reading a message also resolves its related message notification.
- Messages are isolated by recipient role and account ID.
- Notifications are isolated by recipient role and account ID.
- Unread counters come from database rows belonging to the current account.

This prevents one user from seeing another user's messages or notifications.

---

## 11. Security and Data Integrity

The backend includes several important protections:

- Passwords are stored using Werkzeug password hashing.
- Role-based route protection is enforced on the server.
- Client-submitted IDs are checked against the current account when ownership matters.
- SQL values are passed as parameters instead of being concatenated into queries.
- Photo uploads are limited by size and MIME type.
- Database foreign keys protect relationships between reports, claims, messages, and notifications.
- Administrator actions are recorded in `audit_logs`.
- Database errors shown to users are simplified; detailed database errors remain in the Flask server log.

The browser is treated as **untrusted input**. Important validation and authorization decisions are repeated on the Flask backend.

---

## 12. Backend Code Organization

`app.py` is organized into understandable sections:

1. Environment and Flask configuration
2. Account lookup and authentication helpers
3. Database connection helpers
4. Category master-data handling
5. Messaging and notification tables
6. Audit logging
7. Validation and authentication routes
8. Administrator routes
9. Found-item and claim routes
10. Lost-item routes
11. Error handlers and health checks
12. Dashboard page routes and startup checks

Comments in the backend focus on **purpose and reasoning**, not on repeating obvious Python syntax. This makes the code easier to explain during a panel defense.

For a simple defense-oriented explanation and possible panel questions, read `STUDY_GUIDE.md`.

---

## 13. Troubleshooting

### “Cannot reach MySQL”

- Make sure MySQL is running.
- Check `FOUNDIT_DB_HOST` and `FOUNDIT_DB_PORT`.
- Check the MySQL username and password.

### “Database does not exist”

Run the complete `database/foundit.sql` script.

### “Tables are missing or outdated”

Use the clean database script and restart the application. Do not mix old repair scripts with this version.

### Login/account creation fails

Check `/health`, then check the Flask terminal for the database error. Also confirm that the account exists in the current database and has the correct status.

### Uploaded image is rejected

The system accepts JPG, PNG, and WEBP images up to 5 MB.

---

## 14. Defense Quick Answers

**Why Flask?**

Flask provides a lightweight Python web framework for routing, sessions, validation, API endpoints, and server-side authorization.

**Why MySQL?**

FoundIT has related structured data—accounts, reports, claims, messages, notifications, categories, and audit logs—so a relational database is appropriate for consistency and relationships.

**Why are categories in a database?**

Categories are master data that may change. Storing them in MySQL allows administrators to manage the list without editing application code.

**Why validate on the backend if the form already validates?**

Browser validation can be bypassed. The Flask backend is the trusted enforcement point.

**Why separate Student, Faculty / Staff, and Admin tables?**

The project has different account rules and permissions for each role. Separate tables make those rules explicit and easier to enforce.

**Why use an audit log?**

Administrative changes should be traceable. The audit log records who performed an important action, what was affected, and when it happened.

**Why are notifications account-specific?**

A notification belongs to a particular recipient. Filtering by both role and account ID prevents cross-user notification leakage.

**Why does the system keep old category text on historical reports?**

Historical records should describe what was recorded at the time of the report. Changing the master category list should not silently rewrite old reports.

---

## 15. Before Defense

1. Run the clean database script on a test database.
2. Configure `.env`.
3. Start FoundIT and confirm `/health`.
4. Test one Student account.
5. Test one Faculty / Staff account.
6. Test one Administrator account.
7. Test lost-item verification.
8. Test a found-item claim.
9. Test message → reply.
10. Test category add/edit/deactivate.
11. Check the administrator audit log.
12. Read `STUDY_GUIDE.md` and practice explaining **why** each major part exists.

**Goal:** do not memorize code line-by-line. Understand the flow, the reason for each major design choice, and where the database stores the information.

## My Profile and Profile Picture

The signed-in Student, Faculty / Staff, and Administrator profile area is now a single account menu. Clicking the profile area opens **My Profile** and **Logout** options. Logout is no longer shown as a separate button beside the profile.

My Profile displays the account's important information: full name, account ID, school email, role, account status, and registration date. Passwords are never returned by the profile API; the interface only shows a protected/masked password notice and explicitly does not provide password-change controls.

Users can upload or replace their own profile picture. Profile pictures are stored in the account's MySQL row so they persist across sessions and browsers. JPG, PNG, and WEBP are accepted up to 2 MB, with server-side MIME/signature validation. If no picture exists, FoundIT falls back to the user's initials.

Profile-picture access is always scoped to the currently signed-in account. The profile feature does not expose another user's picture or account details.

### Report image uploads

Lost Item and Found Item reports support up to **5 images per report**. Accepted formats are **PNG, JPG, JPEG, and WEBP**. Users can remove an image from the selection before submitting so an accidental photo choice does not require restarting the form. FoundIT does not impose an application-level per-image size limit; actual storage remains bounded by the MySQL/server capacity. Report images are stored in dedicated multi-photo tables, while the first image is mirrored to the legacy primary-photo columns for compatibility.
