# Multi-device accounts — architecture decision

Produced 2026-08-20 by a design workflow: four parallel readers mapped the
real current state (accounts and roles, sync scope, stock/cash data flow, and
how established retail systems solve this), three independent architectures
were then argued from deliberately different angles (event-sourced,
minimum-viable-change, server-authoritative), and a lead architect chose one
rather than averaging them.

It answers the product owner's decision of 2026-08-20: *"there should be an
admin account that makes an employees account, the admin should have the same
data between the desktop and the phone."*

---

# DECISION — ONE MERCHANT ACCOUNT, MANY TERMINALS, ONE SET OF NUMBERS

## 1. The decision
A shop gets **one owner account**. The owner creates employee accounts (manager, cashier) from a screen we are about to build — the server code for this already exists and has simply never been wired to a button. Every till, desktop or phone, is a **registered terminal** of that one shop. Selling still happens on the device, offline, exactly as today — but from now on every sale, refund and stock movement is a permanent, uniquely-numbered receipt that is copied to the other devices. Stock is no longer a number each device keeps for itself; it is the **sum of those receipts**, so both devices arrive at the same figure by arithmetic rather than by agreement. Things people edit rather than record — prices, products, customers, employees — can only be changed from the owner's device, which is how we avoid two people changing the same thing at once instead of trying to referee it afterwards. We are **not** rebuilding the sales database, **not** moving accounts into the Owner server, and **not** syncing purchase orders in this pass. Cash drawers become per-till, so the phone's takings stop landing in the desktop's Z-report.

## 2. Scorecard

| | Correctness | Offline resilience | Migration risk | Time to deliver | Long-term maintainability |
|---|---|---|---|---|---|
| **P1 ledger** | Highest — conflict class deleted by construction | Highest | **Severe** — UUID PK rebuild (`DROP`+`RENAME`) of live `sales`/`sale_items`/`inventory_movements` | Long (v13→v17, 5 destructive steps) | Highest |
| **P2 pragmatic** ✅ | High, with two named debts (dual id space; single-writer is policy not property) | High | **Low** — `ALTER TABLE ADD COLUMN` only, no `id_map`, no FK remap | Shortest | Good, one smell |
| **P3 authority** | Highest | Good (cached verifier keeps login offline) | **Highest** — UUID rebuild *plus* new Postgres identity *plus* a one-shot `adopt` history merge | Longest | Good, but two identity worlds in Owner |

**Chosen: P2** — because the only thing standing between us and "same data on both devices" is migration risk on live shop data, and P2 is the only proposal that reaches the goal without a destructive rebuild. **Grafted from P1:** ledger-is-truth (`inventory_balances` never crosses the wire, always recomputed by `stock_reconciliation.repair_drift`); the **v15 drift gate** — seed an `opening_count` movement for every balance with no movements, then *refuse to advance `user_version` if `compute_drift` ≠ 0*; voids as compensating events, never `UPDATE`; the oversell exception queue. **Grafted from P3:** Owner-**issued** `company_id` (kill `md5(admin_email)`, `onboarding_routes.py:138`); PIN is attribution, never authorization; terminal-owned drawer with the OPENED→ENDED→CLOSED split; `created_at_utc` beside local time; a visible `sync_conflicts` table instead of silent drops; Owner-side event pruning as a **launch blocker**, not a follow-up.

## 3. Account model
- **Store:** `registry.db users` (`registry_db.py:102-119`) stays the account table. `role` widens `{admin, employee}` → `{admin, manager, cashier}`. New: `uid TEXT UNIQUE` (wire identity), `pin_hash`, `row_version`, `updated_at_utc`, `deleted_at_utc`.
- **Creation:** admin path unchanged — `create_admin()` (`onboarding_routes.py:88-209`), gated on "no valid admin exists" (`:120-125`), `ADMIN-0001`. Employees use the **already-written** `create_employee()` (`:369-427`, `mt_role=='admin'` at `:371`, `EMP-%04d`, 7-day single-use `secure_links` invite, `/#setup/<token>` → `employee_setup()` `:632`). The missing piece is purely UI: nothing in `products/retail/frontend/` or `android/aura-retail/` calls `/api/admin/employees`.
- **Authentication:** unchanged endpoint (`auth_routes.py:24-55` → `mt_auth.authenticate_registry_user()` `:124-200` → Flask signed cookie, `app.py:67-74`); Android hits the same embedded Flask over `127.0.0.1` and persists the cookie (`net/ApiClient.kt:19-80`). Two security bugs are fixed unconditionally in Phase 1: `mt_login_required` **fails open on any DB exception** (`mt_auth.py:226-227`), and it **never checks `session_version`**, so the bumps at `onboarding_routes.py:437,462,490` currently revoke nothing.
- **Permissions:** the single literal `@mt_require_subsystem('retail')` on ~80 routes (`retail_api.py:200+`) is replaced by capability codes stored in the **existing** `user_permissions` table (`registry_db.py:122-129`): `retail.sell`, `retail.refund`, `retail.discount`, `retail.stock.adjust`, `retail.reports`, `retail.cash.close`, `retail.cash.approve`, `retail.employees`. Admin bypass (`mt_auth.py:287`) stays.
- **PIN vs password:** password enrols a terminal and opens the back office. A 4-digit PIN switches the *acting user* on an already-authenticated terminal and is stamped on every row. A PIN alone never authorises: void of a closed sale, cost/price edit, employee management, or cash-variance approval — those re-prompt for the password. The AUDIT-032 self-approval bar stands.
- **User ≠ device.** Device = licence slot: `devices`/`user_devices` (`device_registry.py:50-88`), fingerprint, `is_admin_device`, one-admin-device partial index (`:65-66`); we finally call the dormant `user_device_is_authorized` (`:297`, zero production callers today). One cashier on two terminals = two sessions, one `actor_user_uid`, rows merge into one shift figure. Two cashiers on one terminal = PIN switch; the drawer is **not** re-bound. `commercial_runtime/identity/`'s device-admin is the device axis and is not `users.role='admin'`.

## 4. Data classes

| Class | Scope | Id scheme | Conflict rule | Deletes |
|---|---|---|---|---|
| `sales`, `sale_items`, `returns`, `return_items`, `payments` | **shared** | local `INTEGER` PK kept + `uid TEXT UNIQUE` = wire key | **none needed** — `INSERT … ON CONFLICT(uid) DO NOTHING` | never; `sale.void` compensating event, monotonic (completed→voided) |
| `inventory_movements` | **shared** | same | none needed (already strictly append-only) | never |
| `inventory_balances` | **device-local** | local | **not merged** — recomputed from the ledger after every applied batch (`_DRIFT_SQL`, `stock_reconciliation.py:88`) | n/a |
| `branches` | **shared** | `uid` (fixes device A's branch 1 ≠ device B's branch 1, `schema.py:1347`) | admin-device single-writer + `row_version` | soft `deleted_at_utc` |
| `category`, `product`, `customer`, `supplier` | **shared** (today) | already UUID PK | admin-device single-writer; `row_version`, apply only if incoming > local, else row lands in `sync_conflicts`; **changed-field delta**, not full-row snapshot | soft tombstone; hard `DELETE` forbidden |
| `users` (+ capabilities) | **shared** | `uid` | admin-device single-writer; role change never auto-merged | soft delete + `session_version` bump = revocation channel |
| terminals (`devices`) | **shared, read-only to non-admin** | fingerprint-derived uid | licence-authoritative | `revoked_at` |
| `cash_sessions` | **shared read, terminal-owned write** | `id TEXT` uuid4 (already) | only the owning terminal writes → conflict structurally impossible | never; force-closed across business date as unverified variance |
| `cash_movements` | **shared** | `id TEXT` uuid4 (already) | none needed | never |
| `purchase_orders` (+items), receiving | **device-local, deferred** | local + `uid` reserved | out of scope this pass; receiving + reorder-accept gated to `_is_admin_device()` (`retail_api.py:169`) — which also kills the double-PO bug (`reorder_hook.py:117-120`) | n/a |
| `reorder_requests` | **shared** | UUID (already) | admin-device single-writer + `row_version`, **replacing** today's blind LWW upsert (`sync_service.py:414-421`) | soft |
| `held_sales`, `sync_outbox`, `sync_cursor`, `doc_sequences`, `audit_log` | **device-local** | local | never synced | local |

Document numbers: `_next_ref` (`retail_api.py:2956-2961`) gains a terminal prefix — `SALE-<term4>-000101` — against `UNIQUE(company_id, sale_number)`. A globally sequential number is never allocated offline.

## 5. Offline
**Allowed:** sell (cash + manual tender), hold/recall, cash in/out, open and close *this terminal's* drawer, refund **only against a sale present locally**, browse catalogue. **Blocked with a plain reason:** create/edit product, category, customer, supplier or employee; change a role; receive a PO; approve a cash variance; stocktake. **What the user is told:** a persistent banner "Offline since 14:20 — 412 unsynced"; once the last sync is older than 30 minutes the POS tile **hides the on-hand number** rather than show a confident wrong one (a confidently wrong figure is precisely the "stock is not accurate" complaint); logout and app-exit are **blocked while `sync_outbox` is non-empty** (this is exactly how Shopify loses offline orders); soft warning at 24h, **hard stop on new sales at 72h**.

**Both devices sell the last unit offline:** both sales succeed. Each appends its own `sale_out` movement with a distinct `uid`; on merge both rows survive, the recomputed sum is **−1**, `compute_drift` (`stock_reconciliation.py:99`) surfaces it attributed to two terminals, two cashiers and two timestamps, and it lands in an **oversold exception queue** for the owner to resolve as a business exception (backorder / substitute / refund). We do not prevent it, we do not build reservations, and the UI says so. Today the same case produces silent, permanent divergence.

## 6. Migration (retail `RETAIL_SCHEMA_VERSION` 12, `schema.py:190`; registry 2, `registry_db.py:40-48`)
Each step is one idempotent function appended **last** in `_migrate_retail_schema` (`schema.py:876-910`), the convention that file already documents. **No table rebuild, no `id_map`, no `DROP`/`RENAME`** — `_migrate_products_to_uuid`'s risk profile (`:528-660`) is deliberately not repeated on live sales data.
- **registry v3** — `users.uid`/`pin_hash`/`row_version`/`updated_at_utc`/`deleted_at_utc`; role widened; capability rows seeded.
- **retail v13** — add `uid TEXT` + unique index to `branches`, `sales`, `sale_items`, `returns`, `return_items`, `inventory_movements`, `payments` (backfill uuid4 per row); add `actor_user_uid`, `terminal_id`, `created_at_utc` to `sales`/`returns`/`inventory_movements`/`cash_sessions`/`cash_movements`; add `row_version`/`updated_at_utc`/`deleted_at_utc` to the four catalogue tables + `reorder_requests`. Existing free-text `cashier`/`created_by`/`opened_by` are **kept**, never guessed at — unmatched history stays unattributed rather than fabricated.
- **retail v14 (+ registry v3 companion)** — rebind `company_id` from `md5(admin_email)` to the Owner-issued value carried in the licence assertion, updating all ~13 scoped tables in one transaction. Must precede any sync widening or rows arrive scoped to a tenant the receiver doesn't recognise.
- **retail v15 — the no-loss gate.** For every `inventory_balances` row whose `(product_id, branch_id)` has no movements (every pre-existing install, plus `_seed_retail`'s balance-without-movement at `schema.py:1726` and movement-without-balance at `:1799`), insert one `opening_count` movement equal to `quantity_on_hand`. Run `compute_drift` before and after and **refuse to advance `user_version` if drift ≠ 0**. After this the cache is provably derivable.
- **retail v16 — drawer.** Backfill `cash_sessions.terminal_id` from this device's fingerprint; drop `idx_cash_sessions_one_open_per_branch` (`schema.py:1213-1215`) and recreate as `UNIQUE(company_id, terminal_id) WHERE status='open'`; add `ended_at`/`ended_by` for the ENDED/CLOSED split; force-close any session crossing a business date as an unverified variance.
- **retail v17 — cleanup.** Drop dead `quantity_reserved` (`schema.py:1404`, never written); create `sync_conflicts` and `stock_exceptions`.

**Existing installs:** the common case is a single device — it migrates with zero loss and no user action, and its backfilled history pushes once on first sync, bounded by the existing 200-event chunking. The rare install already running two independently diverged devices must **nominate a primary**; the secondary exports CSV, clears its transactional tables and pulls the primary's history. There is no honest way to auto-merge two histories that were never coordinated, and pretending otherwise doubles the ledger.

## 7. Build order
1. **Security + identity surface** (registry v3) — close the `mt_auth.py:226-227` fail-open, enforce `session_version`, wire `user_device_is_authorized`, capability codes on the ~80 retail routes, employee CRUD screens in `app-shell.js` and Compose, PIN switch. *~2 weeks.* Ships value with zero sync change.
2. **Attribution + tenancy** (retail v13, v14) — stamp `actor_user_uid`/`terminal_id`/`created_at_utc`; "by employee" in reports; Owner-issued `company_id`. *~1.5 weeks.* Still single-device, but "who voided this" is answerable.
3. **Ledger truth** (retail v15) — seeding + drift gate; owner-facing "stock accuracy" screen over `compute_drift`; `repair_drift` runs under its own `BEGIN IMMEDIATE` (it manages none today, `:153`). *~1 week.* The complaint becomes measurable before it becomes fixed.
4. **Terminal-bound drawer** (retail v16) — fixes phone takings folding into the desktop Z-report (`_cash_session_report`, `retail_api.py:2339-2351`) *before* sync exists. *~1 week.*
5. **Ledger sync — the payoff.** Widen the allowlist tuple in **both** places (`sync_service.py:292` and `:326`) to `branch, user, sale, sale_item, return, return_item, inventory_movement, payment`; apply by `INSERT … ON CONFLICT(uid) DO NOTHING`; resolve parent uid → local integer id on apply; recompute balances inside the applying transaction; **Owner-side pruning of `owner_sync_events` below the slowest cursor ships in this phase, not after.** Owner routes otherwise untouched. *~3 weeks.* This is where the admin sees one stock figure and one takings figure on both devices.
6. **Catalogue correctness** (v17) — `row_version`, reject-stale, changed-field deltas, tombstones, visible `sync_conflicts`. *~2 weeks.*
7. **Offline UX + exceptions** — banner, stale-stock hiding, 72h stop, logout block, oversell queue, PO partial-receipt model and PO sync. *~2 weeks.*

## 8. What we are explicitly NOT doing
**UUID primary-key rebuilds of `sales`/`returns`/`inventory_movements`** — the additive `uid` satisfies Owner's `uuid.UUID(entity_id)` gate (`routes.py:168`) because the wire key is the uid, not the PK; the local integer stays a private detail. **Promoting Owner into the merchant-identity authority** — accounts are single-writer on the admin device with monotonic `session_version`, which gives the same revocation guarantee without a second identity world in Postgres; login stays offline-capable, which Owner-as-authority cannot deliver. **Syncing `inventory_balances`** — syncing a cache creates a second truth. **Syncing purchase orders now** — the `purchase_in` movement syncs, so *stock* is right on both devices; only PO status is phone-invisible, and receiving is admin-device-gated. **Distributed stock reservations or any till operation that blocks on the network** — no product on the market prevents an offline oversell, and a refused sale is worse than an oversell. **Touching `pg_advisory_xact_lock(hashtext(license_id))` in `owner/app/sync/routes.py`** or anything else in the transport. **LWW on money or stock, and hard deletes on any synced table** — ever.

## 9. Three biggest risks
1. **Unbounded `owner_sync_events`.** Volume goes from tens to **~5,000 events/day/device**, each carrying full-row JSONB, into a table with no TTL and no `DELETE` anywhere in `routes.py`/`models/sync.py`. *Mitigation:* pruning below the slowest device cursor ships **inside Phase 5** as a gate, plus a per-licence row-count alarm; changed-field deltas (Phase 6) cut payload further.
2. **One poison event jams a licence permanently** — apply is all-or-nothing (`routes.py:338-340` rolls back the whole batch on `INVALID_EVENT`), so a single bad row stops that shop syncing forever. *Mitigation:* a quarantine table + skip-and-surface path lands **before** Phase 5 opens the firehose; every quarantined event is visible to the owner and replayable, never silently dropped.
3. **Clock skew silently corrupts every report.** `metrics.py:498,510` bucket on `date(created_at)`/`strftime('%H',…)` of a **local wall clock** written by whichever device rang the sale (`retail_api.py:1572`, `:2206`). Two devices, two timezones or one wrong clock = rows filed on the wrong day. *Mitigation:* `created_at_utc` added in v13 and every report predicate moved onto it; **ordering is by Owner's server seq, never by device time**; observed skew is recorded at each sync and a terminal >5 min out is flagged in the admin console. Accepted residual: daily buckets stay on the shop's configured business-date offset, not per-device local time.
