# Phase 8V-P — Team Handover: Physical Commercial Closure

> **Superseded pointer (additive, this note only):** Phase 8V-P2
> (`docs/owner/phase8vp2/PHASE8VP2-FINAL-ANDROID-CLOSURE-HANDOVER.md`) fixed Scenario 7's remaining
> gap for real, added a `flask commercial preflight` environment-safety command, and built +
> certificate-verified final signed Android rc.3 artifacts. Read that handover first if you're
> picking up the work today — this document is still accurate for everything it describes, just no
> longer the newest starting point.

**Read this first.** This is the entry point for anyone picking up this work — it tells you exactly
where things stand, what's already proven, what's blocked, and the exact next step. Everything else
under `docs/owner/phase8vp/` is supporting evidence you can dip into as needed; you should not need
to read all 29 files to get moving.

---

## 1. Where things stand, in one paragraph

Phase 8 (Owner commercial-operations backend + UI) is functionally complete and has been validated
for real against the actual installed Windows products — real Owner server, real Postgres, real
Ed25519 cryptography, real customer-lifecycle scenarios, not mocks. One production-breaking bug was
found and fixed along the way. The **only** thing standing between this and Phase 8's final,
unconditional sign-off is running the same already-proven scenarios on a **physical Android device**.
Nothing else is missing. No new code needs to be written for Phase 8 to close — the next session's
job is almost entirely "connect a phone and run what already works."

## 2. Git state (verify this first)

```
Conditional tag (unchanged, do not move):  aura-owner-commercial-ops-phase8-conditional-complete
  -> commit 7150564af95bd75cd475df57a6b42ae9ad4b3fb5

Current HEAD (as of this handover):        9eace9e
  ("docs: phase 8v-p physical/real commercial closure evidence")

Final tag (NOT yet created):               aura-commercial-licensing-operations-phase8-complete
```

Working tree was clean at handover time. Run `git status` and `git log --oneline -5` to confirm
nothing has drifted before you start.

The **original** `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` repository is READ-ONLY —
never touch it. All work happens in `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits`.

## 3. The one remaining blocker

**No physical Android device was available in the session that produced this handover.** `adb`
itself works fine (`C:\Users\Dell\AppData\Local\Android\Sdk\platform-tools\adb.exe` — not on PATH by
default, use the full path or add it); `adb devices -l` returned an empty list, checked for real, not
assumed. Per this project's own non-negotiable rule, physical results are never simulated or faked —
so this is disclosed as a real gap, not worked around.

**Exact next step**, once a device is connected and authorized (`adb devices -l` shows `device`, not
`unauthorized`/`offline`):

1. Build the Android artifacts (they were never built this session — only the version bump landed
   at the source level, see §5): `cd android/aura-clinic && ./gradlew clean testDebugUnitTest
   lintRelease assembleRelease bundleRelease` (then the same for `android/aura-retail`). Confirm
   `versionName "1.0.0-rc.3"` / `versionCode 4` in the build output.
2. Install both APKs on the device.
3. Run the same 7 commercial-lifecycle scenarios that are already proven on Windows — see §6 and
   `scenario-1` through `scenario-7-*-evidence.md`. Every Owner-side step (create renewal, approve,
   apply, etc.) can be done exactly the way this session did it (§7 below has the working
   HTTP/Python snippets) — only the *product* side needs to be a real phone instead of a real `.exe`.
4. Capture Logcat per scenario (clear before each, search for the forbidden-content list in
   `final-log-privacy-report.md`).
5. Write the closing decision doc (`phase8-final-unconditional-decision.md`'s own template is a
   good starting structure) and, only once every gate in that doc is genuinely green, create the
   final tag: `aura-commercial-licensing-operations-phase8-complete`.

That's it. No new Owner features, no new UI, no schema changes are expected to be needed for this.

## 4. What's already real and proven (you should not need to re-verify these)

| Area | Status | Evidence |
|---|---|---|
| Owner commercial-ops UI (renewals, pilots, emergency extensions, activation review, device slots, notifications, queues, reconciliation, timeline) | **Done, tested** | `docs/owner/phase8v/owner-ui-closure-report.md` |
| Renewal lifecycle, real wire traffic (Clinic Windows) | **PASS, real** | `scenario-1-early-renewal-evidence.md` |
| Renewal-after-expiry / revival, real wire traffic (Retail Windows) | **PASS, real** | `scenario-2-late-renewal-evidence.md` |
| Past-due detection/notification/dedup (real CLI job) | **PASS, real** | `scenario-3-past-due-evidence.md` |
| Pilot conversion (Owner-side, real) | **PASS, real** (product-side re-check incomplete — see §8) | `scenario-4-pilot-conversion-evidence.md` |
| Emergency extension (real MFA, real HTTP) | **PASS, real** | `scenario-5-emergency-extension-evidence.md` |
| Device replacement (real wire traffic, found+fixed a P0) | **PASS, real** | `scenario-6-device-replacement-evidence.md` |
| Plan downgrade / device overage | **CONDITIONAL** — remediation mechanics real and correct; the auto-sync trigger has a real, disclosed gap (§8) | `scenario-7-plan-downgrade-evidence.md` |
| Automated regression | **380 owner + 214 commercial_runtime, 0 failures** | `final-regression-report.md` |
| Windows artifacts (rc.3, both products) | **Built, real, checksummed** | `final-artifact-build-report.md` |
| Data boundary / traffic privacy | **Clean, real captured traffic** | `real-traffic-evidence.md`, `final-data-boundary-report.md` |

## 5. Version state

All four artifact families are at **`1.0.0-rc.3`** (bumped from rc.2 this phase — see
`final-product-version-decision.md` and `docs/release/versioning-policy.md`'s own "Phase 8V-P bump"
section for the full rationale). Android's `versionCode`/`versionName` were bumped **at the source
level only** (`android/aura-*/app/build.gradle`) — no APK/AAB was actually built. Building one is
step 1 of §3 above.

Windows rc.3 artifacts already exist locally at `dist/installers/AuraClinic-Setup-1.0.0-rc.3.exe` /
`AuraRetail-Setup-1.0.0-rc.3.exe`, alongside the untouched rc.1/rc.2 history. **`dist/` is
gitignored** — these files exist only on the machine that built them. If you're on a different
machine, rebuild them: `pyinstaller products/clinic/packaging/aura_clinic.spec --noconfirm` then
`ISCC.exe products/clinic/packaging/aura_clinic_setup.iss` (same pattern for retail).

## 6. How to reproduce the real validation environment (if setting up fresh, or on a new machine)

This is exactly what this session did — copy it rather than reinvent it.

### 6.1 Trust anchor — **check this first, it's the #1 thing that will bite you**

`commercial_runtime/licensing_contracts/trust_anchor.json` is **gitignored**. It must contain the
public key of whatever Owner signing key you're actually running against, or every real product
activation will fail with `UNKNOWN_SIGNING_KEY`. This session hit exactly that (see
`environment-readiness-report.md`) because the anchor checked into nobody's git history referenced a
key that no longer existed anywhere.

- If you're on the **same machine** as this session, the file already exists locally with the
  correct key (`owner-ed25519-20260727T053324Z-c32537d7`) — verify with `cat
  commercial_runtime/licensing_contracts/trust_anchor.json`.
- If you're on a **different machine**, or the Owner dev database has been reset, regenerate it for
  real against a real running Owner server — do not hand-write it:
  ```
  # 1. Make sure Owner has an ACTIVE signing key (see 6.2 if not)
  # 2. Start Owner (see 6.3)
  # 3. Run the canonical generator:
  python scripts/generate_trust_anchor.py \
      --owner-url http://127.0.0.1:5551/api/licensing/v1 \
      --out commercial_runtime/licensing_contracts/trust_anchor.json \
      --insecure   # local-only flag, never use --insecure for a real remote Owner
  ```
- **Also check each real product's local trust cache** if reusing an existing local install:
  `%LOCALAPPDATA%\AuraClinic\licensing\trust_store.json` / `%LOCALAPPDATA%\AuraRetail\licensing\trust_store.json`.
  This file bootstraps once from the bundled anchor and **refuses to re-bootstrap** if it already has
  keys (by design — anti-TOFU-reset). If it's stuck on a stale key, delete the file (not the whole
  `licensing` folder, not the product's business database) and let the app re-bootstrap on next
  launch. This session hit this exact issue with Retail — see `scenario-2-late-renewal-evidence.md`.

### 6.2 Owner dev database — check permissions are in sync

The persistent `aura_owner_dev` Postgres database can drift out of sync with `seed_data.py` if
permissions were added in code but never re-seeded into an existing database (this session found 10
missing permission codes this way — `pilots.*`, `emergency_extensions.*`, `activation_policy.manage`,
`pending_activations.*`, `device_slot_exceptions.*`). Before assuming a `403` is a real bug, check:

```python
# quick check, from owner/ with the venv active:
from app import create_app
from app.extensions import db_session
from app.models.staff import Permission
from app.staff.seed_data import PERMISSIONS
from sqlalchemy import select
app = create_app('development')
with app.app_context():
    have = {p.code for p in db_session.execute(select(Permission)).scalars().all()}
    want = {c for c, _, _ in PERMISSIONS}
    print("missing:", want - have)
```

If anything's missing, insert it the same way this session did (see
`environment-readiness-report.md` for the exact script) — it's a data-sync operation, not a schema
change.

You'll also need at least one signing key generated and activated if the DB has none:
```python
from app.licensing_service.signing import generate_signing_key, activate_signing_key
row = generate_signing_key(app.config['SIGNING_KEY_DIRECTORY'])
activate_signing_key(app.config['SIGNING_KEY_DIRECTORY'], row.key_id)
```

### 6.3 Running Owner for real (not the test client)

```bash
cd owner
OWNER_DATABASE_URL="postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev" \
OWNER_EXTERNAL_API_ENABLED=true \
OWNER_INTERNAL_SYNC_SHARED_SECRET=<any-local-dev-value> \
python -c "
from app import create_app
from werkzeug.serving import make_server
app = create_app('development')
make_server('127.0.0.1', 5551, app).serve_forever()
"
```

Verify it's actually up and reachable with the correct URL suffix before doing anything else:
`curl http://127.0.0.1:5551/api/licensing/v1/service-info` should return a real
`active_signing_key_id`. **Watch for stray old server processes** on the same port from a previous
session — `Get-NetTCPConnection -LocalPort 5551` (PowerShell) tells you the real owning PID; this
session lost time to exactly this (a zombie process from an earlier session silently answering
requests with stale state). Kill it and start clean if in doubt.

### 6.4 Running the real products

```bash
AURA_OWNER_LICENSING_URL="http://127.0.0.1:5551/api/licensing/v1" \
AURA_OWNER_LICENSING_INSECURE=1 \
./dist/AuraClinic/AuraClinic.exe    # Clinic backend on :5000
./dist/AuraRetail/AuraRetail.exe    # Retail backend on :5001
```

Both will pop an Edge/Chrome `--app` window (no `pywebview` module in this build — falls back
automatically, harmless). Drive them via their real local API, same as their own frontend JS does:
`GET /api/health`, `GET /api/version`, `POST /api/licensing/activate`,
`POST /api/licensing/check-in`, `GET /api/licensing/status`.

## 7. Synthetic test accounts and data created this session (all in `aura_owner_dev`, all fake)

- `phase8vp-admin@example.com` / `Sup3r-Str0ng-Pass!` — super admin, no MFA enrolled.
- `phase8vp-approver@example.com` / `Sup3r-Str0ng-Pass!` — super admin, MFA enrolled, TOTP secret
  `UIQNTBZ2A45E2DKTMSMTKGVQ566ME64P` (use `pyotp.TOTP(secret).now()` to generate a live code).
- Several synthetic customers/subscriptions/licenses, all named `"Phase 8V-P Synthetic ... Co"` —
  safe to reuse or ignore, none are real.
- Reusable Owner UI driver pattern (real login + real MFA + real CSRF over `requests.Session`) is in
  each `scenario-*.py`-style script this session wrote to the scratch temp directory (not committed
  to the repo — if you need it again, the pattern is fully documented inline in
  `scenario-1-early-renewal-evidence.md` and `scenario-5-emergency-extension-evidence.md`; it's
  short enough to rewrite from those descriptions in a few minutes).

## 8. Known gaps — read before you assume something's broken

1. **Renewal `device_allowance_after` never propagates to `License.device_limit`.** This is real,
   found this session, and deliberately **not fixed** (building the sync would be a new
   commercial-operations capability, out of scope for a validation-only phase — see
   `final-residual-risk-register.md` item 3). If you're testing plan-downgrade-driven overage
   detection, you currently have to update `License.device_limit` by hand to see the enforcement
   mechanics (which do work correctly once that field changes).
2. **Pilot conversion's post-conversion assertion wasn't re-verified over a second live wire call**
   (a scripting oversight — the device's private key wasn't saved to disk before it was needed
   again). The underlying field-mapping is already unit-tested against real Postgres
   (`test_pilot_status_reflects_pilot_record`), just not re-proven live a second time. Cheap to
   redo if you want the extra confidence.
3. **`installations.transition` (the older, generic route)** still allows a device
   deactivation/replacement with an optional, often-blank reason, alongside the newer
   `release_device_slot()`/`replace_device_slot()` routes that require one. Low severity
   (restricting-only), carried forward from Phase 8V, still not fixed, still out of scope.
4. Retail's Android licensing screen has no Arabic string coverage (pre-existing gap, not from this
   phase). Windows installers remain unsigned (no code-signing cert available in this environment,
   disclosed since Wave 1B).

None of the above are P0/P1. Full list: `final-residual-risk-register.md`.

## 9. Standing rules that still apply (unchanged from every prior phase)

- Original `AuraEnterprise` repo: read-only, always.
- Never move/overwrite/delete an existing git tag.
- Never regenerate or touch the real Android production signing keys; never print a keystore
  password; never commit `keystore.properties` or a keystore file.
- Never weaken TLS verification, assertion verification, or trust-anchor validation in anything
  that ships. `AURA_OWNER_LICENSING_INSECURE`/`--insecure` are local-loopback-validation-only flags
  and must never appear in a real build's default configuration (they don't today — verified).
- No real customer/patient/payment data, ever — synthetic only.
- No Phase 9 work (no VPS, no public deployment, no payment gateway, no WhatsApp/SMS, no automatic
  updates, no Aura Core integration) until Phase 8 is unconditionally closed.
- Do not fabricate physical Android results. If a device isn't available, say so and stop the
  physical-only parts — exactly as this session did.

## 10. Full document map, if you need to go deeper

Everything under `docs/owner/phase8vp/` — `phase8-final-unconditional-decision.md` has the full
per-dimension verdict table; `final-physical-validation-matrix.md` has the scenario-by-scenario
PASS/CONDITIONAL/NOT-VERIFIED grid; each `scenario-N-*.md` has the real captured request/response
JSON for that scenario. `docs/owner/phase8v/` (no trailing P) is the previous phase's own closure —
worth skimming for the full Owner UI + service-layer design rationale if you're touching that code.
