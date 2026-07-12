# Aura Clinic — RBAC Matrix (Phase 3)

## Real enforced model (verified by reading `clinic_api.py` route-by-route, not assumed)

Clinic's actual access model is **binary**, not the 7-role catalog documented in `core/rbac/roles.py:166-194` (`clinic_admin`, `doctor`, `nurse`, `receptionist`, `lab_technician`, `pharmacist`, `clinic_accountant`, `medical_auditor`). That catalog is **not imported or referenced by any code path in `clinic_api.py`** — confirmed by grep, confirmed again in Phase 3. It is a defined-but-unwired reference design, not enforced behavior. Extracting it as if it were real would misrepresent the product. The three roles the task asks about map onto the real model as follows:

| Task's role | Real session state | Enforcement |
|---|---|---|
| **Admin** | `session['mt_role'] == 'admin'` | Global bypass — every `@require_clinic_role(...)` check passes automatically for admin (`mt_auth.py`'s `require_clinic_role`, line: `if session.get('mt_role') == 'admin': return f(*args, **kwargs)`). Admin also gates the entire `commercial_runtime/identity/onboarding_routes.py` admin-employee-management surface (employee CRUD, status, clinic-role assignment, audit log, stats, company settings). |
| **Doctor** | `session['clinic_role'] == 'doctor'` (set at login from `users.clinic_role`, or via admin's `PUT /api/admin/employees/<id>/clinic-role`) | Gates exactly 5 routes via `@require_clinic_role('doctor')`: `PATCH /visits/<id>` (diagnosis/treatment), `POST /visits/<id>/notes` (clinical notes), `POST /patients/<id>/followups`, `DELETE /followups/<id>`, `POST /prescriptions`. |
| **Secretary** | Any authenticated clinic user who is **not** admin and **not** `clinic_role='doctor'` (includes an explicit `clinic_role='secretary'` value, and also anyone with `clinic_role=''`) | Everything NOT in the doctor-gated list above: patients (create/edit/search/archive), appointments (book/checkin/update), doctors/services CRUD, lab expenses, billing (invoices/payments), reports. Gated only by `@mt_login_required` + `@mt_require_subsystem('clinic')` — i.e. "any logged-in clinic staff member," which is the source's actual design (front-desk staff need to create patients and book appointments without being doctors). |

**This is not a gap to "fix" toward the richer 7-role catalog** — the task says preserve source behavior, and the source's real behavior is this binary gate. The richer catalog remains documented as available-but-unused, exactly as Phase 0 found it, for a future phase to actually wire up if the product needs finer-grained roles.

## Route-level permission matrix

| Area | Route(s) | Secretary (default staff) | Doctor | Admin |
|---|---|---|---|---|
| Dashboard | `GET /dashboard/stats` | ✅ | ✅ | ✅ |
| Patients (read/search/create/edit/archive) | `GET/POST/PATCH/DELETE /patients*` | ✅ | ✅ | ✅ |
| Patient medical overview (visits/appointments on file) | `GET /patients/<id>` | ✅ (read) | ✅ | ✅ |
| Appointments (list/book/checkin/edit) | `/appointments*` | ✅ | ✅ | ✅ |
| Visits — create/view | `POST /visits`, `GET /visits/<id>` | ✅ | ✅ | ✅ |
| Visits — update diagnosis/treatment | `PATCH /visits/<id>` | ❌ 403 | ✅ | ✅ |
| Clinical notes | `POST /visits/<id>/notes` | ❌ 403 | ✅ | ✅ |
| Follow-up sheet — view | `GET /patients/<id>/followups` | ✅ | ✅ | ✅ |
| Follow-up sheet — add/delete | `POST /patients/<id>/followups`, `DELETE /followups/<id>` | ❌ 403 | ✅ | ✅ |
| Prescriptions — view | `GET /prescriptions` | ✅ | ✅ | ✅ |
| Prescriptions — create | `POST /prescriptions` | ❌ 403 | ✅ | ✅ |
| Invoices / payments | `/invoices*`, `/payments` | ✅ | ✅ | ✅ |
| Doctors — manage | `/doctors*` | ✅ | ✅ | ✅ |
| Lab expenses | `/lab-expenses*` | ✅ | ✅ | ✅ |
| Services | `/services*` | ✅ | ✅ | ✅ |
| Reports | `/reports/*` | ✅ | ✅ | ✅ |
| Settings | shared generic settings (not Clinic-owned — see extraction report) | — | — | — |
| User management (employees, roles, audit, company settings) | `commercial_runtime/identity/onboarding_routes.py` `/api/admin/*` | ❌ 403 | ❌ 403 | ✅ |
| Documents / attachments | N/A — feature does not exist in source (see source inventory) | — | — | — |
| Demo wipe/seed (Phase 3 hardened) | `/demo-wipe`, `/demo-seed` | ❌ 403 (admin-only gate) | ❌ 403 | ✅ (plus demo-mode env gate + confirmation token — see extraction report) |

All routes above additionally require `@mt_login_required` (valid session) and `@mt_require_subsystem('clinic')` (tenant has the clinic module licensed AND, for non-admins, an explicit `user_permissions` row with `access_level != 'none'` for `clinic`) — a logged-out user or a user without clinic access is rejected before role logic is even evaluated.

## Verified by test (`products/clinic/tests/clinic_rbac_test.py`)

1. Secretary (non-doctor, non-admin) gets 403 on `PATCH /visits/<id>`, `POST /visits/<id>/notes`, `POST /patients/<id>/followups`, `DELETE /followups/<id>`, `POST /prescriptions`.
2. Doctor gets 403 on `/api/admin/*` user-management endpoints (source does not grant doctors admin access — confirmed, not assumed).
3. Unauthenticated requests to every route category above return 401, not a silent empty response or 500.
4. A logged-out user (no session) cannot reach any protected Clinic endpoint.
5. Changing a user's `clinic_role` via `PUT /api/admin/employees/<id>/clinic-role` bumps `session_version`; the RBAC design intent is that a stale session should stop being valid, but **the actual session invalidation on `session_version` mismatch is not implemented anywhere in `mt_login_required`/`mt_require_subsystem`/`require_clinic_role`** in the source code — confirmed by inspection: `session_version` is bumped and stored, but no decorator ever compares it against the stored session's own version. This is a genuine, pre-existing source gap (documented, not fabricated as fixed) — see the parity matrix and extraction report.
6. A disabled user (`status='disabled'`) is rejected by `mt_login_required` (checks `status` on every request, not just at login) — verified.
7. Cross-tenant isolation: a second clinic installation session cannot see or affect another company's patients/appointments (`company_id` scoping verified end-to-end, including on the newly-hardened demo-wipe).

## Item 5 above — flagged, not silently fixed

Wiring `session_version` comparison into the login-check decorators would change authentication behavior for *both* Retail and Clinic (it lives in the shared `commercial_runtime/identity/mt_auth.py`), is not something the source implementation actually does today (so "preserve source behavior" argues against silently adding it), and is exactly the kind of cross-product behavioral change the task's Retail-freeze rule is meant to prevent from happening as a side effect of Clinic work. Documented here as a real, verified gap for a future security-hardening phase — not fixed in Phase 3.
