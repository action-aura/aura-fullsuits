# Phase 9.5B-R Milestone 1 — Current Owner String Inventory (gated surfaces)

Real strings found via direct grep/read of every gated template and its backing route, `owner/app/`.
Full classification in `string-classification-report.md`. This is the raw inventory.

## `layout/base.html`
"Aura Owner Control Center" (brand/title), "Log out", nav labels: "Dashboard", "Customers", "Catalog",
"Subscriptions", "Licenses", "Installations", "Renewals", "Pilots", "Emergency Ext.", "Activation Reviews",
"Notifications", "My Queue", "Reconciliation", "Staff", "Employees", "Employee Dashboard", "Audit Log",
"Security Events", "Backups", "Activation Service", "My Profile", "Account".

## `auth/login.html`
"Login" (title), "Aura Owner Control Center", "Email", "Password", "Log in". Route messages:
"Invalid email or password.", "Too many attempts -- try again later." (`account_disabled` case renders
generically too — see route).

## `auth/mfa_verify.html`
"Verify MFA", "Enter your authenticator code", "6-digit code (or a recovery code)", "Verify". Route:
"Too many attempts -- try again later.", "Invalid code."

## `auth/mfa_enroll.html`
"Enable MFA", "Enable multi-factor authentication", "Scan this into any TOTP authenticator app (Google
Authenticator, 1Password, etc.), or enter the secret manually.", "Manual secret", "Provisioning URI",
"Enter the current 6-digit code to confirm", "Confirm and enable MFA". Route: "Invalid code -- scan the QR
code again and try the current 6-digit code."

## `auth/mfa_recovery_codes.html`
"Recovery codes", "Save your recovery codes", "Each code can be used once if you lose access to your
authenticator. They will never be shown again after you leave this page.", "No codes to display -- this
page can only be viewed once, immediately after generation.", "Continue to dashboard".

## `auth/accept_invitation.html`
"Accept invitation", "Set up your Aura Owner account", "Email", "Your name", "Choose a password (min 12
chars, mix of 3 character classes)", "Create account", "Return to login". Route: "Invitation is invalid or
has expired.", "Name is required.", "An account already exists for this email.", and
`PasswordPolicyError` messages ("Password must be at least N characters.", "Password must mix at least 3
of: lowercase, uppercase, digit, symbol.").

## `auth/change_password.html`
"Change password", "Current password", "New password (min 12 chars, mix of 3 character classes)",
"Change password" (button). Route: "Current password is incorrect."

## `auth/reauth.html`
"Confirm identity", "Confirm your identity", "This action requires a fresh MFA confirmation.", "6-digit
code", "Confirm". Route: "MFA is not enabled on this account -- enable it to perform this action.",
"Invalid code."

## `employees/list.html`
"Employees", "Search", "Name or employee number" (placeholder), "Department", "All", "Status", "Role",
"Presence", "Apply", "Name", "Employee #", "Title", "Department", "Status", "Presence", "Start date",
"View", "Add employee", "Dashboard", "Pending invitations", "Page X of Y -- N employee(s).", "Previous",
"Next", "No employees match these filters.", status words (Pending/Active/Suspended/Terminated/Archived),
presence words (Online/Recently Active/Offline — title-cased in template).

## `employees/new.html`
"Add employee", "Email", "Employee number", "Full name", "Phone", "Job title", "Department", "Employment
start date", "Manager", "None", "Commission plan", "Roles", "Require MFA (recommended for all new internal
accounts)", "Create employee & send setup link".

## `employees/detail.html`
"Account security", "Email", "Account status", "Active"/"Disabled (reason)", "MFA", "Enrolled"/"Not
enrolled", "Required", "Last login", "Never", "Active sessions", "Roles and permissions", "Changing roles
requires a fresh MFA confirmation and immediately revokes all of this account's active sessions.", "Save
roles", "Edit profile", "Full name", "Job title", "Department", "Manager", "None", "Save changes",
"Lifecycle actions", "Activate", "Suspend" + confirm text, "Reactivate", "Terminate" + confirm text,
"Archive", "Revoke all sessions", "Sessions", "Created", "Last seen", "Platform", "Status", "Revoked",
"Active", "Revoke", "No sessions.", "Audit timeline", "When", "Action", "Reason", "No audit events yet.",
"Presence" + "(application usage only -- not attendance/working hours)".

## `employees/dashboard.html`
"Employee dashboard", "Employee-domain metrics only, generated {ts}.", "Headcount", "Total employee
profiles", "Active employees", "Setup pending", "Suspended", "Terminated", "Archived", "Presence
(application usage, not attendance)", "Online now", "Recently active", "Offline", "Security", "Employees
without completed MFA (where required)", "Locked / disabled accounts", "Currently active sessions".

## `employees/invitation_created.html` / `invitations.html` / `not_found.html`
"Employee invitation created", "Send this one-time setup link to {email} through a secure, trusted
channel. It is shown here only once and expires in 3 days.", "Back to employees", "Pending employee
invitations", "Email", "Roles", "Expires", "Status", "Expired", "Open", "Reissue", "Revoke", "No pending
invitations.", "That employee record does not exist."

## `profile/index.html`
"My profile", "Full name", "Employee number", "Job title", "Department", "Manager", "Employment start
date", "Employment status", "Presence", "No employee profile is linked to this account yet.", "Update your
details", "Only display name and phone are self-editable. Employee number, role, employment status, start
date, manager, and commission plan are management-controlled.", "Display name", "Phone", "Save",
"Security", "Email", "MFA", "Change password", "Enroll / re-enroll MFA", "My sessions".

## `profile/sessions.html`
"My sessions", "Created", "Last seen", "Platform", "Status", "This device", "Revoked", "Active", "Revoke",
"No sessions."

## Non-gated Phase 5-8 templates (37 files) — inventoried at directory level only

`catalog/*`, `customers/*`, `subscriptions/*`, `licensing/*`, `licensing_admin/*`, `installations/*`,
`commercial_ops/*`, `staff/*`, `audit/*`, `system/*` — all confirmed to `{% extends "layout/base.html" %}`
(inherit the i18n/RTL foundation structurally); body strings not individually inventoried this phase per
the documented scope reduction.

## JavaScript

Zero `<script>` tags anywhere in `owner/app/templates/` (`grep -rn "<script"` → 0 matches) — Owner's UI is
server-rendered only. However, inline `onsubmit="return confirm('...')"` attributes **do** exist and carry
real user-visible strings (a browser-native `confirm()` dialog is JS execution, even without a `<script>`
tag) — 2 in the gated set, both `employees/detail.html`:
`"Suspend this employee? This blocks login and revokes all active sessions immediately."` and
`"Terminate this employee? This is not reversible without an explicit rehire process."`. Milestone 14's
real scope this phase is these 2 strings, localized via a server-rendered, already-translated Jinja value
interpolated into the `onsubmit` attribute (no client-side catalog needed — see
`javascript-localization-report.md`).
