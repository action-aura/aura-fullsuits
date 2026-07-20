# Wave 1C -- Release Gate Scorecard (Part M)

Verdicts: PASS / CONDITIONAL PASS / FAIL / NOT APPLICABLE. Every CONDITIONAL PASS lists its enforceable conditions.

## GATE 1 -- Internal Testing

| | Retail Windows | Retail Android | Clinic Windows | Clinic Android |
|---|---|---|---|---|
| Builds/installs | PASS | PASS | PASS | PASS |
| No destructive default behavior | PASS | PASS | PASS | PASS |
| Core workflow starts | PASS | PASS | PASS | PASS |
| Synthetic data only (this wave's testing) | PASS | N/A (no device session this wave; Wave 1A evidence) | PASS | N/A (same) |
| No known P0 | PASS (zero unresolved P0/P1, registry updated this wave) | PASS | PASS | PASS |
| **Gate 1 overall** | **PASS** | **PASS** | **PASS** | **PASS** |

## GATE 2 -- Controlled Pilot

| | Retail Windows | Retail Android | Clinic Windows | Clinic Android |
|---|---|---|---|---|
| Core workflows work | PASS | PASS (Wave 1A device evidence) | PASS | PASS (Wave 1A device evidence) |
| Financial P0/P1 resolved | **PASS** -- 4/4 fresh adversarial cases | PASS (same backend) | **PASS** -- 8/8 fresh adversarial cases | PASS (same backend) |
| Data-integrity P0/P1 resolved | PASS -- full lifecycle re-proven this wave | PASS (Wave 1A device evidence, artifacts unchanged) | PASS | PASS |
| Security/privacy P0/P1 resolved | PASS | PASS | PASS (2 disclosed, non-blocking, documented-design limitations) | PASS |
| Backups exist | PASS | PASS | PASS | PASS |
| Support process exists | CONDITIONAL -- founder-supervised only, no self-service docs | Same | Same | Same |
| Known limitations documented | PASS -- `wave1c-residual-risk-register.md` | PASS | PASS | PASS |
| **Gate 2 overall** | **PASS** | **PASS** | **PASS** | **PASS** |

## GATE 3 -- Controlled Paid Pilot

| | Retail Windows | Retail Android | Clinic Windows | Clinic Android |
|---|---|---|---|---|
| Financial correctness verified | PASS | PASS | PASS | PASS |
| Transaction integrity verified | PASS | PASS | PASS | PASS |
| Data preservation verified | PASS | PASS (Wave 1A) | PASS | PASS (Wave 1A) |
| Backup/restore verified | PASS | PASS | PASS | PASS |
| Signed Android artifacts | N/A | PASS -- fresh signed rebuild, cert verified | N/A | PASS -- fresh signed rebuild, cert verified |
| Windows installer verified | PASS | N/A | PASS | N/A |
| Supervised support | CONDITIONAL (required term) | CONDITIONAL | CONDITIONAL | CONDITIONAL |
| Rollback plan | PASS -- prior installer + backup retained | PASS | PASS | PASS |
| No unresolved normal-use P0/P1 | PASS | PASS | PASS | PASS |
| Hardware claims match evidence | **CONDITIONAL** -- only Android camera scanning is physically verified; HID/printing require per-customer verification before relying on them | **CONDITIONAL** -- same | N/A -- Clinic has no hardware dependency | N/A |
| Limitations explicitly accepted | Required pilot-agreement term (`first-paid-pilot-profile.md`) | Same | Same | Same |
| **Gate 3 overall** | **CONDITIONAL PASS** -- conditions: (1) unsigned-Windows six-point disclosure per `windows-release-gate-report.md`, (2) founder-supervised install/support, (3) hardware claims scoped to verified channels only, per-customer hardware verification before relying on scanner/printer | **CONDITIONAL PASS** -- conditions (2) and (3) above (no Windows-signing condition on Android) | **CONDITIONAL PASS** -- conditions (1) and (2) above (no hardware condition) | **PASS** -- no outstanding condition beyond the general supervised-support model shared across the whole pilot |

## GATE 4 -- General Paid SMB Release

| | Retail Windows | Retail Android | Clinic Windows | Clinic Android |
|---|---|---|---|---|
| Unsupervised/lightly-supervised install | FAIL -- current model assumes founder-supervised install | FAIL | FAIL | FAIL |
| Professional trust/signing posture | **FAIL** -- unsigned | N/A (Android signed) | **FAIL** -- unsigned | N/A (Android signed) |
| Stable upgrade path | PASS (mechanism proven) | PASS | PASS | PASS |
| Customer documentation | **FAIL** -- no install/backup/restore/troubleshooting guides, no support contact | FAIL | FAIL | FAIL |
| Printer/scanner claims aligned with evidence | **FAIL** for an unqualified claim -- only Android camera scanning is physically verified | FAIL (same) | N/A | N/A |
| Support process | FAIL -- no escalation process, no documented rollback procedure for the team | FAIL | FAIL | FAIL |
| Release process | CONDITIONAL PASS -- versioning/checksum/manifest discipline exists and is real, but no automated release pipeline | Same | Same | Same |
| Incident process | FAIL -- not written down | FAIL | FAIL | FAIL |
| No material P0/P1 | PASS | PASS | PASS | PASS |
| Acceptable P2 backlog | PASS -- remaining P2s (AUDIT-013/017/020/026) are documented and non-blocking at pilot scale | PASS | PASS | PASS |
| Predictable hardware compatibility for advertised market | **FAIL** -- cannot make an unqualified "works with your hardware" claim yet | FAIL | N/A | N/A |
| **Gate 4 overall** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |

## GATE 5 -- Enterprise Grade

| | Retail | Clinic |
|---|---|---|
| Mature RBAC | FAIL -- binary/subsystem-level access model, not fine-grained | FAIL -- binary doctor/secretary model, documented as deliberate but not fine-grained |
| Tenant isolation | PASS -- company-scoped throughout, IDOR-hardened (Clinic), re-confirmed no cross-tenant leak this wave | PASS |
| Auditability | CONDITIONAL -- `audit_log`/`clinic_audit_log` exist and are used, not independently exhaustively re-audited this wave | CONDITIONAL |
| Observability | FAIL -- no metrics, no structured monitoring beyond basic health/version endpoints | FAIL |
| Remote operational controls | FAIL -- none exist (no Owner platform yet) | FAIL |
| Controlled updates | FAIL -- not built | FAIL |
| Formal disaster recovery | FAIL -- real, tested backup/restore exists; no formal rehearsed DR drill program | FAIL |
| Scale evidence | FAIL -- no fresh large-scale (10,000+ row) test this wave; historical ~5,000-row test only | FAIL |
| SLA/support readiness | FAIL -- no SLA, no formal support tiering | FAIL |
| Integration governance | FAIL -- integration layer exists (`accounting-integration`-style pattern not present here; Clinic/Retail have no external integration surface at all yet) | FAIL |
| Compliance-readiness controls | FAIL -- no formal privacy notice, no data-retention policy, no export capability | FAIL |
| Enterprise deployment models | FAIL -- single-tenant local-only deployment, no multi-branch/cloud model | FAIL |
| **Gate 5 overall** | **FAIL** | **FAIL** |

## Summary
No product/platform passes Gate 4 or Gate 5 today, and none should be described as such. Both products clear Gate 1, Gate 2, and Gate 3 (Clinic Android unconditionally; every other combination with explicit, enforceable conditions). This is a materially different picture from the pre-Wave-0 baseline audit, where nothing cleared Gate 2 -- every blocker named in that baseline for the ten original release-blocking defects has since been fixed and, for the P0/P1 set, independently re-proven this wave.
