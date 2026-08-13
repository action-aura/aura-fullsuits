# Owner App — UI Security Review (Stage E)

Real, evidence-based security review of the UI layer built across this
whole modernization phase — describes what was actually verified,
cross-check against the real evidence/diffs referenced below, not a plan.

## Method

Consistent with every Stage D pass's own security verification (never a
separate, one-time bolt-on): code review of every real permission
decorator/route touched, curl/browser-based verification of the real
access-control boundary, and this Stage E pass's own real, 215-entry
role-gating matrix (`browser-role-validation-report.md`) as the largest
single piece of empirical evidence gathered in this whole phase.

## CSP — verified, unchanged, still correctly restrictive

`script-src 'self'`, no `unsafe-inline`, confirmed via the real response
headers on every curl-verified page throughout Stage D and re-confirmed
this pass: **zero new inline `<script>` tags were added anywhere in this
Stage E pass** (`grep -rn "<script>" `over every changed template
returns only the pre-existing, already-CSP-compliant external `<script
src="...">` includes). The `axe-core` scan itself never touched the
page's own CSP — verified in `accessibility-report.md`'s Method section:
`page.evaluate()` executes via the browser's debugging protocol, not a
page-loaded `<script>` tag, so it was never a CSP bypass risk introduced
into the app itself, only a testing-tool implementation detail.

## CSRF — verified, unchanged

Every form touched by this pass's 140 label/aria-label fixes was a
pre-existing form with its own pre-existing `<input type="hidden"
name="csrf_token">` — confirmed by reading every diff before applying it
(the automated `fix_bare_labels.py` script only ever touched a `<label>`
tag and its adjacent `<input>`/`<select>`'s opening tag, never a form's
CSRF token or `action`/`method` attributes). No route's CSRF protection
was weakened, removed, or bypassed.

## Real access-control evidence: the 215-entry role-gating matrix

The single most significant piece of security evidence this pass produced
— see `browser-role-validation-report.md` for the full table. Every one
of the 5 real roles' real, browser-observed access matched their real
`seed_data.py` permission grants exactly: no role reached a screen it
shouldn't, no role was incorrectly blocked from a screen its permissions
should grant. This is a real, whole-app, cross-cutting confirmation of
every individual Stage D pass's own permission-gating work, run all at
once for the first time in this phase.

## The IDOR-safe 404-not-403 pattern — re-confirmed live in a real browser

`GET /customers/<id>` for a customer record not owned by/assigned to the
requesting account returned a real `404` for both SALES and SUPPORT
accounts, not a `403` — the established, correct pattern this whole phase
has used throughout (never leak a record's existence to an account that
can't see it). Confirmed via real browser navigation this pass, not just
curl (a genuine, if incremental, confirmation that this pattern holds
under real client-side rendering too, not only at the raw HTTP layer).

## Real bugs found — none new this pass, one indirectly surfaced

No new access-control or injection-shaped vulnerability was found by this
Stage E pass's own work. The one security-adjacent finding — the
`employees.assign_role` vs. `staff.assign_roles` twin-permission-code gap
and the `employees.view_own` unenforced-permission finding — were both
found and disclosed during Stage D.6, not this pass; they remain real,
disclosed, deliberately-unfixed findings (see `employee-permission-ui-contract.md`
for the full reasoning on why fixing either risks locking out real
accounts without a clear, safe migration path).

## Real, disclosed findings from this pass, security-adjacent but not vulnerabilities

- **The ~100-code audit-label gap** (`localization-rtl-report.md`) has a
  minor security-adjacent dimension worth naming explicitly: an
  incomplete audit-log *label* does not mean an incomplete audit-log
  *record* — every one of those ~100 action codes is still correctly,
  immutably recorded by `audit_record(...)` with its real actor/entity/
  timestamp; the gap is purely presentational (a raw code shown instead
  of a translated label), not a logging or traceability gap. Verified by
  reading the real `audit_record()` call sites, not assumed.

## Explicitly out of scope (with the real reason)

- **Dependency/CVE scanning of the newly-added `playwright`/
  `axe-playwright-python` packages** — these are dev-only tooling
  installed into the project venv for this validation pass, never shipped
  to or reachable by the running application; they are not a runtime
  dependency of the Owner app itself and carry no production attack
  surface. Not scanned as a "shipped" dependency for that reason (the
  same reasoning that would apply to `pytest` or any other dev-only
  package already in this project).
- **Penetration testing / fuzzing** — out of scope for a UI-modernization
  hardening pass; this review is a code-and-evidence-based check of the
  UI layer's own access-control correctness, not an offensive security
  engagement.
- **Session/cookie security attributes** (`HttpOnly`, `SameSite`,
  `Secure`) — unchanged by this pass, already covered by prior phases'
  own security work (Phase 1 hardening, per `MEMORY.md`'s own record) —
  re-verified only incidentally via the real `Set-Cookie` headers observed
  during curl-based login flows throughout this phase, not re-audited from
  scratch this pass.

## Ground-rules verification

- No `@require_permission`/`@require_recent_auth` decorator was added,
  removed, or relaxed anywhere in this Stage E pass — every file this
  pass touched is a template, a static asset (`tabs.js`, `components.css`),
  a label dictionary (`i18n_labels.py`), or a translation catalog; zero
  Python route files were modified.
- No new permission code was invented.

## Verification run

Full suite: 1,063/1,063 passing, including the real RBAC/authorization
test files (`test_rbac.py`, `test_phase9_5b_authorization.py`,
`test_security.py`) that exercise the same access-control boundaries this
report's role-gating matrix re-confirmed via a real browser.
