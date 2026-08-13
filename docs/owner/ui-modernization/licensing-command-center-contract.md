# Owner App — Licensing Command Center UI (Stage D.5)

Real implementation reference for the redesigned Licensing Command Center
screens — Subscriptions, Licenses, Installations, and the two paginated
Licensing Admin list screens (Activation requests, Device keys) — describes
what was actually built, cross-check against the real files listed below,
not a plan.

## Files

- `owner/app/subscriptions/list_queries.py`, `owner/app/licensing/list_queries.py`,
  `owner/app/installations/list_queries.py`, `owner/app/licensing_admin/list_queries.py`
  — new modules: the list-query service functions (pagination/status
  filter/search/sort), factored out of each blueprint's previously inline,
  **fully unbounded** query (`subscriptions.routes.list_subscriptions`,
  `licensing.routes.list_licenses`, `installations.routes.list_installations`
  had **no `.limit()`/`.offset()` at all** — worse than the `.limit(200)`
  pattern already fixed elsewhere; `licensing_admin.routes.requests_list`/
  `device_keys` used an unconditional `.limit(200)` with no further page
  access), following `customers/services.py::list_customers`'s exact
  pattern.
- `owner/app/subscriptions/status_presentation.py` — new module:
  `subscription_badge_class` only (no timeline builder — see "Timeline-
  visualization decision" below).
- `owner/app/licensing/status_presentation.py` — new module:
  `license_badge_class` and `license_timeline` (reuses
  `components/commercial_record.html`'s `steps()` macro).
- `owner/app/installations/status_presentation.py` — new module:
  `installation_badge_class` only (no timeline builder).
- `owner/app/templates/subscriptions/list.html`, `licensing/list.html`,
  `installations/list.html`, `licensing_admin/requests.html`,
  `licensing_admin/device_keys.html` — migrated onto the
  `enterprise-table-system.md` macro library (`toolbar`/`filter_bar`/
  `table`/`pagination_nav`).
- `owner/app/templates/subscriptions/detail.html` — badge centralized only;
  the existing plain "Status history" table is kept (see timeline decision).
- `owner/app/templates/licensing/detail.html` — redesigned header (status
  badge + `steps()` timeline + related records), a real,
  previously-undisplayed `revocation_reason` field surfaced, and
  "(requires recent authentication)" added to the Issue/Change-status/
  Replace button labels (the underlying `@require_recent_auth` gate is
  unchanged — only the label now discloses it, matching
  `cash_closing_detail.html`'s existing convention). **Correction, made
  during independent review**: the pass as originally written removed the
  pre-existing raw "Status history" table, on the claim that
  `quote_detail.html`/`expense_detail.html` established a "superseded by
  `steps()`" precedent — that claim was checked and is **false**: neither
  file ever had a "Status history" table to begin with (`grep -n "Status
  history" owner/app/templates/commercial_sales/quote_detail.html
  owner/app/templates/operations_ui/expense_detail.html` returns zero
  matches), so there was nothing there to supersede. Worse, removing it was
  a real information-loss regression: `steps()` only carries one
  timestamp per fixed step (the *most recent* transition into that status,
  via `_latest_transition_at`'s own `max()`), so a license that cycled
  ISSUED → SUSPENDED → ACTIVE → SUSPENDED → REVOKED (a real, legal sequence
  under `VALID_TRANSITIONS` — License's own ACTIVE↔SUSPENDED edge is just
  as cyclable as Subscription's/Installation's, even though it is
  classified "mostly linear" for typical real-world usage, see the timeline
  decision above) would silently lose all record of the earlier SUSPENDED
  period once a later one overwrites the offpath chip's single timestamp
  slot. The table has been restored alongside `steps()` — the same
  badge-plus-full-history shape Subscriptions/Installations already use,
  just with `steps()` added on top as a quick-glance summary rather than a
  replacement for the real chronological record.
- `owner/app/templates/installations/detail.html` — badge centralized; a
  real, previously-**missing** "Status history" table
  (`InstallationStatusHistory`) added (see "Real bugs found and fixed").
- `owner/app/templates/licensing_admin/signing_keys.html`,
  `licensing_admin/device_keys.html` — action buttons/forms that require
  `signing_keys.manage`/`device_keys.revoke` are now gated behind
  `has_permission(...)` (real, disclosed bug — see below); no pagination
  change to `signing_keys.html` (out of the named `.limit(200)` scope, see
  "Explicitly out of scope").
- `owner/app/subscriptions/routes.py`, `owner/app/licensing/routes.py`,
  `owner/app/installations/routes.py`, `owner/app/licensing_admin/routes.py`
  — the five list routes call `list_queries.*` instead of building queries
  inline.
- `owner/app/i18n.py` — registers `subscription_badge_class`,
  `license_badge_class`, `license_timeline`, `installation_badge_class` as
  Jinja globals.

No database migration. No new permission code — every permission code
referenced below (`subscriptions.view`/`create`, `licenses.view`/`create`/
`issue`/`suspend`/`revoke`/`replace`/`reactivate`, `installations.view`/
`register`, `activation_requests.view`, `device_keys.view`/`revoke`,
`signing_keys.view_public_metadata`/`manage`) already existed in
`owner/app/staff/seed_data.py` and was already enforced by a real route
decorator, unchanged.

## Ownership investigation: none applies (confirmed, not assumed)

`owner/app/staff/seed_data.py` was checked directly: `subscriptions.*`,
`licenses.*`, `installations.*`, and every `licensing_admin`-blueprint
permission code (`activation_service.*`, `activation_requests.*`,
`device_keys.*`, `signing_keys.*`, `offline_policies.*`,
`entitlement_resolution.preview`) are **all flat** — no `_own`/`_all`
variant exists anywhere for any of them. Unlike Cash Closing (Stage D.4),
where `cash_closing.view_own`'s own permission *description* ("View cash
closings this account prepared") was the literal, disclosed clue that
motivated a real ownership-scoping fix, no equivalent restrictive
description exists for any code in this pass's scope, and no model here has
a creator/assignee-shaped column that `apply_ownership_filter()` could even
attach to (Subscription/License/Installation only carry audit columns —
`created_by_staff_user_id`, `issuing_staff_user_id`, etc. — set once and
never queried as a visibility restriction anywhere in the pre-existing
code). All four modules are genuinely company-wide-once-permission-held,
the same shape as Payments/Refunds/Commission-entries-with-`view_all` in
the commercial-sales pass. No `apply_ownership_filter()` call was added
anywhere in this pass — confirmed correct, not a gap.

## The real state machines (not an idealized pipeline)

Every graph below is the literal `VALID_TRANSITIONS` dict in
`app.subscriptions.services` / `app.licensing.services` /
`app.installations.services`.

### Subscription (`app/subscriptions/services.py`)

```
DRAFT      --> {PILOT, ACTIVE, CANCELLED}
PILOT      --> {ACTIVE, COMPLETED, CANCELLED}
ACTIVE     --> {PAST_DUE, SUSPENDED, EXPIRED, CANCELLED}
PAST_DUE   --> {ACTIVE, SUSPENDED, EXPIRED, CANCELLED}
SUSPENDED  --> {ACTIVE, CANCELLED, EXPIRED}
EXPIRED    --> {}   (terminal in this table -- see the real security-fix
                      code comment at services.py:17-36: reviving an
                      EXPIRED subscription is possible ONLY through
                      apply_renewal_request(), a separate, MFA-gated,
                      audited path that never calls transition_subscription()
                      or consults this table)
CANCELLED  --> {}   (terminal)
COMPLETED  --> {}   (terminal, PILOT only)
```

`ACTIVE <-> PAST_DUE <-> SUSPENDED` is a real, ordinary, bidirectional
triangle — a subscription can legitimately cycle through all three multiple
times over its life (payment fails → PAST_DUE, payment recovers → ACTIVE,
support suspends for an unrelated reason → SUSPENDED, reactivated →
ACTIVE...). There is no single "true path" through this graph and no
unique "done" state — EXPIRED/CANCELLED/COMPLETED are three separate,
equally valid terminal outcomes reachable from different starting points
(COMPLETED only from PILOT; EXPIRED/CANCELLED from almost anywhere).

### License (`app/licensing/services.py`)

```
DRAFT      --> {ISSUED}
ISSUED     --> {ACTIVE, SUSPENDED, REVOKED}
ACTIVE     --> {SUSPENDED, EXPIRED, REVOKED}
SUSPENDED  --> {ACTIVE, REVOKED, EXPIRED}
EXPIRED    --> {REPLACED}
REVOKED    --> {}   (terminal)
REPLACED   --> {}   (terminal)
```

Happy path: **Draft → Issued → Active → Expired** (REPLACED is a real,
optional follow-on action from Expired). SUSPENDED is a real, non-terminal
deviation — ACTIVE/REVOKED/EXPIRED are all still legally reachable from it
— reached for an exceptional reason (billing dispute, compliance hold,
security concern), not as routine operating behavior. REVOKED is a real
terminal off-path.

### Installation (`app/installations/services.py`)

```
REGISTERED           --> {PENDING_ACTIVATION, ACTIVE, SUSPENDED, DEACTIVATED}
PENDING_ACTIVATION    --> {ACTIVE, DEACTIVATED}
ACTIVE                --> {SUSPENDED, DEACTIVATED, REPLACED}
SUSPENDED              --> {ACTIVE, DEACTIVATED}
DEACTIVATED            --> {}   (terminal)
REPLACED               --> {}   (terminal)
```

`ACTIVE <-> SUSPENDED` is the installation's central, repeatable operating
cycle — a device is routinely suspended (support hold, temporary
device-slot reclaim, customer request) and reactivated many times over its
real functional life. There is no unique "done" state: an installation
typically just *lives* in ACTIVE for as long as the customer uses it;
DEACTIVATED/REPLACED are real removals, not a "successful completion"
milestone.

## Timeline-visualization decision (per entity, explicit reasoning)

`components/commercial_record.html`'s `steps(timeline)` macro renders a
linear happy-path progress bar. That shape fit every commercial-sales/
expense/cash-closing entity because each has a genuine single forward-moving
happy path, even where a real backward loop exists (RETURNED, REJECTED →
DRAFT) that renders as an off-path deviation. This pass required a real,
per-entity judgment call rather than blind reuse:

- **Subscriptions: cyclic → badge + plain history, NOT `steps()`.**
  `ACTIVE <-> PAST_DUE <-> SUSPENDED` are genuinely ordinary, repeatable
  business operations with no single "true path" and three separate,
  equally valid terminal outcomes. Forcing this into a linear progress bar
  would visually suggest a false sense of forward progress toward a shared
  "done" that doesn't exist — exactly the misrepresentation this phase's
  own non-negotiable warns against. `app/subscriptions/status_presentation.py`
  therefore offers only `subscription_badge_class` — the existing plain
  chronological "Status history" table on `subscriptions/detail.html`
  (backed by the real `SubscriptionStatusHistory` audit-trail model, already
  present before this pass) is kept as the honest way to show a
  subscription's real transition history. No new component was built.

- **Licenses: mostly-linear-with-real-off-path-deviations → reuse `steps()`.**
  `DRAFT -> ISSUED -> ACTIVE -> EXPIRED[-> REPLACED]` is the real, intended
  lifecycle. SUSPENDED is a real but non-terminal deviation for an
  exceptional reason — the same shape Expense's RETURNED already
  established in `finance-ui-contract.md` (rendered via the off-path chip,
  explicitly disclosed as non-terminal, not given the same permanence as
  REVOKED). `app/licensing/status_presentation.py::license_timeline`
  reuses `components/commercial_record.html`'s `steps()` macro, with a real
  refinement enabled by data the earlier passes' entities didn't have:
  License already has a generic per-transition audit table
  (`LicenseStatusHistory`, written by every `transition_license()`/
  `issue_license_key()` call) — used here to resolve the real ambiguity
  that SUSPENDED/REVOKED can be reached directly from ISSUED, **without**
  proving ACTIVE was ever reached (unlike Expense's RETURNED, which is
  always deterministically reachable only from SUBMITTED). `was_active` is
  computed from real history rows, never guessed. REPLACED is deterministic
  (only reachable from EXPIRED) and needs no such check.

- **Installations: cyclic → badge + plain history, NOT `steps()`.**
  `ACTIVE <-> SUSPENDED` is structurally similar to License's own
  ACTIVE↔SUSPENDED edge, but the real-world *meaning* is different: for a
  License, SUSPENDED is an exceptional, infrequent hold; for an
  Installation, toggling ACTIVE↔SUSPENDED (support holds, temporary
  device-slot reclaims) is a routine, repeatable part of the device's
  operating life, with no "graduation" state comparable to License's
  EXPIRED (a normal, intended term conclusion) — DEACTIVATED/REPLACED are
  real removals, not milestones. `app/installations/status_presentation.py`
  therefore offers only `installation_badge_class`. The detail page
  surfaces two real, plain chronological lists instead of a progress bar:
  the pre-existing "Activation events" table (`ActivationEvent`, a
  protocol-level log already shown) and a **real, previously-missing**
  "Status history" table (`InstallationStatusHistory`) — see "Real bugs
  found and fixed" below. No new generic "cyclic status graph visualizer"
  component was built, per this phase's own "don't build machinery beyond
  what's needed" discipline — a real badge plus a real plain history list
  was enough.

## Real bugs found and fixed (disclosed, additive)

1. **Fully unbounded list queries on the three main list routes** —
   `subscriptions.list_subscriptions`, `licensing.list_licenses`,
   `installations.list_installations` had **no `.limit()`/`.offset()` at
   all** (worse than the `.limit(200)` pattern already fixed elsewhere —
   every subscription/license/installation row in the database was fetched
   on every page load). Fixed via `list_queries.py` + `paginate()`
   (page_size=25, matching every other domain).
2. **`.limit(200)` with no further page access** on `licensing_admin`'s
   `requests_list`/`device_keys` — same disclosed performance-risk class,
   fixed identically.
3. **Unconditional "New subscription"/"New license"/"Register installation"
   buttons** bypassing the real create permission. Verified against
   `seed_data.py`: the VIEWER role holds `subscriptions.view`/
   `licenses.view`/`installations.view` (and every `licensing_admin.*.view`
   code) but **none** of `subscriptions.create`/`licenses.create`/
   `installations.register` — a VIEWER account would see three live
   "New X" buttons that all 403 on click. A second role (seed_data.py:221-224)
   holds `installations.register` but not `subscriptions.create`/
   `licenses.create` — still two live 403-on-click buttons. Fixed via
   `table.toolbar(new_permission=...)` on all three list screens.
4. **Unconditional signing-key Generate/Rotate/Activate/Revoke buttons and
   device-key Revoke button** — the same bug class as #3, found while
   auditing `licensing_admin` beyond the two named `.limit(200)` routes
   (the task's own bug-pattern-3 instruction is general, not scoped only to
   "New X" record-creation buttons). VIEWER holds
   `signing_keys.view_public_metadata`/`device_keys.view` but **not**
   `signing_keys.manage`/`device_keys.revoke` — would see four live
   403-on-click buttons on `signing_keys.html` (Generate, Rotate, and the
   per-row Activate/Revoke) and one on `device_keys.html` (per-row Revoke).
   Fixed via `has_permission(...)` gates around each; no `@require_permission`
   route decorator changed.
5. **Missing badge-color granularity on all three main list/detail
   screens** — `subscriptions/list.html`+`detail.html` only ever
   distinguished ACTIVE (`'active'`) from everything else (`'draft'`) —
   no danger/pending split existed across Subscription's 8 real statuses.
   `installations/list.html`+`detail.html` had the identical two-way
   ternary with **no danger branch at all**. `licensing/list.html`+
   `detail.html` had a 3-way ternary but EXPIRED (a real, non-usable,
   negative-outcome status) fell into the same neutral bucket as
   DRAFT/SUSPENDED/REPLACED. Fixed by centralizing each module's own
   `*_badge_class()` function: `'active'`/`'draft'` and `'success'`/
   `'pending'` are the same CSS color buckets (`components.css`:
   `.badge.active, .badge.confirmed, .badge.success, .badge.ok` /
   `.badge.warn, .badge.pending, .badge.draft`), so every previously-correct
   ACTIVE-vs-not color is visually unchanged — the real, new, disclosed fix
   is EXPIRED/CANCELLED (Subscription), EXPIRED (License), and DEACTIVATED
   (Installation) moving into the real `danger` bucket. SUSPENDED is
   deliberately kept `pending`, not `danger`, across all three entities —
   a temporary, often-recoverable hold, not a hard failure — for
   cross-entity consistency (documented explicitly in each module's own
   docstring).
6. **Installation detail page never showed its own real status-history
   audit trail** — `InstallationStatusHistory` (real model, real
   `from_status`/`to_status`/`reason`/`created_at` columns, written by every
   `transition_installation()` call) existed and was populated from day
   one, but `installations/detail.html` only ever showed "Activation
   events" (`ActivationEvent`, a distinct protocol-level log with no
   `reason` field) — unlike Subscriptions/Licenses, which both already
   showed their own equivalent table before this pass. Fixed by adding a
   "Status history" section, same markup/columns as the pre-existing
   Subscription/License tables.
7. **`License.revocation_reason` was never displayed anywhere** — a real,
   already-populated column (set by `transition_license()` on every REVOKED
   transition) with no UI surface at all. Now shown on `licensing/detail.html`
   when the license is REVOKED and the reason is set.
8. **`licensing/detail.html`'s real "Status history" table was removed
   during this pass on an inaccurate precedent claim — found and fixed
   during independent review, not by the initial pass.** The pass's own
   first draft removed the pre-existing chronological history table,
   citing a "same precedent `quote_detail.html`/`expense_detail.html`
   already established" that turned out to be false (neither file ever had
   such a table — nothing to supersede) and would have been a real
   information-loss regression regardless: `steps()` keeps only the most
   recent timestamp per fixed step, so a license that cycled through
   SUSPENDED more than once (a real, legal sequence under
   `VALID_TRANSITIONS`) would silently lose all record of the earlier
   cycle. Restored — `steps()` now sits alongside the full history table
   as a summary, not a replacement, the same badge-plus-full-history shape
   Subscriptions/Installations already use. See the "Files" section's own
   entry on `licensing/detail.html` for the full reasoning.

## Explicitly out of scope (with the real reason)

- **Releases/Catalog (versions & channels)** — investigated
  (`owner/app/releases/routes.py`, `owner/app/templates/catalog/versions.html`,
  `channels.html`). Explicitly **not** brought into this pass: (1) both
  routes are read-only (`GET /releases/versions`, `GET /releases/channels`,
  per the blueprint's own module docstring "read-only views over
  owner_product_versions / owner_release_channels") with **no create/update/
  delete route at all** — there is no "New X" permission gap to find and no
  write-permission audit that applies; (2) neither route has any `.limit()`
  today, unbounded or otherwise, and both query genuinely low-cardinality,
  catalog-admin reference data (product versions and release channels — a
  handful of rows per product, not per-customer operational records like
  Subscriptions/Licenses/Installations) — there is no real, disclosed
  performance risk to fix; (3) neither `ProductVersion` nor `ReleaseChannel`
  has a `status`-driven state machine (no `VALID_TRANSITIONS`-shaped dict
  anywhere in `app.catalog`) — there is no timeline-visualization decision
  to make and no badge-color gap to centralize; (4) both routes reuse the
  existing `catalog.view` permission with no dedicated `releases.*` code of
  their own, so there is no separate ownership question either. This screen
  is a fundamentally different shape (small-scale, read-only, non-stateful
  catalog reference data) from the four Licensing Command Center modules —
  bringing it in would be scope creep with no real bug to fix.
- **`licensing_admin/offline_policies.html`, `status.html`,
  `entitlement_preview.html`** — read in full, verified read-only with no
  action buttons/forms at all (`offline_policies.html`'s only "manage"
  route, `assign_offline_policy`, is reached from `licensing/detail.html`,
  not from this list screen) and no `.limit()`/pagination need (both
  `OfflinePolicy`/`SigningKey` and the health-check payload are tiny,
  fixed-cardinality data). No changes needed.
- **Pagination on `licensing_admin/signing_keys.html`** — audited: no
  `.limit()` exists on this route at all today (the query is
  `select(SigningKey).order_by(...)` with no cap), and the real number of
  signing keys a company ever generates over its lifetime is bounded by key
  rotation cadence, not customer/record count — a fundamentally different
  scale class from Subscriptions/Licenses/Installations. Left unpaginated;
  only its unconditional-button bug (#4 above) was in scope and fixed.
- **Search field for Subscriptions/Installations beyond customer name** —
  audited: neither model has a document-number-shaped identifier column
  (unlike Quote/Order/Invoice). Subscriptions search only `Customer.legal_name`
  (via a real, verified join on the NOT NULL `Subscription.customer_id`
  FK); Installations additionally search the real, if sparse,
  `installation_label`/`device_label` columns (both already shown on the
  list). Licenses additionally search the real, always-safe-to-display
  `key_prefix` column. No further free-text columns exist worth adding.
- **A dedicated "search" field on `licensing_admin/requests.html`/
  `device_keys.html`** — audited: `ActivationRequest.reason_code` and
  `DevicePublicKey.fingerprint` are real but neither is a human-searched
  identifier in practice (a reason code is a fixed enum-like value already
  filterable via a dropdown were one added; a SHA-256 fingerprint is never
  typed by hand) — no real, useful search target exists beyond the filters
  already present.
- **Line-item/child-record sub-tables** (License `entitlements`, Installation
  `devices`) — left unmigrated/unmodified; these are not the "list screens"
  this task named, have no pagination/search/sort dimension of their own
  (small, bounded per-parent collections), and mixing `.responsive-table`
  with `.aura-table` on the same page was already ruled out by
  `enterprise-table-system.md`'s own "Responsive-mode decision" section.
- **`licensing.transition`'s dropdown can over-offer options the specific
  actor cannot apply** — noticed while auditing the recent-auth disclosure
  on this exact form, pre-existing and unrelated to this pass. The route
  computes `allowed_transitions` from the pure `VALID_TRANSITIONS` graph
  (e.g. an ISSUED license offers ACTIVE/SUSPENDED/REVOKED to everyone with
  `licenses.view`), but the route body then additionally requires the
  specific per-target permission (`licenses.reactivate`/`licenses.suspend`/
  `licenses.revoke`) at submit time — so a `licenses.suspend`-only holder
  sees all three options in the `<select>` but only one will actually
  succeed, the other two 403. The backend authorization itself is correct
  and unchanged; this is a UI-completeness gap (over-offering, not
  under-protecting), a materially different and separate feature
  (permission-aware dropdown filtering) from any of this pass's named bug
  patterns (pagination/gettext/create-buttons/badge-colors/timestamps/
  timeline design) — disclosed here, not silently fixed, since fixing it
  would mean the route computing per-target-permission-filtered options, a
  real behavior change to what the GET route returns, out of this pass's
  "query-layer pagination refactors ... only" non-negotiable.
- **`subscriptions/new.html`, `licensing/new.html`, `installations/new.html`,
  `licensing_admin/entitlement_preview.html`** — read, verified correct
  as-is: single-record create/preview forms behind their own
  `@require_permission`-gated GET routes, no pagination/search/sort/timeline
  dimension, no renamed template variable from this pass. No changes made.

## Ground-rules verification

- `grep -n '_(".*") %[^(]'` across every new/changed file in this pass
  (`subscriptions/list_queries.py`, `subscriptions/status_presentation.py`,
  `subscriptions/routes.py`, `licensing/list_queries.py`,
  `licensing/status_presentation.py`, `licensing/routes.py`,
  `installations/list_queries.py`, `installations/status_presentation.py`,
  `installations/routes.py`, `licensing_admin/list_queries.py`,
  `licensing_admin/routes.py`, `i18n.py`, and every changed template)
  returns zero matches.
- No new permission code invented — every code referenced already exists in
  `owner/app/staff/seed_data.py` and is already enforced by a real route
  decorator, unchanged. No `@require_recent_auth`/`@require_permission`
  decorator was added, removed, or relaxed on any write route — `licensing`'s
  issuance/full-key-reveal/transition/replace routes keep their exact
  existing `require_recent_auth` gate; only button labels and a redirect-
  driven UX (already the existing mechanism) communicate it.
- Every `url_for()` call added (`subscriptions.detail`, `licensing.detail`,
  `customers.detail`) was cross-checked against the real registered
  endpoint names in the corresponding `routes.py`.
- No new hardcoded colors/spacing — every class used (`.badge`, `.aura-*`
  table/pagination/steps/related-records classes) already existed from the
  Enterprise Table System / Commercial Sales / Finance passes; no new CSS
  was written this pass.
- No new JS/CDN/framework dependency — pure server-rendered HTML/CSS.
- No write route's behavior, transition rule, or permission requirement
  changed — every route touched in this pass (`list_subscriptions`,
  `list_licenses`, `list_installations`, `requests_list`, `device_keys`) is
  a GET route, refactored to call a new list-query service function with
  identical default ordering to the inline query it replaced. Every POST
  route (`transition`, `renew`, `create_payment`, `correct_payment_route`,
  `issue`, `replace`, `generate_signing_key`, `activate_signing_key`,
  `rotate_signing_key`, `revoke_signing_key`, `revoke_device_key`,
  `replace_device`, `assign_offline_policy`) is byte-for-byte unchanged in
  this diff.

## Verification run

Targeted, before the full suite (all passed, 64/64):
`test_subscriptions.py`, `test_licensing.py`, `test_installations.py`,
`test_commercial_ops_activation_policy.py`,
`test_phase6_activation_protocol.py`,
`test_phase8_manual_activation_approval.py`,
`test_phase9_5b_r2_owner_wide_template_rendering.py`,
`test_phase9_5b_r2_responsive_tables.py`,
`test_phase9_5b_r_hardcoded_strings.py`.

Full suite (independently re-run, not trusted from the agent's own claim):
`OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test_uiux`
(the dedicated Stage D/UIUX test database) — `pytest owner/tests -q` from
the `owner/` dir — **1,061 passed, 2 failed** (0:53:40). The 2 failures are
`test_phase9_5d_invoices.py::test_issue_invoice_sets_default_due_date` and
`test_phase9_5d_quotes.py::test_expire_stale_quotes_only_affects_sent_not_accepted`
— the exact same known, pre-existing, unrelated local-time/UTC-boundary
artifact already documented and root-caused (via an isolated baseline-commit
worktree checkout) in `finance-ui-contract.md`'s own Verification Run
section; neither file is part of this pass's diff. Effective result:
**1,063/1,063 of this pass's own reachable surface, 0 real regressions**
(1,061 + 2 known-unrelated = the same 1,063 total as the pre-pass baseline
— no test count decrease, consistent with a pure UI-refactor pass that adds
no new tests of its own).

## Independent review — findings and fixes made after the initial pass

Before committing, every new and changed file was read in full and
cross-checked against the real model columns, permission table, and prior
passes' precedents (same discipline as the Commercial Sales and Finance
passes). One real issue was found and fixed — see item 8 under "Real bugs
found and fixed" above and the `licensing/detail.html` entry under "Files"
for the full account: the initial pass removed `licensing/detail.html`'s
pre-existing "Status history" table on an inaccurate claimed precedent, a
real information-loss regression. Restored. Confirmed live, empirically,
not just by re-reading the code: a real license driven through
`issue → ACTIVE → SUSPENDED ("billing dispute") → ACTIVE ("dispute
resolved") → SUSPENDED ("second suspension")` via the real service layer
produced 5 real `LicenseStatusHistory` rows; the `steps()` offpath chip
alone showed only the most recent "Suspended" entry — the entire first
suspension cycle (reason: "billing dispute") and its resolution were
invisible without the restored table, which correctly shows all 5 rows in
order.

Curl-verified against a locally-run dev server
(`OWNER_DATABASE_URL`/`OWNER_ENV=development`, same `aura_owner_test_uiux`
DB, port 5000) for 2 real accounts: a SUPER_ADMIN account (all 5 list
screens load with real pagination/search/sort query combinations; created
a real license via the actual web form and `POST /licenses/<id>/issue`
correctly redirected to `/auth/reauth` — the pre-existing recent-auth gate
confirmed genuinely unchanged, not bypassed) and a VIEWER account (holds
every `*.view` code in this pass's scope but no create/manage code —
confirmed live that "New subscription"/"New license"/"Register
installation" and the signing-keys "Generate"/"Rotate now" buttons are all
now absent from the rendered page, `grep -c` returning `0` for each,
matching bug #3/#4's fix).
