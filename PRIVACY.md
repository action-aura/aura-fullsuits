# Privacy

## Core rule (corrected 2026-10-13)

By default, customer business data stays on the customer's installation. **Turning on multi-device sync changes that**, and this document says so plainly because an earlier version of it claimed the opposite and the code never did.

### What the sync relay on the Owner server stores (when a shop uses cloud sync)

When two or more devices of one shop sync through Owner (`/api/sync/v1`), Owner stores every synced change as a row in `owner_sync_events` (and a copy of any change it rejected in `owner_sync_quarantine`). The content is **plaintext JSON, not end-to-end encrypted**: the server operator and anyone with database access can read it. It includes the business records the product syncs: products, customers (names, phones), suppliers, sales with their line items and prices, payments, returns, purchase orders, employees/users and their permissions, and general-ledger journals.

How long it is kept: a synced change is deleted by a nightly job once **every active device of that licence** has downloaded it. There is **no age limit**: a shop with a device that is switched off, or retired but not deactivated, keeps its history on the server until that device catches up or is deactivated. Quarantined rows are kept until an operator resolves them and are never auto-deleted. Database backups (encrypted) hold the same content for the backup retention period.

Planned, not yet built: end-to-end encryption of these payloads with a per-shop key (Owner would then store ciphertext only) and an age cap with snapshot-based bootstrap. Until then, do not describe the relay as private from Action Aura.

### What the Owner Control Center still does not receive

Without cloud sync enabled, Owner receives none of the above. Independent of sync, it never receives uploaded documents or the local database file, and diagnostic packages exclude databases, invoices and credentials.

### E-mail sent through the Owner relay

A product may ask Owner to send e-mail (verification, password reset, invitations). The message (recipient, subject, body) is queued on Owner. **Once the message is finished (sent, permanently failed or cancelled) and 30 days old, the body, subject and the recipient's name part are erased**; the row keeps its kind, status, timestamps and the recipient's domain for audit. Window: `OWNER_EMAIL_BODY_RETENTION_DAYS` (default 30, minimum 2). Unsent messages are never erased.

### Sign-in telemetry

Owner records each staff sign-in attempt (address typed, client IP, success, reason) to throttle guessing. Rows older than 180 days are deleted nightly (`OWNER_LOGIN_ATTEMPT_RETENTION_DAYS`).

## What the Owner Control Center *may* receive (operational metadata)

Licensing information, health information, and aggregated usage counters, validated by server-side schema allowlists. (Earlier text pointed at `commercial_runtime/telemetry/`, which does not exist in this repository; the allowlist lives with the Owner API schemas.)

## Telemetry defaults

Telemetry is disabled by default until configuration and consent requirements are satisfied. When enabled, it is tenant-isolated, authenticated, auditable, rate-limited, and privacy-preserving by construction (allowlist, not blocklist).

## Diagnostics

Customer-side diagnostic submission is opt-in. Prohibited in any diagnostic package: database files, patient data, invoices, transaction contents, customer records, passwords, tokens, encryption keys, local file content, unrestricted file-system archives. Centralized redaction is applied — not left to individual developers to avoid sensitive logs by convention.

## Retention

See `docs/architecture/` (once written in later phases) for the retention policy across raw telemetry (short), daily aggregates (longer), audit events (policy-defined), diagnostic packages (short), and commercial records (per business/legal requirement). This document does not make legal compliance claims. Crash reports are kept 90 days. No Arabic version of this document exists in the repository; when one is written it must follow this corrected text.
