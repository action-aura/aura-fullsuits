# Wave 1C -- Localization and UX Gate (Part K)

## Localization
| Item | Status | Evidence |
|---|---|---|
| English default | PASS | Both products |
| Arabic completeness | PASS | Retail: complete since earlier waves. Clinic: MOB-007 fully closed in Wave 1B (~150 call sites + `AppRoot.kt` drawer/nav gap fixed), physically device-reconfirmed ("all good" from real-device pass) |
| RTL | PASS | `AppLocale.isRtl`-driven `LayoutDirection`, physically verified on real device (Wave 1A) |
| Language persistence | PASS | Covered by `retail_localization_test.py`/`clinic_localization_test.py` |
| No raw localization keys | PASS | `StringsCoverageTest` (Clinic) actively guards against blank/duplicate entries |
| No major English leakage in Arabic mode | PASS | `HardcodedStringAuditTest` extended to cover `AppRoot.kt` after the original gap; re-run this wave found zero new hardcoded-string matches under either app's `ui/` tree |
| Financial error messages | PASS | Covered by existing payment/sale error-classifier tests |
| Backup/restore messages | PASS | Translated, part of the MOB-007 closure scope |
| Onboarding | PASS | Both products, both platforms |
| Empty states | PASS | `EmptyState(title=...)` call sites included in the MOB-007 `tr()` sweep |
| Confirmation dialogs | PASS | Restore-replaces-current-data confirmation exists on both mobile apps (Wave 1A); uninstall data-deletion confirmation is two-step and product-specific (Wave 1B, REL-001 fix) |
| Destructive-action warnings | PASS | Same evidence as above -- uninstall, restore, and backup-overwrite all require explicit confirmation, worded per-product (business data vs. patient/clinical data) |
| Small-screen behavior | **NOT INDEPENDENTLY VERIFIED THIS WAVE** | Physically tested only on one device model (Infinix X6528) across all waves to date -- no small-screen/tablet/other-resolution matrix exists |
| Keyboard overlap | **NOT INDEPENDENTLY VERIFIED THIS WAVE** | Same single-device caveat; no adversarial input-field/keyboard-overlap testing recorded in any wave's evidence |
| Windows screen layout | PASS | Confirmed functional through repeated manual smoke testing across Wave 1B installer verification |
| Android back navigation | PASS | Covered by Wave 1A's role-navigation and lifecycle-resilience testing |
| Visible version information | PASS | `/api/version` (both), Android `BuildConfig.VERSION_NAME` wired into Settings → About (Wave 1B fixed a hardcoded fake Clinic version string here, added the missing Retail equivalent for parity) |
| Support information | **NOT FOUND** | See `operational-supportability-gate.md` -- no in-app support contact exists |
| Understandable failure messages | PASS | Payment/login/appointment error classifiers give specific, non-generic messages (Wave 1A MOB-003/004 fixes) |

## Gate-failing categories (per spec's own rule -- do not fail on cosmetic P4)
None of the items above rise to "blocked onboarding," "unreadable Arabic," "inaccessible payment/sale action," "misleading financial message," "hidden destructive action," or "unrecoverable navigation trap." The two "NOT INDEPENDENTLY VERIFIED" items (small-screen behavior, keyboard overlap) are coverage gaps, not known failures -- they are carried forward honestly as untested rather than assumed fine.

## Verdict
**Localization and UX gate: PASS**, with two disclosed, non-blocking coverage gaps (small-screen/keyboard-overlap testing limited to a single device model) and one disclosed cross-cutting gap shared with the Operational Supportability gate (no in-app/customer-facing support contact). None of these are onboarding-blocking, financially-misleading, or destructive-action-hiding, so none independently fail Gate 2 or Gate 3. The single-device testing scope is a real limitation for Gate 4 (General Paid SMB Release), where a broader device/screen-size matrix would be expected before an unqualified "works on your hardware" claim.
