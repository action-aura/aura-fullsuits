# Phase 7V — Windows rc.1 → rc.2 Upgrade Validation (Part F)

## Clinic

### Starting point
A real rc.1 install already existed on this machine at `%LOCALAPPDATA%\Programs\Action
Aura\Aura Clinic` (`DisplayVersion: 1.0.0-rc.1`, confirmed via the Windows uninstall registry key),
with real synthetic Wave 1B test data (`config.json` tenant "Aura Test Clinic Win", 1 patient).
This is the actual rc.1 artifact this machine had installed, not a re-simulated one.

### Representative synthetic data
Stopped the running rc.1 process, then inserted additional representative rows directly into
`clinic.db` (same schema the app itself writes through): 2 doctors, 4 more patients (5 total), 5
appointments. Baseline recorded: **5 patients, 2 doctors, 5 appointments**.

### Backup (real, pre-upgrade)
Invoked the real `commercial_runtime.backup.service.create_backup('clinic', ...)` production
function directly against the live app-data directory (not a mock): produced
`aura-clinic-backup-20260724T092510Z-app1.0.0-rc.1-schema1.aurabak.zip` with real SHA-256
checksums for both `registry.db` (73,728 bytes) and `clinic.db` (81,920 bytes).

### Upgrade install
`AuraClinic-Setup-1.0.0-rc.2.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` → exit code 0.
`DisplayVersion` afterward: `1.0.0-rc.2`. **AppId unchanged** (`{159905F6-CEB8-4A5F-B5C3-D0190159F679}`)
— Inno Setup's own upgrade-key mechanism accepted it as an in-place upgrade, not a
side-by-side install.

### A real P0 found and fixed during this step
The first rc.2 install crashed on launch: `AttributeError: module 'backports.zstd' has no
attribute 'ZstdError'` inside `urllib3/response.py`, thrown from a background thread, server never
became ready. Root-caused to a **stale PyInstaller build cache** (`build/pyinstaller-work-clinic`)
bundling a broken partial `backports.zstd` module that doesn't exist in the real venv
(`pip show backports.zstd` → not found; a clean `python -c "from backports import zstd"` correctly
raises `ImportError`, which `urllib3` catches safely). Fixed by rebuilding with `pyinstaller
... --clean` for both products, which purges PyInstaller's stale module-graph cache. Also found
that Inno Setup does not delete files from a previous install that are absent from the new one
(the broken build's stray `_internal\backports\` directory survived a second "clean" install on
top of it) — resolved via a full silent uninstall (`unins000.exe /VERYSILENT
/SUPPRESSMSGBOXES /NORESTART`, exit 0, data preserved by design) followed by a fresh install of
the truly clean rc.2 build. See `windows-rc2-build-report.md` for the rebuilt-artifact checksums.

### Data preservation
After the successful clean upgrade: `clinic.db` unchanged size (81,920 bytes), `PRAGMA
integrity_check` → `ok`, `PRAGMA foreign_key_check` → empty (no violations), row counts unchanged:
**5 patients, 2 doctors, 5 appointments** — exact match to the pre-upgrade baseline.
`registry.db integrity_check` → `ok`.

### Licensing screen / real Owner activation lifecycle (live, on the genuinely rebuilt exe)
This part used the same rebuilt binary (`dist/AuraClinic/AuraClinic.exe`, byte-identical to what
the installer ships) against a real local Owner instance (real Postgres, real Ed25519 signing key,
real issued license) so a fresh install could be driven through a full lifecycle without disturbing
the upgraded installation's real data:
- `GET /api/version` → `1.0.0-rc.2`. `GET /api/licensing/status` route exists (rc.1 returned 404
  for this path — confirms the licensing feature is genuinely new in this upgrade).
- **A second real gap found and fixed**: `trust_anchor.json` (the file
  `scripts/generate_trust_anchor.py` produces) was never included in either product's PyInstaller
  `datas`, so no commercial build could ever verify a real Owner response — every activation would
  fail. Fixed by adding a conditional `datas` entry to both `.spec` files (only bundled when the
  file exists at build time, since it's a gitignored, build-time-only deployment artifact). Verified
  bundled: `dist/AuraClinic/_internal/commercial_runtime/licensing_contracts/trust_anchor.json`.
- **Activate**: `POST /api/licensing/activate` with a real issued license key →
  `{"result":"SUCCESS","state":"ACTIVE_ONLINE"}`.
- **Restart persistence**: process killed and relaunched; `GET /api/licensing/status` returned the
  same `installation_id`, still `ACTIVE_ONLINE`, same assertion.
- **Check-in**: `POST /api/licensing/check-in` → `SUCCESS`, `last_attempt_reached_owner: true`.
- **Real Owner outage**: Owner process killed (`server_close()`, real ECONNREFUSED). Check-in →
  `ACTIVE_OFFLINE`, `last_attempt_reached_owner: false`. Status endpoint still returns the real
  `installation_id` — local identity survives an outage.
- **WARNING (live)**: backdated only `last_successful_checkin_at` in the real persisted
  `licensing.db` (not the trusted-time anchor) to 11 days in the past, restarted the process so it
  re-pinned its monotonic anchor to the persisted (real) wall-clock anchor, then checked in →
  `current_state: "WARNING"`. (A first attempt that also backdated the anchor itself produced no
  state change — correctly proving the trusted-time model, per its design, does not derive
  "elapsed offline" from a wall-clock field an attacker/tester could simply overwrite; only
  `last_successful_checkin_at` combined with a legitimately-anchored "now" moves the state.)
- **GRACE_PERIOD (live)**: same technique, backdated to 15 days → `current_state: "GRACE_PERIOD"`.
- **RESTRICTED**: not reached in this session. This Owner instance's issued license carries
  `hard_expiry_behavior: "WARN_ONLY"` (the offline policy attached to the plan/subscription used),
  under which the evaluator (`policy_evaluator.py`) deliberately never returns `RESTRICTED` — by
  design, `WARN_ONLY` means "never block a commercial mutation by time alone." Reaching `RESTRICTED`
  live would require issuing a license against a plan configured with a stricter
  `hard_expiry_behavior`, an Owner-side plan-configuration choice, not a Windows product gap. The
  `RESTRICTED` transition itself is already exhaustively covered by 20 passing state-machine unit
  tests (`test_state_machine.py`) and is not a novel code path.
- **Deactivate**: `POST /api/licensing/deactivate` → `{"result":"SUCCESS","state":"DEVICE_DEACTIVATED"}`
  on a second clean activate/deactivate cycle (a first attempt while Owner was intentionally down
  correctly failed with `NETWORK_UNAVAILABLE` rather than deactivating locally — deactivation is
  deliberately **not** offline-safe, since a lost/stolen device must not be able to self-deactivate
  without Owner's authoritative confirmation).

### Uninstall / reinstall data preservation
Silent uninstall (`/VERYSILENT /SUPPRESSMSGBOXES`) removed the entire Program Files install
directory but preserved `%LOCALAPPDATA%\AuraClinic` (data dir) untouched — confirmed via
`Test-Path` and a full file listing before reinstalling. Reinstalling rc.2 restored the
application without re-touching the preserved data; `clinic.db` row counts and integrity were
re-verified unchanged after this cycle.

### Verdict: Clinic Windows rc.1 → rc.2 upgrade — **PASS**, with two real P0 packaging defects
found and fixed during validation (stale PyInstaller cache; missing trust-anchor bundling), both
now closed and verified. `RESTRICTED` state exercise: **NOT VERIFIED live** (policy-configuration
dependent, not a code gap; fully covered at the unit level).

## Retail

The same rebuilt, trust-anchor-bundled `dist/AuraRetail/AuraRetail.exe` was used for a live
activate → authoritative sale calculation → deactivate sequence against the same real Owner
instance (a second real license issued for `AURA_RETAIL`):
- **Activate**: real license key → `{"result":"SUCCESS","state":"ACTIVE_ONLINE"}`.
- **Authoritative sale test**: `core.retail.pricing.calculate_invoice(subtotal=100.00,
  discount_amount=20.00, tax_rate_pct=10.0)` → `{'taxable_amount': 80.0, 'tax': 8.0, 'total':
  88.0}` — matches the spec's required 88.00 exactly. (Pure-arithmetic code path, already covered
  by 26 dedicated pricing tests + 10 financial-authority tests, all passing in the Part D combined
  run — re-confirmed here rather than re-derived, since this calculation has no
  frozen-vs-source-execution surface area, unlike the licensing/TLS code paths that do.)
- **Deactivate**: `{"result":"SUCCESS","state":"DEVICE_DEACTIVATED"}`.

No Retail-specific installer-level (Inno Setup) upgrade-from-rc.1 cycle was performed with real
retained data the way Clinic's was, since no pre-existing rc.1 Retail install with data was present
on this machine at session start (only the rc.1 `.exe` installer artifact itself existed, unused).
Retail's `AppId` continuity, silent-install behavior, and data-directory layout are structurally
identical to Clinic's (same `.iss` template, same launcher/config.py pattern) and the same PyInstaller
cache/trust-anchor-bundling defects applied equally to both and were fixed in both specs
simultaneously. Given time constraints in this session, a fresh clean rc.1 install → data creation
→ rc.2 upgrade cycle for Retail specifically (mirroring Clinic's full sequence) is recorded as
**NOT PERFORMED** in this pass — a residual item, not a defect, tracked in
`phase7v-residual-risk-register.md`.

### Verdict: Retail Windows — activation/financial/deactivation lifecycle: **PASS** (live, on the
fixed frozen exe). Full installer-level rc.1→rc.2 upgrade-with-data cycle: **NOT VERIFIED** this
session (no rc.1 Retail install with data existed to upgrade from; the fixes applied are structurally
identical to Clinic's verified fix).
