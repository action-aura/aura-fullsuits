# Privacy

## Core rule

Customer business data stays on the customer's installation. The Owner Control Center (owned/operated by Action Aura) never stores, transmits, or retrieves:

- patient records, patient names, medical notes, prescriptions, diagnoses
- invoice line details, product names, supplier names
- customer names from POS transactions
- employee personal information
- uploaded documents, database contents, or any other private operational record

## What the Owner Control Center *may* receive

Only explicitly approved operational metadata, licensing information, health information, and aggregated usage counters — see `commercial_runtime/telemetry/` and `docs/architecture/customer-health-model.md` for the exact allowlisted fields per product. Server-side schema allowlists reject unknown fields; rejections are logged without storing the rejected payload.

## Telemetry defaults

Telemetry is disabled by default until configuration and consent requirements are satisfied. When enabled, it is tenant-isolated, authenticated, auditable, rate-limited, and privacy-preserving by construction (allowlist, not blocklist).

## Diagnostics

Customer-side diagnostic submission is opt-in. Prohibited in any diagnostic package: database files, patient data, invoices, transaction contents, customer records, passwords, tokens, encryption keys, local file content, unrestricted file-system archives. Centralized redaction is applied — not left to individual developers to avoid sensitive logs by convention.

## Retention

See `docs/architecture/` (once written in later phases) for the retention policy across raw telemetry (short), daily aggregates (longer), audit events (policy-defined), diagnostic packages (short), and commercial records (per business/legal requirement). This document does not make legal compliance claims.
