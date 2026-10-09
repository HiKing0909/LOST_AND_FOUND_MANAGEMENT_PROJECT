# FoundIT — Backend Defense Guide

## Purpose of the comments

The comments added to `app.py` are written for a project-defense audience. They focus on **what a section does, why it exists, and what security/business rule it protects** instead of explaining every Python keyword.

The goal is to let a panelist read the backend and quickly understand the system flow.

---

## 1. Overall backend flow

```text
Browser / Frontend
       |
       v
Flask Route
       |
       +--> Authentication / Role Check
       |
       +--> Server-side Validation
       |
       +--> MySQL Query
       |
       +--> Business Rule / Ownership Check
       |
       +--> Notification / Audit (when required)
       |
       v
JSON Response
```

### Simple defense explanation

> "The frontend is responsible for user interaction, but the backend is responsible for security and data integrity. Every important request is checked again on the server before MySQL is changed."

---

## 2. Authentication

Important functions:

- `parse_school_email()` — checks the UMindanao email format and extracts the account ID.
- `find_account()` — looks up the account in the correct account table.
- `login_submit()` — verifies the password hash and account status.
- `account_view()` — stores only safe account information in the Flask session.

### Defense explanation

> "The password is not stored as plain text. We store a password hash and verify the entered password against that hash. The user's role is determined by the database account, not by a role value sent from the browser."

---

## 3. Authorization

`role_required()` is the main authorization decorator.

Examples:

- Student-only routes use `@role_required("student")`.
- Faculty/Staff-only routes use `@role_required("faculty_staff")`.
- Admin-only routes use `@role_required("admin")`.

### Defense explanation

> "Authentication tells us who the user is. Authorization tells us what that user is allowed to do. We separate these two responsibilities so a logged-in user cannot automatically access another role's functions."

---

## 4. Database access

The backend uses:

- `fetch_one()` for one row.
- `fetch_all()` for lists.
- `execute_write()` for INSERT, UPDATE, and DELETE.

SQL parameters are passed separately through MySQL Connector instead of concatenating user input into SQL values.

### Defense explanation

> "Database access is centralized through helper functions. User-provided values are passed as SQL parameters, which reduces SQL-injection risk and keeps connection cleanup consistent."

---

## 5. Category database

The `categories` table is the **master list** for item categories.

The flow is:

```text
Admin manages categories
        |
        v
MySQL categories table
        |
        v
GET /api/categories
        |
        v
Student / Faculty report form
        |
        v
Backend validates submitted category
```

### Why this is better than a hardcoded list

- Categories can change without editing Python/HTML.
- Administrators can activate/deactivate categories.
- Sort order is stored in the database.
- New categories become available to report forms automatically.
- The backend still validates the category, so the browser cannot invent a value.

### Important design choice

Reports store the category text instead of relying on a live foreign-key relationship to the category master record.

That means a historical report can remain unchanged even if an administrator later renames or deactivates the category.

### Defense explanation

> "The category table controls what users can select today, while the report keeps the category value that was recorded at the time. This protects historical data when the master list changes."

---

## 6. Lost-item workflow

### Student

```text
Student submits report
        |
        v
status = pending
        |
        v
Faculty / Staff reviews
     /       \
 verify      reject
   |            |
   v            v
 public       private
```

### Faculty / Staff

Faculty/Staff lost reports are automatically verified according to the current project rules.

### Defense explanation

> "Student reports require a review step before becoming public. This prevents unverified student submissions from immediately appearing on the public board. Faculty/Staff reports follow a different trusted workflow defined by the system requirements."

---

## 7. Found-item and claim workflow

Found items are submitted by Faculty/Staff and are automatically verified.

A user can submit a claim with a proof/explanation message.

Claim states:

```text
pending -> accepted -> returned
        \
         -> rejected -> may submit again
```

`accepted` means the claimant is approved to receive the item. `returned` means the physical handover was completed.
When a claim is returned, the system records the staff processor and timestamp, removes the found item from the public claiming pool, closes other pending claims for that same item, and makes a printable return receipt available.

### Defense explanation

> "Claims are stored as separate records because the system needs a controlled lifecycle. An accepted claim represents approval, while returned represents the actual physical handover. The return step is recorded with the Faculty/Staff account and timestamp so the system has a traceable record of who completed the transaction."

---

## 8. Messaging

Messages are stored in the `messages` table.

The backend checks the recipient server-side. A reply uses the original message to determine who the sender is.

### Why the reply endpoint is safe

The browser sends the original message ID and reply body. It does **not** choose the recipient account for the reply.

The backend first verifies that the original message belongs to the current user, then resolves the original sender from MySQL.

### Defense explanation

> "For replies, we do not trust the browser to tell us who should receive the message. We look up the original message and resolve its sender on the server. This prevents a user from changing a recipient ID in the browser and redirecting a reply to another account."

---

## 9. One-week unresolved-item reminder

The Recently Posted interface already displays the report's Posted date. The backend reuses the report's `created_at` value to determine whether seven days have passed.

The rule is:

```text
Posted
  ↓
7 days pass
  ↓
Still active/verified?
  ↓
Has reminder already been sent?
  ↓
No → Notify active Faculty / Staff
  ↓
Store one_week_notified_at
```

The reminder is generated only once per report. Returned found items are excluded. The check is triggered by Faculty / Staff notification polling, which keeps the implementation simple for the Flask school-project deployment.

### Defense explanation

> "We did not create another Posted date because the database already records the exact time the report was created. We compare that timestamp with the current time, and after seven days the system sends a one-time reminder to active Faculty / Staff accounts. The `one_week_notified_at` field prevents repeated notifications."

## 9. Notifications

Notifications are database-backed and include:

- recipient role
- recipient account ID
- notification type
- message/status
- related report/claim/message IDs
- read timestamp

The API filters by the currently signed-in account.

### Defense explanation

> "Notifications are not a shared browser list. Each database row belongs to one exact account, so the notification API only returns notifications for the current user."

---

## 10. Audit log

The `audit_logs` table records important administrator actions such as:

- account activation/suspension
- item edits/deletions
- category changes
- administrative communication actions where implemented

### Defense explanation

> "The audit log provides accountability. Instead of relying on a browser-only history, the server records which administrator performed the action and what target was affected."

---

## 11. Photo security and validation

Photo uploads are checked for:

- allowed MIME type: JPG, PNG, WEBP
- maximum photo size: 5 MB
- file signature/magic bytes for the supported formats
- sanitized filename using `secure_filename()`

Photos are stored as MySQL BLOB data.

### Defense explanation

> "We do not trust the filename or MIME type alone. The backend also checks the file signature and size before saving the image."

---

## 12. Database integrity

The SQL schema uses:

- primary keys
- unique keys
- foreign keys
- check constraints
- indexes
- controlled ENUM status values

The application also checks the expected schema through `/health` and the startup report.

### Defense explanation

> "Validation happens at two levels: the Flask application checks business rules, while MySQL constraints protect the database itself."

---

# Deep scan findings

## Strengths found

1. Role separation is clear.
2. Passwords are hashed.
3. SQL values use parameterized queries.
4. Ownership checks are present for user-controlled records.
5. Notifications are account-isolated.
6. Messages are persistent in MySQL.
7. Replies resolve recipients server-side.
8. Categories are database-driven.
9. Admin actions have an audit trail.
10. Uploaded photos receive server-side validation.
11. Database errors are translated into understandable responses.
12. The clean SQL script provides a reproducible database structure.

## Items to remember during future hardening

These were **not changed in this commenting pass** because the goal was to document the existing backend without changing its behavior.

### A. CSRF protection

The application uses cookie-based Flask sessions and several state-changing POST/PATCH/DELETE routes. A production deployment should consider CSRF protection for browser requests.

### B. Rate limiting

Login and other sensitive endpoints do not currently show application-level rate limiting. A production deployment should consider rate limiting or account lockout controls.

### C. Runtime table creation

`ensure_*_table()` functions are convenient for a school project, but production systems normally use a dedicated migration system rather than creating/checking tables during requests/startup.

### D. Category foreign key trade-off

Reports currently store category text instead of a `category_id` foreign key. This is intentional for historical stability in this project, but a larger production system could use a category ID plus a historical snapshot/name.

### E. Clean database script warning

`database/foundit.sql` intentionally drops and recreates the `foundit` database. It is excellent for a clean testing environment but must never be run blindly against a database containing data that must be preserved.

### F. Secret files

`.env` and `instance/secret_key` contain environment-specific/secrets-related data and should not be distributed in project ZIPs or committed to source control. Use `.env.example` for sharing configuration instructions.

---

# Fast panelist Q&A

### Why use Flask?

> "Flask gives us a lightweight Python web backend where we can clearly separate routes, validation, authentication, authorization, and database operations."

### Why MySQL?

> "The system contains structured relationships between accounts, reports, claims, messages, notifications, categories, and audit records. MySQL provides constraints and relationships that are appropriate for this data."

### Why not trust JavaScript validation?

> "JavaScript can be modified or bypassed by the client. The backend repeats important validation before writing to MySQL."

### Why separate account tables?

> "Student, Faculty/Staff, and Administrator accounts have different ID rules and permissions. Separate tables make those differences explicit and easier to enforce."

### Why use notifications in the database?

> "Browser localStorage is tied to a browser, not a real account. Database notifications are tied to the recipient's role and account ID, so the data is persistent and isolated."

### Why is the category list in the database?

> "Categories are system data that may change. Storing them in MySQL lets administrators maintain the list without changing application code, while the backend still validates every submitted category."

### What is the most important security principle in the backend?

> "Never trust client-side input. The backend authenticates the user, checks the role, validates the data, verifies ownership, and only then performs the database operation."

---

## Scan scope

This review inspected the supplied FoundIT project structure, Flask backend, database schema, templates, and JavaScript source. Syntax checks were run for the Python backend and all JavaScript files.

No live MySQL database was modified or required for this static scan.

## Profile Feature Defense Notes

- **Why is Logout inside the profile menu?** It keeps the sidebar cleaner and groups account actions together while preserving the existing logout route.
- **Can users change their password?** No. The profile is view/personalization only. Password changes are intentionally not exposed to users.
- **Can users change their account ID, email, role, or status?** No. Those are official account fields controlled by the system/admin workflow.
- **Where is the profile picture stored?** In the user's account row in MySQL, with its MIME type, so it persists across sessions and browsers.
- **Who can retrieve a profile picture?** Only the currently authenticated account can retrieve its own profile-picture endpoint.
- **What file types are allowed?** JPG/JPEG, PNG, and WEBP, with a 2 MB server-side size limit and basic file-signature validation.
- **What happens if there is no picture?** FoundIT displays the user's initials as the fallback avatar.

### Report image uploads

Lost Item and Found Item reports support up to **5 images per report**. Accepted formats are **PNG, JPG, JPEG, and WEBP**. Users can remove an image from the selection before submitting so an accidental photo choice does not require restarting the form. FoundIT does not impose an application-level per-image size limit; actual storage remains bounded by the MySQL/server capacity. Report images are stored in dedicated multi-photo tables, while the first image is mirrored to the legacy primary-photo columns for compatibility.
