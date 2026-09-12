# ActionAura / Aura FullSuits — Roadmap

Tracks production requirements as they're handed down, and whether they change
work already in flight. Uses this repo's real Phase/Milestone naming (Phase
8/9/9.5 for Owner Control Center, Milestone 1-11+ for the unified KMP mobile
app) — no invented milestone numbers.

## 2026-08-08 — Partner's finalized production requirements

Source: business partner, delivered as a requirements list. Reviewed against
the current schema/architecture the same day.

### 1. Licensing & Auth
- Strict multi-tenant data isolation on the eventual cloud server (no cross-bleed).
- Licenses span multiple devices; each device requires its own distinct login credentials.
- One device per license can be designated "Admin Device" — target for daily reports/analytics.

**Status:** Not started. Device-level identity already exists for license
binding (`commercial_runtime/licensing_contracts/device_identity.py`), but
per-device *login credentials* and an *admin-device flag* are new concepts.
Scoped to the licensing/identity layer, not the local retail catalog schema —
does not block current schema work.

### 2. Supplier & Inventory Automation
- Products must be strictly linked to suppliers.
- Smart PO splitting: a large order auto-splits by supplier, routed to the correct contacts.

**Status:** `supplier_id` added to `products` — schema, migration, and API
(`create_product`/`update_product`/sync-outbox payloads) all wired and
verified on both master (schema v3) and the demo sync branch (schema v5,
`TEXT` FK matching its UUID'd `suppliers.id`). Smart PO-splitting logic
itself (business rule on top of this link) — not started.

## 2026-08-08 — Owner's requirements list (second source, same day)

Source: project owner, delivered separately from the partner's list above.
Cross-checked item by item: **all 8 items map 1:1 to what's already logged
above**, just regrouped into 5 categories instead of 4 (Architecture &
Licensing / Platforms & Sync / Inventory & Purchasing / Checkout &
Communications / External Integrations). No new schema or architecture
implication beyond what's already captured — treating this as confirmation
from a second stakeholder, not a separate work item, except:

- **"Product Types" — ambiguous, not in the partner's list, not logged as a
  requirement yet.** Could mean the existing `categories` table
  (`Ice Cream`, `Crepes`, etc. — already implemented) restated, or a
  genuinely distinct concept (e.g. simple/variant/bundle/service product
  *behavior* type, separate from merchandising category). Needs
  clarification before scoping — flagged, not assumed.
- "Sync must be absolutely flawless and error-free" (owner's phrasing) is
  stronger than the partner's "bulletproof... validates taking time on
  conflict resolution." Worth setting expectations early: distributed sync
  can be made *correct and recoverable* (every conflict detected, logged,
  and resolvable — which is what the outbox design already targets), not
  literally zero-error in an absolute sense. Recommend reframing the
  acceptance bar as "no silent data loss, no unresolvable conflict" rather
  than "flawless," before this becomes a stated Definition of Done.

## Future architecture — complexity ranking

Ranked by *design* complexity/risk, not effort, for when these move from
roadmap to an actual Phase:

1. **Multi-tenant cloud isolation** — the biggest one. Today's model is
   local-first: one SQLite file per install, no server in the loop at all
   for product data. "Strict isolation on the eventual cloud server" isn't
   an incremental feature on top of this — it's a different deployment
   model (database-per-tenant vs. shared-DB-with-row-level-security,
   tenant-aware connection pooling, per-tenant backup/restore, migrating
   many tenant databases in lockstep). Highest architectural blast radius
   of anything on this list.
2. **Sync correctness** — hardest *algorithmically*. Conflict resolution
   across N devices, clock skew, partial writes, offline queues spanning
   days, schema-version skew between devices on different app builds. The
   outbox/cursor foundation already in progress is the right shape, but
   "done" here is a long tail of edge cases, not a single deliverable.
3. **Per-device login + Admin Device** — moderate. Extends existing
   device-bound licensing (already real) into actual per-device
   authentication — touches session/auth architecture shared by *both*
   Retail and Clinic (`commercial_runtime/identity`), so a design mistake
   here is expensive to unwind later. Bounded scope, but shared-code risk.
4. **iOS distribution** — hard operationally, not architecturally. Code
   side is already correctly scaffolded (KMP `iosMain` targets declared).
   The real complexity is organizational: needs a macOS host in the build
   pipeline, an Apple Developer account, code-signing, App Store review.
5. **WhatsApp/SMS templates + delivery choice** — moderate. Code pattern to
   copy already exists (einvoicing outbox), but real complexity is
   external: WhatsApp Business API approval, phone number verification,
   per-message cost, rate limits — vendor/ops risk more than code risk.
6. **Smart PO splitting** — bounded business logic on top of the
   `supplier_id` link already shipped. Lowest architectural risk on this list.

### 3. Checkout & Communications
- Admin UI to customize WhatsApp template messages.
- At checkout: three invoice delivery options — print, SMS, WhatsApp.

**Status:** Not started. No outbound notification channel exists in the
codebase at all today (checked: zero SMS/WhatsApp/email/push infrastructure).
Recommended approach: mirror the existing e-invoicing outbox pattern
(`commercial_runtime/einvoicing/`) — proven, already in production — instead
of a one-off integration.

### 4. Integrations & Platforms
- Windows, Android, iOS builds.
- Official Jordanian e-invoicing integration.
- Sync must be bulletproof — validates taking time on conflict resolution.

**Status:** Largely already the current direction.
- Jordan e-invoicing (JoFotara/ISTD): **done**, Phase 1, tests passing.
- Windows desktop + Android: **done** (legacy `android/aura-retail`, release-candidate).
- iOS: source correctly structured in the unified KMP app
  (`mobile/aura-retail-unified`), targets declared, but not compilable
  without a macOS host — environment gap, not a code gap.
- Sync: outbox + cursor design (`commercial_runtime/sync/`, `sync_outbox`/`sync_cursor`
  tables) already reflects a conflict-aware architecture, in progress on a
  feature branch.

## Open items carried from earlier review (not yet actioned)

- No RBAC / staff permission matrix beyond admin/not-admin for retail actions
  (2026-08-12 correction: an audit trail already exists and is already
  populated — `_audit()` in `retail_api.py`, 26 call sites including refunds
  and voids — it just has no viewer UI. The real gap is a permission matrix
  and a manager-approval/override flow, not the audit log itself).
- No shift management (cash-drawer float, X/Z reports).
- **`sync_outbox` has no retention policy on installs with no drain path.**
  Both products' outbox writes are UNGATED: retail's `_queue_sync_event`
  (`retail_api.py:568`) and the registry-stream emissions added in wave B2
  stage 2b both write regardless of whether a relay is configured. A Retail
  install with sync switched off, and **every Clinic install** (Clinic
  registers `auth_bp`/`onboarding_bp` and so reaches the registry emission
  sites, but never imports `commercial_runtime.sync` and so never drains
  them), accumulate rows nothing will ever delete. The rate is small by
  construction — every high-frequency path (login, failed-login counting,
  lockout, hash-upgrade) is deliberately excluded, so only deliberate account
  management leaves a row — and the rows are genuine history if that install
  ever does sync, which is why neither product gates today. But it does breach
  the "invisible unless opted in" principle in CLAUDE.md, it affects BOTH
  products, and it needs an owner. See
  `docs/launch-readiness/phase5-waveb2-user-sync.md` §Known cost to Clinic.
- Promotions/discounts engine not started (2026-08-12 correction: basic
  loyalty — `loyalty_points`/`total_spent` accrual at 1pt/$10 — is already
  live on every sale, `retail_api.py:1259`. Gap is a rules engine and point
  *redemption* — points can currently only be earned, never spent).
- No inter-branch stock transfer workflow.
- Stock-check bug (hardcoded `branch_id: 1` in POS checkout payload) — **fixed**,
  `products/retail/frontend/subsystem-retail.js`.

## 2026-08-12 — Schema-version ledger + branch coordination (demo postponed to Sunday 2026-08-16)

Multiple agents worked this branch concurrently tonight. `schema.py` /
`RETAIL_SCHEMA_VERSION` is a single-writer resource — migrations must merge
into `_migrate_retail_schema` in ascending version order, one open at a time,
never claimed in parallel. Ledger:

- **v8 — reorder-automation** (`feat/reorder-automation-foundation`). Claimed
  first, real migration code already written (`products.reorder_method`,
  `reorder_requests` table). Merges first.
- **v9 — hold/park-sale** (`feat/pos-hold-resume-sale`). Rebases onto v8 once
  merged; do not claim v8.
- **v10 — reserved for shift/cash-drawer (X/Z reports)**, if/when started.
  Touches the same checkout path as hold/park — must be built serially
  after it, never in parallel against the same base.

**Investigated same day, corrected after being wrong about it twice:**
`feat/pos-hold-resume-sale` and `feat/sales-invoices-screen` both branched
from `93a3320`, an early point on `feat/retail-mobile-build-baseline`'s own
history (`93a3320` **is a genuine ancestor** of the current tip — same
linear lineage, not a diverged side-branch; an earlier claim in this same
review round that it was "pre-merge lineage"/a different branch was wrong
and got independently caught and corrected by the agent working from it).
What IS real: ~1583 files of drift between `93a3320` and the tip
(`63418e4` as of this entry), almost entirely unrelated feature work
(PO-split/routing, sync/device-registry, reorder-automation) that neither
branch touches or needs.

On the UUID question specifically: `products.id`/`customers.id`/
`suppliers.id`/`categories.id` do NOT stay INTEGER on `93a3320` — the
static `CREATE TABLE IF NOT EXISTS` text shows the pre-migration shape,
but `_migrate_retail_schema` (schema.py:756) runs
`_migrate_products_to_uuid`/`_migrate_customers_to_uuid`/
`_migrate_suppliers_to_uuid` unconditionally on any database behind the
version marker, remapping `sale_items.product_id`/`sales.customer_id`/
`payments.party_id` to real UUID strings in the same pass. `sales.id`/
`sale_items.id` themselves are NOT converted (out of that migration's
scope) and stay plain autoincrement ints on every branch. Anyone reasoning
about "is branch X's schema stale" should check what the migration chain
actually does at runtime, not just the literal `CREATE TABLE` text.

Net: both branches stayed on `93a3320` rather than rebasing, by deliberate
decision after direct verification — for `sales-invoices-screen`,
confirmed its two touched routes are byte-for-byte unchanged at the tip;
for `pos-hold-resume-sale`, its `held_sales.customer_id INTEGER` column
would need to become `TEXT` if/when it's actually integrated onto the
real UUID'd schema (documented as a known follow-up, not done yet).
Neither issue blocks either branch standing alone; both need this
reconciled at actual merge time, not before.

**Final correction on the UUID point:** the migration functions
(`_migrate_products_to_uuid` etc.) genuinely do NOT exist at `93a3320` —
confirmed by grep on that checkout: only `_apply_retail_alters` (the old,
much smaller v2→v3 migrate_fn) is present. They were added somewhere in
the ~1583 files of drift between `93a3320` and the tip — schema.py itself
is one of the changed files. So on `93a3320`, `products.id`/`customers.id`/
`sales.customer_id`/`sale_items.product_id` really are plain integers,
full stop, no migration to reconcile with. The earlier note above (about
the migration existing and applying "on any database behind the version
marker") is real and correct **only for `feat/retail-mobile-build-baseline`
itself** — it doesn't retroactively apply to code that predates the
migration being written. Both can be true at once; check which branch's
actual checked-out file you're reasoning about, not just its ancestry.

**Separately flagged, not fixed, real latent bug:** `subsystem-retail.js`'s
`_saveReturn` does `product_id:+cb.dataset.pid` (numeric coercion) —
pre-existing code, unrelated to tonight's work. Harmless today since
`93a3320`-based branches have integer product ids anyway, but this would
silently break Returns on any branch that actually carries the UUID
migration. Whoever eventually reconciles these branches should grep for
this pattern (`+`-coercion on an id field) more broadly before merging
onto the UUID'd schema.

## 2026-08-12 — Three branches merged onto feat/retail-mobile-build-baseline (RETAIL_SCHEMA_VERSION now 8)

Executed the merge plan from the ledger above, in the specified order, with
a full individual-file retail regression run + a fresh-temp-dir boot/schema
smoke test after each step. All three landed clean; none required a
judgment call risky enough to stop for. `feat/pos-hold-resume-sale` (v9,
rebases onto v8) was deliberately **not** touched — still pending its
author's explicit sign-off, as flagged.

1. **`feat/reorder-automation-foundation`** → merge commit `de5c0d5`.
   Schema-version writer: bumps `RETAIL_SCHEMA_VERSION` 7→8
   (`products.reorder_method`, `reorder_requests` table), adds the
   post-sale reorder hook and the Admin Center page. One real conflict,
   in `app-shell.js`'s `init()`: both this branch and the (already-merged,
   untouchable) AI Assistant work insert setup code at the same point,
   right after `KPIDragManager.init()` — this branch's `isAdminDevice`
   fail-closed default vs. HEAD's `/api/auth/active-modules` fetch for the
   AI Assistant button. Independent, non-competing additions; kept both
   verbatim (isAdminDevice default first, then the AI fetch, both still
   ahead of the auth gate). AI Assistant code (`/ai/chat`, `sub-ai.js`,
   `AI_TOKEN` handling) was never actually at risk — this branch predates
   that feature and has no history of `sub-ai.js` at all, so git's 3-way
   merge treated it as a clean one-sided addition, not a conflict.
2. **`fix/i18n-language-switch-freeze`** → merge commit `335ea1a`. Exactly
   as advertised: `i18n.js` only, +27/-3 lines, zero conflicts, merged
   cleanly onto v8.
3. **`feat/sales-invoices-screen`** → merge commit `bcabf60`. Adds the
   Sales History screen and extends `GET /sales/recent` with optional
   `q`/`date_from`/`date_to` filters (backward-compatible, zero schema
   change). Branched from `93a3320` as documented; verified the real 3-way
   merge (base `93a3320`, not the ~1583-file drift diff against the tip)
   resolved automatically with zero conflicts, touching only
   `retail_api.py` and `subsystem-retail.js` — it never actually touches
   `app-shell.js`, so the plausible nav-entry collision with
   reorder-automation's Admin Center entry the plan flagged as a risk
   didn't materialize.

**Regression status:** all 30 `products/retail/tests/*.py` files, run
individually (per this codebase's known cross-file test-pollution issue),
355 tests total, 0 failures / 0 errors, after *each* of the three merges.
Boot + schema-migration smoke test (fresh `tempfile.mkdtemp()` data dir,
`OWNER_LICENSING_BASE_URL`/`SYNC_RELAY_BASE_URL` unset, deleted after each
run — no real `AURA_APP_DATA`/`dist*`/demo database touched at any point)
passed after each merge: `PRAGMA user_version` = 8, `PRAGMA integrity_check`
= `ok`.

**Still pending, unchanged:** `feat/pos-hold-resume-sale` (v9) — flagged by
its own author for explicit human sign-off before merging this close to the
demo; that stands, not merged here.

## 2026-08-13 — Four more branches merged onto feat/retail-mobile-build-baseline (RETAIL_SCHEMA_VERSION now 9)

Continuation of the batch above. All four were pre-verified in isolated
worktrees, based on the same `ab01d4f` tip (`feat/reorder-automation-foundation`)
already merged into this branch, so each was a comparatively clean 3-way
merge. Executed in the specified order — three additive/no-schema-change
branches first, the schema-version writer last — with a full individual-file
retail regression run + a fresh-temp-dir boot/schema smoke test after each
step. All four landed clean; zero conflicts required a judgment call across
any of the four (git's 3-way merge auto-resolved everything, including the
two overlapping touches on `app-shell.js` and the three overlapping touches
on `retail_api.py`, since each branch's edits landed in different regions of
those files). `feat/pos-hold-resume-sale` and the AI Assistant RAG/model-
upgrade work (`feat/ai-assistant-rag-and-model-upgrade`, currently active on
its own branch) were both deliberately left untouched, as instructed.

1. **`feat/reports-branch-comparison`** → merge commit `ac91664`. Adds
   `GET /reports/by-branch` and an optional `branch_id` filter on existing
   reports routes, plus a branch-comparison chart. No schema change; default
   (no `branch_id`) behavior previously verified byte-identical via MD5.
   Touched `retail_api.py` and `subsystem-retail.js` only — auto-merged
   cleanly against the drift already on this branch from
   `feat/sales-invoices-screen`'s earlier `retail_api.py` changes (different
   regions of the file).
2. **`feat/audit-log-viewer`** → merge commit `bcbcf18`. Read-only audit log
   viewer reading the existing `audit_log` table (populated by `_audit()`
   since the first version of `retail_api.py`, 26 call sites, never read
   back until now). Real server-side admin-device gate on the route itself
   (`_is_admin_device()`, fail-closed on any resolution error — not just
   nav-hiding), verified by reading the diff directly. One nav-entry addition
   to `app-shell.js` right after Admin Center's entry — additive, no
   conflict. No schema change.
3. **`feat/sync-freshness-indicator`** → merge commit `c819191`. Replaces
   the failure-only sync banner with a calm, persistent "Synced Ns ago · N
   pending" state sharing the same `#aura-sync-banner` element with the
   original full-width alarm state (unchanged, still triggers at
   `SYNC_DEGRADED_THRESHOLD`+ consecutive failures). Touches
   `commercial_runtime/sync/sync_service.py` (adds `pending_count` to
   `get_health()`) and `app-shell.js`'s sync-banner functions (~line 795+) —
   a completely different region from audit-log-viewer's nav-entry addition
   (~line 258), so no conflict despite both branches touching the same file.
   No schema change.
4. **`feat/email-outbox-foundation`** → merge commit `6f8c776`. Schema-
   version writer: bumps `RETAIL_SCHEMA_VERSION` 8→9. Adds
   `commercial_runtime/notifications/` (SMTP outbox, worker, tokens,
   settings — mirrors the e-invoicing outbox pattern, per this repo's
   established convention for external-facing async features), plus
   low-stock/report/verification email triggers wired into `retail_api.py`,
   `app.py`'s `init_app()`, and `core/retail/reorder_hook.py`. Also extends
   two existing test files (`retail_category_delete_fk_sync_test.py`,
   `retail_reorder_migration_test.py`) — verified before merging that both
   were byte-identical between `ab01d4f` and this branch's merge base, so
   the patches applied cleanly with no interleaving-edit risk. `retail_api.py`
   auto-merged cleanly against the accumulated changes from merges 1–2
   above (three branches touching the same file, three different regions).

**Schema-version ledger correction:** the 2026-08-12 ledger above reserved
v9 for `feat/pos-hold-resume-sale` ("rebases onto v8 once merged; do not
claim v8"). `feat/email-outbox-foundation` was *not* on that ledger and
also branched from the v8 tip (`ab01d4f`) claiming v9 independently — a
genuine collision on the single-writer resource the ledger exists to
prevent, caught here rather than at merge time by chance (the two branches
never touched schema.py's version comment in a way git would conflict on;
`_migrate_retail_schema` gates on live schema shape, not the version
integer, so this would NOT have failed loudly if merged in the wrong order
either). Net effect: `RETAIL_SCHEMA_VERSION` is now genuinely 9 via
email-outbox-foundation. `feat/pos-hold-resume-sale`, when it merges, must
be rebased to claim **v10**, not v9 — its own migration code (adding
`held_sales` and friends) has not been reconciled with this. The previous
ledger's "v10 — reserved for shift/cash-drawer" is bumped to **v11**
accordingly. Both `feat/pos-hold-resume-sale` and `feat/shift-cash-drawer`
are active on their own branches right now — whoever merges either next
should re-read this correction first, not just the original ledger.

**Regression status:** all `products/retail/tests/*.py` files, run
individually (per this codebase's known cross-file test-pollution issue),
after *each* of the four merges:
- After merge 1 (reports-branch-comparison): 31 files (30 + new
  `retail_report_branch_filter_test.py`), 363 tests, 0 failures / 0 errors.
- After merge 2 (audit-log-viewer): 32 files (+ `retail_audit_log_test.py`),
  0 failures / 0 errors.
- After merge 3 (sync-freshness-indicator): 32 files, 0 failures / 0
  errors. Also ran `commercial_runtime/sync/tests/test_sync_service.py`
  directly (not part of the retail suite) — 38 passed.
- After merge 4 (email-outbox-foundation, final): 33 files (+
  `retail_email_outbox_test.py`), 0 failures / 0 errors. Also ran all of
  `commercial_runtime/notifications/tests/*.py` directly — 47 passed
  (`test_outbox_repository` 13, `test_settings` 11, `test_smtp_client` 9,
  `test_tokens` 5, `test_worker` 9).

Boot + schema-migration smoke test (fresh `tempfile.mkdtemp()` data dir,
`AURA_STANDALONE=1`, no `OWNER_LICENSING_BASE_URL`/`SYNC_RELAY_BASE_URL`
set, deleted after each run — no real `AURA_APP_DATA`/`dist*`/demo database
touched at any point) passed after each of the four merges: `PRAGMA
user_version` = 8 (merges 1–3), then 9 (merge 4); `PRAGMA integrity_check`
= `ok` throughout. After merge 4 specifically, also verified on the fresh
install that both the v8 shape (`products.reorder_method`,
`reorder_requests`) and the new v9 tables
(`email_settings`/`email_outbox`/`email_verification_tokens`) are present.

**Untouched, as instructed:** `feat/pos-hold-resume-sale` (still not an
ancestor of HEAD — confirmed via `git merge-base --is-ancestor`) and the AI
Assistant RAG/model-upgrade work (`feat/ai-assistant-rag-and-model-upgrade`,
confirmed still exactly at `25279c8`, its merge-base with HEAD — i.e.
untouched, not diverged). `owner/`, `docs/owner/`, `android/`, `dist/`,
`dist2/` all confirmed unchanged across all four merge commits. Nothing
pushed to any remote — branch remains local-only, `ahead 20` of
`origin/feat/retail-mobile-build-baseline`.

**Tooling note:** `rtk`'s compact `git log` filter was observed dropping
merge commits from `--oneline` output entirely during this session (showing
a branch tip commit as HEAD instead of the actual merge commit on top of
it) — misleading enough that it looked like a merge hadn't happened.
`git rev-parse`, `git reflog`, and `git show --stat` were reliable and used
for all verification in this entry instead. Worth a closer look before
trusting `rtk git log` output around merge commits again.

---

## 2026-08-21 — schema-version reservation: launch-readiness Phases 1-7

Reserved **before** dispatching any agent, not after, because this ledger's own
2026-08-12 correction records what happens otherwise: two branches both claimed
v9, and it did **not** fail loudly at merge time. `_migrate_retail_schema` gates
on live schema shape rather than the version integer, so a double-claim produces
a silently wrong `user_version` rather than a conflict git would surface.

Programme: `docs/launch-readiness/multi-device-design.md`, branch
`feat/launch-readiness`.

### Claimed

| Version | Database | Phase | What |
|---|---|---|---|
| registry **v3** | `registry.db` | 1 — **DONE**, commit `28150c8` | `users.uid`/`pin_hash`/`row_version`/`updated_at_utc`/`deleted_at_utc`; role widened to {admin, manager, cashier}; capability rows seeded |
| registry **v4** | `registry.db` | 5 prerequisite — **CLAIMED** | identity-side `company_id` rebind from `md5(admin_email)` to the Owner-issued `license_public_id`, across every `company_id`-bearing table **discovered at runtime, never hardcoded**. Retail v14 is deliberately inert until this lands — it converges, it never leads. See `docs/launch-readiness/phase5-prerequisites.md` §1 |
| registry **v5** | `registry.db` | 5 wave B2 stage 1 — **DONE** | registry-side `sync_outbox`/`sync_cursor`, so a `user` row and its sync event commit in ONE transaction in ONE file. `ATTACH`-ing registry.db to the retail connection was considered and **ruled out**: both databases run `PRAGMA journal_mode=WAL`, and SQLite gives no cross-database atomic commit once any attached database is in WAL — it would look correct and lose the event exactly when the process died between the two commits. See `docs/launch-readiness/phase5-waveb2-user-sync.md` §Decision 1. Schema only — no `user` entity type, no user sync event, no write-site change; `SyncService`'s cross-stream entity allowlist guard (`handled_entity_types`) also landed in this stage, ahead of any second stream actually running. Stage 2 (write sites, `user`/`user_permissions` entity types) is separate and not started |
| registry **v6** | `registry.db` | 5 wave B2 stage 2a — **DONE** | registry-side `sync_apply_quarantine`, mirroring retail.db's — `_apply_event`'s new `user` branch has to catch `users`' two non-wire UNIQUE constraints (`email`, `UNIQUE(company_id, employee_id)`) BY NAME and park the loser via `SyncService._quarantine_apply_event`, which writes unconditionally to whatever connection it is called with (registry.db, for the registry-configured instance) — exactly wave A's defect #1, reproduced by construction the moment a second unique constraint exists on a synced table's own wire-identity column. Not appended to v5's own migration function: `ensure_schema_version` never re-enters a database already at its target version, so a silent edit to v5's function would never reach a database that had already upgraded. See `docs/launch-readiness/phase5-waveb2-user-sync.md` §Decision 4 and `registry_quarantine_schema.py`. Apply-side only (`REGISTRY_SYNC_ENTITY_TYPES`, the `user` branch, and the registry-configured `SyncService` instance in `products/retail/backend/app.py` all land in this stage) — still no write site emits a `user` sync event; that is stage 2b, tracked separately |
| retail **v13** | `retail.db` | 2 | `uid` + unique index on `branches`/`sales`/`sale_items`/`returns`/`return_items`/`inventory_movements`/`payments`; `actor_user_uid`/`terminal_id`/`created_at_utc` on the transactional tables; `row_version`/`updated_at_utc`/`deleted_at_utc` on the four catalogue tables and `reorder_requests` |
| retail **v14** | `retail.db` | 2 | rebind `company_id` from `md5(admin_email)` to the Owner-issued value in the licence assertion, across all ~13 scoped tables in one transaction |
| retail **v15** | `retail.db` | 3 | opening-count movement for every balance row with no ledger history; refuses to advance `user_version` if drift ≠ 0 |
| retail **v16** | `retail.db` | 4 | terminal-bound drawer; `UNIQUE(company_id, terminal_id) WHERE status='open'`; `ended_at`/`ended_by` for the ENDED/CLOSED split |
| retail **v17** | `retail.db` | 6 stage 6a-i — **DONE** | create `sync_conflicts` (empty until stage 6a-ii writes it); drop the dead `quantity_reserved` column from `inventory_balances`. **`stock_exceptions` is deliberately NOT created here** even though it was reserved alongside: nothing in Phase 6's scope writes it — it belongs to the oversell exception queue, which no stage of this phase implements — and a shipped table with no writer actively misleads the next reader into assuming the feature exists. It stays reserved for the phase that implements the queue. Dropping `quantity_reserved` is the lowest-risk destructive migration available here: confirmed dead by three audits and by grep, and `inventory_balances` is fully derivable from `inventory_movements` (Phase 3), so even total loss is recoverable via `repair_drift`. See `docs/launch-readiness/phase6-catalogue-correctness.md` |

`RETAIL_SCHEMA_VERSION` is **16** at `schema.py:344` (Phases 2–4 landed v13–v16).
`REGISTRY_SCHEMA_VERSION` is **6** at `registry_db.py:65` — v4 landed with the
Phase 5 prerequisites (commit `681b0fa`); v5 (wave B2 stage 1 — schema and
the sync allowlist guard only) landed with this ledger entry's update; v6
(wave B2 stage 2a — the registry-side quarantine table, the `user` apply
branch, and the registry-configured `SyncService` instance) landed with this
entry's own update.

Do not read those two numbers as live state — this line has already been stale
once. Read the constants.

### Rules for anyone else touching schema.py before Phase 7 lands

1. **Do not claim 13 through 17 on any other branch.** Take v18 and add a row
   here first. The cost of asking is a minute; the cost of colliding is a
   migration that runs against the wrong shape on a customer's live database.
2. v13 through v17 are applied **strictly in order**, each as one idempotent
   function appended last in `_migrate_retail_schema` — the convention that file
   already documents at `schema.py:861-911`.
3. **No table rebuild, no `id_map`, no `DROP`/`RENAME`** on any of them.
   `_migrate_products_to_uuid`'s risk profile (`schema.py:528-660`) is
   deliberately not repeated on live sales data. Additive only.
4. v14 **must** precede any sync widening. Rebinding `company_id` after rows have
   already been pushed means they arrive scoped to a tenant the receiver does not
   recognise.
5. v15 is a **gate, not just a migration**: it runs `compute_drift` before and
   after and refuses to advance the version marker if drift is non-zero. Do not
   "fix" a failing v15 by relaxing that check — a failure there means the
   ledger genuinely cannot reproduce the cached balances, which is the whole
   complaint the phase exists to answer.

### Two things Phase 5 must not open the firehose without

Recorded here because they are easy to defer and expensive to retrofit:

- **Owner-side pruning of `owner_sync_events`** below the slowest device cursor.
  Volume goes from tens to roughly 5,000 events/day/device of full-row JSONB into
  a table with no TTL and no `DELETE` anywhere in `owner/app/sync/routes.py`.
- **A quarantine path for poison events.** Apply is all-or-nothing today
  (`routes.py:338-340` rolls the whole batch back on `INVALID_EVENT`), so one bad
  row stops a shop syncing permanently. Quarantine + skip + surface must land
  before Phase 5, and every quarantined event stays visible and replayable —
  never silently dropped.

Unchanged and not to be touched by any of this:
`pg_advisory_xact_lock(hashtext(license_id))` in `owner/app/sync/routes.py`.

#### The Alembic head is a single-writer resource too — claimed here

Both Owner prerequisites above need an Alembic migration, and the revision chain
has exactly ONE head. Two agents or two branches each running
`alembic revision` against the same head produce **two heads and a broken
chain** — the same class of collision this document's schema-version table
exists to prevent, in a different notation.

Verified 2026-08-24 by parsing all 30 files under `owner/migrations/versions/`
and walking `down_revision` to root: chain length 30, one root
(`62e4adb0a7b9`), **one head (`d8dfeb46d1d6`)**, zero branch points.

**CLAIMED: the pruning and quarantine migrations both chain off `d8dfeb46d1d6`,
written by a single writer, in that order.** Anyone else adding an Owner
migration before Phase 5 should re-derive the head first — do not trust this
line, it records a moment, not live state — and add a row here.

## 2026-08-28 — Gaps surfaced by Phase 6b (tombstones), unscheduled

Found while building `deleted_at_utc` into a real tombstone across the
catalogue. None is a regression; each is a pre-existing gap that only became
visible — and worth naming — once deletion stopped being an overloaded `status`
flag. Recorded rather than fixed, because each is a product decision, not a
correctness one.

- **No restore UI exists anywhere in the app, for any entity.** Undelete for
  products and suppliers is reachable only by a raw `PATCH` with
  `status: 'active'`; customers gained the equivalent in stage 6b-iii-b. No
  screen in `products/retail/frontend/` offers any of them — grepped, not
  assumed. So an operator who deletes something by mistake has no in-app way
  back, and the sync machinery that carries a restore between devices is
  currently exercised only by tests and by re-import. Whoever schedules this
  should decide whether restore belongs on the list screens, behind a "show
  deleted" toggle, or nowhere.

- **A customer with no email on file can never be resurrected by re-import.**
  `_handle_retail_customers` dedupes on email, and only when non-empty, so a
  customer without one is always inserted as a NEW row rather than matched.
  Deliberately not "fixed" by widening the dedupe key: matching on name would
  silently merge distinct same-named customers, which is a far worse failure
  than a missing undelete. Pinned by
  `test_reimporting_a_customer_with_no_email_does_not_match_an_existing_one`
  so it stays a known, deliberate gap rather than a surprise.

- **`create_purchase_order` still has no cross-tenant validation on
  `supplier_id`.** Stage 6b-iii-b added the product existence + tombstone +
  company scoping check that route had never had at all, which closes the
  product half of the pre-existing cross-tenant PO gap that route's own comment
  already acknowledges. The supplier half is untouched and remains open.

- **Legacy rows deleted before tombstones keep `status='inactive'` and a NULL
  `deleted_at_utc`, permanently.** This is the deliberate backfill posture from
  `docs/launch-readiness/phase6b-decisions.md` — we do not fabricate a deletion
  time nobody recorded. The consequence is that every catalogue read filters on
  BOTH conditions and must keep doing so; `test_a_legacy_row_deleted_before_
  tombstones_is_still_hidden` fails if anyone "simplifies" the pair. If a future
  cleanup wants to retire the `status` half, it needs a real backfill decision
  first, not a refactor.

- **The new write gates check the tombstone only; the read paths check both
  conditions.** Every catalogue READ filters `status='active' AND
  deleted_at_utc IS NULL` (the two-population rule above). The write gates added
  in stages 6b-iii-a/b — `accept_reorder_request`, `create_purchase_order`,
  `reorder_hook`, `create_supplier_contact`, `create_sale`'s credit-customer
  lookup — filter `deleted_at_utc IS NULL` alone. So a row deleted BEFORE
  tombstones existed (`status='inactive'`, NULL tombstone) is hidden from every
  list yet can still be put on a NEW purchase order or reorder request.

  Not a regression: those routes had no deletion check whatsoever before, and
  `create_purchase_order` had no product validation at all. But it is exactly
  the "filter present on one path, absent on another" shape this codebase has
  shipped before, so it is written down rather than left to be rediscovered.
  The fix is one extra condition per gate plus a test; the reason it was not
  folded into 6b-iii-b is that it is a behaviour change to a legacy population
  and deserves its own commit and its own proof, not a quiet ride-along at the
  end of a long stage.

## 2026-08-28 — retail schema v18 CLAIMED for Phase 7

`RETAIL_SCHEMA_VERSION` is a single-writer resource, and this project has
already had two branches silently claim the same version — nothing objected,
because the migration gated on live shape rather than on the number. So the
claim is written down before any agent is dispatched, not after.

**CLAIMED: retail v18, by `feat/launch-readiness`, covering exactly two things:**

1. **Persisted sync freshness.** `SyncService`'s `last_success_at` is in-memory
   only (`_fresh_half_health`, `sync_service.py:2389`), so it resets to `None`
   on every app start. Phase 7's 30-minute stale-stock hiding, 24-hour warning
   and **72-hour hard stop on new sales are all defined in elapsed time since
   the last successful sync**, so on a till that is restarted each morning —
   the normal way a shop opens — none of them can ever fire. The 72h stop would
   appear built, pass any test written against a single long-lived process, and
   be inert in the field. Stored as device state (one row), not as a column on
   a business table.

2. **`stock_exceptions`** — the oversell queue, deliberately NOT created in v17
   because a shipped table with no writer misleads the next reader into
   thinking the feature exists. Phase 7 stage 7d is what writes it.

Anyone else adding a retail migration before Phase 7 lands should re-derive the
head first and add their own claim here — this records a moment, not live state.

See `docs/launch-readiness/phase7-offline-ux.md` for both findings in full,
including the second one, which is not a schema matter: implemented literally,
**"block logout while `sync_outbox` is non-empty" would make every install that
does not sync unable to log out, permanently, from its first sale.**
`_queue_sync_event` writes to the outbox unconditionally; what is inert on an
unconfigured install is the SERVICE, not the queueing. The block must be
conditional on sync actually being configured, and must carry a logged operator
override for the case where the outbox genuinely cannot drain (relay down,
licence lapsed, or the poison-event jam already tracked above).

## 2026-08-28 — `po_number` collides across companies, and the failure is not contained

Found 2026-08-28 while adding a purchase-order test; **not fixed** — recorded
because it is a real production bug on a multi-company install, not a test
artefact.

`purchase_orders.po_number` is declared globally `UNIQUE` in `schema.py`, but
`_next_ref` numbers it **per company**, starting at 1. So two different
companies on one install both mint `PO-000001` for their first purchase order,
and the second one raises `IntegrityError`.

Two things make it worse than a failed insert:

* **`create_purchase_order` has no `try/except` around that INSERT**, so the
  exception escapes with the connection never closed — it leaks, holding a WAL
  lock, and every later write on that database fails with `database is locked`.
  A single colliding PO therefore takes the install down, not just that request.
  This is the identical containment gap `delete_category`'s own comment records
  being fixed there for exactly this reason.
* It is the **same bug class AUDIT-032B already fixed** for `sale_number` and
  `return_number`, using a per-device discriminator. That fix was never applied
  to `po_number`.

How it surfaced: a new allow-half test became the second company in a shared
test database to create a PO, and six later tests in the same file cascaded into
`database is locked`. The test was made robust by seeding `doc_sequences` with a
per-test starting sequence, documented inline — the PRODUCTION defect is
untouched and still live.

**FIXED 2026-08-28.** And the sentence that stood here was wrong, so it is
corrected rather than deleted: it said fixing this "means touching
`schema.py`/`_next_ref`, i.e. a schema-version claim". It did not. `po_number`
stays `TEXT UNIQUE` and `_next_ref` is untouched — the shared helper must keep
its format for every other document type. The fix is entirely in how the two
mint sites in `retail_api.py` COMPOSE the string, exactly as AUDIT-032B already
did for `sale_number`/`return_number`: sequential part + per-company fragment +
per-device fragment. **No schema version was taken.** Anyone reading the
original claim would have burned a version number for nothing.

## 2026-08-28 — two gaps Phase 7 stage 7c surfaced, neither fixed

**1. Two devices can both receive the same purchase order, and stock is
double-counted.** Found while narrowing §5's offline block list.

`multi-device-design.md` §8 justifies not syncing purchase orders partly on
"receiving is admin-device-gated". That is **not true in the code**:
`receive_purchase_order` carries `mt_login_required`, `mt_require_subsystem`,
`require_license_capability` and `mt_require_capability(CAP_STOCK_ADJUST)`, and
no `_is_admin_device` check — that predicate guards exactly one route in
`retail_api.py`, the audit log.

So any two users holding `retail.stock.adjust`, on two devices, can each
receive the same PO. The route's double-receive guard is a conditional UPDATE
on `status='pending'`, which is per-device-local, and PO status is deliberately
never synced (§8), so the guard structurally cannot see the other device's
receipt — **online or offline**. Each writes its own `purchase_in` movement
with its own `uid`; both survive the merge because movements are additive by
design; the delivery lands twice.

Stage 7c blocks receipt while a device is behind, which is a partial mitigation
only. The real fix is either syncing PO status or moving the guard somewhere
that can see both devices, and it needs its own decision — note that §8's
reason for not syncing PO status was that only status was device-local and
stock was already correct, which this finding contradicts.

**2. `sync_conflicts` has no UI.** Phase 6 stage 6a-ii writes a row every time
an incoming catalogue edit is discarded as stale, precisely so a rejection is
visible rather than a silent drop. Nothing renders that table.

This is why stage 7c deliberately does NOT block catalogue edits offline: a
rejected offline edit is discarded and its author is never told. Blocking would
trade a rare, recorded loss for a guaranteed inability to work, which is the
worse deal — but the missing screen is what makes the rare case invisible, and
it should be built.

## 2026-08-28 — retail schema v19 CLAIMED for Phase 7 stage 7c-ii

Same single-writer discipline as the v18 claim above: written down before any
agent is dispatched, because this project has already had two branches silently
claim the same version and nothing objected.

**CLAIMED: retail v19, by `feat/launch-readiness`, for exactly one column —**
`sync_freshness.offline_override_at TEXT`.

Stage 7c-ii blocks NEW SALES once a device has been behind for 72 hours, behind
a manager override (`CAP_CASH_APPROVE`, per Decision 2 in
`docs/launch-readiness/phase7-offline-ux.md`). The override has to survive a
restart for the same reason the freshness clock did — a till restarted each
morning would otherwise re-prompt a manager on the first sale of every day.

**The override is validated by comparison, not by expiry**, which is why one
nullable timestamp is the whole schema change:

    valid  <=>  offline_override_at IS NOT NULL
                AND offline_override_at > last_<x>_success_at

A successful sync therefore invalidates a standing override automatically, with
no expiry job, no cleanup path, and no second state to get wrong. It also gives
the right behaviour on the case that matters: a shop that overrides, reconnects,
and later goes offline for another 72 hours is blocked again rather than
silently riding the old approval — because that approval now predates the last
success.

It goes on `sync_freshness` (the v18 single-row device-state table) rather than
a new table: it is device state with exactly the same lifetime as the
timestamps it is compared against, and splitting it out would invite the two
halves to be read separately when the whole point is that they are compared.

Anyone else adding a retail migration before 7c-ii lands should re-derive the
head first and add their own claim — this records a moment, not live state.

## 2026-08-28 — retail schema v20 CLAIMED for Phase 7 stage 7d-i

Same discipline as the v18 and v19 claims above; written before dispatch.

**CLAIMED: retail v20, by `feat/launch-readiness`, for one table —**
`stock_exceptions`, the oversell queue.

Reserved alongside `sync_conflicts` back in v17 and deliberately NOT created
then, because nothing in that phase would have written it and a shipped table
with no writer misleads the next reader into thinking the feature exists. This
is the stage that writes it.

**It has a genuine writer from day one, without relaxing anything.** A negative
balance is already reachable in shipped code: `create_sale` refuses
`qty > on_hand` against THIS device's own balance, so two tills each holding
the last unit each pass their own check and each sell it — and
`_apply_event`'s `inventory_movement` branch adds the merged quantity with no
floor at zero (`quantity_on_hand = quantity_on_hand + excluded.quantity_on_hand`).
Both devices land at −1. `compute_drift` surfaces the discrepancy today;
nothing records it as a business exception anyone can act on.

So 7d-i is detection and recording only: the table, the writer at the apply
site, and a read path. It changes no refusal and relaxes no guard.

**Deliberately NOT written from the local sale path.** A local sale cannot
drive its own balance negative — that check still stands — so the only way a
balance goes below zero is a merge. Writing from both places would invent a
second source for the same fact.

Resolution (writing an `inventory_movement` through the existing paths, gated
`CAP_STOCK_ADJUST`, never auto-resolved — Decision 4 in
`docs/launch-readiness/phase7-offline-ux.md`) is stage 7d-ii, and so is the
Decision 1 correction's relaxation of `create_sale`'s refusal when the device
is behind. Neither lands until there is a queue to record the result in.

### Correction to the v20 claim above (2026-08-29)

That entry says stage 7d-ii bundles resolution AND the Decision 1 relaxation of
`create_sale`'s refusal. **It was split when dispatched, and the split is the
right shape:** 7d-ii is resolution only; the relaxation is 7d-iii.

The reason matters, and it is a consequence of 7d-i's own design rather than a
preference. 7d-i records exceptions ONLY from the sync apply site, justified by
"a local sale cannot drive its own balance negative, because its own
`qty > on_hand` check still stands". Relaxing that check makes the local sale
path a NEW source of negative balances — so 7d-iii must also make it a
recorder, or a relaxed local oversell would go entirely unrecorded and the
queue would silently under-report.

That is a real behavioural coupling and it deserves its own commit and its own
proof, not a ride-along on a stage whose whole job is letting a human close a
row.

**Also recorded, a limitation of 7d-ii as shipped:** the required resolution
note has no column on `stock_exceptions` — the schema was frozen at v20 for
this stage — so it lives only in the audit log, reachable through
`list_audit_log` (itself admin-device gated). The exception row records THAT it
was resolved and when, not WHY. Whoever builds the queue's UI should either
surface the audit entry alongside the row or add the column in a later version;
a resolution whose reason is one join away from the screen showing it is a gap
worth closing deliberately rather than discovering.

## 2026-08-29 — two lines for the SAME product in one sale can jointly exceed stock

Found while building Phase 7 stage 7d-iii; **pre-existing, not introduced or
worsened by it, and not fixed.**

`create_sale`'s validation loop checks each line's `qty > on_hand` against the
SAME un-decremented balance. So a single sale containing two lines for the same
product — 3 units on one line and 3 on another, against 5 on hand — passes both
checks individually and commits 6.

This is independent of Phase 7 entirely: it happens on a device that is fully
synced, on an install with sync switched off, on any device at all. It is the
classic check-then-act shape this file has already fixed twice elsewhere
(`receive_purchase_order`'s double-receive race, and `create_sale`'s own
`BEGIN IMMEDIATE`), just applied within one request rather than across two.

The fix is to accumulate per-product demand across the line loop and check the
total, not each line in isolation. Deliberately not bundled into 7d-iii: that
stage changes when the refusal applies, and folding in a change to WHAT the
refusal computes would have made its mutation proofs ambiguous about which
behaviour they were pinning.

Worth noting the interaction: after 7d-iii a device that IS behind records a
`stock_exceptions` row when a sale drives the balance negative, so this bug is
now at least *visible* on a behind device. On a fully-synced device it still
passes silently, which is the case worth fixing.

### RETRACTED 2026-08-29 — the PO double-receive entry above is WRONG

The entry "two devices can both receive the same purchase order" is false, and
the stage 7c-i guard built on it has been removed. Left in place rather than
deleted, because the mistake is the useful part.

**Purchase orders never sync.** `sync_service.py:195` says `purchase_orders`
"stays local-only and is never pushed through this outbox at all";
`accept_reorder_request` carries an explicit "Deliberately NOT
`_queue_sync_event(...)` for this PO"; and a grep for a `purchase_order` sync
emission returns zero matches anywhere in the codebase. `receive_purchase_order`
emits only `inventory_movement` events.

So a purchase order exists on exactly ONE device — the one that created it. A
second device cannot receive it because it does not have it. **The cross-device
double-receive is unreachable.**

What went wrong in the original analysis: it correctly established that PO
*status* is never synced, and then reasoned as if the PO itself were present on
both devices. The narrower fact was true; the conclusion drawn from it was not.
A guard was then built, shipped, and justified in three places on that
conclusion — and it did real harm, refusing to book in a delivery that had
physically arrived, on a device that happened to be behind.

The genuine same-device race — a double-clicked Receive button, or two requests
against one shared database — was already fixed before Phase 7 touched it, by
`BEGIN IMMEDIATE` plus a conditional UPDATE, and is untouched by the removal.

**A real finding from proving that:** the same-device race is defended TWICE
over — an early `status == 'received'` check AND the conditional UPDATE's own
filter — and neither is individually pinned. Disabling either alone leaves
`test_double_submitted_po_receive_adds_stock_exactly_once` green; only
disabling both reproduces the original bug. A future refactor could delete one
believing it redundant, and no test would object. Worth an isolation probe for
each, the way stage 7c-ii and 7d-iii ended up needing one.

## 2026-08-29 — the two exception queues both need ONE screen, not two

Refining the "`sync_conflicts` has no UI" entry above, after checking what
actually exists.

It is worse than recorded: `sync_conflicts` has **no read route at all**. Grep
finds zero references in `retail_api.py` and zero in the frontend. Phase 6
stage 6a-ii writes a row every time an incoming catalogue edit is discarded as
stale — precisely so a rejection is visible rather than a silent drop — and
nothing can read it back through any interface.

`stock_exceptions` (Phase 7 stage 7d-i) is one step further along: it has a
read route and a resolve route, but still no screen.

**These are the same feature and should be built as one surface, not two.**
Both answer "something happened that the software could not resolve on its own
and a human must decide". A shop owner does not want two places to look, and
two half-built queues is how one of them ends up permanently unvisited.

What a combined screen needs:
* a read route for `sync_conflicts`, mirroring `list_stock_exceptions`
  (7d-i) — the smaller half, and a prerequisite;
* one page listing both kinds, each row carrying enough context to act:
  which product/record, which device, when, and what was discarded;
* the resolve action `stock_exceptions` already has, and for
  `sync_conflicts` an equivalent acknowledgement — a discarded edit cannot be
  "re-applied" (the newer value legitimately won), so the honest action there
  is "seen", not "fix".

Two related gaps that belong on the same page rather than separately:
* **the resolution note has no column** on `stock_exceptions` — it lives only
  in the audit log, so the row records THAT it was resolved, not WHY. Surface
  the audit entry beside the row, or add the column;
* **no restore UI exists for any entity** — a deleted product, supplier or
  customer can only be restored by a raw API call. A "deleted records" view
  belongs in the same neighbourhood as the exception queues: both are
  "things needing an owner's attention that no screen currently shows".

Deliberately NOT started as an API-only route in isolation: `stock_exceptions`
already demonstrates that a queue with a route and no screen is invisible in
practice, and adding a second one would double the invisible surface without
closing the gap either was recorded for.

## 2026-08-29 — the same unvalidated-supplier hole exists on the PRODUCT routes

Found while fixing the purchase-order cross-tenant leak (`6b79b5a`), and
deliberately left unfixed there to keep that change's mutation proofs
unambiguous.

`create_product` (~line 1360) and `update_product` (~line 1423) write
`data.get('supplier_id')` straight through with **no validation at all** — no
existence check, no `company_id` match, no tombstone check. Identical bug class
to the one just fixed on `create_purchase_order`, on a different table.

**What is already contained, and what is not.** The PO fix scoped the supplier
joins in `list_purchase_orders` and `get_purchase_order`, so a poisoned
`products.supplier_id` cannot leak a foreign supplier NAME through purchase-order
reads. `accept_reorder_request` is also safe on its own account: it reads
`supplier_id` off the products row rather than from request data, and that row is
read scoped to company, active status and tombstone.

What has NOT been checked, and must be before this is called contained: every
other read that joins `suppliers` through `products.supplier_id`. If any is
unscoped the same name-leak applies by a different route, and the fix is the
same two halves — validate on write, scope the join.

The write half should mirror `create_purchase_order`'s new check exactly, and
`supplier_id` must stay OPTIONAL there too: a product with no supplier is a real
and exercised state.

### Two stale comments to correct when this is fixed

Both now describe the purchase-order bug as unfixed, and were accurate when
written:

* `preview_po_split`'s own comment — "unlike `create_purchase_order` above (a
  real, pre-existing bug ... OUT OF SCOPE for this route to fix)";
* `retail_po_split_route_test.py::test_split_preview_rejects_cross_tenant_product_id`'s
  docstring — "a real, pre-existing, deliberately NOT-fixed-here bug".

This repo corrects stale comments in place rather than letting them rot, and a
comment asserting a live bug that no longer exists will send the next reader
looking for it.

### Checked 2026-08-29 — that product-route hole is real but currently NON-LEAKING

The entry above says every read joining `suppliers` through
`products.supplier_id` must be checked before the product-route hole is called
contained. Checked, and the answer is better than assumed.

**No such read exists.** Every read of the `suppliers` table in
`products/retail/backend` and `commercial_runtime` was enumerated, not just the
joins:

* exactly TWO joins exist, both in the purchase-order routes, both scoped by
  `6b79b5a`;
* `list_suppliers`' own query is company-scoped in its WHERE;
* every other read is either an existence check carrying
  `AND company_id=?`, or a read-back by id immediately after a company-scoped
  UPDATE (so the row is already proven to be this tenant's);
* nothing anywhere fetches supplier DETAILS via a product's `supplier_id`.

`list_products` returns `p.*`, which carries the raw `supplier_id` but no
supplier name, so a poisoned value exposes an opaque foreign identifier and
nothing else.

**So this is a dangling cross-tenant reference, not an active data leak** — a
correction to the severity implied above, which was written before the check.

Still worth fixing, for two reasons that are not "it leaks today":
1. CLAUDE.md's rule is that a business query skipping `company_id` scoping is a
   bug rather than a simplification, and an unvalidated FK write is the same
   class;
2. it is one added join away from becoming a real leak. The moment anyone puts
   a supplier name on the products list — an obvious, likely feature — the
   poisoned id starts rendering another tenant's data, and whoever adds that
   join will have no reason to suspect the id is untrusted.

Priority accordingly: real, worth doing, not urgent. The write-side validation
should mirror `create_purchase_order`'s, and `supplier_id` must stay optional.

### Corrected 2026-08-29 — those "isolation probes" cannot be written, and should not be

The retraction entry above ends by saying the two PO double-receive guards each
want an isolation probe, "the way stage 7c-ii and 7d-iii ended up needing one".
**That was wrong, and following it would have sent someone chasing a test that
cannot exist.**

The two guards are:
1. an early `if po['status'] == 'received': return 409`;
2. the closing `UPDATE ... WHERE status <> 'received'` and its rowcount check.

Under `BEGIN IMMEDIATE`, request B only reads the PO after request A commits.
At that point B sees `'received'` and guard 1 fires — and guard 2 would fire
too, since the row it needs to flip is already flipped. **Each guard alone is
genuinely sufficient for the only race the route can actually experience.** So
a test that fails when exactly one is removed cannot be written, because
removing one does not break anything.

That is the crucial difference from the 7c-ii and 7d-iii cases. There, two
conditions overlapped by accident and one was carrying the other, so a mutation
passing green was hiding a guard nobody had verified. Here the second guard is
deliberate, and the route's own docstring says so:

    Belt and braces: even a future caller that somehow reaches this code
    without the lock still cannot double-apply, because the row it needs to
    flip is no longer there to flip.

Its purpose is a caller that BYPASSES the lock — a future refactor, a new entry
point — which is a scenario the current code cannot reach and therefore cannot
be provoked by driving the route normally.

**What IS worth testing, and is not written today:** that belt-and-braces
property itself — drive the inner path in a way that skips the early check, and
assert the conditional UPDATE still refuses. That pins the backstop's actual
value rather than trying to prove a redundancy is not redundant.

Recorded rather than built because it needs a way to reach the closing UPDATE
without the early read, and inventing a test-only seam into a money path to
prove a defensive guard is a trade worth making deliberately, not in passing.

## 2026-08-29 — the outbox growth gap, quantified, and why it needs the owner's call

The entry above records that `sync_outbox` has no retention policy on installs
with no drain path. Quantified now, because "no retention policy" understates it
and because the obvious fix trades away data.

**Rate, measured not estimated:** `create_sale` queues **11 events per sale**
(the sale, its items, the stock movements, the payment). A shop ringing 200
sales a day writes on the order of 2,000 outbox rows daily, each carrying a
full-row JSON payload, plus catalogue edits on top. Over a year that is
comfortably half a million rows in a SQLite file sitting on a till, and nothing
ever deletes one.

**The accumulation is DELIBERATE, which is what makes this hard.**
`multi-device-design.md` §6 states the intent plainly: a single-device install
"migrates with zero loss and no user action, and its **backfilled history pushes
once on first sync**, bounded by the existing 200-event chunking." Queueing while
sync is unconfigured is what preserves that history for a shop that enables sync
later. It is not an oversight.

**So the fix is not "stop queueing when unconfigured".** That silently discards
the very history the design promises to deliver on first sync, and the shop that
enables sync in year two would arrive with no past — the failure being
invisible, which is the worst kind.

**And a retention policy cannot distinguish the two cases.** "Never going to
sync" and "has not synced yet" are the same state on disk. Any pruning rule
therefore chooses, on the shop's behalf, between an unbounded file and a
truncated history — and there is no signal in the data that says which the shop
would want.

**Deliberately NOT decided unilaterally.** The plausible options each give
something up, and the trade belongs to whoever owns the product:

* **Age-based pruning** (drop rows older than N months when sync has never been
  configured) — bounds growth, and a late-enabling shop silently starts from N
  months ago rather than from the beginning.
* **Cap with a warning** — keep everything up to a row count, then surface "sync
  has never been configured and N events are queued; enable it or they will be
  discarded". Honest, but needs a surface to say it on, and the exceptions
  screen (`fa5a89b`) is now a plausible home.
* **Prune only what is already durable elsewhere** — sales and movements are in
  their own tables and the outbox row is a wire copy, so pruning loses the
  ability to REPLAY history to a new device, not the history itself. This is
  probably the most defensible option and the one worth pricing first.
* **Do nothing and document it** — a till with a 500 MB database still works;
  SQLite does not care. The cost is backup size and restore time.

What is NOT in doubt: nothing today bounds it, and no install is told. Recorded
with the measurement so the decision can be made on numbers rather than a
feeling about "some rows".

## 2026-08-29 — retail schema v21 CLAIMED for the POS scale fix

Same single-writer discipline as v18/v19/v20. Claimed before dispatch.

**CLAIMED: retail v21, by `feat/launch-readiness`, for lookup indexes only.**

A senior systems/finance review confirmed and deepened a suspected blocker:
`list_products` returns EVERY active product for the company with no `LIMIT`
and no search parameter, `_findByCode` filters that array client-side for POS
scan / product search / PO scan, `_loadPOSData()` re-fetches the whole
catalogue **after every completed sale**, `_renderPOSGrid` renders every match
as inline HTML with no cap, and the search input rebuilds that grid on every
keystroke with no debounce. Android's `ProductLookup.kt` repeats the
client-side scan over a fully-fetched list.

Estimated payload per POS load AND per sale: ~250 KB at 500 SKUs, ~2.5 MB at
5,000, ~25 MB at 50,000 — with 50,000 DOM buttons built from one innerHTML
string.

The index half of the fix, and the only part needing a version:

* `products(company_id, barcode)` and `products(company_id, sku)` — the scan
  path has NO index today;
* `sale_items(sale_id)`, `sales(customer_id)`, `payments(party_type, party_id)`
  — none exist either, and `recent_sales`, `customer_statement` and the returns
  lookup all scan without them.

**A second, separate finding to fix WITHOUT a version:** every date filter is
wrapped in `date(...)` — `date(s.created_at)` in `recent_sales`, and
`metrics.py` builds `f'date({...})'` predicates literally. That is
non-sargable, so the `idx_*_created_at_utc` indexes v13 already created **can
never be used**. Rewriting those to half-open range bounds computed in Python
is pure query change, no migration.

Deliberately NOT in this claim: promotions, variants, open-item AR allocation,
LAN relay. Each is real and recorded separately; none needs a schema version
yet.

## 2026-08-29 — the case-insensitive lookup is only PARTLY case-insensitive

Found while verifying the parallel Android/backend work, and measured rather
than argued.

`lookup_product` was made "case-insensitive" by building a fixed set of
variants of the TYPED code — as-typed, `.upper()`, `.lower()` — and matching
with `IN (...)`. The reasoning given is sound as far as it goes: neither
`COLLATE NOCASE` on the column nor `LOWER(col)` may be used, because wrapping
the column is non-sargable and would destroy the v21 indexes.

**But three variants of the INPUT cannot match a mixed-case STORED value.**
Measured, in-memory SQLite, one row `sku = 'AbC-123'`, typed `abc-123`:

    variants: ['ABC-123', 'abc-123']      match: None

Android's `findProductByCode` used `equals(ignoreCase = true)`, which matches.
So for any catalogue with mixed-case SKUs this is a REGRESSION against the
behaviour the endpoint was explicitly changed to preserve. Barcodes are
usually numeric and unaffected; SKUs are where it bites.

**The correct fix is a NOCASE-collated INDEX, and it is fully sargable** —
the premise that case-insensitivity costs the index is wrong. The collation
lives in the index, not in a function wrapping the column:

    CREATE INDEX idx_x ON products(company_id, sku COLLATE NOCASE)
    SELECT ... WHERE company_id=? AND sku=? COLLATE NOCASE

    PLAN:  SEARCH products USING COVERING INDEX idx_nocase (company_id=? AND sku=?)
    match: ('AbC-123',)

Both measured in the same probe. So the right change is:

* a new schema version adding NOCASE-collated indexes on
  `products(company_id, barcode)` and `products(company_id, sku)` — these are
  ADDITIONAL to the v21 plain ones, which still serve exact-match callers;
* the lookup query using `= ? COLLATE NOCASE` and dropping the variant set.

Not done immediately only because another agent held `retail_api.py` when this
was found. It needs its own version claim; v21 is committed and cannot absorb
it.

**Worth keeping as a lesson:** the variant trick looked like a clever way to
stay sargable, and its own docstring explains the reasoning convincingly. It
was wrong, and the only thing that showed it was running the query against a
mixed-case row. A plausible explanation in a comment is not evidence.

## 2026-08-30 — retail schema v22 CLAIMED for the case-fold lookup

Same single-writer discipline as v18/v19/v20/v21. Claimed before dispatch.

**CLAIMED: retail v22, by `feat/launch-readiness`, for two NOCASE-collated
indexes only.** No table, no column, no data migration.

This closes the gap the entry above measured: the three-variant `IN (...)`
trick cannot match a mixed-case STORED value, and a NOCASE-collated index is
fully sargable, so the reasoning that ruled it out was wrong.

    CREATE INDEX idx_products_company_barcode_nocase
      ON products(company_id, barcode COLLATE NOCASE)
    CREATE INDEX idx_products_company_sku_nocase
      ON products(company_id, sku COLLATE NOCASE)

The v21 plain indexes are KEPT, not replaced. They are what makes the
exact-match rung of the lookup ladder below provably indexed, and a NOCASE
index cannot serve a BINARY equality (SQLite will not use an index whose
collation differs from the comparison's).

### The part that is not just an index: three doors, not one

Making the READ case-insensitive without making the WRITES case-insensitive
re-opens the duplicate-barcode scanning bug `AUDIT (2026-08-14)` closed,
through a different door. If `abc` and `ABC` can both be stored, a case-folding
scan resolves to an arbitrary one of two different products — which is exactly
the "scanning silently resolves to the wrong item" failure that audit names.

Three writers can currently create that state, and all three are in scope:

1. `create_product` — `WHERE company_id=? AND barcode=?` (retail_api.py:1477)
   and the SKU check above it (:1462);
2. `update_product` — `WHERE company_id=? AND barcode=? AND id<>?` (:1604);
3. **`import_api.py:1424` — the CSV product upsert keys on `sku=?`.** This one
   was not on the original list and is the one that matters most at scale: a
   hypermarket onboards its catalogue by import, so the import door is the one
   most likely to manufacture the duplicates at volume.

All three become case-insensitive.

**What import can no longer do, stated rather than discovered:** importing SKU
`abc` when `ABC` exists now UPDATES that product instead of inserting a second
one. A shop deliberately using case to distinguish two products loses that.
Judged correct — it is the same rule the scan now follows, and the alternative
is an import that builds rows the till cannot disambiguate — but it is a
behaviour change on a shipped path, so it is pinned by a test rather than left
to be found.

### Lookup resolution order (deterministic, and it matters)

Legacy installs may ALREADY hold case-duplicates, created before this change.
The lookup must not pick between them arbitrarily. Four rungs, first hit wins:

    barcode exact  ->  barcode NOCASE  ->  sku exact  ->  sku NOCASE

Column precedence (barcode before sku) is unchanged from the shipped route;
exactness wins within a column. Every rung is a single indexed point lookup,
and the common case — an exact barcode scan — returns on the first.

Deliberately NOT in this claim: promotions, variants/modifiers, kitchen
tickets, split tender, LAN relay. Each is real, each is recorded separately,
and none needs a schema version reserved today.

## 2026-08-30 — retail schema v23 CLAIMED for promotions, wave 1

Same single-writer discipline as v18–v22 — with one honest exception recorded
below rather than hidden.

**This claim was written before dispatch and then not committed with the rest.**
It was held back from ROADMAP.md to avoid colliding with an agent that held the
file, and never appended. Commit f4fed95 nevertheless carried a message stating
the v23 claim had been made, so for a stretch the message overstated its own
diff. Caught by the frontend agent, which was told to read this section as its
spec, went looking, and reported that it did not exist instead of inventing one.

Worth recording because the entire value of claiming a schema version in writing
is that the claim is IN THE REPO where another branch can see it. A claim that
exists only in a commit message and a scratch file is exactly the collision this
discipline was adopted to prevent. The frontend work was unaffected — its
prompt carried the full frozen contract directly.

**CLAIMED: retail v23, by `feat/launch-readiness`, for two tables and two
indexes.** No change to any existing table, and no change to `pricing.py`.

A senior review named the missing promotions engine as a hypermarket blocker,
and re-verification confirms it: a MANUAL per-line `discount_pct` exists,
clamped server-side and gated behind `CAP_DISCOUNT`, but nothing automatic —
no rules, no date windows, no scoping, nothing in schema. A shop that runs a
weekend offer has to have every cashier type it by hand on every line.

    CREATE TABLE promotions (
      id, company_id, name, discount_pct,
      product_id, category_id,     -- exactly one of the two
      branch_id,                   -- NULL = every branch
      starts_at, ends_at,          -- NULL = unbounded on that side
      status, created_at)
    CREATE TABLE sale_item_promotions (
      id, company_id, sale_item_id, promotion_id,
      name_snapshot, discount_pct_snapshot, discount_amount_snapshot)

    idx_promotions_company_live      ON promotions(company_id, status)
    idx_sale_item_promotions_item    ON sale_item_promotions(sale_item_id)

Both new tables carry `company_id` even though `sale_items` itself does not.
`sale_items` inherits tenancy through `sale_id`, which predates the rule
CLAUDE.md now states; a new table has no reason to repeat that, and it lets a
promotion-performance report scope itself without a three-table join.

### Why it resolves to a PERCENTAGE, and what that costs

A promotion resolves to an effective `discount_pct` on the line and is then fed
through the existing `pricing.calculate_line`. That is the whole integration:
**`pricing.py` is not modified.** It is documented as the only module allowed
to compute a persisted financial total, and both tax modes
(TAX_AFTER_DISCOUNT / TAX_BEFORE_DISCOUNT) already interact with a line
discount correctly, so expressing promotions in the units it already speaks
makes the tax interaction correct by construction rather than by a second
implementation that has to be kept in agreement.

**The cost, stated rather than discovered: wave 1 has no fixed-amount
promotion** ("2 JOD off"). A fixed amount can only enter this path as
`amount / gross * 100`, and converting amount to percentage and back can land
a cent away from the amount the shop advertised. A promotion that prints 1.99
off when the poster says 2.00 is worse than not having the feature. Doing it
properly means giving `calculate_line` a real per-line discount AMOUNT, which
is a change to the financial core and deserves its own wave and its own
mutation proofs.

Also NOT in wave 1, each deferred deliberately: buy-X-get-Y and any other
basket-level rule (needs a cross-line engine, not a per-line resolver),
mix-and-match, customer-group pricing, coupon codes, loyalty.

### The two rules most likely to be got wrong

**1. Best price wins; discounts do not stack.** If a line carries an automatic
promotion of P% and the cashier also types a manual M%, the line takes
`max(P, M)`, never `P + M`. Summing is how a 60% promotion plus a 50% manual
discount becomes 110%, clamps to 100, and hands the item over for nothing.

**2. The `CAP_DISCOUNT` gate is judged on the MANUAL component only.** Today
`create_sale` refuses a discounted sale from a cashier lacking `CAP_DISCOUNT`.
If that check starts seeing the promotion's percentage, then a cashier without
the capability can no longer sell a promoted item at all — the shop's own
weekend offer locks out its own till. Configuring a promotion, by contrast,
DOES require `CAP_DISCOUNT`: deciding to give value away at scale is exactly
the authority that code names. Both halves get tested; the allow-half (a
cashier with no `CAP_DISCOUNT` successfully selling a promoted line) is the one
that a "deny everything" mutation would otherwise pass.

### Snapshot, because a receipt is read later than it is printed

`sale_item_promotions` stores the promotion's name, percentage and resulting
amount AS APPLIED. A receipt reprinted next year, and a return processed
against it, must show what the customer was actually charged — not what that
promotion's row says today, and not what it says after someone edits it. Same
discipline the e-invoicing sequence and the cash-session closing figures
already follow.

**Returns need no change, and that is a consequence of the design rather than
luck.** `create_return` already ignores every client-sent figure and recomputes
the refund from the original `sale_items` row, proportionally to the quantity
coming back, so tax and discount reverse correctly. A promotion IS that row's
`discount_pct`, so a promoted line refunds the price actually paid without the
returns path knowing promotions exist. Had promotions been modelled as a
separate amount hanging off the line, returns would have had to learn about
them or would have silently refunded list price on every promoted item.

Pinned by a test anyway: return one unit of a promoted line, assert the refund
matches what was paid for one unit and not the list price.

### The cart must show the promoted price BEFORE the customer pays

Applying promotions server-side only would mean the POS cart displays list
price, the customer agrees to it, and the receipt then charges less — the
total changing after checkout. In a shop that reads as the till being wrong,
even when it is wrong in the customer's favour.

So `GET /promotions/active` returns the currently-live rules (a small list,
loaded once per POS mount alongside the bounded first page), and the cart
applies the same best-price-wins rule client-side. This is NOT a second
authority: it is exactly the arrangement `pricing.py`'s own module docstring
already documents for `_recalc` — a deliberately exact mirror for INSTANT
DISPLAY ONLY, with `create_sale` recomputing everything server-side regardless
of what the client displayed or submitted. A disagreement produces a wrong
preview followed by a correct charge, never a wrong charge.

### Invisible unless opted in

Zero promotion rows must mean byte-identical behaviour on the sale path, and
that is pinned by a test rather than assumed — the same contract e-invoicing
and licensing enforcement already hold to.

The device's LOCAL clock decides whether a promotion is live. Sales are
explicitly designed to continue while the network is down, so there is no
authoritative remote clock to consult at the moment it matters.

## 2026-08-30 — registry schema v7 CLAIMED for branch-scoped users

Same single-writer discipline as retail v18–v23, and claimed BEFORE dispatch
this time — the v23 entry above records what happened when that slipped.

**CLAIMED: registry v7, by `feat/launch-readiness`, for ONE nullable column.**

    ALTER TABLE users ADD COLUMN branch_scope_uid TEXT   -- NULL = every branch

Backing the chain-oversight ask: a chain of five stores needs a branch manager
who sees only their own store and a head office that sees all of them. Today
the capability model has no branch dimension at all — `?branch_id=` on a report
is a free parameter for anyone holding `retail.reports`, and mutations accept
any branch id.

**Deliberately NOT a new capability code.** `CAPABILITY_CODES` is a FIXED
eight-tuple and it is the seeding contract: every account gets a row for every
code, so "never provisioned" and "explicitly denied" stay distinguishable.
Adding a ninth code changes that contract for every existing account on every
install. A scope column answers "which branches" without touching "which
powers", which are genuinely different questions — a branch manager and a head
office manager hold the SAME capabilities over DIFFERENT data.

### Why this is safe for Clinic, which shares registry.db

`users` lives in `registry.db`, shared by Retail and Clinic. The owner's
instruction is not to work on Clinic, so:

* the column is **nullable with no default**, so every existing row means
  "every branch" — i.e. exactly today's behaviour, for both products;
* nothing in Clinic's code path reads it;
* enforcement lives in Retail's branch-taking routes only.

Clinic's behaviour is byte-identical before and after. That is a property to
TEST, not to assert — the migration test must prove a Clinic-shaped database
migrates and behaves unchanged.

### Ordering constraint, stated because it is easy to get wrong

This must land AFTER the seat-entitlement wave's edits to
`commercial_runtime/identity/` (both touch `user_accounts.py` /
`onboarding_routes.py`), and its enforcement needs device→branch pinning to
exist first — otherwise there is nothing meaningful to scope against, because
every till currently resolves to the company's FIRST branch (see below).

---

## 2026-08-30 — the multi-branch capture defect, recorded before it is fixed

**Not a schema claim. A live defect, written down so it is not rediscovered.**

`_default_branch(conn, cid)` (retail_api.py:616) resolves a company's working
branch as `SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1` —
the company's FIRST branch. `create_sale` uses `data.get('branch_id') or
_default_branch(...)`, and **the POS client sends no `branch_id` at all** in its
sale payload (verified by reading the payload literal in subsystem-retail.js).
Nothing anywhere pins a device to the branch it physically stands in.

For a single-branch shop this is invisible and always correct, which is why it
has never surfaced. For the chain case it is severe: sync converges every
device on one licence into one dataset, so all five stores' branch rows exist on
every till, and every till picks the first one. **Five stores would file every
sale under store 1 and decrement store 1's stock**, regardless of where the sale
happened. The resolved branch drives both attribution and the inventory
movement.

So a head-office comparison screen built today would report confident, wrong
numbers — the most expensive kind. Device→branch pinning must land BEFORE any
chain reporting work, and it needs no schema version: the pin belongs in the
existing device-local `config.json` (`onboarding_routes._read_config` /
`_write_config`), which is deliberately never synced — exactly the property
required, since "which branch is this till standing in" is the one fact that
must NOT be shared between devices.

`_default_branch` stays as the last-resort self-heal, with its existing
comment intact; the resolution order becomes pinned branch → explicit request
value → `_default_branch`.

---

## 2026-08-30 — retail v24 RESERVED BY NAME (not claimed) for inter-branch transfers

Confirmed absent: `transfer` appears nowhere in retail's schema or API. It is a
real gap for a chain — branches exist, stock is branch-scoped, and there is no
way to move stock between them.

Reserved by NAME only, deliberately. Nobody is building it today, and a claim
that sits open invites a second branch to assume it is dead and take the number.
When it is scheduled it gets a full claim entry like the ones above, at whatever
the next free version is at that time — this note does not hold v24 hostage.

## 2026-08-30 — commercial model DECIDED by the owner (devices, not seats)

Answers given directly by the product owner, recorded here because they are
pricing calls nobody else may make, and because the design work dispatched
against them must not silently drift from what he actually said.

1. **The paid meter is DEVICES.** Two devices included, **50 JOD per additional
   device**. This accepts the recommendation in
   `docs/launch-readiness/seats-and-chain-design.md` §1.2 over his own opening
   framing ("more than 2 users must pay 50 JOD"), on the argument that users are
   rows a customer's own admin creates offline, so a per-head charge is
   enforceable only against honest customers — and the dishonest response
   (one shared "Cashier" login) destroys the attribution machinery (PINs, audit
   trail, cash-variance separation) that is the product's own value.

   Devices are the hard axis: Ed25519 device-bound, enforced server-side at
   activation (`DEVICE_LIMIT_REACHED`), and unmintable offline.

2. **The owner's own account counts inside the included allowance.** Marketing
   reads "owner plus one included", not "owner plus two".

3. **The 50 JOD is per additional device.** Whether it is one-time or per
   subscription term was not separated in his answer; the add-on machinery
   supports either and it is an Owner-side catalog value, so it does not block
   any product-side work. Flagged, not guessed.

4. **Clinic: not yet.** Seat/device entitlement work stays off Clinic until he
   says otherwise. Anything landing in shared runtime must leave Clinic
   byte-identical, and prove it by test rather than assert it.

### What this changes about the work already claimed

`max_users` drops from "the revenue mechanism" to "an optional anti-abuse
backstop", and is no longer on the critical path. **The device limit it would
have duplicated already exists and is already enforced** — so the honest
consequence of this decision is that the single largest piece of the seat design
may not need building at all. That is being re-examined rather than built by
momentum.

The chain work (device→branch pinning, branch-scoped users, head-office view) is
UNAFFECTED by the metering decision and remains the real work.

### A fifth answer that was not a pricing answer

Asked whether branches should be priced, the owner instead described a
requirement: *"i want to be able to have an owner account that creates admins for
managers for the branches and employees for the workers in these branches."*

That is an account-hierarchy requirement, not a price, and it collides with a
deliberate architectural constraint: `ROLE_MANAGER` excludes `CAP_EMPLOYEES` on
purpose, because `users` is an admin-device single-writer table and a manager
minting accounts would be writing to a table their device is not the writer for
(see the comment in `user_accounts.py`). Whether that constraint is real today or
a stale comment is being verified rather than assumed — this codebase has
already produced three stale "missing feature" claims and one stale "products are
not synced" comment contradicted by live code.

Branch pricing therefore remains genuinely unanswered, and is not blocking.

## 2026-08-30 — device fee DECIDED: 50 JOD, ONE TIME

Answered directly by the product owner: the 50 JOD per additional device is a
**one-time** charge, not per year. Recorded because it is a pricing call nobody
else may make, and because the design that proposed it recommended the opposite.

`docs/owner/packages-and-issuance-design.md` recommended per-year on the grounds
that device fees fund the relay bandwidth and support those devices generate.
The owner chose one-time. His call, taken with the trade-off stated.

**The consequence, written down rather than discovered later:** all recurring
revenue now comes from the annual plan alone. A 20-till hypermarket pays
900 JOD once for its extra devices plus 420/year thereafter, while generating
relay traffic and support load proportional to twenty devices forever. If margin
on large installs ever looks wrong, this is the first place to look — it is a
pricing decision, not a bug, and it can be revisited per-plan in the catalog
without any code change.

Nothing in the product enforces or cares about the billing period; the meter is
`max_devices`, already enforced server-side at activation. One-time versus
annual is entirely an Owner catalog value.

## 2026-08-31 — chain wave C3 was ALREADY BUILT. Verified, not assumed.

`docs/launch-readiness/account-hierarchy-design.md` §5.2 item 4 lists "a
comparison screen" among what is genuinely missing for a chain of five stores:
"reports exist per-branch and all-branch, but nothing puts Store A next to
Store B. Screen work only."

**That is false.** Verified end to end before any work was dispatched:

* `GET /reports/by-branch` exists (`retail_api.py:6997`, `report_by_branch`,
  CAP_REPORTS-gated) and returns revenue and transactions PER BRANCH. Its own
  docstring says every active branch appears -- including ones with zero sales
  in the window -- "which is what makes a comparison chart meaningful (a branch
  with 0 revenue is a real, visible bar, not a silently missing one)".
* The Reports page already fetches it, in the same `Promise.all` as the other
  report widgets, and renders it as `rep-branch-chart`, a BAR chart over
  `byBranch.labels`.
* It is deliberately NOT filtered by the page's own `?branch_id=` selector,
  with a comment explaining why: "compare branches" and "scope to one branch"
  are contradictory asks for the same chart. That is exactly the head-office
  semantic, already reasoned about and already implemented.

So the head-office view needed no build at all. Combined with §5's own finding
that the all-branches view is the ABSENCE of scope coercion rather than a
feature -- and that head-office staff who are not the owner are simply a
`manager` with NULL scope, supported by construction -- **chain wave C3 is
complete and requires no code.**

### The pattern this is the fourth instance of

Stale "missing feature" claims in this repo, all found by checking rather than
trusting, all in the direction of UNDERSTATING what works:

1. CLAUDE.md's "genuinely missing" list -- wrong on three of seven (notifications,
   RBAC, and cash-drawer management all exist).
2. `docs/launch-readiness/infrastructure.md` -- the APK bearer-token blocker was
   already fixed three ways in the gradle build.
3. `aura_retail.spec` -- "NOT YET BUILD-VERIFIED" for a build that succeeds
   (10.6 MB exe, exit 0).
4. This one.

And its mirror image, twice: `required_entitlement` and
`requires_password_reprompt` are both complete, tested mechanisms with ZERO
production callers -- machinery that exists and does nothing.

The rule both halves teach: **"the code exists" and "the behaviour exists" are
different claims, and neither implies the other.** Check the callers, not just
the definition; check the screen, not just the route.

## 2026-08-31 — retail schema v25 CLAIMED for product variants

Claimed BEFORE dispatch, and committed before dispatch — the discipline slipped
once on v23 (the claim sat in a scratch file while a commit message asserted it
existed) and that is not repeating.

**CLAIMED: retail v25, by `feat/launch-readiness`, for two columns on `products`.**
v24 stays RESERVED BY NAME for inter-branch transfers and is deliberately skipped.

    ALTER TABLE products ADD COLUMN parent_product_id TEXT   -- NULL = not a variant
    ALTER TABLE products ADD COLUMN variant_label     TEXT   -- 'Red / Large'

### A variant is a PRODUCT, not a new table

Design: `docs/launch-readiness/variants-and-modifiers-design.md`.

A variant has its own SKU, its own barcode, its own price and **its own stock**.
It is a thing you COUNT. So it is a product with a parent, and that reuses the
entire existing inventory subsystem -- balances, movements, the v22 four-rung
scan ladder, sale lines, reporting -- for free. A separate `product_variants`
table carrying its own stock would duplicate the inventory subsystem, which is
the wrong call in a codebase that already has one that works.

The argument came out STRONGER than the prior that motivated it: `product` is
already a SYNCED entity whose UUID id is its wire identity, so the parent pointer
syncs across devices with no new machinery at all.

**Corrects a stale comment while we are here:** the base DDL says products are
not synced; `sync_service.py`'s live `product` apply branch says otherwise. Trust
the code.

### What must not be got wrong

**The sync payload.** The two new columns must be added to the product sync
payload, the apply-branch column list, AND the changed-field delta allowlist. Miss
any one and variants arrive on peer devices as ORPHAN STANDALONE PRODUCTS --
silently, with no error, on exactly the multi-device installs this product is
built for. That is the single most likely defect in this wave and it gets its own
test.

### What rides free, and is pinned rather than assumed

`pricing.py` untouched. Returns unchanged -- `create_return` recomputes from the
original `sale_items` row and a variant IS a product_id, so nothing there learns
a new concept. The scan ladder unchanged -- a variant's barcode is a barcode.
Stock, movements and reports unchanged.

**And the latent refund bug does NOT activate here.** `create_return` matches by
`(sale_id, product_id)` with `fetchone()`, which is ambiguous only when ONE sale
holds two lines with the SAME product_id at different prices. Two variants of one
parent are two DIFFERENT product_ids, so a variant sale cannot produce that
ambiguity. Modifiers can, and that fix stays scheduled with them -- stated here
so the two are not conflated.

Deliberately NOT in this claim: modifiers (their own wave, own version, own
tables with UUID ids and row_version from day one), a size x colour matrix
generator (the owner has not said whether per-variant manual creation suffices),
and variant-aware purchase ordering.

## 2026-08-31 — retail schema v26 CLAIMED for restaurant modifiers, wave 1

Claimed AND committed before dispatch. v24 remains reserved by name for
inter-branch transfers; v25 shipped variants (bd272ca).

**CLAIMED: retail v28, by `feat/launch-readiness`, for inter-branch transfers.**
Two new tables, indexes. Additive only: CREATE TABLE / CREATE INDEX.

    stock_transfers       -- one movement of goods between two branches
    stock_transfer_items  -- what was sent, and what actually arrived

This is the 2026-08-30 "v24 RESERVED BY NAME" note being cashed in. That note
says explicitly it does not hold the number hostage and that the work takes
whatever version is free when it is scheduled. v25-v27 have shipped since, and
a database already at 27 would never run a v24 migration, so 28 it is.

### Two phases, because goods take time to arrive

A transfer is SENT from one branch and RECEIVED at another, and the two are
separate events with a gap between them. The one-phase alternative -- move the
stock instantly -- is simpler and wrong for a chain: it makes goods in a van
invisible, and it has nowhere to put the case where six cartons were sent and
five arrived.

So stock leaves the source on SEND and appears at the destination on RECEIVE,
and `quantity_received` is recorded separately from `quantity_sent`. A
discrepancy is data the shop can see and act on, not an error to suppress. In
transit, the stock belongs to neither branch, which is the honest answer to
"where is it" rather than a convenient lie in favour of one end.

Both ends write through `inventory_movements`, the ledger that already owns
every stock change, so a transfer is auditable the same way a sale or an
adjustment is and cannot drift from the balances.

Capability is `CAP_STOCK_ADJUST`: moving stock between branches is a stock
adjustment at both ends, and no new capability is minted for it.

---

**CLAIMED: retail v29, by `feat/launch-readiness`, for the loyalty return link.**
One nullable column, one partial index. Additive only: ADD COLUMN /
CREATE INDEX.

    ALTER TABLE loyalty_ledger ADD COLUMN return_id INTEGER

This cashes in the deviation recorded under v27 below, in the section headed
"ONE DEVIATION FROM THE DECISION ABOVE". The v27 decision said the returns
reversal rows would be "linked to the return rather than to the original sale".
They were not: `loyalty_ledger` had a `sale_id` column and no `return_id`, so
both reversal rows went in with `entry_type='adjust'` and `sale_id` NULL, and
the tie to the return survived only in the audit log. That was a defensible
scope call at the time -- v27 was already claimed and shipping -- and it left a
real cost written down: the ledger alone cannot answer "which return reversed
these points", so anyone auditing a disputed balance has to join through the
audit log to find out. This is the version that opens for it.

INTEGER, not TEXT: `returns.id` is `INTEGER PRIMARY KEY AUTOINCREMENT`
(schema.py), so the column matches the key it points at. It is deliberately
NOT a declared FOREIGN KEY, matching how `loyalty_ledger.sale_id` is already
written and the convention the transfers tables' own commit spells out -- a
sync apply must never fail on a referential technicality.

### No backfill, and that is the honest answer, not a shortcut

Existing `adjust` rows CANNOT be linked retroactively. The information needed to
do it lives only in the audit log's free-text detail, and parsing prose to
manufacture a foreign key would produce rows that look authoritative and are
guesses. So pre-v29 reversal rows keep `return_id` NULL, and NULL here means
exactly one thing: "written before this column existed, tie is in the audit
log". Every reversal written from v29 onward carries the real id.

That makes NULL ambiguous in one narrow way worth stating rather than
discovering: a NULL `return_id` on an `adjust` row means either "pre-v29" or
"an adjustment that was not a return at all" (a manual correction). The
`entry_type` does not separate those two. It is not worth a second column or a
sentinel -- the created_at of the migration separates them for anyone who ever
needs to, and inventing a sentinel value here would repeat the mistake the
`LOCAL_STATE_CORRUPT` guard made: a value that can also arrive legitimately.

The partial index is `WHERE return_id IS NOT NULL`, matching
`idx_loyalty_ledger_uid`'s shape from v27: the overwhelming majority of ledger
rows are earns and redeems that will never carry one, and indexing their NULLs
buys nothing.

---

**CLAIMED: retail v27, by `feat/launch-readiness`, for loyalty redemption.**
One new table, two indexes, one column on `sales`. Additive only:
CREATE TABLE / CREATE INDEX / ADD COLUMN.

    loyalty_ledger  -- immutable +/- entries per customer; earn, redeem, adjust
    ALTER TABLE sales ADD COLUMN points_redeemed_amount REAL DEFAULT 0

The table shipped first (e6debe0). The column is claimed here BEFORE the API
work that needs it, rather than being appended to a released version later --
v27 is not released, so widening the claim is honest; widening it silently
would not be.

### Redemption is a TENDER, not a discount, and that decides the column

Two models were possible and they are not equivalent.

As a DISCOUNT, points would reduce the taxable base. That under-declares VAT
and changes what the JoFotara e-invoice reports about a mandated tax document.
Wrong shape for Jordan.

As a TENDER, the invoice total and its tax stay whole and points simply settle
part of what is owed, alongside cash. The e-invoice remains an ordinary
full-value invoice, and the arithmetic in `calculate_line` is untouched -- no
risk to the per-line pricing every money test already pins.

The tender model then forces the column. `create_sale`'s ledger records the
amount actually RETAINED, which feeds the daily cash summary and the drawer
close. If points silently inflated `amount_paid`, the drawer would expect cash
that was never taken and the till would fail its own Z-report every time a
customer redeemed. So the redeemed value must be recorded DISTINCTLY from cash
rather than folded into `amount_paid`.

Note also that `create_sale` ignores every top-level money field a client
sends -- only per-line `discount_pct` is trusted as commercial intent -- so a
redemption amount cannot simply be posted. It is authorised server-side
against the ledger balance, and `CAP_DISCOUNT` is the capability, whose stated
authority is already "give value away with no matching payment".

### RETURNS AGAINST A SALE THAT USED POINTS -- decided, not yet built

Recording the decision now because the shape of the answer is not obvious and
an improvised one loses money in one direction or goodwill in the other.

A returned sale has TWO loyalty consequences and both are owed:

  1. Points REDEEMED on that sale must come back. The customer paid with them.
     Refunding the cash and keeping the points is taking payment twice.
  2. Points EARNED on that sale must be clawed back. Otherwise a customer can
     buy, earn, return, and keep the points -- a free points generator that
     costs the shop real money at the next redemption.

Both are ledger rows, positive and negative respectively, linked to the return
rather than to the original sale. That is precisely why the ledger exists: the
same operation against a decremented integer would be an unauditable guess,
and could drive a balance negative with nothing to explain it.

PARTIAL returns settle proportionally, on the returned VALUE rather than the
line count -- returning the cheap half of a basket must not refund all of the
points. A claw-back must be allowed to take the balance below zero rather than
silently clamping at zero: a shop needs to see that a customer is in deficit,
and clamping would let the buy-earn-return cycle mint points one unit at a
time. Whether the till then refuses to sell, warns, or absorbs it is a policy
question for the owner, not a silent default.

BUILT, in `create_return`, inside the same transaction as the refund itself.
The proportion is taken on the returned VALUE:

    proportion      = min(1, refund / original_sale_total)
    earn_clawback   = earned_on_sale   * proportion
    redeem_giveback = redeemed_on_sale * proportion

Both source figures are read fresh from `loyalty_ledger` filtered by the
original `sale_id`, never from `sales.points_redeemed_amount`, so several
partial returns against one sale each take their own share of the FULL
original amounts and sum correctly instead of double-reversing.

Double- and over-returns are already refused upstream by `create_return`'s
existing remaining-returnable check, so the reversal inherits that guard and
adds none of its own. A walk-in sale and a pre-feature sale with no ledger
rows both no-op.

### ONE DEVIATION FROM THE DECISION ABOVE, recorded rather than glossed

The decision said the reversal rows would be "linked to the return rather than
to the original sale". They are not, quite. `loyalty_ledger` has a `sale_id`
column and no `return_id`, so both rows are written with `entry_type='adjust'`
and `sale_id` NULL, and the tie to the return lives only in the audit log
entry, which now carries the clawback and giveback amounts.

That is a defensible scope call -- adding `return_id` is a schema change and
v27 was already claimed and shipped -- but it has a real cost: the ledger
alone cannot answer "which return reversed these points". Anyone auditing a
disputed balance has to join through the audit log to find out. Adding
`loyalty_ledger.return_id` is the clean fix and belongs to whichever version
next opens for a schema change.

### Why a ledger and not the column that already exists

`customers.loyalty_points` has been incremented on every sale since v1 and
NOTHING can spend it: zero redemption routes, zero `redeem` in the backend.
A shop that switches loyalty on accrues a promise it cannot honour, which is
worse than not having the feature.

The obvious fix -- a route that decrements that column -- would ship a
double-spend on any shop with two tills. `total_spent` and `loyalty_points`
are ACCUMULATORS that sync deliberately never carries (`SYNCED_CUSTOMER_FIELDS`
is name/phone/email/address/status; see sync_service.py's customer branch and
create_sale's own comment). They are excluded on purpose: last-write-wins on an
accumulator loses value, exactly as it would for stock. So points earned on
till A are invisible to till B, and the same balance could be redeemed on both.

An immutable ledger does not have that problem, and it is the mechanism this
codebase already uses for the same shape: `inventory_movements` alongside
`inventory_balances`. A ledger row is written once and never updated, so it
syncs safely under last-write-wins; the balance is the SUM. Redemption becomes
"insert a negative row", which cannot double-spend once the rows converge, and
is auditable in a way a decremented integer never is.

`customers.loyalty_points` stays as the fast read path and keeps its existing
no-row_version-bump, no-sync-emit rule (mutation-proved by
`test_loyalty_accumulator_sale_does_not_bump_row_version_or_emit`). It becomes
a cache of the ledger rather than the source of truth. Existing balances are
backfilled as one opening entry per customer so no shop loses points it has
already promised.

Design note: earn rate is currently hardcoded `int(total / 10)` in create_sale.
The ledger records what was actually granted, so changing the rate later cannot
retroactively rewrite history.

---

**CLAIMED: retail v26, by `feat/launch-readiness`.** Four new tables, one column
on an existing money table, four indexes. All additive: CREATE TABLE / ADD COLUMN
/ CREATE INDEX only.

    modifier_groups          -- "Size", "Extras", min_select/max_select
    modifier_options         -- "Extra cheese" +0.50, price_delta and cost_delta
    product_modifier_groups  -- attaches a group to a product
    sale_item_modifiers      -- the SNAPSHOT, sale_item_promotions' sibling
    ALTER TABLE return_items ADD COLUMN sale_item_id INTEGER  -- nullable

Design: `docs/launch-readiness/variants-and-modifiers-design.md` §4.

### Why this is a different mechanism from variants, not the same one

A variant (Red / Large) has its own SKU, barcode, price and STOCK. It is a thing
you COUNT, so it is a product with a parent (v25) and it reuses the inventory
subsystem.

A modifier (no onion, extra cheese +0.50) has NO stock. It adjusts a price and it
must print on a kitchen ticket. It is a thing you SAY ABOUT an item. Forcing both
through one mechanism would give half of them a stock ledger that lies.

### UUID ids and row_version from day one — correcting my own v23 work

The three config tables carry client-generated UUID ids and `row_version` from
the start, deliberately unlike the promotions tables I shipped in v23, whose
autoincrement ids permanently lock them out of sync without a migration.
`sale_item_modifiers` carries a `uid` under a partial unique index, the v13
convention.

Wave 1 does not turn sync ON for them. It makes it possible without a second
migration, which v23 did not.

### THE MONEY-PATH DEFECT THIS ACTIVATES — and the fix that must not be cut

`create_return` matches a returned line with

    SELECT ... FROM sale_items WHERE sale_id=? AND product_id=?

and `fetchone()` (retail_api.py:5245). If one sale holds TWO lines for the SAME
product at DIFFERENT prices, that returns an arbitrary one and refunds ITS price.

Safe today only because the POS cart merges same-product lines -- an invariant
nothing enforces and nothing documents. **Modifiers make duplicate-product lines
the COMMON case**: a burger with cheese and a burger without are one product, two
lines, two prices.

So the fix ships WITH this wave, not after it: `return_items.sale_item_id` makes
a return address a specific LINE, and the resolver must REFUSE an ambiguous
match rather than guess. Nullable, because every existing return row legitimately
predates it.

This is the piece most tempting to cut under time pressure and the one that
silently refunds the wrong amount if it is.

### pricing.py stays untouched, for the third wave running

A line's selected modifiers resolve to an EFFECTIVE UNIT PRICE fed through the
existing `calculate_line`. Returns, `top_products` and the sale_item sync payload
all re-read `sale_items.unit_price`, so all three stay correct with no changes.
Promotions (v23) then discount the modifier-inclusive price, which falls out of
the composition and is the right answer.

### Decided, not dodged

Modifier cost is a margin-only snapshot with NO stock movement. Depleting
ingredients is a recipe/BOM engine; doing it for modifiers alone would produce a
precisely-wrong half-ledger, which is worse than an honestly absent one.

### Deliberately NOT in this wave

Kitchen tickets and split tender/tips. A restaurant is NOT sellable on modifiers
alone -- restaurant-ready is modifiers plus kitchen tickets (~0.5-0.75 of this
wave, depends on it) plus split tender and tips (~0.75-1, independent; the
payments ledger already supports multi-row tender, the gap is create_sale and the
POS screen). Stated so the owner sequences with his eyes open rather than
discovering it after this lands.

## 2026-08-31 — the restaurant roadmap, DECIDED by the owner (planned, not scheduled)

Three decisions from him, recorded together because each one changes the next.

**1. Same product, two EDITIONS — retail and restaurant.** Confirms the
recommendation in `docs/launch-readiness/restaurant-edition-plan.md`. Not a
separate product code: the launcher refuses anything that does not answer
`AURA_RETAIL`, and the assertion verifier raises `ASSERTION_PRODUCT_MISMATCH` per
check-in, so a distinct SKU code would mean a forked build that gates nothing.
An edition is a per-install setting (defaults, visibility, demo preset) plus an
Owner catalog plan row -- deliberately NOT entitlement wiring, since
`required_entitlement` has zero production callers and the owner's model has no
tiers.

**2. LAN-local operation.** Tablets, tills and kitchen on one shop wifi, still
serving when the internet is gone. Designed in
`docs/launch-readiness/lan-restaurant-design.md`: a site relay on the main till
speaking the existing push/pull contract, QR pairing with a key pin, an
Owner-signed device roster, and a forwarder to the cloud when it returns. Every
device stays a FULL install, so hub-down means convergence pauses and nobody
stops selling.

**3. Waiter tablets take table orders through to the kitchen.** This is the one
that moves the roadmap. Asked explicitly, answered yes.

### What decision 3 costs, stated plainly

The table / open-order lifecycle moves from "maybe after pilots" to **committed**.
It is the deepest piece in the restaurant programme and the one most likely to be
underestimated.

In a shop, paying IS the sale: `create_sale` writes a completed transaction. In a
restaurant an order exists FIRST, grows for an hour, moves between tables, splits
across payers, and settles at the end. Nothing today models that:

* `held_sales` is real and works, but is deliberately LOCAL-ONLY (not a synced
  entity type) -- a waiter's tablet and the kitchen cannot share a held sale;
* a credit sale needs a named customer, which a walk-in table does not have;
* the money path assumes one settle event per sale.

So this is a new synced entity with its own lifecycle, not an extension of
`held_sales`. It also depends on LAN sync existing first, because a table order
that only lives on one tablet is useless.

### The sequence, as it now stands

    go-live work still owed  (clean-machine test, droplet setup)   <- gates everything
    R1  counter service      modifiers (v26, in flight) + kitchen tickets
                             + split tender/tips + takeaway tag
    R-LAN                    site relay, roster, pairing, forwarder,
                             AND roster-aware pruning on the Owner side
    R2  table service        synced open orders, the committed piece above
    ---
    inter-branch transfers   (v24, reserved by name) after the above
    Aura Clubs               still deferred

Rough shape: R1 is about 1.5-2 waves after modifiers lands; R-LAN is 3-4; R2 adds
2-3. Restaurants outrank transfers because the investor model already counts
restaurants and cafes inside Aura Retail's 1,725 year-one licences at 250 JOD per
device -- this defends planned revenue rather than adding a line to the plan.

### Still open, and only the owner can answer

Tips policy (blocks the split-tender wave's drawer and Z-report design); whether
a kitchen DISPLAY device pays its own 50 JOD once it exists (the printed ticket in
R1 does not); and the recurring-cost question from
`docs/owner/one-time-pricing-design.md`, which gets sharper here -- a LAN
restaurant may never touch the cloud, and it is the heaviest device count under
lifetime pricing.

## 2026-08-31 — the restaurant programme is DEFERRED WHOLESALE

The owner, directly: *"for the restaurant's version we will decide everything
later like the pricing the how the when just keep in mind."*

So every restaurant decision is deferred — pricing, sequencing, timing, and the
open questions the designs above raise (tips policy, whether a kitchen display
pays its own device fee, the recurring-cost question). **Do not re-raise them
unprompted.** They are recorded where they can be found when he wants them.

What the three design documents remain good for: they are REFERENCE, not a
schedule. Nothing in them is committed work, and no schema version is claimed for
any restaurant wave beyond v26 (modifiers), which is in flight and is useful to
retail on its own — a shop selling coffee with a choice of milk needs modifiers
just as much as a cafe does.

The technical shape stands and does not need re-deriving when it comes back up:
same product in two editions, LAN site relay on the main till, every device a
full install, table orders as a new synced entity after LAN. Those were
established against the code, not assumed, and the verification is in the
documents.

**What is NOT deferred**, and remains the actual gate on revenue: the
clean-machine test and the droplet setup in `docs/release/go-live-runbook.md`.
Nothing in the restaurant programme matters until an install can be sold at all.

## 2026-09-01 — Owner: the customer dropdown silently caps at 200, and one of them takes money

**Found, verified, NOT fixed. It is the collaborator's file and the right fix is
a judgement call about scale, not a mechanical change.**

Three customer pickers in `owner/app/commercial_sales/routes.py` are built with

    select(Customer).order_by(Customer.legal_name).limit(200)

at lines 147 (`new_quote_form`), 165 (`create_quote_route`) and 572
(`new_payment_form`).

Past 200 customers, each dropdown silently shows only the first 200
ALPHABETICALLY. There is no message, no truncation notice, and no search. The
operator simply cannot find the customer, and the natural conclusion is that the
customer does not exist.

**`new_payment_form` is the one that matters.** A customer whose legal name sorts
after the 200th pays, and the operator cannot record it against them. Money
arrives that cannot be booked. On a plan of 2,750 licences in year one, 200
customers is months away, not years.

### Why this is NOT already fixed

`commercial_sales/list_queries.py` exists precisely to kill this pattern and its
docstring says so -- it replaced the unconditional `.limit(200)` with real
pagination across the seven commercial-sales LIST screens.

But a list and a dropdown are different problems: you cannot paginate a
`<select>`. These three sites were not part of that fix and cannot be fixed the
same way, which is exactly why they survived it. A fix that made them paginated
would still leave the operator unable to reach customer 201.

### The options, none of them mechanical

* A searchable / typeahead customer input, which is the real answer and the
  largest change.
* Keep a bounded list but make truncation VISIBLE and add a search fallback --
  a shop that cannot find a customer must at least be told the list is partial.
* Remove the cap. Simplest, and adequate for a long time at this business's
  scale, but it moves the problem rather than solving it and gets slower quietly.

Deliberately not chosen here. It belongs to whoever owns `owner/`, and the right
answer depends on how many customers they expect a single Owner instance to
carry -- which is a business question.

**Not touched, on purpose.** `master` lags well behind the owner/ feature
branches, and a wide edit in their most active file is exactly the collision this
branch has avoided all week.

## 2026-09-01 — audit-log writes fail silently, by design, with no signal

Found while sweeping for swallowed exceptions. Recorded, then **FIXED the
same day** — see "Fixed" at the end of this section.

`_audit()` (retail_api.py ~1002) and `_sec_audit`'s call sites wrap the INSERT in
a bare `except Exception: pass`. Nothing is logged, counted or surfaced.

**The trade is defensible and probably right:** a sale must not fail because the
audit table is full, locked or corrupt. Refusing to sell in order to record that
you sold is the wrong answer in a shop.

**What is not right is that the failure leaves no trace at all.** Audit exists
for accountability -- "who changed the price", "who approved that variance", and
the business-day change whose own comment says "the numbers changed and nobody
touched a sale has to have an answer". If those writes start failing, the shop
keeps operating and keeps believing it has a trail. The gap is discovered when
somebody goes looking for an entry that was never written, which is exactly the
moment it is most expensive.

The fix is small and does not change the trade: keep swallowing, but log the
exception, so a silent failure becomes a discoverable one. Anything stronger --
failing the operation, retrying, queueing -- would be worse for the reason above.

Not fixed here only because retail_api.py was being read by a running full-suite
sweep at the time. Small, safe, and worth doing.

### For calibration, since sweeping for this class turned up mostly good news

Zero single-line `except: pass` anywhere in the retail backend or
commercial_runtime. Of the 25 multi-line ones, the sampled majority are
legitimate -- `except FileNotFoundError: pass` around an idempotent `os.remove`
and similar. The audit sites are the ones worth acting on.

### And one thing checked that is NOT a defect

`list_customers` caps browsing at 200 (ordered by total spend), which looked at
first like the same silent-truncation defect found in Owner's dropdowns the same
day. It is not: the `?q=` branch has NO limit and searches the whole table
server-side on name, phone and email, so every customer stays findable. Owner's
dropdowns have no search at all, which is precisely what makes those unreachable.
Recorded so nobody re-raises it.

### Fixed

`log.exception` before the swallow, at `_audit()` and the three `_sec_audit`
sites that swallowed (tax mode, business day, branding). The fourth `_sec_audit`
site (~9640, demo-wipe) was deliberately left alone: it sits inside an error
handler that already returns 500, so it swallows nothing.

The trade is unchanged — the write still cannot fail a sale. Only the silence is
gone.

`details` is deliberately **not** logged: it carries prices, customer
identifiers and variance amounts, and the log has a wider audience and a longer
life than the audit table that value was bound for. Action/entity/id are enough
to find the missing row.

`retail_audit_write_failure_test.py` pins both directions, which is the part
worth keeping. A test that only proves "a failure gets logged" is satisfied by an
implementation that logs unconditionally, or by one that *raises* — so the
success half is pinned too: an ordinary write must land its row **and** stay
silent. Measured 2 failed / 2 passed before the fix and 4 passed after, with five
mutations all caught, including "stops swallowing — raises instead", which is the
half that would actually cost money and the half a failure-only test cannot see.

## 2026-09-01 — the unreachable-feature list reaches zero

`KNOWN_GAPS` in `retail_route_reachability_test.py` is now **empty**. It is kept
in place, empty and commented, so filing the next one stays the cheap option.

Six complete-backend-no-doorway defects were found and closed this cycle. All
six had the same shape: a route written, validated, capability-gated, in several
cases audited and covered by its own test file — and no client anywhere that
could call it. None of them was findable by reading the backend, because nothing
about the backend was wrong.

The two closed today:

* **`settings/business-day`.** The boundary every report, the dashboard and the
  shift Z-report group by. With no client able to set it, every install ran on
  `UNCONFIGURED_BUSINESS_DAY`, which is **not** midnight UTC — it buckets each
  row on the clock of whichever DEVICE wrote it. Defensible for one till; wrong
  the moment a second device syncs, because two terminals whose clocks disagree
  file the same evening under different days. A shop trading past midnight could
  not move its boundary at all. Now a card in Admin Center.
* **`reports/email`.** Queues a summary report. Its WhatsApp twin
  (`reports/whatsapp`) had an on-demand trigger in `whatsapp.js` all along; the
  email side had a full settings screen — recipients, retry policy, an outbox
  drainer — and nothing that could queue a report. Now a card on Email
  Notifications.

### The guard earned its keep twice, in opposite directions

Both closures were confirmed by `retail_route_reachability_test.py` FAILING with
"these routes are listed as unreachable but a client now calls them". That is
the guard working, and it is independent evidence the wiring is real rather than
a claim in a commit message.

It also caught a defect in its own sibling. The first version of the business-day
render check asserted `html.includes('business-day-card')` — which
`'business-day-card-REMOVED'` satisfies as a substring. It went green against a
card that had been renamed *and* hidden, and only mutation testing found it. A
reachability guard that cannot tell "present" from "present-ish" certifies the
exact bug it exists to catch. Both guards now match the whole `id` attribute and
reject a card rendered with `display:none`.

### One defect was introduced and caught in the same day

The business-day card shipped green, with seven passing cases, and was wrong.
On an install with no `tzdata` the timezone field renders disabled — correct —
but Save still posted `business_timezone: null`, which the route treats as
CLEARING the key. So an owner opening Settings to change the *hour* would wipe a
declared timezone they were never shown, surfacing much later as "the reports
moved". Fixed by omitting the key entirely while the field is disabled; the route
accepts a partial payload, so the hour is still savable.

Found by re-reading the change after it was committed and green — not by a test
and not by running it. None of the seven cases had any reason to model an install
whose `tzdata` went missing *after* a zone was declared, which is the only state
where the bug lives. Recorded because "the tests passed" was, again, not evidence.

### The reverse direction, checked once and deliberately NOT made a test

`retail_route_reachability_test.py` proves every route has a caller. It says
nothing about the opposite failure: a client calling an endpoint that does not
exist, which is a button that 404s and which nothing in the suite would notice.

Checked by hand on 2026-09-01, across every shipped client (the desktop frontend
JS and Android's network layer) against every retail-side blueprint:

    routes discovered                         133 distinct signatures
    distinct /api/ literals in the clients    130
    calls with no matching route                0

The only two that survived the matcher were `/api/sub/${systemId}/ai/chat` and
`/api/sub/${active}/demo-wipe`, both assembled at runtime; `systemId` resolves to
`retail`, and both routes exist (`retail_api.py:10825` and `:9621`).

**Not committed as a guard, on purpose.** The matcher needed three rounds of
correction — an incomplete blueprint list, then a too-narrow decorator regex,
then a missing set of URL prefixes — and each round produced a screenful of
confident false alarms. A guard that cries wolf gets suppressed, and a suppressed
guard is worse than none; that is this repo's own stated principle, in this very
file's forward guard. Keeping every mount prefix registered by hand is exactly
the maintenance burden that turns a guard into noise.

Recorded as a measurement, not a mechanism. Worth re-running by hand if the
client ever starts constructing paths more dynamically than it does today.

## 2026-09-01 — the product was photographed for the first time, and it was wrong

Everything in this repo had been verified by reading code or running tests.
Nobody had ever looked at the running product. Playwright was installed on this
machine the whole time.

The first sweep found four defects in about an hour, while **43 JS suites, 113
Python files and 238 Android tests were green**. Harness kept at
`scripts/ui-sweep/`, because the tool that finds this class of bug is worth more
than the individual fixes.

### Retail — fixed

* **The till showed dollars in a dinar shop.** `#pos-sub`/`#pos-tax`/`#pos-total`
  were seeded with the literal `"$0.00"` and only corrected by `_recalc()`, which
  runs on cart mutations. An empty cart never mutates — so the POS **at rest**,
  what a cashier looks at all day between sales, read `$0.00` beside a button
  reading `Charge — JD 0.000`. Measured, both states, on the running app.
* **All four chart axes hardcoded `'$' + v`.** The dashboard's Revenue-Today axis
  read `$1 / $0.8` directly under a `JD 0.000` headline. Now one shared
  `axisMoney()` that degrades to the bare number when the currency is not
  resolved yet — an unmarked axis is ambiguous, a wrongly-marked one is a misread.
* **`step="0.01"` on cash tendered**, which makes a fils amount a step mismatch
  on a three-decimal currency.
* **The admin-device banner filled ~45% of a 390px screen**, wrapping to one word
  per line over the till, because `flex:1` gave the message a 0% basis.

`retail_currency_surface_test.js` now asserts on what the SCREEN renders rather
than on what the formatter returns. Six mutations, all caught, two of which
restore the shipped bugs verbatim.

**Why none of this was caught:** the currency formatter was correct and
unit-tested throughout. The bugs lived in an initial-HTML literal, in a callback
Chart.js owns, in an input attribute, and in a flex basis. A money surface is not
covered because the money function is covered.

### Owner — measured, NOT fixed

`owner/` belongs to the other collaborator's active branches, so these are
recorded rather than changed.

* **Eight KPI labels each render TWICE on the dashboard**, counted rather than
  eyeballed: Active customers, Active subscriptions, Expiring in 30 days, Open
  items awaiting action, Active licences, Active installations, Renewals awaiting
  finance approval, Activations pending manual review. Plus a duplicated
  "Overview" heading and "New subscription" action. This is the concrete version
  of the owner's own complaint that Owner carries "a bunch of useless stuff" —
  with real data those are eight numbers a reader must reconcile against
  themselves.
* **The "Show" control overflows the password field** on the login page, clipped
  at the right edge.
* **The login page is very sparse** next to Retail's first-run screen — flat,
  utilitarian, a large empty area below a small card. Functional, not finished.

### Owner — what is genuinely good, and should not be "simplified" away

* MFA enrolment is **mandatory on first login with no skip**, and hands off to a
  recovery-codes page before the app. Correct posture; it is also a hard gate for
  any automation, which is the point.
* The server is fast: `GET /auth/login` answers in ~125ms. An early report that
  login "hung" was the harness posting to `/` (405) and then calling
  `page.content()` mid-navigation — not Owner.
* Grouped dark sidebar, Ctrl+K search, quick actions, Light/Dark/System, EN/عربي,
  and a seven-step first-run tour.

### Still unphotographed

The Android app. Same treatment is owed to it.

## 2026-09-02 — the phone till QUOTES a different number than it CHARGES

Found while auditing the Android app against a screen recording from a real
device. Recorded, **not fixed**, because what number a cashier should be shown
is a product decision rather than a code one.

### What is fine

The Android POS sends only line items — `SaleItemReq(id, qty)`, no prices, no
total (`RetailScreens.kt`, `createSale`) — and takes the backend's response as
authoritative. That is deliberate and correct (Wave 0, AUDIT-002/003,
`docs/architecture/financial-authority-contracts.md`). **No money is lost, and
nothing is mischarged.**

### What is not

The on-screen PREVIEW is a naive sum:

```kotlin
val total = cart.entries.sumOf { (id, qty) -> (byId[id]?.sell_price ?: 0.0) * qty }
```

No tax. No manual discount. **No promotions.** And that number is what the
Charge button shows: `tr("Charge") + "  " + money(total)`.

Meanwhile `create_sale` (retail_api.py:4251) runs
`promo_engine.resolve_line_discount_pct` for every line, and applies tax.

So on any shop with VAT configured or a promotion running — i.e. the normal
case once schema v23 shipped — **the cashier reads one price to the customer
and the receipt prints another.** The desktop till does not have this problem:
`_recalc()` mirrors the server's promotion and tax rules client-side, so its
preview matches.

The existing code comment even names half of this ("previewTotal never
included tax or a server-validated discount at all") but treats it as closed
because the *result* is now authoritative. The result is. The *quote* is not.

### Why this was not just fixed

Three options, and the cheap one is wrong:

1. **Mirror the pricing rules in Kotlin.** This is what the web does. It would
   be a THIRD copy of `resolve_line_discount_pct` + tax-mode logic, and the
   web's own comment says its copy must match the server "EXACTLY". Three
   copies of money logic is how this project got a till that showed dollars in
   a dinar shop. Rejected.
2. **Ask the server for the quote.** Architecturally right, and it would serve
   both clients. But a round-trip per cart change is poor at a till and fails
   outright when the shop is offline, which is precisely when a phone till
   earns its keep. Needs a real decision about offline behaviour.
3. **Make the preview honest** — present it as a pre-tax, pre-discount
   subtotal and stop putting an exact figure on the Charge button. Cheapest,
   removes the false promise, and makes the screen tell the truth. But it also
   makes the phone worse at the one thing a cashier wants: knowing what to
   ask for.

Owner's call. Recorded here rather than guessed at.

## 2026-09-02 — Android is missing seven desktop features

Counted, not estimated: the Android nav graph registers 20 routes; the web
shell offers 19 nav destinations, and they are **not the same set**.

Present on desktop, absent from the phone entirely:

* **WhatsApp reports** — no screen, no route, no API call
* **Email notifications** — same
* **Promotions** (schema v23) — one incidental reference in `AuraApi.kt` and
  nothing in the POS or cart; see the entry above for the money consequence
* **Branches management** — only the per-device branch PIN inside Settings
* **Audit log**
* **Stock accuracy**
* **Exceptions queue**

Two things the owner believed were missing are actually present and were
probably invisible for another reason: **Employees** is a real route, gated on
`RetailSession.isAdmin`, and **Settings** has always been in the More list.
Their APK also predates the Android currency fix, which is why the recording
shows "$4.00" — current source calls `money(p.sell_price)` with
`Currency.apply()` wired from `/settings/tax` (`AppRoot.kt:104`), so a fresh
build renders `JD 4.000`. Unverified on a device.

## 2026-09-02 — the AI assistant is NOT broken; the APK was just old

The owner, of the app on their phone: "the ai doesn't work obviously". An
investigation concluded, with a full and otherwise-correct trace through every
hop, that `AURA_AI_BEARER_TOKEN` is empty in every build, so "every APK --
debug or release -- ships with a dead assistant."

**That conclusion is wrong, and the way it went wrong is worth keeping.** The
trace reasoned from `android/aura-retail/local.properties` (which holds only
`sdk.dir`) and from CI (which never builds Android), and inferred an empty
token. It never checked the artefact. `build.gradle` resolves the secret from
three sources, and the third is an environment variable — which IS set on this
machine. The generated `BuildConfig.java` from a local debug build carries a
real 64-character token.

Reading the inputs is not the same as measuring the output. Same lesson as the
KPI grid fix earlier this cycle, which also looked right and did nothing.

### What was then measured, against the live service

Using the exact library and settings the backend uses (`requests.post`, TLS
verification ON — the route passes no `verify=`, so it verifies):

```
model phi3.5:3.8b   HTTP 200 in 9.8s      (verified TLS)
model phi3.5:3.8b   HTTP 200 in 15.4s     (first call; 13s of it model load)
model llama3        HTTP 404 {"error":"model 'llama3' not found"}
```

So: host reachable, certificate accepted, bearer token authorised, the
configured model (`AURA_AI_MODEL_NAME`, default `phi3.5:3.8b`) present and
answering. The 404 is only what a WRONG model name returns, which is itself
useful — it proves auth succeeds before model lookup.

**Conclusion: the assistant works. The owner's APK predates the token being
available in this build environment**, which they had already suspected ("this
apk is not the newest"). A freshly built APK from this machine embeds it.

### What IS real about the AI

Latency. ~10-15s here for a trivial prompt, and `retail_api.py`'s own comment
records ~35s uncapped for a realistic one. `docs/release/go-live-runbook.md`
already lists this as a known gap ("roughly 30 seconds per answer... weakest
at Arabic"). A cashier will read that as broken even when it is working, so
the honest fix is either a faster host or a UI that sets the expectation --
not a code bug hunt.

Minor, observed: asked in English to "Say OK", the model replied in Spanish.
phi3.5 language drift, consistent with the runbook's note about Arabic being
its weakest case.

## 2026-09-02 — the "LIVE" badge is gone, and a contrast finding I did NOT fix

### The LIVE badge, removed

The owner, looking at the running app: "check the usless stuff and remove them
like the live thing i dont know for what its there."

They were right and the code agrees. `_updateLiveBadge(true)` fired after a
section rendered; `(false)` fired only in the catch, which immediately
replaces the whole screen with a "Failed to load / Retry" panel. So the badge
was visible in every state a user could actually observe, and said nothing.

It had one honest job once. `retail_dashboard_error_propagation_test.js`
records the original defect: `_renderDashboard` swallowed its fetch error, so
the badge lit up over a dashboard frozen on placeholders. Fixing THAT — by
rethrowing, which that test now pins — removed the only condition under which
the badge could disagree with the screen. The guarantee is tested directly;
the badge was residue.

It also carried costs both a dark theme and an Arabic layout would have had to
pay: `margin-left` (physical, in a product that mirrors), five hardcoded
colour literals, an infinite CSS animation, and header room on a 390px phone
whose header is now a single slim row.

### Android: the Sign In button measures 2.81:1 on a real device. NOT fixed.

First usable Android visual evidence — the owner connected a real Mi Note 10
after the emulator ANRed continuously on this machine. Launch there was
**2.9 seconds** (`am start -W`, TotalTime 2912), not the ~12.7s the loaded
emulator reported, so the earlier cold-start figure should not be quoted as an
app measurement.

Sampling the login screenshot (darkest glyph pixel against the dominant fill,
51,357 px of it):

    Sign In label vs button fill    2.81 : 1     (WCAG AA needs 3.0 even for
                                                  large text)

That is the primary action on the first screen, failing.

**Why it is recorded and not fixed.** The obvious change — make the label
white — computes WORSE, not better:

    theme primary rose-500 (244,63,94)
      current onPrimary (0,37,31)      4.45 : 1
      white                            3.67 : 1
      near-black (11,11,15)            5.35 : 1

Against the theme's DECLARED colours the current on-colour is fine. The
device rendered the fill as (175,45,67) — about 72% of the declared
rose-500 — so something in the rendering path is darkening it, and dark text
on a darker fill is what loses the contrast. Ruled out: no system colour
filter is enabled on that device (daltonizer off, inversion and night display
unset), and `screencap` reads the framebuffer so panel brightness is
irrelevant.

Not isolated, so not fixed. Guessing at a fix here would have made the button
harder to read, which is the opposite of the request.

Worth noting alongside it: the `Color.kt` names have drifted from their
values — `AuroraTeal` is `0xFFF43F5E` (rose-500), `AuroraCyan` and
`AuroraViolet` are rose-400 and rose-300. The palette became crimson and the
identifiers never followed. The `on*` colours in the dark scheme still carry
teal/blue/violet-era values, which happen to compute acceptably against rose
but are not what anyone would choose deliberately.

### The two surfaces have opposite themes

Confirmed on real hardware: the **Android app is dark**. The **web app forces
light** and deletes any saved preference (`index.html`, and for a documented
reason — see the 2026-09-02 dark-mode work). Same product, opposite
identities, on a shop floor where a manager may hold both at once.

---

## Money precision beyond the cash drawer (2026-09-03)

`_money()` in `products/retail/backend/api/retail_api.py` was hardcoded to
`Decimal('0.01')` for its whole life. `create_sale` had already been corrected
to read the company's currency for exactly this reason -- its own comment says
rounding JOD to two places "silently moved persisted money by up to 5 fils per
line, on the receipt AND in what is filed with the tax authority" -- but that
reasoning never reached `_money()`, which is what the cash drawer, customer
and supplier payments and PO payments all coerce through. So a sale's TOTAL
kept its fils while the PAYMENT recorded against it did not.

`_money(x, currency=None)` is now currency-aware. The default of None
reproduces the old behaviour exactly, which is what makes the change safe:
only the three cash-drawer sites whose defect a test demonstrates
(`opening_float`, `counted`, `variance`) were switched over.

**UPDATE, same day: nearly all of this is now done.** The paragraph below used
to read "roughly 34 other `_money()` call sites still at hardcoded 2dp,
deliberately deferred". Leaving that standing would be the exact staleness
this file keeps suffering from, so here is what actually landed, in three
further waves:

- **Payments and credit balances.** `_record_payment`, `customer_payment`,
  `supplier_payment`, `pay_purchase_order`, `create_purchase_order`,
  `void_payment`, `accept_reorder_request`. Plus `_adjust_credit()`, which
  carried its OWN independent hardcoded `Decimal('0.01')` -- converting only
  the payment amount would have produced a correct-looking response while the
  STORED BALANCE still lost fils.
- **The sale path itself.** `create_sale` quantized its summed subtotal,
  discount, tax and total to hardcoded cents even though it already passed the
  currency to `calculate_line` per line -- so each line was computed at three
  decimals and the sum rounded to two. `paid`, `change` and `balance_due` were
  currency-blind too. Also `create_return`'s refund, `hold_sale`, cash
  movements (float/paid in-out), and both statement `running_balance` figures,
  which used a raw `.quantize(Decimal('0.01'))` that no `_money()` search
  could find.
- **The response boundary**, which would have defeated the rest silently:
  `create_sale` returned `round(change, 2)` and `round(total, 2)`, and
  `create_return` returned `round(refund, 2)`. The database row would have
  been right and the till still told the wrong number.

**Genuinely still deferred**, and now a short list rather than a vague ~34:
the two `_adjust_credit` calls in `create_sale` and `create_return` that omit
`currency` (a documented pre-existing deferral, recorded in that function's
own docstring), and the report/display aggregation sites in receivables,
payables, daily cash and aging, where changing the formatting does not fix a
stored value and would alter SUM rounding.

One knock-on worth knowing: correcting the sale sum exposed
`retail_promotions_test.py`, which computed its expectation via
`pricing.calculate_line` with NO currency (two decimals) and asserted the API
matched. It had been comparing JOD arithmetic against a cents reference and
passing only because the API was also wrong. Both sides now get the same
currency, and the fixture's currency is pinned so the comparison cannot
silently drift again.

### WhatsApp's shift-close report formats money with `:.2f` -- CLOSED same day

WhatsApp rendered every money placeholder with a hardcoded `:.2f` while email
was currency-aware, so one JOD close read `JD 12.350` by email and `12.35` by
WhatsApp. That is worse than either channel simply being wrong: a shop can run
both, and two different "correct" answers to "how much was in the drawer"
leave the owner no way to tell which to believe.

Fixed by EXTRACTING rather than copying: `core/retail/money_format.py` now
owns `company_currency()` and `format_money()`, and both hooks call it, so the
two cannot drift apart again. Eight `:.2f` sites converted -- the shift-close
trio, the daily-sales trio, the two AR-overdue totals. The low-stock alert's
`on_hand`/`reorder_level` keep `:g`; those are unit counts, not money.

Worth remembering from that work: the first mutation proof came back GREEN on
a broken code path, because the fixture opened and closed the drawer with the
same amount, so a still-correct figure satisfied the check meant for the
broken one. The test was tightened (two different amounts, indexed per-field
equality) rather than accepted.

## Automatic email had one trigger; now it has two (2026-09-03)

Recorded because the asymmetry is easy to re-introduce. WhatsApp has four
report types and two automatic triggers. Email had ONE -- the low-stock alert
in `reorder_hook._maybe_queue_low_stock_email` -- so a shop that chose email
instead of WhatsApp got nothing when a till was closed. Shift-close is now
wired for email too.

**Still WhatsApp-only:** the daily sales summary and the AR-overdue notice.
Both are `whatsapp_hook` trigger points with no email counterpart, and both
are the same shape as the shift-close one that was just closed.

## Android sync: the roles are split, and that is deliberate (2026-09-03)

Written down because it looks like a bug twice over and is not.

`android/aura-retail/app/src/main/python/main.py::start()` forwards
`AURA_OWNER_LICENSING_URL`, `AURA_INTERNAL_SHARED_SECRET`,
`AURA_AI_BEARER_TOKEN` and both WhatsApp keys into the embedded Python
environment -- but NOT `AURA_SYNC_RELAY_URL`. That omission is correct. On
Android the embedded Python never holds the device signing key, so `app.py`
constructs its `SyncService` with `client_factory=None` and registers only
`make_sync_internal_blueprint`; Kotlin's `SyncCoordinator` makes every signed
call. Forwarding a relay URL into Python would not enable anything -- it would
create a second pusher with no key.

Consequence worth knowing: `/api/sub/retail/sync/health` is served on Android
but describes a service that never runs there. **Any Android sync UI must read
`SyncCoordinator.health()`, not that route.**

## Accounts do not sync to the phone (2026-09-03)

> **STATUS UPDATE, same day.** The BACKEND half of this is now built: the
> Android branch of `app.py` constructs a second `SyncService` over
> registry.db with `REGISTRY_SYNC_ENTITY_TYPES`, and
> `make_sync_internal_blueprint` is parameterised (name + url_prefix,
> defaulted to today's values) so a second blueprint can be registered under
> `/api/registry-sync`. Ten tests cover it, including the allow-half: a `user`
> event must reach registry.db and must NOT be applied to retail.db, and a
> `product` event sent to the registry route must be applied to neither.
>
> **Accounts still do not sync end to end.** What remains is the Kotlin half --
> `SyncCoordinator` driving BOTH streams, each with its own cursor and its own
> outbox/ack endpoints, with per-stream failure isolation so a broken retail
> outbox cannot starve the registry stream. The signing and health plumbing is
> already stream-agnostic; what is missing is any notion of more than one
> stream. The diagnosis below stays for the reasoning; steps 1 and 2 of "what
> closing it takes" are done, 3 and 4 are not.

**The gap, stated plainly: a cashier created on the desktop cannot log in on
the Android app, and an account created on the phone never reaches the
desktop.** Each Android install keeps its own independent user list.

Verified, not assumed:

- Sync is a TWO-STREAM design. `RETAIL_SYNC_ENTITY_TYPES` (category, product,
  customer, supplier, reorder_request, sale, sale_item, payment, return,
  return_item, inventory_movement, branch) rides retail.db.
  `REGISTRY_SYNC_ENTITY_TYPES` = {`user`, `user_permission`} rides
  registry.db, on a SECOND `SyncService` instance.
- `products/retail/backend/app.py`'s **Windows** branch builds both:
  `_sync_service` and `_registry_sync_service`, the latter constructed
  explicitly with `handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES`.
- The **Android** branch (`elif LICENSING_PLATFORM == 'ANDROID'`) builds
  exactly ONE: `_android_sync_service`, over `_sync_get_conn` (retail.db),
  with no `handled_entity_types` argument -- so it takes the default,
  `RETAIL_SYNC_ENTITY_TYPES`, which deliberately excludes `user`. A `user`
  event arriving on Android is dropped by `_apply_event`'s gate.
- Nothing on the Kotlin side compensates: no file under
  `android/aura-retail/.../sync/` or `.../licensing/` references `users` or
  `user_permission` at all, so no activation or onboarding path back-fills
  accounts either.

So this is not a misconfiguration and not a relay-URL problem. The Android
registry stream was never built.

Worth reading alongside `REGISTRY_SYNC_ENTITY_TYPES`'s own comment, which
records the desktop version of this same wound: before stage 3 added
`user_permission`, "a cashier synced to a second till arrived with NO
permissions there -- an account that logs in and can do nothing, which read to
the shop as 'the system is broken'." On Android the account does not arrive at
all.

### What closing it actually takes

Not a one-liner, and not a quick patch worth improvising:

1. `commercial_runtime/sync/internal_routes.py` --
   `make_sync_internal_blueprint` hardcodes both the blueprint name
   (`"sync_internal"`) and the url_prefix (`/api/sync`), so a second
   registration collides on both. Needs both parameterised, with defaults so
   the existing caller and Clinic are untouched.
2. `products/retail/backend/app.py` Android branch -- a second `SyncService`
   over registry.db with `handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES`,
   plus a second blueprint registration under its own prefix.
3. `SyncCoordinator.kt` -- drive BOTH streams, each with its own cursor and
   its own outbox/ack endpoints. The signing and health plumbing is already
   there and stream-agnostic; what is not there is any notion of more than
   one stream.
4. Tests on both sides, including the allow-half: a `user` event must reach
   registry.db and must NOT be applied to retail.db.

Until it is built, the honest description of the Android app is
**single-operator**: it syncs products, sales and customers with the rest of
the shop, but its logins are local to that device.

## What the desktop has and the phone does not (2026-09-03)

The owner, after using a real build: "there isn't anything like the new
updates and features we added the admin the employees any thing else like the
WhatsApp the branches". This is that complaint turned into a checked list, so
it stops being a feeling and becomes work someone can pick up.

FIRST, WHAT IS NOT WRONG. Every one of the Android app's 21 registered routes
has a doorway -- checked route by route against `AppRoot.kt`'s `composable(...)`
registrations and every `onNavigate(...)`/`navigate(...)` call site. There is no
built-but-unreachable screen on the phone, which is this repo's usual defect
shape and is worth stating as a negative result rather than leaving someone to
re-derive it. `employees` also EXISTS on the phone now (admin-gated, in More ->
Team), so that half of the complaint above is already closed.

The gaps below are genuinely unbuilt, not hidden.

| Desktop nav entry | Android | Note |
|---|---|---|
| Promotions | missing | Engine shipped server-side at retail schema v23 and the POS resolves it at checkout, so a promotion CONFIGURED on the desktop does already apply to a phone sale -- the phone simply cannot see or configure one. |
| Branches | missing | `branches` is core to the data model (every row is branch-scoped) and the phone cannot view or switch them. |
| Email Notifications | missing | Settings only. The email itself works on the phone -- same backend, same outbox. |
| WhatsApp settings | missing | Desktop reaches it via `whatsapp.html` off the Settings card, not a nav row. Same situation as email: the sending works, the configuring does not. |
| Audit Log | missing | adminOnly on desktop. |
| Stock Accuracy | missing | ownerOnly, reports capability. |
| Exceptions | missing | reports capability. Pairs with the sync work -- this is where stock exceptions surface. |
| E-invoicing | missing | `einvoicing.js` exists on desktop; no Android route. Opt-in feature, so this only matters to installs that enabled it. |

Barcode Scanner is deliberately NOT on this list: it is `desktopOnly: true` in
the desktop nav because the phone has camera and HID scanning built directly
into its POS screen. That is parity achieved differently, not a gap.

### The shape of the gap, which matters more than the count

Every missing item is CONFIGURATION or REPORTING. Not one is on the money
path: the phone can ring a sale, take payment, open and close a drawer, handle
credit, returns, suppliers and purchase orders. So the honest framing is that
the Android app is a complete TILL and an incomplete BACK OFFICE -- which is a
defensible product position, and a very different statement from "the phone is
missing features". If that framing is accepted, the fix for most of the table
above is to stop implying otherwise rather than to build eight more screens.

Related and recorded separately above — and **superseded on 2026-09-05**, see
the next section: accounts now sync to the phone (a cashier made on the
desktop signed in on the Mi Note 10, and the owner's own account followed a
day later). The sentence that stood here said they did not; it was true when
written. The framing above was then accepted by the owner: the phone is a
till.

## 2026-09-06 — two nights on real hardware: shipped, decided, deferred

Everything in this section was RUN on a real Mi Note 10 and one or two real
desktop tills against the local rehearsal Owner, every result read back from
the artefact, never inferred from a green suite. Full per-line evidence in
`docs/release/sellability-status.md`; the demo sequence in
`docs/release/sunday-demo-runbook.md`.

### Shipped (each with tests that were mutation-proved)

- **A phone can be licensed.** `trust_store.json` was seeded once and shadowed
  every corrected anchor; a guarded add-only re-anchor now recovers the
  stranded path (`TRUST_ANCHOR_READMITTED`).
- **Screen-created employees can use the app.** `mt_require_subsystem` demanded
  a legacy `retail` row nothing ever wrote; the gate is now a derived view of
  the capability codes (`user_holds_subsystem`). This had locked every cashier
  out of every retail route on every device. Record:
  `docs/corrections/identity/employee-locked-out-of-retail-root-cause-analysis.md`.
- **One owner login on every device.** A colliding employee code from another
  device is re-numbered on arrival instead of parked (`_resolve_employee_code`).
- **Shop settings sync** (`retail_setting` events): currency, tax mode, credit
  defaults, business day, branding text. The logo blob deliberately does not.
- **The last currency-blind money figures** (receivable/payable totals, daily
  cash, aging, CSV export) keep the fils.
- **The owner's price list is data in the catalogue and enforced:** two
  devices included (250 JOD), extra device 50 JOD, branch 250 JOD behind a
  real `max_branches` entitlement that rides the assertion with no schema
  change (`flask_guard.make_entitlement_reader`, `create_branch` → `403
  BRANCH_LIMIT`; absent/0 = no limit).
- **A branch can be renamed** (`PUT /branches/<id>`, desktop Edit control);
  the rename converges through the existing `branch` update event.
- Every colour on Android painted from `Color.kt`; a stale test fixture that
  hid 14 sync-service failures for weeks repaired; `retail_sync_relay_url_
  validation_test` repaired (it asserted the lie the APP_NOT_INITIALISED guard
  exists to refuse).
- Ops: `scripts/ops/rehearsal_up.ps1` (whole rehearsal, idempotent),
  `phone_watcher.ps1`/`phone_free_runner.ps1`, `battery_guard.ps1`, and the
  proof scripts under `scripts/ops/`.

### Decided by the owner (2026-09-05)

- The phone is a **till**; the eight back-office screens in the table above
  are added only on request. That table is now a description, not a backlog.
- One owner login everywhere (built, above).
- Prices: 250 base with two devices, +50 per device, 250 per branch.

### Deferred, deliberately

- **Retiring a branch** (only rename exists): touches stock balances, the
  per-device branch pin and open cash drawers — its own design.
- **"Join an existing shop" first run**: a second device still creates its
  own placeholder admin — not because activation needs a signed-in user (it
  does not, measured) but because first run offers no other door. Designed,
  with every premise checked against the code, in
  `docs/launch-readiness/join-existing-shop-design.md`. **Built and proven
  on the desktop** (2026-09-06: the seed row, then the first-run modal's
  "join" mode driven in a real browser on two fresh tills — key in,
  "Connected to your shop", restart, the owner signs in, zero
  `create-admin` calls). **The Android door is built too** (same day:
  `FirstRunDecision`, `JoinChoiceScreen`/`JoiningScreen`, phases
  `JOIN_CHOICE`/`JOINING`; 303 unit tests, decision mutation-proved, APK
  assembled) **and proven on the wiped Mi Note 10 the same evening**: key,
  "Yes — connect to my shop", "Connecting to your shop…", sign-in, the
  dashboard as `ADMIN-0001`; no admin created; second witness by HTTP
  against the handset's own backend (`scripts/ops/phone_join_door.py`).
  Done on both clients. No security change.
- **Themes and brand, 2026-09-07 (owner's ask that night: "bring the night
  mode back, some themes for both, an intro and a logo").** Shipped: five
  themes on both clients — Day, Sand, Calm, Night, Dusk — as token blocks
  held to the dark theme's safety argument, with the desktop's two design
  guards and the phone's parity test running per theme. First cut of the
  brand under `products/retail/frontend/brand/` (mark, app icon, lockup,
  six-second intro; showcase artifact "Aura Brand"). Still open: wire the
  mark into the sign-in and first-run screens and the shell header (the
  bolt and bag icons are still there), give the desktop launcher and the
  phone a splash that plays the intro, and convert the lockup's wordmark
  text to paths so the SVG does not depend on Outfit being installed.
- **Seeded payment-method names stayed English in Arabic mode — FIXED on
  the phone 2026-09-07.** Seen on the phone's Settings and POS cart with the
  app in Arabic (2026-09-06 evening): "Cash / Card / Bank Transfer / Mobile
  Wallet / Check" are `payment_methods` rows seeded by name. The phone
  already routed the chip label through `tr()` but only Cash and Card had
  entries; the three missing entries and the Settings list are in, pinned
  against `retail_api.py`'s `_DEFAULT_METHODS`. The desktop was already
  right (نقدًا / بطاقة / … measured). A shop's custom method names still
  show as typed, which is correct.
- **The default "Main Branch" is a different wire entity on every device.**
  Found 2026-09-06 while proving the join door: a freshly joined till logged
  nine pulled rows whose `branch_uid` "did not resolve to any local branch"
  and were filed under its default branch. Traced through the relay
  (`owner_sync_events`): seven were the phone's own Main Branch sales and
  stock movements, two the desktop's — each install self-heals its Main
  Branch with a fresh `uuid4` (`_default_branch()` / `_resolve_branch_id`'s
  self-heal), so the same physical shop floor has N uids across N devices.
  The fallback maps them all onto the receiving device's branch 1, which is
  the right answer for a one-location shop, so nothing is mis-filed today
  and this is NOT a bug in the fallback. It becomes one the first time
  someone renames Main Branch: the `branch` update event carries the
  desktop's uid, which the phone does not hold, so the phone would gain a
  third branch called by the new name while its own stays "Main Branch".
  Principled fix, deferred: mint the self-healed default branch's uid
  deterministically from the tenant — `uuid5(namespace, f"{company_id}:main")`
  — on every device, plus a one-off migration that rewrites an existing
  random uid on branch 1 to the deterministic one where branch 1 is the
  self-healed row. Needs a schema version claim in writing first (see
  "reserve single-writer resources") and a two-device rename proof after.
- **Per-shop WhatsApp on Android**: credentials are build-time on the phone;
  moot while the phone is a till (reports go out from the desktop), revisit
  if that changes.
- **`licenses.issue` for the SALES role** and **250 JOD per year vs one-time**:
  the owner's two open answers.
- Owner: partial UNIQUE index on subscriptions.sales_order_id (belt-and-braces
  for fulfill_order's advisory lock; needs an Alembic migration, coordinate the
  head first) — see docs/corrections/owner/fulfillment-lock-released-by-nested-
  commits-root-cause-analysis.md
- **Cash-drawer kick, byte layer only (2026-09-08).** Measured: the till prints
  via a hidden iframe + `window.print()` (`subsystem-retail.js::_printReceipt`),
  which sends a rendered page to the OS spooler and never emits the ESC/POS
  drawer-kick bytes a real thermal printer's drawer connector needs — so a
  shopkeeper opens the drawer by hand on every sale today. Built and
  mutation-proved (money precision, both directions of the kick guard):
  `core/retail/escpos_receipt.py` (pure sale-dict-to-bytes renderer — init,
  header, line items, totals via `money_format` for real JOD 3dp precision,
  payment/change, an e-invoicing `GS ( k` QR block, partial-cut, and an
  opt-in `drawer_kick()`) and `core/retail/escpos_transport.py` (Windows RAW
  spooler transport via `ctypes` against `winspool.drv`, guarded to import
  cleanly and fail with a named error off Windows). Still needed before this
  reaches a shop: (a) a settings key for which printer to use, (b) a POS
  code path that calls these instead of `window.print()`, (c) proof on real
  hardware — an actual 80 mm printer and drawer, which nothing in this dev
  environment can supply — and (d) the Arabic raster/graphics path, which
  the text-only byte layer explicitly does not attempt.
- **E-invoicing default-ON: a brand-new shop's very first sale waits until
  the next app start before it submits (2026-09-08).** Now that e-invoicing
  defaults ON (`commercial_runtime/einvoicing/settings.py` DEFAULTS
  ['enabled']='1'), the only two places a company's `OutboxWorker` actually
  gets `.start()`-ed are `products/retail|clinic/backend/app.py`'s boot-time
  `_resume_einvoicing_workers()` sweep and
  `commercial_runtime/einvoicing/routes.py`'s `_post_settings()` (on any
  settings write that resolves to enabled). A company that has never done
  either — a fresh install's very first sale, enqueued purely by the new
  default with nobody ever visiting the e-invoicing settings screen — gets
  a real `einvoice_outbox` row but no running worker to submit it, until
  the process is next restarted (which resweeps and picks it up). This is a
  bounded LATENCY, not a data-loss risk: the row is durable in the outbox,
  nothing is dropped, and a restart (or any settings write, which now
  starts the worker regardless of transition — see routes.py's
  `_post_settings` comment) resolves it. Eventual fix: start the worker
  event-driven, at the moment of first enqueue
  (`core/retail/einvoice_adapter.py`'s `enqueue_sale` /
  `core/clinic/einvoice_adapter.py`'s `enqueue_invoice`), instead of only at
  boot or on a settings write — needs the enqueue path to reach the same
  per-company worker registry `app.py` owns, which it currently doesn't
  import (deliberately, to avoid a layering inversion — see
  `build_document`'s comment on why `core/retail` doesn't import
  `api.retail_api`).
