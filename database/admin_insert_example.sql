-- FoundIT administrator setup example
--
-- This is an example only. Replace the account details and password hash before use.
-- Generate the password hash with: python generate_admin_hash.py
-- Never store a plain-text administrator password in this file.

USE foundit;

INSERT INTO admins (
    account_id,
    first_name,
    last_name,
    email,
    password_hash,
    status
) VALUES (
    '0001',
    'System',
    'Administrator',
    'system.admin.0001.tc@umindanao.edu.ph',
    'YOUR_HASH',
    'active'
);
