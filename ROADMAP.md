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
