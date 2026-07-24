# Phase 7 -- Clinic Restriction Capability Matrix

Built directly from every `@clinic_bp.route` in `products/clinic/backend/api/clinic_api.py` (36 routes, enumerated by grep during Part A, not assumed from the spec's suggested list). Every mutation route below must carry `@require_license_capability(...)` (Part T); every read route is explicitly capability-tagged too, even though its guard always passes in every state except `LOCAL_STATE_CORRUPT`, so the mapping stays exhaustive and auditable rather than "everything not listed is implicitly fine."

## Route -> capability mapping

| Route | Method | Capability | Allowed when RESTRICTED/EXPIRED/SUSPENDED/REVOKED? |
|---|---|---|---|
| `/dashboard/stats` | GET | `clinic.records.read` | Yes |
| `/patients` | GET | `clinic.records.read` | Yes |
| `/patients` | POST | `clinic.patient.create` | **No** |
| `/patients/<id>` | GET | `clinic.records.read` | Yes |
| `/patients/<id>` | PATCH | `clinic.patient.update` | See note below |
| `/patients/<id>` | DELETE | `clinic.patient.update` | **No** |
| `/appointments` | GET | `clinic.records.read` | Yes |
| `/appointments` | POST | `clinic.appointment.create` | **No** |
| `/appointments/<id>` | PATCH | `clinic.appointment.update` | See note below |
| `/appointments/<id>/checkin` | POST | `clinic.appointment.update` | See note below |
| `/visits` | POST | `clinic.visit.create` | See note below |
| `/visits/<id>` | GET | `clinic.records.read` | Yes |
| `/visits/<id>` | PATCH | `clinic.visit.update` | See note below |
| `/visits/<id>/notes` | POST | `clinic.notes.create` | See note below |
| `/patients/<id>/followups` | GET | `clinic.records.read` | Yes |
| `/patients/<id>/followups` | POST | `clinic.appointment.create` | **No** |
| `/followups/<id>` | DELETE | `clinic.appointment.update` | **No** |
| `/doctors` | GET | `clinic.records.read` | Yes |
| `/doctors` | POST | `clinic.staff.manage` | **No** |
| `/doctors/<id>` | PATCH | `clinic.staff.manage` | **No** |
| `/services` | GET | `clinic.records.read` | Yes |
| `/services` | POST | `clinic.settings.update` | **No** |
| `/lab-expenses` | GET | `clinic.records.read` | Yes |
| `/lab-expenses` | POST | `clinic.invoice.create` | **No** |
| `/lab-expenses/<id>` | DELETE | `clinic.invoice.create` | **No** |
| `/invoices` | POST | `clinic.invoice.create` | **No** |
| `/invoices` | GET | `clinic.records.read` | Yes |
| `/invoices/<id>` | GET | `clinic.records.read` | Yes |
| `/payments` | POST | `clinic.payment.record` | **No** |
| `/prescriptions` | POST | `clinic.prescription.create` | See note below |
| `/prescriptions` | GET | `clinic.records.read` | Yes |
| `/reports/appointments` | GET | `clinic.records.read` | Yes |
| `/reports/revenue` | GET | `clinic.records.read` | Yes |
| `/reports/audit` | GET | `clinic.records.read` | Yes |
| `/demo-wipe` | DELETE | `clinic.settings.update` (dev/onboarding only, gated separately -- see note) | **No** |
| `/demo-seed` | POST | `clinic.settings.update` (dev/onboarding only, gated separately -- see note) | **No** |

Not yet present as routes but reserved in the capability vocabulary per the spec's suggested list, for forward compatibility with the guard decorator design (`clinic.backup.create`, `clinic.backup.restore`, `clinic.data.export`, `clinic.record.print`, `clinic.license.manage`): backup/restore already exist as a *separate* blueprint (`commercial_runtime.backup.routes.make_backup_blueprint`), guarded independently -- see Part P note below, not omitted, just not part of `clinic_api.py`'s own route list.

## Decision (resolved by reading `clinic_api.py`'s actual handler bodies during Part Q implementation)

- `POST /visits` (`create_visit`) only ever creates a brand-new visit row -- no "resume an existing visit" concept exists. **Blocked** (`clinic.visit.create`), same bucket as new patient/appointment creation.
- `PATCH /visits/<id>` (`update_visit`) mutates an *existing* visit already in progress -- sets diagnosis/treatment, can mark it `completed`. This is squarely "completion of an already-open encounter." **Allowed** (`clinic.visit.update`).
- `POST /visits/<id>/notes` (`add_note`) appends a clinical note to an *existing* visit. Same continuity-of-care category as the line above. **Allowed** (`clinic.notes.create`).
- `POST /prescriptions` (`create_prescription`) is tied to an existing patient (and usually an existing visit) -- a doctor examining a patient in an in-progress encounter must be able to prescribe; blocking this would create real patient-safety harm disproportionate to a licensing lapse. **Allowed** (`clinic.prescription.create`) -- this is the "narrowly scoped emergency clinical" carve-out Part Q names explicitly.
- `POST /appointments/<id>/checkin` (`checkin`) only flips an *already-scheduled, pre-existing* appointment to `waiting` -- the patient is already physically present. Blocking this would strand a patient who showed up for a legitimate pre-existing appointment. **Allowed** (`clinic.appointment.checkin`, new capability code -- Part Q's suggested list didn't anticipate a separate checkin action, added since the route exists).
- `PATCH /appointments/<id>` (`update_appointment`) can change `appointment_dt` (reschedule) in addition to status/notes/doctor -- reschedule is functionally equivalent to creating a new future booking (sidesteps "no new appointment creation" by just moving an old one forward indefinitely). No route-level way to allow status-only edits without also allowing reschedule. **Blocked** (`clinic.appointment.update`), erring toward the stricter reading given the reschedule capability.
- `POST /patients/<id>/followups` (`add_followup`) always creates a brand-new followup row -- no "open followup being completed" concept exists (unlike visits). Optional documentation, not a life/safety-critical action. **Blocked** (`clinic.followup.create`, new capability code).
- `PATCH /patients/<id>` (`update_patient`) is routine demographic correction (phone/address/notes) on an existing patient, not new-record creation. **Allowed** (`clinic.patient.update`).
- `DELETE /patients/<id>` (`delete_patient`) supports a genuinely destructive hard-delete path (permanently removes clinical records, admin-only, blocked if billing history exists) in addition to a soft-archive. Per Part P's "must never delete records" principle, this is **blocked** entirely under restriction (`clinic.patient.delete`, new capability code) -- both the soft and hard path, since the route can't be split by outcome at the guard layer.

## Baseline restricted-mode policy (applies regardless of how the open decision above resolves)

**Always allowed in `RESTRICTED`/`EXPIRED`/`SUSPENDED`/`REVOKED`**: every `clinic.records.read`-tagged route (patient search/history, appointments, visits, prescriptions, invoices/payments viewing, reports), printing existing records (handled client-side against already-fetched data, no separate print route exists in the backend to guard), backup, restore, export (once those routes exist -- Part T requirement, tracked in `phase7-implementation-plan.md`), the licensing status/activation/deactivation routes themselves (Part T: a capability guard must never be able to lock out the very interface used to fix the licensing state), and normal staff login (existing `commercial_runtime.identity.auth_routes`, entirely unrelated to and untouched by the licensing guard).

**Always blocked**: new patient registration, new appointment creation, new invoice creation, new payment recording, new doctor/service/staff creation, non-essential settings changes, `/demo-wipe` and `/demo-seed` (destructive/data-seeding routes -- already development/onboarding-only per their existing gating, and blocking them under restriction is strictly additive safety, never a regression).
