# Phase 7 — Final Release Validation Decision, v2 (Phase 7V-F, Part T)

Supersedes `docs/licensing/phase7v/phase7-final-release-validation-decision.md` as the current
verdict. That document is preserved unmodified for history.

## 1. Clinic Windows rc.2 — **PASS**

Installation, real rc.1→rc.2 upgrade with real data, activation, check-in, real Owner-outage
offline continuity, **real live RESTRICTED-mode enforcement (now genuinely reachable — the P0 that
blocked this is fixed)**, read-access preservation in RESTRICTED, mutation denial in RESTRICTED,
data preservation, artifact hygiene: all PASS with live evidence this session. Condition unchanged
from Phase 7V: installer remains unsigned (known, disclosed).

## 2. Clinic Android rc.2 — **CONDITIONAL PASS** (gated on physical validation)

Build, signing, certificate continuity (real, on-device verified), physical signed upgrade, and
physical data preservation: **PASS**, real, on-device. Physical activation-onward lifecycle: **NOT
VERIFIED** — device disconnected before this could be completed.

## 3. Retail Windows rc.2 — **PASS**

Real rc.1→rc.2 upgrade with real data (closes the gap left open by Phase 7V), real
activate/check-in/offline/RESTRICTED lifecycle, real 88.00 financial-integrity re-confirmation
(including live, in RESTRICTED state), read/mutation enforcement verified. Condition unchanged:
installer remains unsigned.

## 4. Retail Android rc.2 — **CONDITIONAL PASS** (gated on physical validation)

Build, signing, certificate continuity (real, on-device verified): **PASS**. Physical
upgrade/activation/lifecycle: **NOT VERIFIED** — device disconnected before this part began.

## 5. Clinic overall — **CONDITIONAL PASS**

Windows: strong, complete, live evidence, including a real defect found and fixed as a direct
result of this phase's required testing. Android: build/signing verified, physical upgrade proven,
physical lifecycle beyond that not completed.

## 6. Retail overall — **CONDITIONAL PASS**

Windows: complete. Android: build/signing verified only; no physical proof at all this session
(device disconnected before Retail's Android portion began).

## 7. Phase 7 overall closure — **CONDITIONAL PASS**

The underlying licensing implementation is now demonstrably MORE correct than at any prior
checkpoint — this phase's live testing surfaced and closed a genuine, previously-invisible P0 in
the offline-enforcement core. What remains is exclusively the physical Android completion gap,
which is an external hardware-connectivity dependency, not a code defect.

## Evaluated dimensions (delta from Phase 7V)

| Dimension | Phase 7V | Phase 7V-F |
|---|---|---|
| Trusted-time offline/warning/restricted correctness | Assumed correct (never live-tested through a real continuous outage) | **Found broken, fixed, proven correct live** |
| Retail Windows rc.1→rc.2 upgrade | NOT VERIFIED | **PASS** |
| Live Windows restricted-mode enforcement | NOT VERIFIED | **PASS** |
| Physical Android certificate continuity | N/A (no device) | **PASS** (real, on-device) |
| Physical Android signed upgrade | N/A (no device) | Clinic: PASS; Retail: NOT VERIFIED |
| Physical Android full lifecycle | N/A (no device) | NOT VERIFIED (both products) |

## Verdicts

- Clinic Windows rc.2: **PASS**
- Clinic Android rc.2: **CONDITIONAL PASS**
- Retail Windows rc.2: **PASS**
- Retail Android rc.2: **CONDITIONAL PASS**
- Clinic overall: **CONDITIONAL PASS**
- Retail overall: **CONDITIONAL PASS**
- **Phase 7 overall closure: CONDITIONAL PASS**

A missing mandatory physical Android lifecycle result (both products) prevents a final
unconditional PASS, exactly as the governing spec requires. The Windows unsigned installer remains
a known, disclosed, controlled-pilot condition.

## Explicit non-claims

Nothing in this decision, or in any Phase 7V-F document, describes these products as: publicly
deployed, production-operated, enterprise-grade, internet-scale, ready for unsupervised customer
release, or connected to real customers.
