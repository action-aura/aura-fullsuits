# Wave 1C -- Commercial and Enterprise Scorecard (Part N)

Scores are 0-100, evidence-based against this wave's own re-verification (`financial-release-gate-report.md`, `data-integrity-and-zero-loss-gate.md`, `security-release-gate.md`, `clinic-privacy-release-gate.md`, `android-release-gate-report.md`, `windows-release-gate-report.md`, `operational-supportability-gate.md`) plus the historical baseline (`docs/audit/23-enterprise-grade-scorecard.md`) where a dimension was not independently re-tested this wave. **A numerical average never overrides a gate failure** -- see `release-gate-scorecard.md` for the gate-level verdicts this scorecard does not supersede.

Classification bands: 0-29 Prototype/unsafe -- 30-49 Early MVP -- 50-64 Beta/controlled pilot -- 65-74 Commercially usable for small customers with limitations -- 75-84 Strong SMB, production-capable with identified gaps -- 85-92 Enterprise-capable with remaining gaps -- 93-100 Mature enterprise-grade.

## Aura Retail -- Windows
| Dimension | Score | Basis |
|---|---|---|
| Functional completeness | 78 | Onboarding, sales, returns, stock, backup all real and working; no export/reporting depth assessed |
| Financial correctness | 90 | 4/4 fresh adversarial cases pass this wave (manipulated totals, idempotency, over-return all correctly rejected/handled) |
| Data integrity | 85 | Full install/upgrade/uninstall/crash lifecycle re-proven this wave, zero defects |
| Security | 85 | 18-point fresh re-check, zero new issues, REL-006/SEC-001 confirmed still fixed |
| Privacy | 80 | Not a patient-data product, lower inherent exposure, no findings |
| Reliability | 78 | Hard-kill crash recovery proven this wave; no long-duration (multi-day) soak test performed |
| Offline resilience | 85 | Fully local architecture, real restart-persistence proof |
| Performance | 55 | Not re-benchmarked this wave; historical ~5,000-row test only, no fresh index work |
| Usability | 70 | Functional, real onboarding; single-device UX testing only |
| Localization | 88 | Real, tested, working (en/ar) |
| Test maturity | 80 | 285 backend tests + this wave's 4 new adversarial financial tests, all passing in isolation |
| Deployment maturity | 70 | Real installer, verified full lifecycle; unsigned |
| Packaging maturity | 72 | Checksummed, versioned, manifested |
| Backup and recovery | 85 | Real, tested, adversarially re-proven this wave (corrupted archive + cross-product rejection) |
| Commercial readiness | 68 | Clears Controlled Paid Pilot conditionally; blocked from general release by signing + docs + hardware verification gaps |
| **Enterprise readiness** | **22** | Gate 5 fails outright (no observability, controlled updates, formal DR, scale evidence, SLA) |

## Aura Retail -- Android
| Dimension | Score | Basis |
|---|---|---|
| Functional completeness | 72 | Same backend; camera scanning physically verified, HID/printing unverified |
| Financial correctness | 90 | Same server-authoritative backend, same fresh proof |
| Data integrity | 82 | Same backend; device-level upgrade proof from Wave 1A on unchanged (bit-identical) artifacts |
| Security | 84 | Same backend posture; manifest/exported-component checks clean |
| Privacy | 78 | Same basis as Windows |
| Reliability | 65 | Real device testing exists (Wave 1A, one device model) but not repeated this wave |
| Offline resilience | 80 | Architecturally sound, device-proven in Wave 1A |
| Performance | 45 | Never benchmarked on-device |
| Usability | 60 | Real device UX proven functional on one device model only |
| Localization | 85 | Real, tested; MOB-007 fully closed and device-reconfirmed |
| Test maturity | 75 | 36/36 unit tests this wave, clean lint, signed build verified |
| Deployment maturity | 78 | Fresh signed rebuild, bit-identical to shipped, cert-verified |
| Packaging maturity | 75 | Same basis |
| Backup and recovery | 82 | Same backend, device-proven working in Wave 1A ("working just fine... even the back up") |
| Commercial readiness | 65 | Clears Controlled Paid Pilot conditionally on hardware-claim scoping (camera-only, until per-customer HID/printer verification) |
| **Enterprise readiness** | **20** | Same Gate 5 failures as Windows, plus single-device-model testing scope |

## Aura Retail -- Overall product
**Commercial readiness: 67/100 -- "Commercially usable for small customers with limitations."** **Enterprise readiness: 21/100 -- Prototype-band, far from enterprise-grade.** Weighted toward the platform a realistic deployment would combine (Windows till + Android for scanning).

## Aura Clinic -- Windows
| Dimension | Score | Basis |
|---|---|---|
| Functional completeness | 82 | Broadest feature set of either product; only missing invoice states (AUDIT-015, non-blocking) |
| Financial correctness | 88 | 8/8 fresh adversarial cases pass this wave, including a fault-injected transaction-rollback proof |
| Data integrity | 82 | FK enforcement correct by design; backup/restore adversarially re-proven this wave |
| Security | 85 | Same 18-point fresh re-check as Retail, zero new issues |
| Privacy | 68 | Two disclosed, documented, non-blocking limitations (secretary read access, session-version staleness) meaningfully cap this for a patient-data product |
| Reliability | 85 | The single most thoroughly tested workflow across every wave (13-step Wave 1B smoke test + this wave's fresh evidence) |
| Offline resilience | 85 | Same architecture, well-proven |
| Performance | 55 | Same historical basis as Retail, not re-benchmarked |
| Usability | 75 | Real, working onboarding through daily use |
| Localization | 90 | MOB-007 fully closed and physically device-reconfirmed this-wave-adjacent |
| Test maturity | 82 | 91 Clinic-specific backend tests + 8 new adversarial financial tests, all passing |
| Deployment maturity | 70 | Real installer, verified lifecycle (shared architecture with Retail); unsigned |
| Packaging maturity | 72 | Same basis |
| Backup and recovery | 85 | Real, tested, adversarially re-proven this wave |
| Commercial readiness | 72 | Clears Controlled Paid Pilot with only the shared signing/docs conditions -- no hardware dependency at all, closer to unconditional than Retail |
| **Enterprise readiness** | **26** | Gate 5 fails outright, same structural gaps as Retail, slightly ahead on auditability |

## Aura Clinic -- Android
| Dimension | Score | Basis |
|---|---|---|
| Functional completeness | 78 | Same backend completeness; invoice drill-down and appointment-range features added in Wave 1A |
| Financial correctness | 88 | Same fresh proof as Windows (server-computed, platform-agnostic) |
| Data integrity | 80 | Same backend, device-level proof from Wave 1A on unchanged artifacts |
| Security | 84 | Same posture |
| Privacy | 72 | Same two disclosed limitations as Windows, plus FLAG_SECURE confirmed correctly applied this wave (a plus specific to Android) |
| Reliability | 70 | Real device testing exists (Wave 1A), not repeated this wave |
| Offline resilience | 82 | Device-proven in Wave 1A |
| Performance | 48 | Never benchmarked on-device |
| Usability | 68 | Real device UX proven, one device model |
| Localization | 90 | Same basis as Windows |
| Test maturity | 80 | 49/49 unit tests this wave, clean lint, signed build verified |
| Deployment maturity | 78 | Fresh signed rebuild, bit-identical, cert-verified |
| Packaging maturity | 76 | Same basis |
| Backup and recovery | 82 | Device-proven working in Wave 1A |
| Commercial readiness | **74** | Clears Controlled Paid Pilot **unconditionally on hardware** (no scanner/printer dependency) -- the single highest commercial-readiness score of any platform in this scorecard |
| **Enterprise readiness** | **24** | Same Gate 5 failures |

## Aura Clinic -- Overall product
**Commercial readiness: 73/100 -- "Commercially usable for small customers with limitations,"** the strongest of the two products, consistent with `launch-order-recommendation.md`'s conclusion. **Enterprise readiness: 25/100.**

## Cross-cutting note on the "enterprise readiness" gap
Both products score in the low-to-mid 20s on enterprise readiness -- a wide, honest gap from the 65-74 commercial-readiness band they've earned. This is not a scoring inconsistency: commercial readiness measures "can one small customer use this safely today," which both products now satisfy with disclosed conditions; enterprise readiness measures a structurally different bar (observability, controlled updates, formal DR, SLA, compliance controls, Owner/licensing governance) that no wave to date has attempted to build, by design. See `docs/audit/23-enterprise-grade-scorecard.md` for the historical pre-Wave-0 baseline these numbers improve on -- the improvement is concentrated entirely in financial/data-integrity/security/packaging dimensions, exactly the areas Wave 0 through Wave 1C targeted, and is genuinely near-zero in the enterprise-maturity dimensions, exactly as expected since nothing in any wave to date has worked on them.
