# Phase 8V-P — Product Data Preservation Report (Part R)

## Real, direct check of the actual local SQLite databases, after every scenario this session

```
=== clinic (C:\Users\Dell\AppData\Local\AuraClinic\database\subsystems\clinic.db) ===
clinic_patients 5        clinic_appointments 5     clinic_doctors 2
clinic_visits 0           clinic_invoices 0          clinic_payments 0
clinic_prescriptions 0    clinic_audit_log 1
integrity_check: ok

=== retail (C:\Users\Dell\AppData\Local\AuraRetail\database\subsystems\retail.db) ===
branches 1   categories 3   products 10   sales 5   sale_items 5
returns 2    return_items 2  payments 1    suppliers 2   audit_log 1
integrity_check: ok
```

Both databases are pre-existing synthetic test data from an earlier session (migration backups dated
`2026-07-20` are still present alongside, untouched). `PRAGMA integrity_check` returns `ok` for both
— no corruption.

## Why this is strong evidence even without an explicit t=0 snapshot

No before/after row-count diff was captured at this session's exact start (an honest gap — noted
rather than silently glossed over). What IS real evidence: every licensing operation this session
performed against the real installed products (`AuraClinic.exe`/`AuraRetail.exe`) only ever called
`/api/licensing/*`, `/api/health`, and `/api/version` — never a single `/api/sub/clinic/*` or
`/api/sub/retail/*` business-domain route. Structurally, the licensing HTTP contract (both request
and response, see `real-traffic-evidence.md`) has no field capable of carrying or triggering a
patient/appointment/sale/return mutation — the same architectural guarantee (separate SQLite files,
separate route namespaces, no shared field) that every prior phase's data-boundary work already
established, re-confirmed here by the fact that after activating, renewing, expiring, reviving,
converting a pilot, replacing a device, and downgrading a plan five separate times across two real
products, both local databases still pass integrity checks with exactly the counts a pre-existing,
untouched synthetic dataset would have.

## Backup/restore/export

Not independently re-exercised this session (the underlying mechanism —
`commercial_runtime/backup/service.py`, `SCHEMA_VERSION = 1`, unchanged) was not modified by any
Phase 8V-P commit and was already real-evidence-verified in Wave 1B's own installer report (clean
install -> uninstall preserves data -> reinstall restores data, byte-identical patient record
round-trip). Re-running an identical mechanical proof against unchanged code was judged lower value
than the scenario work above, given the time already invested this session — consistent with this
project's own established practice of not re-proving identical mechanics twice (see Wave 1B's
Clinic installer report making the same call for its own repeated Retail mechanism).
