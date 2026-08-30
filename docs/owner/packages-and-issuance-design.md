# Packages, licence issuance, and "Owner controls everything" — decisions

**Status:** DESIGN + DECISION document. No source file changed. Written
2026-08-30 against `feat/launch-readiness` (worktree
`ci-hardening-w0.3-continue`), retail schema v23 on disk (promotions wave 1
present), registry v6 on disk / v7 claimed. Successor to
`docs/launch-readiness/seats-and-chain-design.md` and
`account-hierarchy-design.md`; the owner's decisions recorded in ROADMAP.md
2026-08-30 ("commercial model DECIDED") are treated as settled and designed-to,
not reopened: **devices are the meter, 2 included, 50 JOD per additional
device, the owner's account counts inside the allowance, Clinic out of scope.**

`owner/` is the other collaborator's area. Everything in §B and §C that touches
`owner/` is a **proposal to hand over**, not a work order for this branch.
Coordination risk is flagged per item.

---

## 0. What was verified before designing — and what the verification changed

Every claim below was checked by reading code this session, because this
codebase has burned "missing feature" claims in both directions before.
Findings that change the shape of the answer:

1. **The owner's suspicion is CORRECT: the catalog plans cannot differ today
   in any way the entitlement system enforces.** Full evidence in §A.1. The
   short version: `PlanEntitlement` and `AddonEntitlement` are written by
   **tests only** — no production code, no route, no seed ever attaches an
   entitlement to a plan (grep: the only non-model, non-test writes are in
   `owner/tests/test_phase6_entitlements.py`). The plan-creation form
   (`owner/app/catalog/routes.py:39-55`) offers exactly: code, product, name,
   billing model, currency, included device count. That is the entire
   differentiation surface reachable from the UI.

2. **The product consumes zero feature entitlements.** The transport is
   complete (signed assertion → persisted snapshot → re-read per guarded
   request), and `capability_guard.evaluate_capability` takes a
   `required_entitlement` argument (`commercial_runtime/licensing_contracts/
   capability_guard.py:34,59-60`) — but **no production route in `products/`
   passes it** (grep: zero matches outside `licensing_contracts` tests). Today
   the only remotely-governed axes that actually bite are: licence state,
   device limit, platform, and the Part W commercial/term fields. Every
   boolean feature switch is plumbing with nothing on the end of it.

3. **Three catalog descriptions are stale in the "feature missing" direction.**
   `_CANONICAL_ENTITLEMENTS` (`owner/app/catalog/services.py:36-47`) labels
   WhatsApp notifications, daily-report and low-stock "not yet built" —
   but `commercial_runtime/notifications/` contains a working email outbox +
   SMTP worker AND a WhatsApp outbox/worker/settings/routes stack, imported by
   `retail_api.py`. (What's genuinely missing there is per-customer Meta
   Business API credentials — the demo install had placeholder literals.)
   Promotions (percentage, best-price-wins) are real as of v23
   (`products/retail/backend/core/retail/promotions.py`). The AI assistant is
   real (`retail_api.py:9573+`, `config.py:177+`): sidebar chat, RAG over a
   company-scoped local summary, proxied to a hosted phi3.5 on the droplet,
   gated on `CAP_REPORTS`, fails closed to "temporarily unavailable".

4. **A live defect in the sales pipeline: the licence key is issued and then
   thrown away.** `owner/app/commercial_sales/fulfillment.py:206` calls
   `issue_license_key(...)` and **discards the returned plaintext key**; the
   route (`commercial_sales/routes.py:405-417`) then redirects to the order
   page. Under ADR-9 the plaintext exists only in that one return value —
   never persisted, never re-derivable — and re-issue is impossible (issue
   only from DRAFT; the licence is now ISSUED,
   `licensing/services.py:issue_license_key`). **A licence fulfilled through
   the "proper" quote→order→invoice→payment→fulfill pipeline produces a key
   nobody, staff or customer, can ever read.** The only recovery is
   `replace_license` (revoke + new DRAFT) and re-issuing through the direct
   path. This should be recorded as an AUDIT item; the fix (render the key
   once in the fulfill response, exactly as `licensing/routes.py::issue`
   does) is owned by the collaborator's area.

5. **A UI-created plan cannot be sold through the sales pipeline at all.**
   `describe_plan_for_sale` (`commercial_sales/catalog_for_sales.py:57-63`)
   returns None when `plan.effective_date is None` — and the plan-creation
   form has no effective-date field and no route ever sets one. Every plan
   made through the UI is unsellable-by-pipeline until someone runs SQL.
   Same class of finding as (4): the long path is not merely long, it is
   broken at both ends.

6. **The only ways to change `device_limit` after issuance** are the
   MFA-gated renewal-request pipeline (`commercial_ops/renewal_requests.py:
   300-336`, which mirrors the new allowance onto the licence) and temporary
   `DeviceSlotException`s (≤90 days, `device_slot_ops.py`). The `EXTRA_DEVICE`
   add-on is PLANNED-only, and `fulfillment.py` explicitly does not implement
   ADD_ON fulfillment. **Under the decided commercial model — devices ARE the
   meter — the single most common paid action ("customer buys a 3rd till,
   50 JOD") has no first-class path.** §C.2 makes this the top build item.

---

## A. The packages

### A.1 What is actually in the catalog today (the owner asked; here is the answer)

- **No plan rows ship at all.** `seed_canonical_catalog()` seeds products,
  platforms, channels, entitlement *definitions*, and DRAFT/PLANNED add-on
  rows — deliberately never a plan (`catalog/services.py::seed_canonical_catalog`).
  Any plan on the droplet was hand-typed.
- A plan can differ from another plan, via the UI, in exactly: **name, code,
  billing model, currency, price, included device count.** `support_level`
  exists on the model but not in the form; `effective_date` likewise (see §0.5).
- Entitlement attachment (the thing that would make tiers real) has **no write
  UI anywhere** — `licensing_admin` has only a read-only *preview*
  (`licensing_admin/routes.py:150-157`). Licence-level overrides
  (`LicenseEntitlement`) are equally SQL-only.
- So: "the packages don't differ from each other" is not just true, it is
  structural — the machinery to make them differ exists (resolver, signing,
  transport) but is reachable only from tests and raw SQL.

### A.2 What a tier boundary can actually be, today vs. with small work

| Differentiator | Enforced today? | What it takes |
|---|---|---|
| **Device count** | **YES — hard.** `DEVICE_LIMIT_REACHED` at activation, server-side, Ed25519-bound (`licensing_service/activation.py:241-242`) | Nothing. This is the meter, and it already works. |
| **Branch count** (Shop = 1 branch) | No | Product-side enforcement point E5 (`POST /branches`, designed in seats-and-chain §3) + `max_branches` value on the Shop plan. Days, not weeks. Until built: contractual only. |
| **AI assistant** | No — works wherever `AURA_AI_BEARER_TOKEN` is set on the device | New `ai_assistant_enabled` entitlement definition (Owner) + `required_entitlement="ai_assistant_enabled"` on the one `/ai/chat` route (product). Days. |
| **WhatsApp alerts** | No — local settings | `whatsapp_notifications_enabled` definition already seeded; gate the product-side enable/settings route on it. Days. |
| **Platforms (Windows/Android)** | YES — activation checks `allowed_platforms` | Nothing. Not worth pricing on (both platforms should sell everywhere). |
| **Support tier / onboarding** | Human-enforced | Nothing technical. Legitimate differentiator. |
| **Term & renewal** | YES — Part W assertion fields + grace ladder | Nothing (but the issuance path must actually SET term dates — §B.4). |
| Core POS features (sales, stock, AR/AP, cash sessions, promotions, reports, backup, permissions) | — | **Deliberately never tier-gated.** Tearing the core apart per tier multiplies support load, violates the "invisible unless opted in" doctrine in reverse, and none of it is gate-wired anyway. Every paid tier gets the whole product. |

**Rule this document enforces on itself:** every boundary below is marked
either *(enforced)* or *(needs enforcement built — named)*. Anything neither
is support/service, which humans enforce.

### A.3 Willingness-to-pay assumptions, stated not hidden

- A small Amman shop treats software like a phone bill: **10–20 JOD/month
  equivalent** is defensible if it visibly replaces the notebook AND answers
  the JoFotara mandate. Above ~25 JOD/month equivalent, the notebook wins.
- The JoFotara e-invoicing mandate is the strongest single purchase trigger in
  this market right now; small shops are actively looking for the cheapest
  compliant path.
- Hypermarkets/co-ops/foundations anchor against legacy local POS quotes
  (historically thousands of JOD one-time plus maintenance) and against
  international per-till subscriptions (~40–70 JOD/till/month). Anything
  under ~100 JOD/till/**year** reads as aggressive value.
- Jordanian SMBs distrust auto-renewing subscriptions but accept **annual
  licences with renewal**, which is exactly the shape the licensing runtime
  already implements (terms, check-ins, grace ladder, renewal pipeline).
- Foundations/co-ops need paper: quote → invoice → payment trail. That is what
  the 9.5D pipeline is actually FOR (§B keeps it for them, demotes it for
  everyone else).

### A.4 The package set — three things to sell, not five

Two paid tiers, one trial, and a short add-on list. Not more. A middle tier
was considered and rejected: with devices already metered separately, the only
honest differentiators left are branches and service; a third software tier
would be marketing without an enforcement mechanism — exactly what this
document is not allowed to produce.

All prices are Owner catalog values (`PlanPrice` rows, JOD) — changeable
without touching product code.

---

**TRIAL — "Aura Pilot" (تجربة) — 0 JOD, 30 days**
- The pilot machinery already exists end-to-end (`billing_model=PILOT`,
  `commercial_ops/pilot_lifecycle.py`, conversion through the renewal
  pipeline). Use it; do not invent a parallel trial system.
- Full product, 2 devices, 1 branch, 30 days. Converts by paying for Shop or
  Chain (the pilot→paid path is the renewal pipeline, already
  separation-of-duties gated).
- On expiry the install walks the signed ladder to RESTRICTED: **read-only,
  data preserved, backup/export/returns/customer-payments still allowed**
  (`RETAIL_RESTRICTED_ALLOWLIST`, `retail_api.py:136-144`). Sell this
  honestly: *"your data is never held hostage"* — it is true, it is built,
  and no local competitor can say it.
- There is deliberately **no free tier**: an unlicensed install is read-only
  by design (`NOT_CONFIGURED` ∉ `ACTIVE_FAMILY`), and that stays.

---

**TIER 1 — "Aura Retail — Shop" (المتجر) — 180 JOD/year**
- **Who:** the single-location shop — minimarket, pharmacy-adjacent retail,
  boutique, small trader. The buyer who compares against a notebook.
- **Includes:** 2 devices *(enforced)*, 1 branch *(needs enforcement built:
  E5 + `max_branches=1` on this plan — until then contractual)*, the entire
  core product: POS + barcode hardware scanning, returns, inventory,
  customers/suppliers, AR/AP with statements and aging, cash sessions with
  X/Z reports and variance approval, promotions (percentage, wave 1),
  reports, the 8-code staff permission system with PIN attribution, local
  backup/restore, data export, **JoFotara e-invoicing** (see positioning
  below), Windows + Android, sync between its 2 devices via the relay,
  standard support (WhatsApp, business hours).
- **Does NOT include:** AI assistant, WhatsApp alerts, priority support,
  more than one branch, more than 2 devices without the add-on.
- **Upgrade triggers:** a third till (→ +50 JOD/yr device add-on — this is
  the designed growth path, not a tier jump); a second location (→ Chain).
- Equivalent 15 JOD/month. At 4 tills: 280 JOD/yr — still under any
  per-till international subscription for ONE till.

---

**TIER 2 — "Aura Retail — Chain" (السلسلة) — 420 JOD/year**
- **Who:** multi-branch minimarket chains, co-ops with outlets, the
  hypermarket that opens a second site. The buyer whose real question is
  "what is happening in the store I am not standing in".
- **Includes:** everything in Shop, plus: **unlimited branches** under one
  licence *(enforced by absence of the branch cap — deliberate)*,
  branch-scoped managers and delegated staffing (account-hierarchy waves
  C2/D), device→branch pinning, the head-office comparison screen (wave C3),
  **priority support** (named contact, response SLO), and included
  onboarding (staff import + first-branch setup, remote).
- **Same device meter:** 2 devices included, 50 JOD/yr per extra — the meter
  is identical across tiers on purpose. A 5-store chain physically needs ≥5
  tills, so scale pays through devices automatically: 5 stores / 7 tills =
  420 + 5×50 = **670 JOD/yr**. A 12-till single-site hypermarket doesn't
  need Chain at all: Shop + 10 devices = **680 JOD/yr** — devices, not
  tiers, carry the size signal. That is the meter working as decided.
- **Honesty constraint, printed on the roadmap not hidden in a footnote:**
  the chain features (pinning, scoped managers, comparison screen) are
  waves C1–C3/D — designed, claimed, **not shipped yet**. Chain is sellable
  the day wave C lands. Until then: sell Shop + devices to chains that ask,
  with a written upgrade path, or offer Chain as pilot-priced early access.
  Do not sell the comparison screen before device→branch pinning exists —
  the numbers would be confidently wrong (ROADMAP 2026-08-30 defect entry).

---

**ADD-ONS (all tiers, all prices per year, JOD):**

| Add-on | Price | Enforcement status |
|---|---|---|
| **Extra device** | **50 / device** (owner-decided) | Enforced today via `device_limit` at activation. Needs the one-screen sale path (§C.2) — today it takes the renewal pipeline. |
| **Aura Assistant (AI)** | 60 | Needs `ai_assistant_enabled` definition + one decorator argument on `/ai/chat`. |
| **WhatsApp alerts** | 40 (+ Meta's own conversation fees, at cost) | Infra built; needs entitlement gate + per-customer Meta Business setup (real onboarding labour — that's what the 40 pays for). |
| **Priority support** (Shop only) | 60 | Human. Included in Chain. |

**Deliberately NOT in the catalog at launch:** cloud backup (not built — the
`CLOUD_BACKUP` add-on stays DRAFT, and the existing DRAFT-add-ons-never-apply
rule in `resolve_entitlements` keeps it inert), digital receipts, customer
owner-dashboard (not built), and any perpetual/one-time licence (§A.5).

### A.5 Three positioning decisions, made not offered

**E-invoicing is included free in every paid tier — never an add-on.**
Reasons: (1) it is a *tax compliance obligation*; charging rent on compliance
reads as extortion in this market and hands competitors the line "they charge
you to obey the law"; (2) it is opt-in and costs the vendor nothing when off
(three independent disable layers, default OFF —
`commercial_runtime/einvoicing/settings.py`); (3) it is the single best
door-opener with small shops (§A.3). Market it as "JoFotara-ready, at no
extra cost" and make it the headline of the Shop tier.

**The AI assistant is a paid opt-in add-on, not a tier feature and not in
the base.** The chat sends a company-scoped business-data summary to a
vendor-hosted droplet (`retail_api.py::_build_ai_context`) — for some buyers
(co-ops, foundations, anyone with a data-locality stance) that is
disqualifying, so it must never be something a tier forces them to carry.
It costs real money (~$48/month droplet ≈ 410 JOD/yr): at 60 JOD/yr, seven
subscribers cover the droplet; capacity of the current 4vCPU box is limited,
so the price also throttles adoption to what the hardware can serve. It is
correctly built for the offline doctrine already (fails closed, never on the
sale path). One decorator argument makes it Owner-governable (§A.2).

**Annual only at launch; no perpetual SKU.** The runtime is term-shaped
(check-ins, signed grace ladder, renewal pipeline, Part W term fields); a
perpetual licence forfeits the recurring revenue that funds the relay, the
droplet, and JoFotara compliance updates — and note that the current direct
issuance path *accidentally* sells perpetual today because nobody sets term
dates anywhere (§B.1). The minimal path (§B.4) fixes that by construction.
The annual price is framed as "licence + updates + support + sync relay +
compliance updates", which is what it actually funds.

**Restaurants: not sellable, and say so.** No modifiers, no kitchen
tickets/KDS, no split tender, no table/course management, no service-charge
handling — none of it in schema or API. A restaurant tier would be a promise
the product cannot keep for at least one full phase. When it is scheduled it
is its own product surface (order routing is a different workflow from
retail checkout), not a plan row. Politely decline restaurant leads or sell
them Shop for their retail counter only, in writing.

### A.6 What must be BUILT for these packages to be real (work orders, named)

1. **Owner: a `seed-commercial-packages` CLI command** (idempotent, beside
   `seed-catalog` in `owner/app/cli.py`): creates the two plans WITH
   `effective_date`, prices in JOD, `max_branches=1` entitlement row on Shop,
   `ai_assistant_enabled`/`whatsapp_notifications_enabled` add-on entitlement
   rows, flips `EXTRA_DEVICE`/`PRIORITY_SUPPORT` to AVAILABLE with prices.
   One command replaces four hand-forms and fixes the §0.5 effective-date
   trap wholesale. *(Collaborator's area — hand over as spec.)*
2. **Product: `required_entitlement` on `/ai/chat`** and on the WhatsApp
   enable route; **E5 branch cap** at `POST /branches` (already fully
   designed, seats-and-chain §3). Wire nothing else — the ≤0/absent =
   unenforced rule (already the reserved contract) grandfathers every
   existing licence.
3. **Owner: the extra-device sale screen** — §C.2, the top item.
4. Nothing else. Specifically: do NOT build `max_users` (deviation already
   reasoned in account-hierarchy §8), do NOT wire the six DRAFT add-ons, do
   NOT build per-feature gates on core POS.

---

## B. The licence-issuance flow — traced, then cut

### B.1 The flow as it exists — two paths, both counted honestly

**One-time bootstrap** (not per-licence, but it gates everything and has
burned this project before — the droplet was found with RBAC never seeded and
a stale trust anchor): `create-superadmin` → `seed-rbac` → `seed-catalog` →
`seed-offline-policy` → `licensing generate-signing-key` + activate →
`LICENSE_PEPPER` env → hand-create plans (+ SQL for effective dates, §0.5).
Keep `flask commercial preflight` in the deploy runbook.

**Path A — direct (what support actually does), per licence:**

| # | Human act | Where | Fields |
|---|---|---|---|
| 1 | Create customer | `customers/routes.py::create` | legal_name (min) |
| 2 | Create subscription | `subscriptions/routes.py::create` | customer, plan, billing_cycle, device_allowance |
| 3 | Create licence (DRAFT) | `licensing/routes.py::create` | subscription (a dropdown of ALL subscriptions, unscoped), allowed_platforms free text, device_limit typed again |
| 4 | Press Issue (+ MFA re-auth if stale) | `licensing/routes.py::issue` | idempotency key auto-embedded |
| 5 | Copy the once-shown key, WhatsApp it to the customer | manual | — |
| 6 | (If activation policy ≠ AUTOMATIC: approve the pending activation) | `commercial_ops` | — |

**Six steps, ~10 fields, the device count typed up to three times**
(plan.included_device_count, subscription.device_allowance,
license.device_limit — nothing reconciles them), platforms typed as free
text (the exact field whose "ALL" typo already silently broke every
activation once — fulfillment.py's own comment). And note what Path A
*cannot* do: set a term. `valid_from/valid_until` and subscription
start/end are never set anywhere on this path — every directly-issued
licence is accidentally perpetual, invisible to the expiry scans and the
whole Part W term machinery.

**Path B — the 9.5D sales pipeline, per sale:** create quote → add line(s)
→ submit → accept decision → (approval decide, if discounted) → create order
from quote → confirm order → create invoice → issue invoice → record payment
→ confirm payment → allocate payment → fulfill. **Thirteen steps — and per
§0.4 it ends with a licence key nobody can read.** The pipeline's internal
correctness is genuinely good (idempotency ledgers, FOR UPDATE
serialization, canonical-service-only writes, snapshot pricing) — it is the
right machine for tenders and foundations and the wrong default for selling
a 180 JOD licence to a shop.

### B.2 Classification — load-bearing / ceremony / mis-sequenced

**Load-bearing — must survive any cut:**
- Key generation, HMAC-only storage, single-response reveal, idempotent
  issuance (`security/license_keys.py`, `licensing/services.py::issue_license_key`).
- `require_recent_auth` (MFA) on issue/transition/replace.
- Audit rows + status history on every transition (`LICENSE_CREATED`,
  `LICENSE_KEY_ISSUED`, `LicenseStatusHistory`, `LicenseKeyIssuanceEvent`)
  — this is the "who issued this and why, a year later" answer, and it is
  written by the *services*, so any thinner route keeps it for free.
- The transition state machine incl. revocation/suspension and
  `replace_license`.
- Device binding + `DEVICE_LIMIT_REACHED` at activation; the separation-of-
  duties gates on reactivation and renewals (deliberate security fixes with
  their own comments — do not "simplify" those).
- A customer row (identity to bill and audit against) and a plan row
  (pricing + entitlements source).

**Ceremony — real code earning nothing at issuance time; cut from the path:**
- The **manual subscription step** (create it automatically from
  customer+plan; `fulfillment.py` already proves the derivation).
- The **user-visible DRAFT licence stage** (collapse create+issue into one
  act; DRAFT remains as an internal state for `replace_license`).
- **`allowed_platforms` free text** (derive from the plan's supported
  platforms — `describe_plan_for_sale`'s `supported_platform_codes`, the
  exact fix fulfillment already uses).
- **`device_limit` and `device_allowance` typed by hand** (derive:
  plan.included_device_count + purchased extra devices).
- The **billing_cycle field** (it is the plan's billing_model).
- The **subscription-status transition ceremony** (auto-ACTIVE at issue;
  activation only requires the licence to be ISSUED —
  `activation.py:140` — but ops screens and Part W read subscription
  status, so set it correctly once, automatically).

**Wanted but mis-sequenced — keep, move off the issuance path:**
- **Payment recording.** In this market the sale often closes on trust/cash
  with a dealer relationship; blocking issuance on a confirmed, allocated
  payment (Path B's rule) is why staff use Path A and lose the paper trail
  entirely. Correct sequence: issue first, record the `PaymentRecord`
  after; an issued-but-unpaid licence surfaces on the attention queue to be
  chased. The full invoice pipeline stays available for customers who need
  paper *before* money (tenders).
- **Activation approval** (MANUAL_APPROVAL/RISK_REVIEW policies): keep the
  machinery, keep AUTOMATIC as the universal default (it already is —
  `activation_policy.py::DEFAULT_MODE`).
- **Offline-policy assignment, entitlement overrides, release-channel
  pinning:** licence-detail actions for the rare case, never issuance
  fields.
- **CRM enrichment** (contacts, addresses, notes): after the sale, never
  gating it.

### B.3 The one defect to fix regardless of any redesign

`fulfillment.py:206` discarding the plaintext key (§0.4). Fix shape: return
`full_key` from `fulfill_order`, render it once in the fulfill response the
way `licensing/routes.py::issue` renders `revealed_key` — same ADR-9
discipline, same "reload never re-shows" property. Record as AUDIT-NNN.
*(Collaborator's area.)*

### B.4 The minimal issuance path — the target

**One screen: "Issue licence". Three acts.**

1. **Pick or inline-create the customer** (one legal-name field when new).
2. **Pick the package** (Shop / Chain / Pilot) **+ extra-device count**
   (default 0) — term shown, defaulted to 1 year from today.
3. **Press Issue** (recent-auth MFA prompt exactly as today) → the key is
   revealed once, beside a pre-composed bilingual WhatsApp message
   containing the key + activation instructions, and a "record payment"
   affordance that can be used now or later.

Behind the button, in order, all existing services, no new writes invented:
`create_subscription` (ACTIVE, start/end set from the term — **new: this
path finally sets term dates**, device_allowance derived) →
`create_license` (platforms derived from plan, `device_limit` =
included + extras, `valid_from/until` mirroring the term) →
`issue_license_key` (idempotency key generated server-side as today). Every
audit row, status-history row, and issuance-event row falls out unchanged
because the services write them.

**Step count: 6 → 3 for a cash sale (with a term, which today's 6 can't
even produce); 13 → 3 for the same sale routed through the pipeline today.**
The quote→invoice pipeline remains, unchanged, as the tender path — with
§B.3 fixed so it actually delivers a key.

**What is deliberately NOT lost:** every item in the load-bearing list
above, verbatim. The cut is forms and sequencing, not records or gates.

*(Implementation home: a new thin route module beside
`owner/app/licensing/routes.py` calling only canonical services — the same
"orchestrate, never insert" contract `fulfillment.py` documents. Collaborator's
area; hand over with this section as the spec. The `attention/` module is the
right host for the "issued, unpaid" chase queue.)*

---

## C. "Owner controls everything" — what that means concretely

### C.1 The levers that exist today (verified)

| Lever | Where it lives | When it bites on the install | Offline behaviour |
|---|---|---|---|
| Licence lifecycle (suspend / revoke / reactivate) | `licensing/routes.py::transition` (per-target RBAC + MFA) | Next check-in or assertion expiry (TTL + 24h cadence, `standard-v1` policy) | Signed grace ladder: WARNING → GRACE (14d) → RESTRICTED read-only; data preserved |
| Device slots | `device_limit` + `device_slot_ops` (release/replace/temporary exception ≤90d) | At activation — an inherently online moment | N/A (offline devices already activated keep working) |
| Term / renewal / past-due | `renewal_requests.py` pipeline → Part W assertion fields (`assertion_fields.py`) | Next check-in re-resolves everything fresh | Same ladder |
| Offline policy itself (grace lengths, cadence) | `offline_policy.py`, per-licence assignment | Signed into every assertion | It IS the offline behaviour — and its enum structurally cannot express a destructive instruction |
| Entitlements | `resolve_entitlements` → signed → persisted → re-read per request | Would bite per-request — **but nothing consumes them (§0.2)** | Snapshot-based: zero network on the request path, by design |
| Activation gating | `activation_policy.py` (AUTOMATIC default, MANUAL/RISK_REVIEW available) | At activation | N/A |
| Sync relay | `owner/app/sync/routes.py`, licence-scoped streams | Continuously, when reachable | Outbox buffers locally; devices diverge-then-converge; **selling never stops** |
| Release download authorization | `releases/distribution.py` via the external API, channel on the licence | On download — but **no product-side auto-updater consumes this yet**; updates are manual installers today | N/A |
| Pilots, emergency extensions | `pilot_lifecycle.py`, `emergency_extensions.py` | Via assertion fields | Extension is precisely the offline-emergency lever |

So "Owner controls everything" is already ~80% true for the *commercial*
install — state, devices, term, grace — all of it through one channel (the
signed assertion) with the correct offline posture. What is missing is
narrower and more specific than the owner's framing suggests:

### C.2 What Owner should control and does NOT — in priority order

1. **Selling an extra device in one act.** The meter is devices; the add-on
   is PLANNED; ADD_ON fulfillment is unimplemented; the only real path is a
   five-state renewal-request pipeline or SQL. **Build: an "Add devices"
   action on the licence page** — quantity → `device_limit += n` (FOR
   UPDATE, MFA, audited, mirrored to `subscription.device_allowance`), plus
   an optional PaymentRecord in the same screen. The renewal pipeline's own
   apply code (`renewal_requests.py:300-336`) is the exact template,
   including the deliberate "never kick an active device on decrease" rule.
   This is the highest-revenue-per-line-of-code item in the company.
2. **Feature switches that actually switch something.** Wire the two
   entitlement consumers named in §A.6 (AI, WhatsApp) + the branch cap.
   Three gates make the tier table real; the transport for all of them
   already ships. Absent/≤0 = unenforced stays the wire rule, so nothing
   regresses for existing licences.
3. **Authoring entitlements without SQL.** The `seed-commercial-packages`
   command (§A.6.1) covers launch; a licence-page override editor (writing
   `LicenseEntitlement` with `_validate_typed_value`) covers the exceptions.
   The read-only preview at `licensing_admin/entitlement-preview` already
   shows the resolution with sources — the write side is the missing half.
4. **The AI token lifecycle.** Today `AURA_AI_BEARER_TOKEN` is hand-placed
   env config per install (and has already leaked into git history once —
   the comment in `config.py` records it). Once `ai_assistant_enabled` is an
   entitlement, per-customer token provisioning/rotation should ride the
   same channel (a check-in-delivered credential or per-licence token on
   the droplet's auth layer). Until then AI enablement is install-time
   labour, not Owner control.
5. **Seeing usage truth.** The assertion/check-in stream is deliberately
   one-way on business data (`FORBIDDEN_ASSERTION_MARKERS`) — right and
   untouched — but check-in *request* metadata (app version, platform,
   last-seen) is already Owner-visible; surfacing "installs that have not
   checked in for N days" on the attention queue closes the loop support
   actually needs. (Partially exists via expiry/queue scans — verify with
   the collaborator before building.)

### C.3 What Owner must NOT control — the offline line, drawn explicitly

The product's one non-negotiable: **a shop with no network keeps selling.**
Anything that makes Owner reachability a precondition for a sale-path action
is a design error, full stop. Device-local by doctrine, never Owner-governed:

- The sale path itself: sales, returns, cash sessions, stock, pricing,
  promotions (local clock decides liveness — already documented in the v23
  entry), login/PIN attribution.
- Enforcement reads: every guard reads the **persisted snapshot** of the last
  verified assertion, never the network (`flask_guard.py` re-reads
  `licensing_state` per request — local SQLite). This is already correct;
  the entitlement gates in §C.2.2 inherit it automatically.
- Device→branch pinning (`config.json`, deliberately never synced — ROADMAP
  2026-08-30), printer/hardware config, UI language.
- E-invoicing: credentials live on the install, submission goes
  install→JoFotara directly with its own outbox/kill-switch. An Owner outage
  must never block a legal obligation. Keep Owner out of this path entirely.
- Backups and export: local, and available even in RESTRICTED state — the
  data-hostage promise depends on Owner having *no* lever here.

**Things that would cross the line if built naively — named so nobody builds
them:** an online licence check per sale; seat/entitlement checks that
consult Owner at enforcement time (predecessor E4/D8 already forbids the
sync-apply variant); AI on the sale path; e-invoicing routed through Owner;
remote data wipe of any kind (the offline-policy enum's "no destructive
option" is a doctrine, not an accident).

**The relay caveat, stated honestly:** if Owner infrastructure is down,
multi-device shops keep selling but stop converging — stock and the money
ledger go stale across tills until it returns. That is the accepted design
(outbox buffers, cursors resume). But the moment devices are the paid meter,
the relay is the service being paid for: droplet uptime, backups of the
Owner Postgres, and the preflight runbook (`seed-rbac`, trust-anchor
freshness — both already bitten once) stop being dev-ops hygiene and become
the product's SLO. Budget it like one.

---

## D. Questions only the owner can answer

1. **Is the 50 JOD per additional device one-time or per year?** Everything
   in §A assumes **per year** (recommended: devices fund the relay and
   support, and a perpetual device slot is a support liability with no
   revenue). One sentence settles it; the catalog supports either.
2. **Sign off the two catalog numbers:** Shop 180 JOD/yr, Chain 420 JOD/yr
   (and 60/40/60 for the add-ons). These are PlanPrice rows, adjustable any
   time without code; the *structure* (two tiers + device meter + add-ons)
   is the part this document asks him to lock.
3. **AI as opt-in add-on at 60 JOD/yr** — confirm he accepts it NOT being in
   any base tier (the data-locality argument in §A.5), and that the droplet
   gets a capacity ceiling before the tenth subscriber.
4. **Chain launch posture:** hold Chain until wave C ships, or sell
   early-access now at pilot pricing with the roadmap in writing?
5. **Pilot policy:** 30 days standard, who approves pilots (the pipeline
   requires an approval step today), and does a pilot include the AI add-on
   for taste-making or not?

---

*Verification note, per house rules: every "exists/absent" claim above was
checked against code this session (files cited inline). The two claims taken
on prior verified record rather than re-read: the restricted-mode measurement
in CLAUDE.md (2026-08-24) and the demo WhatsApp placeholder credentials
(memory, desktop-exe wiring gaps). Another agent is editing `products/` and
`commercial_runtime/identity` concurrently — line numbers in those trees may
drift; symbol names are the stable reference.*
