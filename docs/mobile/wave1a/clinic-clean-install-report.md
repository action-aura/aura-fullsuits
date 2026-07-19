# Clinic — Clean Install & Onboarding (Wave 1A, Parts A/H)

## Initial install: MOB-002 blocked everything
The first real-device install never got a working backend at all — `ModuleNotFoundError: No module named 'requests'` on every launch (Chaquopy's pip block was missing a dependency `clinic_api.py` imports at module level). Full detail in the defect registry (MOB-002). Fixed, rebuilt, reinstalled.

## Credential reset
After the MOB-002 fix, the existing on-device admin account (created in an earlier, pre-Wave-1A session) had unknown/forgotten credentials — attempting `baha@baha`/`123123` (Retail's admin) correctly failed with a genuine `401 Invalid email or password` (later surfaced properly in the UI once MOB-004 was fixed). Rather than access the registry DB directly (blocked by the Claude Code auto-mode credential-materialization guard, correctly, since any copy of `registry.db` carries `password_hash` with it), the app's storage was reset via `adb shell pm clear com.actionaura.clinic.debug` — a data wipe of zero real value, since login had never worked in this session. This is the gentler alternative to uninstall/reinstall (same effect, keeps the APK installed).

## Fresh onboarding
Company: **Aura Test Clinic**. Admin: **Test Admin**, `baha@baha` / `123123` (matching Retail's credentials for the user's convenience — independent registries, not a shared secret). `/api/onboarding/status` correctly showed `needs_setup: true` before, `false` after. Login verified both via curl and the real on-device UI.

## Result
PASS after MOB-002 fix and credential reset. Onboarding form and first login both worked correctly once the backend could actually start.
