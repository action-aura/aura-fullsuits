# Aura Clinic — Sensitive Data Boundary (Phase 3)

Technical privacy protections only. **No claim of HIPAA, GDPR, or Jordanian medical-data compliance is made anywhere in this document or this codebase.**

## What's sensitive in this product

Patient names, national/medical IDs, phone numbers, addresses, diagnoses, treatment notes, prescriptions/medications, appointment reasons, blood type, emergency contacts. All of it lives in the `clinic_*` tables (see `clinic-dependency-map.md`) and nowhere else by design — there is no separate cache, log sink, or telemetry system in this phase that could leak it (telemetry itself is explicitly out of scope for Phase 3 and does not exist in this codebase yet).

## Inspection performed

| Area | Finding | Action |
|---|---|---|
| Exception handlers | `create_patient`'s error path returned `traceback.format_exc()` to the HTTP client — a stack trace from a SQL insert can echo back bound parameter values (patient name/DOB/phone/etc.) in the exception message on certain DB errors. | **Fixed** (Phase 3): removed `detail` from the client response; the full traceback is now logged server-side only via `logging.getLogger('aura.clinic')`, never serialized to the client. Test: `test_patient_creation_error_does_not_leak_traceback_to_client`. |
| Other exception handlers in `clinic_api.py` | Every other `except Exception as e: ... return jsonify({'error': str(e)})` pattern (e.g. `create_admin` in onboarding, generic 500s) returns only `str(e)` — a short exception message, not a traceback, not request data. Reviewed each one; none echoes patient field values. | No change needed — this is the safe pattern already used everywhere else. |
| `print(...)` debug statements | `clinic_api.py` has 2 `print()` calls, both in the Accounting-sync best-effort blocks (`print(f'[clinic] lab expense accounting sync skipped: {e}')` / similar for invoices) — logs only the exception message from a failed cross-subsystem call, never a patient field. | No change needed. |
| Audit log (`clinic_audit_log`, `_audit()` helper) | Records `action`, `entity`, `entity_id` (a numeric row ID), and an optional short `details` string. Reviewed every `_audit(...)` call site in `clinic_api.py`: `details` is only ever a short label (a patient code, an entity type tag like `'hard'` or `'patient:{pid}'`) — **never** a patient name, diagnosis, or note content. | No change needed — audit trail is already ID-based, not content-based. |
| Serialization helpers | `dict(row)` (sqlite3.Row → dict) is the only serialization mechanism in `clinic_api.py` — used exclusively to build the intentional API response payload for an authenticated, authorized request. There is no generic "serialize any object" helper that a future telemetry/diagnostics system could accidentally point at a `clinic_patients` row. | No change needed in this phase — flagged as the boundary a future telemetry implementation MUST respect (allowlist fields explicitly, never `dict(row)` a patient object directly into a telemetry payload). |
| Export helpers | None exist (confirmed, see parity matrix — no export feature in source). | Not applicable. |
| Temporary files | None created by `clinic_api.py` (no file uploads, no export, no temp-file usage anywhere in the module). | Not applicable. |
| Crash reports | No crash-reporting integration exists in this codebase (confirmed absent in Phase 0's platform-wide scan). | Not applicable — nothing to redact yet; flagged for whoever adds one later. |
| Database connection errors | `get_clinic_conn()`/`sqlite3.connect()` errors, if they occur, propagate as generic `sqlite3.OperationalError` messages (file path, lock state) — not credentials (SQLite has none) and not patient data. Verified no connection string embeds a password (none exists; this is a local file path). | No change needed. |
| Debug mode in packaging | `app.config['DEBUG']` is never set (Flask defaults to `False`); `app.run(..., debug=False)` explicit in `app.py`'s `__main__` fallback; the primary serving path is `waitress`, which has no debug/reloader mode at all. | Verified off by construction. Test: `test_debug_mode_disabled`. |

## Privacy tests (`products/clinic/tests/clinic_privacy_test.py`)

1. **Patient objects cannot leak into a future telemetry-safe payload** — there is no telemetry system in this codebase (Phase 3 scope excludes it per the task), so this is tested as a boundary contract: a test asserts that `dict(clinic_patients_row)` contains fields (`name`, `phone`, `notes`, etc.) that must never appear in any future allowlisted-telemetry schema, documenting the exact field list a Phase-7-equivalent telemetry client (when built) must exclude — same allowlist-not-blocklist principle used in the overall Aura FullSuits privacy model.
2. **Medical notes do not appear in sanitized exception logs** — triggers the `create_patient` error path with a payload containing a distinctive note string and asserts it is absent from the JSON response.
3. **Passwords and tokens are redacted** — reuses the same session-serialization check pattern as Retail's `test_session_never_contains_plaintext_password_or_hash`, applied to a Clinic login.
4. **Database connection errors do not expose local secrets** — asserts a forced connection-path error surfaces a generic message with no absolute developer-machine path or credential.
5. **File upload errors do not reveal unrestricted filesystem paths** — **NOT APPLICABLE**: no file upload feature exists in Clinic (see source inventory / parity matrix). A test documents this as an explicit "not applicable" marker rather than being silently absent.
6. **Raw patient database files are never treated as diagnostic artifacts** — there is no diagnostics-submission feature yet (out of scope, matches the overall commercial suite's Phase 10 which hasn't started); documented as a boundary rule for that future feature: it must never accept or auto-attach `clinic.db`/`registry.db`.
7. **Debug mode is not enabled in production packaging** — verified via the packaged Windows build (see `docs/build/clinic-windows-build-report.md`), and via a source-level assertion on `app.debug`.

## Retention

No retention policy is implemented in this phase (no telemetry, no Owner Control Center exists yet to define one against). Clinic's own local `clinic_audit_log` table has no automatic pruning — it grows indefinitely on the customer's own machine, same as the source implementation. Not a regression; flagged as a future operational concern for whoever builds backup/retention tooling.
