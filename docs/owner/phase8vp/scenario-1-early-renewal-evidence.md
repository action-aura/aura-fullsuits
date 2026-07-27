# Phase 8V-P — Scenario 1: Early Renewal (Clinic Windows) — REAL EVIDENCE

**Tier**: real installed product (the actual frozen `dist/AuraClinic/AuraClinic.exe`, rc.3, run as a
real Windows process) against the actual running Owner Flask server (real Postgres, real Ed25519
signing key, real trust anchor). Not the pytest harness, not a mock.

**Android leg**: NOT VERIFIED (no physical device — see `physical-device-readiness.md`).

## Setup

- Owner: `owner_ed25519-20260727T053324Z-c32537d7` active signing key, `aura_owner_dev` Postgres.
- Real synthetic Clinic customer/subscription/license created via the Owner service layer:
  subscription `9e4399e5-0cae-48f2-a157-eaf6aae1327c`, license `e825cbd7-b2c4-459d-a4c0-af43c864477c`,
  term end `2026-08-27`, device limit 2.
- Real Clinic Windows product launched (`AuraClinic.exe`, `AURA_OWNER_LICENSING_URL` pointed at the
  live Owner server), real `/api/health` and `/api/version` (`1.0.0-rc.3`) confirmed.

## Activation (real)

`POST http://127.0.0.1:5000/api/licensing/activate {"license_key": "AURA-CLN-1-UAXM-ZFSU-XN2W-4CB3-RU3K"}`
-> `{"installation_id":"1bc69547-8778-46b0-a77d-f1aff5776f04","result":"SUCCESS","state":"ACTIVE_ONLINE"}`.

Before-state (from Owner's own database): `assertion_id = 5c884c70-4daf-4d69-a8bc-084f75f2e851`,
device fingerprint `aa9863efd597b9b5de3dba90a0b8c90eed145580013a74f6506b6d31246705f8`, 1 installation
on the license.

## Renewal (real, through the real Owner staff UI, real HTTP, real MFA)

Real login as a staff "creator" account -> real form POST creating the renewal
(`date_rule=EARLY_RENEWAL_FROM_CURRENT_END`, `2026-08-27 -> 2026-09-27`) -> real transitions through
`QUOTED`/`AWAITING_CONFIRMATION`/`AWAITING_PAYMENT`/`PAYMENT_RECORDED` -> a **different** staff
account logs in, completes real MFA (`pyotp` TOTP against a real enrolled secret), completes real
recent-auth (`/auth/reauth`), then approves and applies the renewal — all real HTTP `302` redirects
(success), no mocked step. `aura_owner_dev`'s `Subscription` row confirms: `status=ACTIVE`,
`end_date=2026-09-27` (was `2026-08-27`).

## Real check-in on the real product, after renewal

`POST http://127.0.0.1:5000/api/licensing/check-in` (empty body — the endpoint takes no license-key
or body parameter at all, structurally) ->
`{"current_state":"ACTIVE_ONLINE", "installation_id":"1bc69547-8778-46b0-a77d-f1aff5776f04", "last_attempt_reached_owner":true, "last_sync_result":"SUCCESS", ...}`.

## Before/after comparison (from Owner's own database, not the product's self-report)

| Field | Before | After | Result |
|---|---|---|---|
| Installation ID | `1bc69547-...` | `1bc69547-...` | **unchanged** |
| Device key fingerprint | `aa9863ef...` | `aa9863ef...` | **unchanged** |
| Assertion ID | `5c884c70-...` | `7ef8fe5a-...` | **fresh assertion issued** |
| Installations on this license | 1 | 1 | **no new slot consumed** |
| Subscription term end | `2026-08-27` | `2026-09-27` | **extended from previous end date** |
| Product local state | `ACTIVE_ONLINE` | `ACTIVE_ONLINE` | unchanged (never dropped) |

## Restart persistence

Killed the real `AuraClinic.exe` process and relaunched it fresh. `GET /api/licensing/status`
immediately after restart still reports `ACTIVE_ONLINE`, the same `installation_id`, and the correct
cached assertion — local state survives a real process restart, not just an in-memory artifact of
the first run.

## License key never re-entered or transmitted for renewal

The full key was supplied exactly once, at the initial `/api/licensing/activate` call. The renewal
happened entirely through the Owner staff UI (no product interaction at all) and the check-in call
that picked it up has no key-shaped field in its request body at all — confirmed both by reading
`commercial_runtime/licensing_contracts/client.py::check_in()`'s body construction and by this
session's own real check-in call above, which was a bare `POST` with an empty body and still
succeeded.

## Result: **PASS** (Windows leg). Android leg: **NOT VERIFIED**.
