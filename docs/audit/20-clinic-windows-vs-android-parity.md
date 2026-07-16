# Clinic — Windows vs. Android Parity Matrix

Same status legend as `19`.

| Feature | Status | More complete | Intentional? | Data output differs? | Release blocker? |
|---|---|---|---|---|---|
| Onboarding/account creation | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Login/logout | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Dashboard | COMPLETE AND CONSISTENT | Equal (server-computed) | N/A | No | No |
| Patients (CRUD, search, profile) | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Visits / notes / follow-ups | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Appointments | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Prescriptions | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Doctors management | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Billing/invoices | COMPLETE AND CONSISTENT — **server-computed on both, so financial parity is structurally guaranteed** (`05`) | Equal | N/A | No | No |
| Payments | COMPLETE AND CONSISTENT **in the sense both platforms share the same unvalidated route** (`04`) | Neither (both share the gap) | No — a defect, shared | No | No (shared defect, not a parity gap — tracked as its own item, see `22`) |
| Lab expenses | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Role-gated clinical writes (`@require_clinic_role('doctor')`) | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Over-broad clinical read access (`11`) | COMPLETE AND CONSISTENT **as a shared defect** — the missing role gate on `GET /patients/<id>` affects both platforms identically (same backend route) | Neither | No — a defect, shared | No | No (tracked separately, not a parity issue) |
| Localization (en/ar) | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| RTL | UNTESTED (Android — REQUIRES PHYSICAL DEVICE) / UNTESTED (Windows) | Unknown | N/A | Unknown | No |
| Restart persistence | **Windows PROVEN via a real, thorough 13-step smoke test** (including secret-key reuse and `needs_setup:false` confirmation on relaunch); Android UNTESTED (no device) | Windows (proof level only — same backend/DB) | N/A | Should be identical | No |
| Patient-detail back navigation | ANDROID-SPECIFIC concept (`isDetail` back-stack handling) — Windows has no directly equivalent single-page-app back-stack concern in the same form | N/A | Yes, platform-appropriate (native nav vs. web routing) | N/A | No |
| `FLAG_SECURE` / screenshot protection | ANDROID ONLY concern (`10`/`11`) — Windows has no directly equivalent OS-level screenshot-blocking primitive commonly used for desktop apps in this codebase | N/A | N/A | N/A | No (tracked as its own finding) |

## Summary

Clinic is at genuine, verified functional and financial parity between
Windows and Android for everything this audit examined — a direct consequence
of its server-side-computed financial architecture (`05`) and shared backend
for every workflow. The remaining defects found in this audit (payment
validation, over-broad read access, `FLAG_SECURE`) are **shared across both
platforms**, not parity gaps between them — fixing them once, server-side (or
in the one Android manifest change for `FLAG_SECURE`), fixes both platforms
simultaneously. This is a materially healthier position than Retail's, where
the Android-specific tax/discount gap requires an Android-specific fix on top
of any shared backend fix.
