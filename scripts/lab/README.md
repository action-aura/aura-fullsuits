# Aura Lab -- local Owner Control Center + till test rig

Everything here is **loopback-only and test-only**. Nothing in this
directory is a deployment path for a real customer -- see
`docs/ops/free-hosting-owner.md` for that. This is a local rehearsal of the
exact same bootstrap sequence that runbook prescribes (migrate -> restore
signing key -> seeds -> create admin -> preflight), run against a local
Postgres instead of Neon and a local Owner process instead of Oracle/Fly.

## Scripts

Run these from any shell with PowerShell available (Windows PowerShell 5.1
or pwsh 7 both work -- none of these scripts use `&&`, `?:`, or `??`).

| Script | What it does |
|---|---|
| `lab_common.ps1` | Not run directly. Dot-sourced by every script below: resolves paths, secrets, HTTP polling, pid-file bookkeeping. |
| `lab_bootstrap.ps1` | One-time, idempotent: creates the `aura_owner_lab` database, generates `lab.env.json`, restores the existing signing key, runs migrations, seeds RBAC/catalog/plans/offline-policy, creates the first Super Admin, and runs `flask commercial preflight` + `flask licensing verify-signing-key-health`. Safe to re-run any time. |
| `lab_up.ps1` | Starts Owner on `127.0.0.1:5551` if it isn't already up, waits for `/healthz` and the device-facing licensing API to answer, and prints where the credentials are. `-WithDesktop` also starts the desktop till afterward. |
| `lab_desktop.ps1` | Starts an Aura Retail till pointed at the lab Owner. Default: the real desktop app (a window). `-Headless -Port <n> -DataDir <path>`: the bare backend only, no window -- for automated proofs. |
| `lab_down.ps1` | Stops every process this lab started, by pid file. Optional `-Only <name>` to stop just one (`owner`, `desktop-app`, `till-<port>`). Never touches a process whose command line no longer matches what was recorded when it started (pid reuse guard). |
| `lab_status.ps1` | Read-only report: Postgres, Owner health, the lab DB's alembic revision, any running till's licensing status, the site-relay hub port (5443) and its firewall posture, the LAN IPv4 address, and `adb` device/APK state. Never changes anything. |
| `lab_e2e.py` | Playwright + plain-HTTP proof of multi-device sync end to end: issues a licence through the real Owner UI, activates till A, joins till B, and measures convergence. Run with the SYSTEM Python (has `playwright`), not the retail venv -- see the file's own module docstring. |
| `lab_sauth_proof.py` | S-AUTH (AUDIT-033) proven on this same real rig, on ports 5061/5062: issues its own fresh 2-device licence, activates till A and joins till B, reads back which device each till AND Owner's own database call "main" (and that they agree), then proves a legitimate authority change (a cashier account) and a legitimate catalogue change (a product) both cross from A to B for real. Reuses `lab_e2e.py`'s own helpers rather than re-deriving them. Run with the SYSTEM Python, same as `lab_e2e.py`. Does **not** cover the forged/insider half of S-AUTH (an attacker re-signing an authority event as the main computer, and the matching mutation-proof run) -- that mechanism already exists, reviewed and merged, in `products/retail/tests/_insider_forge.py`; porting it to the real HTTP relay instead of that test's in-process file relay is left for whoever picks this up next. Chromium refuses to navigate to port 5061 by default (it is in Chromium's own restricted-port list for SIP/SIP-TLS, unrelated to this app) -- this script launches with `--explicitly-allowed-ports=5061,5062` to allow it. |
| `day_in_shop.py` | A "day in the shop" through the REAL retail app on port 5071 (fresh data dir under `C:\Users\MSI\AuraLab\proof\day`, every `AURA_*`/`OWNER_*` variable stripped): morning set-up, keyboard-only sales S1-S14 in headless Chromium, return, customer payment, paid-out, cashier hand-over, evening X report / close / approve, reports and ledger checks, with every expected figure computed independently. Finds and measures only; writes `result.json`, `result.md` and numbered screenshots. Run with the SYSTEM Python (has `playwright`), same as `lab_e2e.py`. No licence = READ-ONLY, so it signs up first and then seeds an active licence (same trick as `retail_smoke_e2e.py`). |

## Typical flow

```powershell
# first time, or after a change to the seed/migration set:
scripts/lab/lab_bootstrap.ps1

# bring Owner up (idempotent -- safe to run again, no-ops if already up):
scripts/lab/lab_up.ps1

# check what's running:
scripts/lab/lab_status.ps1

# a headless till for an automated proof:
scripts/lab/lab_desktop.ps1 -Headless -Port 5020 -DataDir C:/Users/MSI/AuraLab/proof-a

# a real desktop window for a human to click through:
scripts/lab/lab_up.ps1 -WithDesktop

# stop everything this lab started:
scripts/lab/lab_down.ps1
```

## Lab root layout

Default `C:/Users/MSI/AuraLab` (override with the `AURA_LAB_ROOT` environment
variable before running any script):

```
AuraLab/
  lab.env.json      # secrets + settings -- generated ONCE, never overwritten
  run/*.pid.json     # one file per started process (name, pid, command line, start time)
  logs/              # owner.log/.err, till-<port>.log/.err, desktop-app.log/.err, lab.log
  desktop/           # AURA_APP_DATA for the full desktop app (its own sqlite db, logs)
  proof-*/           # AURA_APP_DATA for headless proof tills (created by whoever calls lab_desktop.ps1 -Headless)
```

## Where secrets live

`lab.env.json` in the lab root holds `owner_secret_key`, `owner_license_pepper`,
and the generated admin credentials (`admin_email`, `admin_display_name`,
`admin_password`). **Never regenerate this file's values** -- the license
pepper is baked into every signed licence assertion; changing it after the
fact invalidates every licence issued from this lab Owner. If the file is
deleted, treat every existing licence issued from this lab as dead, not as
"needs a new secret."

The signing key's PRIVATE key material lives outside both the lab root and
the repo, at `C:/Users/MSI/.aura-signing/owner-signing-keys/` -- copied once
from its only known source, verified byte-for-byte by SHA256, and never
printed by any script here.

## What this is not

- Not a production deployment. `OWNER_ENV` is deliberately left unset
  (`DevelopmentConfig`), Owner binds loopback only (127.0.0.1:5551), and
  `FLASK_DEBUG` is never set (no debugger, no reloader).
- Not a substitute for `docs/ops/free-hosting-owner.md` -- that is what a
  real customer-facing deploy still follows. This lab exists to rehearse and
  prove that exact sequence locally before or alongside a real deploy, and to
  give the desktop/Android test suite something real to activate against.
