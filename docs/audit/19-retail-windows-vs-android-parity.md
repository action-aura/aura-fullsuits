# Retail — Windows vs. Android Parity Matrix

Statuses: COMPLETE AND CONSISTENT · PRESENT BUT DIFFERENT · WINDOWS ONLY ·
ANDROID ONLY · BROKEN · UNTESTED · NOT PRESENT · NOT APPLICABLE.

| Feature | Status | More complete | Intentional? | Data output differs? | Release blocker? |
|---|---|---|---|---|---|
| Onboarding/account creation | **BROKEN** (both platforms — shared backend gap) | Neither | No — a defect | N/A | **YES** |
| Login/logout | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| Dashboard | COMPLETE AND CONSISTENT | Equal (server-computed) | N/A | No | No |
| Products/categories/suppliers CRUD | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| POS cart — discount | **PRESENT BUT DIFFERENT → effectively ANDROID ONLY MISSING** | Windows | Not intentional (no design doc/comment indicates this was a deliberate simplification for mobile) | **YES — Android total omits it entirely** | **YES** |
| POS cart — tax | **PRESENT BUT DIFFERENT → effectively BROKEN on Android** | Windows | Not intentional | **YES — Android total is always $0 tax** | **YES** |
| Barcode scanning (camera) | ANDROID ONLY (Windows has no camera) | Android (by platform necessity) | Yes — intentional, phone camera isn't a Windows-desktop feature | N/A | No — Windows correctly doesn't need this |
| Barcode scanning (keyboard wedge) | WINDOWS ONLY (implied — no equivalent HID-scanner-as-keyboard-input concept applies to touch-only Android) | Windows | Yes, platform-appropriate | N/A | No |
| Returns/refunds | COMPLETE AND CONSISTENT **in the sense that both are equally unvalidated** — see `03` | Neither (both broken the same way) | No — a defect, shared | No — both platforms produce equally-unvalidated results | **YES** (as a shared defect, not a parity gap) |
| Import (CSV/Excel/.db) | WINDOWS ONLY | Windows | Not stated as intentional anywhere found; treated as a scope gap rather than a defect (import is inherently a bulk/desktop-oriented workflow, plausibly a deliberate scope choice, but no comment confirms this) | N/A | No — not a core POS workflow |
| Export | NOT PRESENT (both) | N/A | N/A (never existed in source) | N/A | No |
| Localization (en/ar) | COMPLETE AND CONSISTENT | Equal | N/A | No | No |
| RTL | UNTESTED (Android — REQUIRES PHYSICAL DEVICE) / UNTESTED (Windows — not visually re-verified this pass) | Unknown | N/A | Unknown | No (not proven broken, just unverified) |
| Restart persistence | COMPLETE AND CONSISTENT **by architecture** (same backend/DB), but Windows is PROVEN via real smoke test while Android is UNTESTED | Windows (proof level, not behavior) | N/A | Should be identical (same DB layer) | No |
| Printing | NOT PRESENT (both) | N/A | N/A | N/A | No |
| Offline operation | COMPLETE AND CONSISTENT by architecture | Equal | N/A | No | No |

## Summary

Retail's Windows and Android clients are **not at parity on the two things
that matter most for a POS product: tax and discount**. Every other
meaningful difference (camera scanning being Android-only, import being
Windows-only) is either platform-appropriate or a scope choice rather than a
proven defect. The onboarding and returns-validation gaps are not
parity issues at all — they are identical, shared defects present on both
platforms via the common backend, which is arguably worse than a parity gap
(fixing one platform won't fix the other).
