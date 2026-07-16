# Commercial Readiness Audit

Evaluated as if a real customer will pay next week, per the audit's own framing.

## First impression

- **Branding**: no custom app icon on either Windows product (default
  PyInstaller icon, `14`); Android apps have proper launcher icons (adaptive,
  per-product, `docs/android/android-source-inventory.md`). Inconsistent
  branding maturity across platforms.
- **Splash / first-run**: Android shows a real loading screen
  (`LoadingScreen()` composable, branded "Action Aura" wordmark); Windows'
  first thing a user sees is either a native window or a browser tab — no
  splash screen concept exists for Windows.
- **Professional appearance / consistency**: Compose UI (Android) is a
  polished, purpose-built native design system (Material 3, custom theme,
  glow/aurora effects per source read in Phase 4); the web frontend
  (`subsystem-retail.js`/`subsystem-clinic.js`) was not visually inspected in
  this pass (no browser available) — UNVERIFIED for visual quality, though
  functionally proven correct where tested.
- **Empty states**: Android has explicit empty-state composables
  (`EmptyState` in `RetailScreens.kt`, "No products yet" / "Add products to
  start selling."); not independently confirmed for the web frontend.

## Onboarding

- **Clinic**: real, working, smoke-tested end to end (`13`/`14`) — a genuine
  commercial-readiness strength.
- **Retail**: **completely broken** — no self-service account creation exists
  at all (`12`). This alone makes "time to first value" undefined/infinite for
  a real Retail customer today; this is the single largest commercial-
  readiness blocker found in this entire audit.

## Daily use

Not independently assessed via a real user-testing session (no such
methodology was in scope for this analysis-only phase). Structural
observations only: Android's POS flow (search/scan → cart → charge) is a
short, few-tap flow by source read; the missing discount capability (`03`)
is itself a workflow gap a real cashier would immediately notice and be
unable to work around. No undo/recovery UI was found for a mis-rung sale on
either platform (a wrong sale, once submitted, has no "undo" — only a manual
return, which per `03` has no linkage back to the original sale anyway).

## Supportability

- **Error messages**: Clinic's `create_patient` deliberately returns a
  generic client message while logging detail server-side (`11`) — good
  practice, applied inconsistently elsewhere (not verified as universal
  across every route in either product).
- **Diagnostic codes**: none found — errors are free-text messages, no
  structured error codes for a support agent to search against.
- **Log usefulness**: desktop launchers log lifecycle events with timestamps
  to a per-install `logs/startup.log` (`11`) — a reasonable baseline for a
  support agent to request from a customer.
- **Backup/restore availability**: **none exists** (`18`) — a real
  commercial-readiness blocker; "can you recover my data if my laptop dies"
  currently has no good answer for either product.
- **Documentation**: extensive *internal engineering* documentation exists
  (`docs/migration/`, `docs/build/`, `docs/android/`, this audit itself) —
  none of it is customer-facing. No user manual, no support-contact
  information, no FAQ was found anywhere in the repo.

## Commercial trust signals

| Signal | Retail | Clinic |
|---|---|---|
| Visible version number | Present but inconsistent across platforms (`01`) | Same |
| Company identity in-app | Not independently confirmed (no "About" screen located in either Kotlin source tree) | Same |
| Privacy messaging | None found (no in-app privacy notice/policy link) | Same — notable given Clinic handles patient data |
| Backup messaging | Android has a Settings row literally labeled "Backup & restore" that opens a "coming soon" toast (per the data-integrity agent's evidence sweep) — **actively tells the user a feature exists that does not** | Same |
| Subscription/licensing readiness | `commercial_runtime/licensing_contracts/` exists as a directory but was not found wired into any enforcement path in the routes sampled — scaffolding only, matches the explicit "no licensing enforcement" scope exclusion already established for this phase and prior phases | Same |
| Signed builds | No (`09`/`10`) | Same |
| Support contact | Not found anywhere | Not found anywhere |
| Terms/privacy availability | Not found anywhere | Not found anywhere |

## Verdict inputs for `24`

Retail is not commercially usable today in any capacity beyond a controlled
internal demo (cannot onboard a real customer at all, and undercharges tax on
Android even once bypassed). Clinic is materially closer — a real customer
could plausibly complete onboarding, use core workflows, and be billed
correctly — but shares Retail's total absence of backup/restore, signing, and
customer-facing documentation, all of which matter before any paid
relationship, especially one involving patient data.
