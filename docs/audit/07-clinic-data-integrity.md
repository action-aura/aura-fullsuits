# Aura Clinic — Data Integrity Audit

Evidence gathered by direct source read (`products/clinic/backend/database/schema.py`,
`products/clinic/backend/api/clinic_api.py`).

## Schema

13 tables (`schema.py` lines 74-243), all `CREATE TABLE IF NOT EXISTS`,
`id INTEGER PRIMARY KEY AUTOINCREMENT`. Declared FKs:
`clinic_appointments.patient_id`, `clinic_visits.patient_id`,
`clinic_visit_notes.visit_id`, `clinic_invoices.patient_id`,
`clinic_invoice_items.invoice_id`, `clinic_payments.invoice_id`,
`clinic_prescriptions.patient_id`, `clinic_followups.patient_id`,
`clinic_lab_expenses.patient_id`. **Not declared as FKs despite naming**:
`clinic_appointments.doctor_id`, `clinic_visits.doctor_id`/`appointment_id`,
`clinic_invoices.visit_id`, `clinic_invoice_items.service_id`,
`clinic_prescriptions.visit_id`, `clinic_followups.visit_id`.
`patient_code UNIQUE NOT NULL` and `invoice_number UNIQUE NOT NULL` are the only
two non-PK unique constraints.

**Finding — FK enforcement IS ON for every Clinic connection**, correctly, and
this is actually asserted by an existing passing test
(`clinic_workflow_test.py::test_foreign_key_enforcement_on_clinic_connection`,
**PASS** this audit). `schema.py`'s `_conn()` sets `journal_mode=WAL`,
`busy_timeout=30000`, and `PRAGMA foreign_keys=ON` — all three, every connection,
only one connection factory exists. This is the **correct** posture and directly
contrasts with Retail's identical-shaped schema, which does not set this PRAGMA
(`06`). Given both products share the same author/pattern, this is best read as
an inconsistency that crept in (Clinic got it right, Retail didn't), not a
deliberate design difference — worth fixing in Retail to match Clinic, not the
other way around.

## Migrations

No `schema_version` table. `init_clinic()` runs 5 unconditional
`ALTER TABLE ADD COLUMN` statements every startup (`discount`, `notes`,
`prescription_id`, `accounting_synced` on `clinic_invoices`; `invoice_id` on
`clinic_prescriptions`), each independently wrapped in
`try: cur.execute(_alter) except Exception: pass` — no `PRAGMA table_info`
pre-check (unlike Retail's `addcol()`), purely try-and-swallow-duplicate-column-error.
Functionally equivalent end-state to Retail's approach, same "no distinction
between expected and unexpected failure" weakness, same "no version tracking, no
rollback" characteristics.

## Soft delete vs. hard delete

`delete_patient` is the most complete delete path in either product: soft-deletes
by default (`status='archived'`); a `?hard=1` real cascade delete is
**admin-role-gated**, **blocked entirely if the patient has any billing history**
(a real, correctly-designed safety rail Retail's `delete_product` does not have
an equivalent of), and cascades across `clinic_followups`, `clinic_visit_notes`
(via a subquery on `clinic_visits.patient_id` — safe because the visit's
`patient_id` was itself already validated), `clinic_visits`, `clinic_appointments`,
`clinic_prescriptions`, then `clinic_patients` — all correctly `company_id`-scoped
except the visit_notes subquery, which is transitively safe (scoped through an
already-validated parent). `delete_followup` and `delete_lab_expense` are plain
hard deletes, both `company_id`-scoped. Demo-wipe: 10 statements, correctly
scoped/gated, and — unlike Retail's — **wrapped in an explicit
`BEGIN TRANSACTION`/`commit()`**.

## Multi-tenant isolation — the historical IDOR, and its current state

**Historical finding, already fixed, re-verified in this audit:** commit `57e74a0`
("security: fix cross-tenant IDOR across 8 clinic routes", 2026-07-12) introduced
the `_owned(conn, table, row_id, cid)` helper and applied it to 8 previously
unguarded routes — `create_visit`, `add_note`, `add_followup`,
`create_prescription`, `create_invoice`, `create_lab_expense`,
`create_appointment`/`update_appointment`, and **`record_payment`, described in
the commit message as "the worst case: its SELECT and UPDATE against
clinic_invoices had NO company_id filter at all"** (meaning, prior to that fix,
any authenticated user of any company could read another company's invoice total
and mark it as paid). `git diff 57e74a0 HEAD -- products/clinic/backend/api/clinic_api.py`
is **empty** — nothing has touched this file since the fix; no new route has been
added without the same care. `_owned()` is now used at 15 call sites across the
file. **PROVEN, by diff, that the fix is intact and unmodified.**

Two non-exploitable style gaps found (mutating statement doesn't repeat a check
its input already passed): `create_visit`'s appointment-status update
(depends on an already-`_owned()`-validated `appointment_id`), and
`create_invoice`'s best-effort accounting-sync block (operates on `inv_id` =
this same request's own newly-inserted row, not user input). Neither is a live
IDOR.

## Transaction boundaries

Neither `create_invoice` nor `record_payment` — Clinic's two money-writing
routes — wraps its multi-statement sequence in an explicit `BEGIN TRANSACTION`
or a `try/except`/rollback. Both rely entirely on sqlite3's implicit transaction
and a single trailing `conn.commit()`. **This is a real gap, and it's worse here
than Retail's equivalent gaps** because these are the two routes that move money:

- `create_invoice`: if the `clinic_invoice_items` insert loop raises after the
  parent `clinic_invoices` row has already been inserted, the exception
  propagates uncaught — no rollback, connection not explicitly closed. A
  partially-written invoice (header row exists, some or all line items don't)
  is a plausible outcome of any single malformed item in a multi-item invoice.
- `record_payment`: if an exception occurs between the `clinic_payments` insert
  and the `clinic_invoices` status `UPDATE`, a payment is recorded but the
  invoice's `status`/`amount_paid` is never updated to reflect it — the
  invoice would show as unpaid/partial while a real payment row exists,
  directly contradicting the audit's own reconciliation goal ("total collected"
  vs. "total billed" would disagree with the sum of actual payment rows).

Classified **P2** (data-integrity, with a direct financial-reporting consequence
via the second case above) — not P1, since it requires an exception mid-function
(not normal-path behavior), but the blast radius (money already moved, invoice
state now wrong) is more severe than a typical partial-write bug.

`demo_wipe` does use an explicit `BEGIN TRANSACTION`/`commit()` — so the
inconsistency is specifically "the two real, everyday money-writing routes lack
it; the rarely-used demo-wipe utility has it," which is backwards from what a
production system should prioritize.

## Clinic data-model relationships audited

users/roles (shared registry, not clinic's own schema) ↔ patients ↔ appointments
↔ visits ↔ visit_notes, doctors (referenced by id, not FK-enforced) ↔
appointments/visits, prescriptions ↔ patients/visits, invoices ↔ patients ↔
invoice_items, payments ↔ invoices, followups ↔ patients/visits, lab_expenses ↔
patients. All relationships used consistently in the routes read; several
`_id` columns referencing `doctors`/`services`/`visits` are not FK-declared
despite being used as such — same "declared where the author remembered to,
omitted elsewhere" pattern seen in Retail, just with FK enforcement actually
turned on here so the ones that ARE declared are actually checked.

## Backup / restore

**None exists** — same repo-wide finding as Retail (`18` covers this once for
both products; there is no product-specific backup code to differentiate).

## Android

Same conclusion as Retail (`06`): no separate Android data-integrity surface.
Clinic's Chaquopy build stages the identical `products/clinic/backend` tree; no
native Kotlin database code exists in `android/aura-clinic/`.
