# Phase 5 -- Owner Data Classification (Part W)

## Class 1: Allowed -- Owner-native commercial/technical metadata
Staff identity/roles/sessions/MFA; product/platform/version/plan/price/add-on/entitlement catalog; customer organization/contact/note metadata; subscription/renewal/payment records (Owner's own commercial bookkeeping); license records and license-key hashes (never plaintext); installation/device labels and status; activation-event records; Owner's own audit log; Owner system settings; Owner database backup records.

## Class 2: Forbidden -- customer business/medical data
Retail: sale/invoice line items, inventory quantities, product cost/price data belonging to a customer's own catalog, the customer's own end-customer records.
Clinic: patient identity/demographics, appointments, prescriptions, diagnoses, medical/clinical notes, invoice/payment amounts for patient care.
Universal: card/bank credentials, local database file paths or contents, precise geolocation, raw hardware identifiers (IMEI, full MAC address).

## Class 3: Secrets -- never stored in recoverable form
Staff passwords (Argon2id hash only), MFA TOTP secrets (encrypted at rest, app-level key), MFA recovery codes (hashed only), license key secrets (HMAC only, pepper never in DB), session/invitation tokens (SHA-256 hash only), the Owner database's own connection credentials (environment-only).

## Enforcement mapping
| Class | Enforcement mechanism | Evidence |
|---|---|---|
| Class 1 | Explicit SQLAlchemy models under `owner/app/models/`; nothing else exists | `owner-database-schema.md` |
| Class 2 | No column/table name matches a forbidden term (structural test); no import path from `owner/` into `products/*` or `commercial_runtime/*` (structural test); allowlist serializers reject any forbidden key at construction time | `owner/tests/test_data_boundary.py` (6/6 passing) |
| Class 3 | Dedicated hashing/encryption modules (`app/security/passwords.py`, `app/security/mfa.py`, `app/security/license_keys.py`, `app/security/tokens.py`); audit redaction (`app/audit/services.py:redact`) | `owner/tests/test_licensing.py`, `owner/tests/test_audit.py` |

See `product-to-owner-data-allowlist.md` for the field-by-field contract allowlists and `forbidden-data-enforcement-report.md` for the test-execution evidence.
