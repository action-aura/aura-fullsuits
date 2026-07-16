# Clinic Privacy and Sensitive-Data Audit

No legal/regulatory compliance claim is made anywhere in this document (per the
audit's own instructions) — this is a technical review of where patient data can
appear and who can see it, not a HIPAA/GDPR determination.

## NEW FINDING (not previously documented): Secretary role can read full clinical content

`GET /api/sub/clinic/patients/<id>` (`clinic_api.py` line 180-195) is guarded only
by `@mt_login_required` + `@mt_require_subsystem('clinic')` — **no
`@require_clinic_role` gate** — and returns the patient row plus their last 20
`clinic_visits` rows and last 20 `clinic_appointments` rows in full, to **any**
authenticated user of the company regardless of role. `require_clinic_role('doctor')`
is applied only to the *write* routes for visit-completion, visit notes,
follow-ups, and prescriptions (lines 428, 451, 489, 815) — not to any *read*
route. **This directly answers the audit's explicit question: yes, a Secretary
(or any non-doctor role) can read the same clinical visit history a Doctor can,
including whatever diagnosis/treatment content is stored on `clinic_visits`
rows** — the role model only restricts who can *write* clinical content, not who
can *read* it. Neither `docs/privacy/clinic-sensitive-data-boundary.md` (the
existing privacy doc referenced by the codebase's own comments) nor any prior
phase's documentation identifies this. **Classified P2** — "authorization risk" /
over-broad read access to sensitive medical data by a role that shouldn't need
it for front-desk duties (scheduling/billing); not P1 because it requires an
already-authenticated, already-employed user of the correct company (not an
external/cross-tenant exposure) — the risk is over-privileged internal access,
not a breach.

## PII in logs — PROVEN, one narrow, correctly-scoped exception

Zero application-level logging of request/response bodies exists anywhere in
`clinic_api.py` except one deliberate, already-fixed exception handler:
`create_patient`'s `except` block (lines 165-179) logs
`traceback.format_exc()` **server-side only**, specifically because the prior
(pre-Phase-3) behavior returned that same traceback to the *client*, which
could echo back patient name/DOB/phone as literal values inside a SQL-bind
error message — an information-disclosure risk explicitly called out in the
code's own comment, with the client-facing fix already applied (client now
gets a generic `'Could not create patient record.'` message). **Residual,
lower-severity point not previously documented**: the server-side log file
itself (wherever `logging.getLogger('aura.clinic')` writes to) *will* contain
patient PII in that specific error path, since `traceback.format_exc()`
includes the exception's argument values. This is standard practice for
server-side diagnostic logs (more detail server-side than client-side is the
correct general pattern) but means local log-file access controls / retention
policy matter for this product in a way that isn't documented anywhere. P4 —
worth a one-line note in an ops doc, not a code defect.

## Android — reused/re-verified from Phase 4 (`docs/privacy/clinic-android-data-boundary.md`)

Re-confirmed in this pass (not just cited): zero `Log`/`println` calls in
`android/aura-clinic/app/src/main/java`, no `HttpLoggingInterceptor`,
`allowBackup="false"`, no exported components beyond the launcher activity, no
WebView. **New in this pass, not in the Phase 4 doc**: neither app sets
`FLAG_SECURE` (see `10`), so a screenshot or the Android recent-apps switcher
preview can capture whatever patient data happens to be on-screen at the OS
level — Phase 4's privacy doc did not check for this specific flag. This is a
genuine addition to Clinic's privacy exposure list, UNVERIFIED for actual
consequence (no device to actually screenshot/verify the OS behavior) but
PROVEN by source (the flag is provably absent).

## Windows — desktop-side check (new in this pass)

`products/clinic/desktop/launcher_clinic.py` logs to
`<app-data>\logs\startup.log` via Python's `logging` module — the messages
logged (confirmed by reading the launcher in a prior phase and re-checked
here) are lifecycle/status messages ("Aura Clinic launcher starting", "App
data: <path>", server start/ready status) — **no request bodies, no patient
data, no session tokens**. **No finding** — Windows-side logging does not leak
patient data, consistent with the backend's own logging posture above.

## Backups / exports / temp files — see `18`

No backup mechanism exists at all (see `06`/`07`/`18`), so there is no
backup-file exposure surface to assess for either product — a null result, not
a pass, since there's nothing yet to audit.

## Access by role — summary table

| Role | Can read patient demographics | Can read visit/clinical content | Can write clinical content (notes/prescriptions/visit completion) | Can manage billing |
|---|---|---|---|---|
| Admin | Yes | Yes | Yes | Yes |
| Doctor | Yes | Yes | Yes | Not independently confirmed either way in this pass — UNVERIFIED |
| Secretary (or any other non-doctor authenticated role) | Yes | **Yes — over-broad, see finding above** | No (blocked by `@require_clinic_role('doctor')`) | Yes (billing routes were not found to be role-restricted in the sample read) |

## Search for hardcoded/sample sensitive data in source

Grepped `products/clinic/` and `commercial_runtime/` for common test-fixture
patient-name patterns and phone-number-shaped literals outside the `tests/`
directory: none found in application code. Test fixtures (`clinic_workflow_test.py`
etc.) use obviously synthetic values ("Patient Test One"-style names,
fabricated phone numbers) consistent with the audit's own synthetic-data
requirement — not flagged as a finding, this is expected/correct test hygiene,
confirmed rather than assumed.
