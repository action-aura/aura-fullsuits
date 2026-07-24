# Phase 7 — Final Release Validation Decision (Phase 7V, Part S)

Evaluated separately per the governing spec's required breakdown.

## 1. Clinic Windows rc.2 — CONDITIONAL PASS

Installation, upgrade (real rc.1→rc.2 with real data), signing (N/A — unsigned, disclosed),
activation, check-in, assertion verification, device identity, offline continuity, WARNING/
GRACE_PERIOD restricted-mode safety, data preservation, backup/restore/export, financial integrity
(N/A for Clinic), privacy boundary, artifact hygiene: all **PASS**, all with live evidence gathered
this session. Condition: installer is unsigned (known, disclosed, unchanged from Wave 1B) and
`RESTRICTED` state was not live-exercised (policy-configuration-dependent, not a code gap).

## 2. Clinic Android rc.2 — CONDITIONAL PASS (gated on physical validation)

Build, signing, certificate continuity: **PASS**, with live evidence. Physical-device
installation/upgrade/activation/lifecycle/privacy validation: **NOT VERIFIED** — no device
connected. Cannot be upgraded past CONDITIONAL PASS until a physical device is connected and Parts
K/L/N are completed for real.

## 3. Retail Windows rc.2 — CONDITIONAL PASS

Build, signing (N/A — unsigned), activation, financial-authority sale calculation (88.00 exact),
deactivation, artifact hygiene: **PASS**, live evidence. Condition: no pre-existing rc.1 install
with data existed on this machine to drive a full installer-level upgrade-with-data cycle the way
Clinic's was driven — the activation/financial/deactivation lifecycle was proven live instead, but
the specific "upgrade with pre-existing rc.1 data" sequence is **NOT VERIFIED** for Retail
specifically this session (the underlying mechanism — `.iss` AppId, config.py, PyInstaller spec —
is structurally identical to Clinic's verified one and received the identical fixes).

## 4. Retail Android rc.2 — CONDITIONAL PASS (gated on physical validation)

Same as Clinic Android: build/signing/certificate continuity **PASS**; physical validation
(including barcode and receipt-share regressions) **NOT VERIFIED** — no device connected.

## 5. Clinic overall — CONDITIONAL PASS

Windows: strong live evidence. Android: build-verified but physical-unverified. Overall gated on
the same physical-device dependency as #2.

## 6. Retail overall — CONDITIONAL PASS

Same structure as #5, plus the Retail-specific Windows upgrade-cycle gap noted in #3.

## 7. Phase 7 integration overall — CONDITIONAL PASS

The underlying Phase 7 licensing integration (shared Python core, signed-assertion protocol,
offline state machine, capability guards, authority-boundary design) is proven correct and now
additionally proven correct **on genuinely frozen/signed release artifacts**, not just source runs
— a real gap Phase 7V closed. Two real P0 packaging defects (stale PyInstaller cache,
missing trust-anchor bundling) and one real P1 security gap (Windows commercial-build TLS bypass)
and one real P0 Android build blocker (unguarded StrongBox API call) were found and fixed during
this validation pass — evidence that live release-artifact validation was necessary and caught
real issues unit tests alone had not (and could not, since they don't build/sign real artifacts).

## Evaluated dimensions

| Dimension | Verdict |
|---|---|
| Installation | PASS (Windows live; Android build-verified, install NOT VERIFIED physical) |
| Upgrade | PASS (Windows Clinic live; Windows Retail/Android NOT VERIFIED) |
| Signing | PASS (Android real production keystores, continuity confirmed); N/A/disclosed (Windows unsigned) |
| Activation | PASS (live, both products, Windows) |
| Check-in | PASS (live, Windows) |
| Assertion verification | PASS (live + 15 unit tests) |
| Device identity | PASS (DPAPI live; Android AndroidKeystore build-verified, not physically exercised) |
| Offline continuity | PASS (live, real Owner outage) |
| Restricted-mode safety | PASS for WARNING/GRACE_PERIOD (live); NOT VERIFIED for RESTRICTED (policy-dependent) |
| Data preservation | PASS (live, SQLite integrity + row-count checks across upgrade/uninstall/reinstall) |
| Backup/restore/export | PASS (live, real `create_backup()` call + Owner's 7/7 real pg_dump/pg_restore tests) |
| Financial integrity | PASS (live 88.00 recalculation + 36 unit tests) |
| Privacy boundary | PASS (source-level: no PII in licensing traffic, confirmed); NOT VERIFIED (physical Logcat inspection) |
| Artifact hygiene | PASS (all 4 artifact families inspected, clean) |
| Physical-device proof | **NOT VERIFIED** — no device connected |
| Suitability for future VPS deployment | Not evaluated — explicitly out of scope, no VPS work performed or implied |
| Suitability for controlled paid pilot after production infrastructure | Not evaluated here — see explicit non-claims below |

## Verdicts

- Clinic Windows rc.2: **CONDITIONAL PASS**
- Clinic Android rc.2: **CONDITIONAL PASS**
- Retail Windows rc.2: **CONDITIONAL PASS**
- Retail Android rc.2: **CONDITIONAL PASS**
- Clinic overall: **CONDITIONAL PASS**
- Retail overall: **CONDITIONAL PASS**
- **Phase 7 overall closure: CONDITIONAL PASS**

A missing mandatory physical signed-upgrade result prevents a final unconditional PASS, exactly as
the governing spec requires. The Windows unsigned installer remains a known, disclosed,
controlled-pilot condition — not hidden.

## Explicit non-claims

Nothing in this decision, or in any Phase 7V document, describes these products as: publicly
deployed, production-operated, enterprise-grade, internet-scale, ready for unsupervised customer
release, or connected to real customers. Those claims would require separate, later phases.
