# Retail — Clean Install & Onboarding (Wave 1A, Parts A/B)

## Install
Fresh debug APK installed on Infinix X6528 (Android 13). App launched, embedded Flask/waitress backend confirmed bound to a dynamically-chosen `127.0.0.1:<port>` (discovered per-launch via `/proc/net/tcp`, since `_find_free_port()` can select different ports across restarts). `/api/health` returned `200 {"status":"ok"}`.

## Onboarding / auth
Company: **Aura Test Retail**. Admin: **Test Admin**, `baha@baha` / `123123` (synthetic credentials, chosen by the user for easy cross-app memorization; Retail and Clinic have independent registries so this is not a shared secret in any real sense).

`GET /api/onboarding/status` correctly reported `needs_setup: true` pre-onboarding and `false` after. Login verified both via direct API call and through the real on-device UI.

## Result
PASS — clean install, onboarding, and first login all worked without defects.
