# Phase 8V-P — Scenario 6: Device Replacement (Clinic) — REAL EVIDENCE

**Tier**: real Owner server, real Ed25519-signed simulated devices (two distinct real keypairs).
**Android leg**: NOT VERIFIED.

## A real, production-breaking defect found by this exact scenario, fixed before it could pass

Setting this scenario up (Scenario 4, before this doc) reused the real installed `AuraClinic.exe`'s
persistent device identity against a *second*, different license. Owner crashed with a real
unhandled `500 Internal Server Error`:
`sqlalchemy.exc.IntegrityError: ... duplicate key value violates unique constraint
"owner_device_public_keys_fingerprint_key"`. Root cause (`owner/app/licensing_service/activation.py`):
the device-key-reuse check only matched when the existing key was ACTIVE **and bound to the same
license** being activated; a device already holding an ACTIVE key on a *different* license fell
through to the "brand-new registration" branch, which unconditionally tried to `INSERT` a new
`DevicePublicKey` row with a fingerprint the table's own `UNIQUE` constraint already held — a
genuine crash any real customer machine with more than one Aura license would hit today.

Fixed: detect this case explicitly and reject cleanly with a new, stable public reason code,
`DEVICE_ALREADY_REGISTERED`, instead of attempting the doomed `INSERT`. No silent identity
reassignment — freeing a device for a different license stays an explicit, staff-mediated
`replace_device_slot()`/`release_device_slot()` action, matching Milestone 5's own established
principle. Verified live: the exact same real installed Clinic device, attempting to activate a
second (pilot) license, now gets a clean `400 {"reason_code":"DEVICE_ALREADY_REGISTERED"}` instead
of a crash.

## The scenario itself, real, on a fresh license (device limit 1)

- Device A (real Ed25519 keypair) activates real license `49dacfbe-0a77-478f-bee0-3a9a14431b94`
  (device_limit=1): `SUCCESS`, `installation_id=caec4381-8c56-4e4f-954e-4880e76b4365`.
- Device B (a genuinely different real keypair) attempts activation on the same license while A is
  still active: real `400 {"reason_code":"DEVICE_LIMIT_REACHED", "decision":"REJECTED"}` — the
  license is genuinely at capacity, not a simulated denial.
- Support approves the replacement through the real Owner domain service,
  `replace_device_slot(installation, reason=..., actor_staff_user_id=...)` — never a direct database
  row edit. Old installation A: `status = REPLACED`.
- Device B activates for real, immediately after: `SUCCESS`,
  `installation_id=e0205c27-ef6b-4c53-a786-32ef9a21ed9e` — a genuinely **different** installation
  ID from A's, no silent identity reuse.

## Final state (queried directly from Owner's database)

| Installation | Status |
|---|---|
| A (`caec4381-...`) | `REPLACED` (full history retained, not deleted) |
| B (`e0205c27-...`) | `ACTIVE` |

`count_slot_consuming_installations()` for this license: **1** — never exceeded the allowance at any
point, including during the brief window both rows existed.

## Result: **PASS** (Windows-equivalent leg, real, including a real defect found and fixed). Android
leg: **NOT VERIFIED**.
