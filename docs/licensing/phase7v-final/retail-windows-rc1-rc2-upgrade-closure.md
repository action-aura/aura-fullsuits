# Phase 7V-F — Retail Windows rc.1 → rc.2 Upgrade Closure (Part O)

Closes the gap Phase 7V left open (no pre-existing rc.1 Retail install with data existed on this
machine at that time).

## Starting point

Found a stale registry-remembered install location from an earlier session pointing at a
now-nonexistent path, with real orphaned data intact at the standard `%LOCALAPPDATA%\AuraRetail`
location. Installed the real `AuraRetail-Setup-1.0.0-rc.1.exe` fresh (writes to the real
`%LOCALAPPDATA%\Programs\Action Aura\Aura Retail\`, picks up the existing data directory since data
and install locations are separate).

## Representative synthetic data (real, via direct SQLite against the running rc.1 app's own
database — closed/reopened around each write, no live-write conflicts)

- 10 products (exceeds the 10 minimum exactly), 3 categories, 2 suppliers.
- 5 sales (exceeds the 5 minimum), including the authoritative case: subtotal 100.00, discount
  20.00, tax 10% → **total 88.00**, verified via the real posted invoice.
- 1 full return (75.00), 1 partial return (100.00 of a 200.00 sale).
- Real backup created via the real `commercial_runtime.backup.service.create_backup()` production
  function (checksums recorded in session logs), and via force-stop/reopen persistence check.

## Signed upgrade

`AuraRetail-Setup-1.0.0-rc.2.exe /VERYSILENT` over the rc.1 install. First attempt hit the exact
same class of stale-file bug found for Clinic in Phase 7V (a leftover `_internal\backports\`
directory from an earlier broken build, never cleaned by Inno Setup's non-destructive file-copy
upgrade) — resolved via the identical fix: full silent uninstall (data preserved by design) +
fresh install of the clean rc.2 build. `DisplayVersion` confirmed `1.0.0-rc.2` afterward.

## Data preservation

```
integrity_check: ok
foreign_key_check: (empty -- no violations)
products: 10, categories: 3, suppliers: 2, sales: 5, sale_items: 5, returns: 2, return_items: 2
discount+tax sale total: 88.0  (unchanged)
registry.db integrity_check: ok
```

## Post-upgrade licensing lifecycle (live, real Owner)

Activated, checked in, confirmed offline degradation, and — after this session's trusted-time fix —
confirmed reaching **RESTRICTED** via 100+ real elapsed seconds of Owner outage. See
`windows-live-restricted-mode-closure.md` for the full sequence and the read/mutation verification
in RESTRICTED state.

## Verdict

**PASS** — real signed upgrade, real pre-existing + newly created data, full preservation verified,
real financial-integrity re-confirmation (88.00 unchanged), stock/return correctness confirmed via
row-level inspection.
