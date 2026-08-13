# Phase 9.5B-R2 — Full Viewport Browser Validation

Real Playwright session against the live local dev server
(`aura_owner_dev`), synthetic data only. Screenshots saved under
`C:\Users\Dell\Desktop\AuraEnterprise\*.png` (one directory above the repo,
the same location Phase 9.5B-R's own screenshots landed at).

## Required viewport matrix coverage

| Viewport | Dashboard EN | Dashboard AR | Other pages |
|---|---|---|---|
| 1440×900 (desktop) | ✅ `r2-01` | ✅ `r2-08`, `r2-09` (post-fix) | Catalog AR `r2-12`; licenses list AR (snapshot) |
| 1024×768 | ✅ `r2-02` | ✅ `r2-07` | — |
| 768×1024 (tablet) | ✅ `r2-03` | ✅ `r2-06` | Installations list AR `r2-11` |
| 390×844 (mobile) | ✅ `r2-04` | ✅ `r2-05` | Customers list AR `r2-10`; Catalog AR `r2-13` (broken), `r2-14` (fixed) |

All four required viewports validated in both locales for the dashboard
(the page exercising the shared layout/nav/language-switcher most broadly).
Additional route families spot-checked across a mix of the remaining three
viewports, per the governing spec's "representative pages from every
current route family" requirement — not an exhaustive every-page ×
every-viewport × every-locale matrix (stated honestly; see
`final-residual-risk-register.md`).

## Route families covered by real browser evidence (screenshot or full
   accessibility-tree snapshot)

Dashboard, Customers (list + new-record form + create-and-view flow),
Installations (list), Licenses (list + detail, via accessibility snapshot),
Catalog (Products/Plans/Add-ons/Entitlement-definitions/Versions — full
page). Auth/login already covered by Phase 9.5B-R's own validation, spot-
re-confirmed this wave via the locale-switch-before-login flow used to set
up every other screenshot.

## Real defects found (see `rtl-defect-and-fix-log.md` for full technical
   detail; summarized here per Milestone 8's own evidence-table
   requirement)

1. **Mobile table collapse never worked for any of the 44 tables in the 50
   newly translated templates** (only `<thead>`/`<tbody>` had been added,
   missing the `class="responsive-table"` that actually triggers the CSS
   collapse rule). Found via the Catalog page at 390×844. **Fixed**: all 44
   tables given the class; re-screenshotted and confirmed fixed
   (`r2-13` before → `r2-14` after).
2. **All four "create new record" forms 405'd on submit** (customers,
   installations, licenses, subscriptions) — pre-existing, unrelated to
   translation, found only because this wave performed a real form
   submission rather than GET-only checks. **Fixed**: added the missing
   `action` attribute to all four; re-tested, confirmed working end-to-end.
3. **Audit-action labels incomplete for real production data**: the dev
   database's actual audit-log rows contained action codes
   (`EMPLOYEE_ACTIVATED`, `CHECK_IN_ACCEPTED`, `LICENSE_KEY_ISSUED`, etc.)
   not yet in `generic_audit_action_label()`'s dictionary — found via the
   dashboard's real "Recent staff actions" panel showing raw codes instead
   of Arabic text. **Fixed**: extended the label dictionary with the real
   codes found; re-screenshotted and confirmed fixed.

## No safety violations

No setup token, MFA secret, recovery code, password, session cookie,
private key, pepper, or real customer/patient data appears in any retained
screenshot — every account used was synthetic (`phase8vp-admin@example.com`,
a pre-existing dev-only test account with its password reset for this
session's testing only), and every customer/subscription/license created
was clearly synthetic test data.
