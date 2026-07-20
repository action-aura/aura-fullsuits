# Wave 1C -- Clinic Privacy Release Gate (Part J)

Technical privacy readiness only. No legal/regulatory compliance claim is made anywhere in this document.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | No patient PII in release logs | **CONFIRMED OK** | `logging.getLogger('aura.clinic')` calls log operation names/exceptions, not patient field values |
| 2 | No patient PII in Logcat | **CONFIRMED OK** | No `Log.d`/`Log.i`/`println` calls anywhere under Clinic Android's `app/src/main` |
| 3 | Sensitive screens protected (FLAG_SECURE or equivalent) | **CONFIRMED OK** | `SecureScreen.kt` component applied to `BillingScreen.kt` and `PatientDetailScreen.kt` -- the intended sensitive screens; mechanism physically verified working in Wave 1A |
| 4 | Recent-app preview behavior | **PASS (by construction)** | `FLAG_SECURE` on the sensitive screens above also blocks the OS recent-apps thumbnail from showing patient content on those screens -- this is the same Android flag, not a separate mechanism, so nothing additional to test |
| 5 | Role exposure (secretary vs. doctor read access) | **KNOWN, DOCUMENTED, NOT A NEW FINDING** | `docs/security/clinic-rbac-matrix.md` documents a real, deliberate, pre-existing design: any authenticated clinic staff member (including "secretary") can **read** patient overview/visits/appointments/prescriptions; only **writing** clinical notes/diagnosis/treatment/prescriptions is doctor-gated (403 for non-doctors). This is the source product's actual binary access model (verified by `clinic_rbac_test.py`, 24/24 passing), not an unwired richer 7-role catalog. Carried forward as an accepted, explained limitation -- see Residual Risk Register |
| 6 | Backup privacy | **CONFIRMED OK** | Backups stay local, never uploaded; restore requires admin role (`commercial_runtime/backup/routes.py::_require_admin()`) |
| 7 | Restore privacy | **CONFIRMED OK** | Same admin gate; cross-product restore explicitly rejected (`service.py:227-231`) |
| 8 | File path privacy | **CONFIRMED OK** | Data lives under `%LOCALAPPDATA%` / Android app-private storage, never a shared/world-readable location |
| 9 | Invoice/payment error privacy | **CONFIRMED OK** | Payment/login error responses are generic, do not echo patient/financial detail (Wave 1A MOB-003/004 fix, unchanged since) |
| 10 | No patient data in release artifacts | **CONFIRMED OK** | No `.db` files, no seed/demo patient data bundled in the signed APK/AAB or the Windows installer (Wave 1B security review, re-confirmed no change to packaging manifests this wave) |
| 11 | No synthetic patient data in Git | **CONFIRMED OK** | `.gitignore` excludes `*.db`/`*.sqlite*`; no committed database files found |
| 12 | No cloud transmission of patient data | **CONFIRMED OK** | Only outbound call in Clinic backend is `clinic_api.py:55`'s `_emit()` to `AURA_EVENT_BUS_URL`, unset by default (no-op), an explicit local/opt-in hook, not telemetry |
| 13 | No Owner connection | **CONFIRMED OK** | No Owner Control Center, licensing server, or telemetry endpoint exists in this codebase at all |
| 14 | No telemetry | **CONFIRMED OK** | Same basis as #13 |

## One real, pre-existing, documented gap carried forward (not new)
`docs/security/clinic-rbac-matrix.md` item 5: changing a user's `clinic_role` bumps `session_version`, but no decorator anywhere actually compares a live session's version against the stored value -- a demoted/disabled staff member's *existing* session is not automatically invalidated mid-session (a fresh login would pick up the new role; the existing session would not). This is a genuine security-adjacent gap, inherited from the original source product, explicitly documented rather than silently left in place. Not P0/P1 (requires an admin to have already granted access and then to demote/remove it while the affected user has an open session) -- registered as a residual risk, not a pilot blocker.

## Verdict
**Clinic privacy gate: PASS**, with two carried-forward, explicitly-documented, non-blocking limitations (item 5 above, and the session-version gap). Neither is new to this wave, neither is P0/P1, both are disclosed rather than hidden. No legal/regulatory compliance claim (HIPAA, GDPR, or otherwise) is made by this document or by either product.
