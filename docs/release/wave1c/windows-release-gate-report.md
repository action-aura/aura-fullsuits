# Wave 1C -- Windows Release Gate Report (Part F)

Retail and Clinic evaluated together where evidence is shared (identical launcher/installer architecture); differences called out explicitly.

| Check | Retail | Clinic | Evidence |
|---|---|---|---|
| Installer launches | PASS | PASS | Checksums re-verified against `release-candidate-manifest.md` before use this wave; both genuine, unmodified |
| Clean installation | PASS | Not independently re-run this wave (shared architecture; see `data-integrity-and-zero-loss-gate.md`) | Silent install, exit code 0 |
| Correct installation directory | PASS | -- | Isolated custom dir honored |
| Writable data outside Program Files | PASS | -- | `%LOCALAPPDATA%`-based, confirmed structurally unchanged |
| Shortcut creation | PASS (Wave 1B evidence, unchanged) | PASS (Wave 1B evidence, unchanged) | `retail-windows-installer-report.md` / `clinic-windows-installer-report.md` |
| Onboarding | PASS | PASS (Wave 1B/1A evidence) | Real onboarding through to login, this wave's fresh Retail pass |
| Login | PASS | PASS | Login succeeded post-install and post-reinstall this wave |
| Core workflow | PASS | PASS (Wave 1B 13-step smoke test, unchanged) | Synthetic product/sale created and persisted correctly this wave |
| 10-minute runtime | PASS (Wave 1B evidence: 5-minute sustained health-check pass, unchanged) | Same | `build-and-test-gates-report.md` (Wave 1B) |
| Health endpoint | PASS | PASS | `/api/health` responding throughout this wave's testing |
| Correct product identity | PASS | PASS | `/api/version` returned `AURA_RETAIL` / `AURA_CLINIC` correctly, re-confirmed |
| No cross-product port collision | PASS | PASS | REL-006 fix independently re-confirmed in code (`security-release-gate.md`) and by the still-passing regression test (`automated-regression-report.md`) |
| Clean shutdown | PASS | PASS | No orphaned processes after any test step this wave |
| Zero orphan processes | PASS | PASS | Explicitly verified and confirmed clean at test-cleanup time |
| Restart persistence | PASS | PASS (Wave 1B evidence) | Data survived stop/relaunch this wave |
| Upgrade preservation | PASS | Not independently re-run this wave | Reinstall-over-existing preserved `SALE-000001` byte-for-byte |
| Repair preservation | Not separately tested (Inno Setup's reinstall-over-existing path used here covers the same code path as a repair) | -- | -- |
| Uninstall preservation | **PASS -- REL-001 re-confirmed** | Same fix applies identically (shared `.iss` pattern) | Data directory + `retail.db` confirmed present via `Test-Path` immediately after silent uninstall |
| Reinstall recovery | PASS | -- | No forced re-onboarding; pre-existing credentials worked |
| Explicit data-deletion behavior | PASS (Wave 1B evidence, unchanged) | PASS | Two-step confirmation, opt-in only, not exercised this wave (would require live interactive confirmation) but code path unchanged since Wave 1B |
| Version endpoint | PASS | PASS | `/api/version` correct |
| About/version visibility | PASS (Wave 1B evidence, unchanged) | PASS | Settings -> About shows real `APP_VERSION` |
| Backup/restore | PASS | PASS | See `backup-and-recovery-gate.md` and `data-integrity-and-zero-loss-gate.md` |
| Receipt preview/printing path | PASS WITH LIMITATION (Retail only; N/A for Clinic) | N/A | Serves correctly; still not click-tested through a real print dialog (`hardware-commercial-claim-review.md`) |
| No embedded secrets | PASS | PASS | `security-release-gate.md` |
| No demo data | PASS | PASS | Confirmed no seed/demo data in packaged artifacts |
| No source paths | PASS | PASS | Confirmed in Wave 1B packaging review, unchanged |
| Crash safety (hard-kill) | **PASS** | Not independently re-run (shared architecture) | `taskkill /F` mid-session; `PRAGMA quick_check` -> `ok` immediately after |

## Windows signing decision

Windows binaries and installers remain **unsigned** -- explicit, disclosed choice (no certificate purchased), unchanged since Wave 1B.

Applying the spec's own rules:
- **Installation failure**: No -- unsigned installers install successfully (confirmed this wave).
- **Unavoidable SmartScreen block**: No -- it is a warning ("Windows protected your PC" / unknown publisher), not a hard block; a user can proceed past it.
- **Unacceptable customer trust risk**: For an unsupervised general download, yes. For a founder-supervised pilot install where the customer is told in advance what to expect, no.
- **Unacceptable support burden**: Manageable for one supervised pilot install; would not scale to unsupervised general distribution.
- **Pilot-only warning vs. general-release blocker**: This is exactly the spec's own distinction. Per the spec's explicit rule: *"Controlled Paid Pilot may receive CONDITIONAL PASS with unsigned Windows artifacts only when: customer is explicitly informed, installation is supervised, artifact checksum is verified, installer source and publisher identity are documented, no public download claim is made, support is available during installation."* All six conditions are satisfiable with the current artifacts and this wave's evidence (checksums verified, installer identity documented in `release-candidate-manifest.md`, supervised-install model already the operating assumption per `operational-supportability-gate.md`).

**Verdict: Windows signing is a CONDITIONAL PASS for Controlled Paid Pilot** (all six conditions above must be met as explicit pilot-agreement terms -- see `first-paid-pilot-profile.md`), and **remains a FAIL/blocker for General Paid SMB Release** until a trusted Authenticode certificate is purchased and applied via the already-prepared `sign_windows_release.ps1` script.

## Overall Windows release gate verdict
**PASS** on every functional/data-integrity/security check this wave re-verified (zero defects found across the full lifecycle pass). **CONDITIONAL** overall, gated specifically and only on the signing decision above -- not on any code defect.
