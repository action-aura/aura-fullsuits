# Aura Restaurant — plan entry: what it is, what it costs, where it sits

**Status:** PLAN ENTRY, not a build. Written 2026-08-31 against
`feat/launch-readiness` (worktree `ci-hardening-w0.3-continue`), retail schema
**v25 on disk** (variants shipped, `bd272ca`), **v26 claimed and in flight**
(modifiers, being built concurrently — expect churn in `products/retail`).
The owner's ask, verbatim: *"can we make a version for restaurants? not now
but make it in the plan."* Execution is explicitly deferred; this entry exists
so that when he says "now", the decision, the scope, and the sequence are
already settled and only need re-verification against whatever has landed
since.

Per repo convention, this gets no Phase/Milestone number here — it goes into
ROADMAP.md as an unscheduled item until the owner assigns one.

---

## 1. Recommendation up front

**Aura Restaurant is NOT a separate product. It is Aura Retail — same
codebase, same binary, same `AURA_RETAIL` product code, same licence key —
sold as a named edition: a distinct plan row in the Owner catalog, a distinct
pitch and demo, and a POS configured for food service.**

Ship it in two releases:

* **R1 — counter-service** (cafés, fast food, shawarma/juice/coffee — the
  bulk of Jordan's مطاعم وكافيهات long tail): modifiers (in flight) + kitchen
  tickets + split tender & tips + a takeaway/dine-in tag. Remaining effort
  after v26 lands ≈ **1.5–2 variants-waves** (~1.5–2 sessions).
* **R2 — full table service** (open tables, settle-at-table, split by seat,
  service charge, course/fire): a genuinely new, synced order lifecycle.
  ≈ **+2–3 variants-waves**. Only after R1 has paying counter-service sites.

Price: **unchanged — 250 JOD per device lifetime, 50 JOD per extra device**,
because the owner's own investor model already counts restaurants and cafés
*inside* Aura Retail's 1,725-licence line at that price. A kitchen screen or
waiter handheld **consumes a device slot** (recommended; see §5). No
entitlement wiring, no new licensing machinery, no second build.

Sequence position: **after** the still-owed go-live work (clean-machine exe
test, droplet setup) and the v26 wave now in flight; kitchen tickets
immediately after v26; split tender/tips in parallel; **before** inter-branch
transfers (v24) and far before any Clubs work. Argument in §6.

---

## 2. Separate product or Retail packaged differently? — argued, not assumed

### 2.1 The domain test (the Clinic comparison)

Aura Clinic earns its separate codebase because healthcare changes the
**nouns**: patients, appointments, clinical records — entities Retail has no
concept of and should never grow. A restaurant changes almost no nouns. Its
menu items are `products`. Its stock is `inventory_balances`. Its cash drawer,
shifts, X/Z reports, returns, AR/AP, branches, staff permissions, e-invoicing,
sync, licensing — all identical, all built, all hardened by three phases of
adversarial verification. What a restaurant changes is two **behaviours**:

1. items carry spoken options that must reach a kitchen (modifiers + tickets);
2. in full service, the sale's *lifecycle* inverts — the order exists before
   payment, accumulates over time, and settles late (§4.4).

Behaviour 1 is already being built inside Retail as schema v26, on the
invisible-unless-opted-in doctrine: a grocery that never configures a modifier
group pays one empty indexed probe per sale. The codebase has, in effect,
already voted: restaurant capability is a Retail feature wave, not a fork.
Behaviour 2 is R2 — a new entity (`orders`) *alongside* the sale machinery,
not a rewrite of it, and it too can be invisible to non-adopters.

### 2.2 The maintenance test

Two codebases double every future cost forever: every schema wave, every sync
fix, every security audit, every licensing change lands twice. This suite
already carries Retail + Clinic, `master` already lags feature branches by
hundreds of files, and two people work here. A restaurant fork would share
~90% of its files with Retail on day one and start diverging on day two —
the worst possible ratio for a fork. Hard no.

### 2.3 The mechanical test — what "separate product" would actually cost

Verified this session: a separate catalog product is nearly free on the Owner
side (`_CANONICAL_PRODUCTS` is a two-entry list in
`owner/app/catalog/services.py:28`; the key prefix map is
`owner/app/security/license_keys.py:22`) — **and expensive on the product
side, for zero return**. Activation hard-binds the product:
`activation.py` raises `PRODUCT_MISMATCH` when the licence's product differs
from the client's declared code, the assertion verifier raises
`ASSERTION_PRODUCT_MISMATCH` on every check-in
(`commercial_runtime/licensing_contracts/assertion_verifier.py:191`), and the
desktop launcher refuses to boot anything but `AURA_RETAIL`
(`products/retail/desktop/launcher_retail.py:266`). So an `AURA_RESTAURANT`
product code means either a second build flavour (installer, update channel,
test matrix — forked forever) or weakening the product-binding check that
exists to prevent key reuse across products. And what would the separate code
*gate*? Nothing: the owner's model has no feature tiers, and
`required_entitlement` — the mechanism that could gate features — has **zero
production callers** under `products/` (re-verified by grep today).

### 2.4 What "distinct commercial product" means mechanically, given what exists

Everything a distinct SKU needs is already a catalog value or already shipped:

| Commercial need | Mechanism | Status |
|---|---|---|
| A line on the price list: "Aura Restaurant" | A `Plan` row under the `AURA_RETAIL` product (plans differ in name, code, billing model, currency, price, included devices — the exact surface needed) | Exists today; one row via the planned `seed-commercial-packages` command |
| Different price, if ever wanted | `PlanPrice` value | Catalog data, zero code |
| Restaurant look on receipts/app | Per-buyer white-label branding | Shipped (`cefe5a6`) |
| A restaurant demo that closes deals | Demo seed with menu + modifier groups + defaults | Small work item in R1 (and it de-risks §7's tap-budget perception trap) |
| Device metering | `device_limit` at activation, Ed25519-bound | Enforced today — the meter IS the restaurant upsell (§5) |

One prior ruling to amend: `docs/owner/packages-and-issuance-design.md` §A.5
says restaurants, when scheduled, are "its own product surface … not a plan
row." Its instinct was right — do not sell a restaurant *tier* before the
capability exists — but its mechanism reading is superseded: the workflow
differences land as Retail waves (v26 is already one of them), and the
commercial artefact is precisely a plan row plus a pitch. The one genuinely
separate *surface* a restaurant may eventually want is a kitchen-display
view — a screen, not a codebase (§4.2).

---

## 3. What restaurant-ready requires — verified against the code, 2026-08-31

Every "missing" claim below was re-grepped today, because this repo produced
four stale missing-feature claims in one week, all understating what works.
What was found: **no** occurrence of tips/gratuity, service charge, kitchen,
dine-in/takeaway, or table management anywhere in retail backend or frontend;
modifiers have zero tables on disk yet (v26 in flight); and two things the
brief might assume missing that **exist** — `held_sales` (park/resume carts,
v11) and a full multi-row synced `payments` ledger (`_record_payment`,
`retail_api.py:7555`).

Unit: 1 ≈ the variants wave (v25), per
`docs/launch-readiness/variants-and-modifiers-design.md` §5–6.

| # | Capability | What exists today | What's missing | Effort | Blocks first restaurant sale? |
|---|---|---|---|---|---|
| 1 | **Modifiers** | v26 claimed; design complete (§4 of the variants doc); **in flight right now** | The build itself, incl. the returns line-addressing fix (`return_items.sale_item_id`) that must NOT be cut — it closes a real wrong-refund defect modifiers activate | 2–2.5 | **YES — and already underway** |
| 2 | **Modifier config sync (wave M2)** | v26 tables carry UUID ids + `row_version` from day one, deliberately, so M2 is mechanical | Three catalogue-style apply branches + emission at config CRUD | ~0.5 | **YES for multi-till** — and restaurants are multi-till by default. Single-till pilot survives without it (menu entered per till) |
| 3 | **Kitchen tickets** | Receipt rendering + 80 mm printer config (`subsystem-retail.js:3466–3481`) | Ticket document (no prices, big modifier text), print-on-checkout hook, station routing config, reprint | 0.5–0.75 | **YES** in minimum form: one kitchen copy per sale. Multi-station routing = refinement |
| 4 | **Split tender** | `payments` is already a rich, synced, multi-row money ledger with one funnel | `create_sale`'s single `payment_method`/`amount_paid` contract (verified: one column each on `sales`), the POS payment screen, Z-report reconciliation by method | 0.75–1 (with tips) | **YES for the vertical pitch**; a cash-dominant counter café could pilot without it, but do not market restaurant without it |
| 5 | **Tips** | Nothing (verified) | A `sales` column, drawer/Z treatment, owner policy on card tips; touches cash-session variance (Phase 4/16 territory — design WITH the drawer) | inside #4 | Same as #4 |
| 6 | **Takeaway vs dine-in** | Nothing | A tag on the sale/ticket, printed on the kitchen ticket | ~0.1 (rides #3) | No — but so cheap it ships with #3 |
| 7 | **Open tabs (counter-service)** | **`held_sales` covers this** — park/resume with label + customer, server-authoritative repricing on resume | Nothing for single-till counter flow | 0 | Already covered |
| 8 | **Tables / open orders (full service)** | `held_sales` is deliberately **local-only, never synced** (its own migration comment says two devices don't need each other's carts — exactly the property a restaurant tab breaks); credit sales to a *named* customer exist (AR) but a table is not a customer | A synced `orders` entity that exists **before payment**, accumulates lines over rounds, and settles into a `sale` at the end — a genuinely new lifecycle, the deepest piece here (§4.4) | 1.5–2 | **No — deliberately.** R2 boundary |
| 9 | **Service charge** | Nothing (verified) | An automatic percentage at sale level; small mechanically but touches subtotal/tax composition and e-invoice figures, so it is a financial-core slice needing its own verification | 0.25–0.5 | No for counter service (uncommon there in Jordan); **yes for full service** → ships with R2 |
| 10 | **Course/fire timing, seats** | Nothing | Course tags on order lines, fire actions to the kitchen | ~1 | No — refinement after R2 |

**R1 total (counter-service, sellable):** #1–#6 ≈ 4–4.85 variants-waves, of
which #1 (2–2.5) is already in flight → **≈1.5–2 waves remain after v26
lands.** This matches the variants doc's 3.5–4.5× sizing plus M2.

**R2 total (full service):** #8 + #9 + polish ≈ **2–3 waves.**

### 4.4 The deepest question, answered: does an order exist before payment?

In Retail today, no — a `sale` is born settled (`create_sale` writes the sale,
its items, its payment, and its stock movements in one transaction; `status`
defaults `'completed'`). Two partial escapes exist and define the R1/R2 line:

* `held_sales` parks a cart pre-completion — but it is a **cashier's draft on
  one device**, deliberately outside the financial-authority boundary and
  deliberately unsynced. Perfect for "prepare order 12 while ringing order
  13". Structurally wrong for "table 5's tab, opened on the waiter's handheld,
  settled at the till" — that requires the draft to be shared state.
* Credit sales exist — a completed sale can settle later via AR — but against
  a *named customer*, and the sale's lines are frozen at ring-up. A table
  accumulates lines for an hour before anyone knows the total.

So full service needs a new synced entity with its own lifecycle
(open → lines added over rounds → settle → becomes a sale; void/transfer as
managed exceptions), and *that* is real design work touching sync, drawer
attribution (which shift owns a table opened on shift A and settled on
shift B?), and the returns/void story. It is exactly the kind of work this
codebase does well when given its own wave — and exactly the kind that sinks
a rushed release. Hence: R2, explicitly not blocking, stated so the owner
sequences with eyes open. Counter-service — pay at the counter, kitchen makes
it — needs none of it, which is what makes R1 genuinely sellable early.

---

## 5. Commercial shape

### 5.1 Price: keep the model's own numbers

The investor model (issue 1.0, 2026-08-28) already counts مطاعم and كافيهات
inside **Aura Retail — 250 JOD per device, lifetime** — restaurants are not a
new revenue line, they are a chunk of the existing 1,725-licence Retail plan.
Two consequences:

* **Building restaurant capability defends revenue already promised.** If the
  product cannot serve a café, the Retail licence count in his own model is
  overstated. This is the mirror image of Clubs (planned in the model, absent
  from the codebase, deferred): restaurant work closes a plan-versus-reality
  gap instead of opening one. That is the single strongest argument for its
  sequence position.
* **No separate price is needed, and inventing one adds friction.** "Aura
  Restaurant" appears on the price list as an edition at the same
  250-per-device; if the owner later wants a premium (Clinic sits at 300),
  that is one `PlanPrice` value — a catalog edit, zero code, decidable the
  week he wants it.

### 5.2 The device meter does the restaurant math by itself

A small shop runs 1–2 devices. A restaurant runs a till + a kitchen screen +
1–2 waiter handhelds: **3–5 slots, at 250 for the first + 50 per extra**. The
meter — already enforced server-side at activation, already the decided model
— makes a restaurant worth roughly 1.5–2× a corner shop with no tiering, no
entitlements, and no new machinery. That is the meter working as designed.

**Does a kitchen display consume a slot? Recommend: yes, at launch.** Every
activated device is a licensing device; a "display-only" exemption requires a
device-class concept that does not exist anywhere in the activation path, and
building it buys a discount, not a feature. 50 JOD one-time for the screen
that runs the kitchen is defensible in a market anchored on
thousands-of-JOD legacy POS quotes. Revisit only if it demonstrably kills
deals — and then price it as a cheaper *slot*, not as a slotless device.

### 5.3 Entitlements: not needed, and say why once

The question "can a restaurant product be gated on?" has a precise answer:
the machinery exists end-to-end (signed `entitlements` in the assertion,
resolver, `required_entitlement` in the capability guard) and **zero
production routes use it** (re-verified today). Wiring it for restaurants
would be building tier-gating the owner's no-tier model explicitly does not
sell. It is also unnecessary: modifiers, tickets, and tabs all follow the
invisible-unless-opted-in doctrine, so a grocery pays nothing for their
existence and a restaurant simply configures them. Gate nothing. If a
paid restaurant add-on is ever wanted, the entitlement wiring is days of
work and this paragraph is where to start.

### 5.4 The honest cost warning that must ride any restaurant push

Restaurants are the **worst-case profile under lifetime pricing**: the most
devices, all-day sync traffic through the relay, the highest support load —
and zero recurring revenue after the sale. This is the exact tension the
one-time-pricing design (`docs/owner/one-time-pricing-design.md` §3, §6)
resolves with an annual Care line and a relay Care-gate. **Decision to take
before marketing the restaurant edition hard, not before building R1:**
adopt the Care mechanism (or an equivalent answer to "who pays for the relay
in year 3") for multi-device sites. Flagged, not decided — it is an
owner/collaborator call and an Owner-side build.

---

## 6. Sequencing — market reach per unit of effort

Recommended order, with the reasoning that survives scrutiny:

1. **Go-live work still owed (clean-machine exe test, droplet setup,
   runbook).** Gates *all* revenue, restaurant included. Nothing below makes
   sense while the product cannot be handed to a stranger's Windows machine.
2. **v26 modifiers (in flight — finish and verify).** Already the plan of
   record; the returns line-addressing fix ships inside it or the wave is not
   done. Also independently valuable outside restaurants (bakeries, phone-
   accessory bundling, per-line notes).
3. **Kitchen tickets (0.5–0.75).** Strictly follows modifiers, small, and it
   converts v26 from "a schema wave" into "a demo that closes café deals" —
   the highest reach-per-effort step on this list.
4. **Split tender + tips (0.75–1), in parallel where staffing allows** —
   independent of modifiers, different files (the §6 disjoint-file-set rule),
   but its tips half needs the owner's policy answer first (§7 Q3).
5. **Modifier config sync M2 (~0.5)** before marketing to multi-till
   restaurants — which is the default restaurant. → **R1 sellable here.**
   Begin counter-service pilots (1–2 friendly cafés, single- or dual-till).
6. **Inter-branch transfers (v24, reserved by name).** After R1, before R2:
   it serves the chain/hypermarket verticals that are *already sellable* and
   is the last "genuinely missing" retail-side item; but it opens no new
   vertical, whereas steps 3–5 do — hence it yields to them.
7. **R2 — tables/open orders + service charge (2–3).** Only with R1 pilot
   feedback in hand; the open-order lifecycle is too expensive to design
   against guesses.
8. **Clubs: stays deferred.** Owner's explicit call (2026-08-31). Nothing
   here assumes it, and restaurant work must not be traded against it —
   restaurants defend planned Retail revenue; Clubs would add a codebase.

Why restaurants beat transfers in the queue (the one contestable call): the
owner's model puts restaurants *inside* the largest licence line; every month
the product cannot serve a café is a month 62.7% of the plan is partly
fictional. Transfers serve buyers the product can already close. Reach per
effort favours steps 3–5, and they are individually smaller than the
transfers wave.

---

## 7. Open questions — only the owner can answer these

1. **R1 target confirmed?** Counter-service cafés/fast-casual first, full
   table service second. If he needs table service for the *first* sellable
   restaurant, R2 moves inside R1 and the distance roughly doubles — say so
   now, not after R1 ships.
2. **Does a kitchen screen / waiter handheld pay the 50 JOD device fee?**
   Recommendation: yes at launch (§5.2). One sentence from him settles it.
3. **Tips policy** (pre-blocks step 4's design): are card tips pooled, paid
   out of drawer nightly, or kept off the drawer entirely — and do tips enter
   cash-session variance? This is a money-handling policy, and the Z-report
   is shaped by it before any code is written.
4. **Service charge at R1 or R2?** Recommendation: R2 (uncommon in counter
   service in Jordan). If any early prospect is a table-service restaurant
   that runs 10% service, it jumps to R1 and adds ~0.25–0.5.
5. **Multi-till restaurant pilots from day one?** If yes, M2 (config sync)
   collapses into the modifiers wave instead of trailing it — the variants
   doc's §8.2 question, still open, now with a deadline attached.
6. **Same price or premium?** 250/device keeps his model intact; if "Aura
   Restaurant" should carry Clinic-style pricing (300), it is one catalog
   value — but the model's Retail revenue math was built at 250, so a change
   re-runs that line of the model.

---

## 8. What this entry deliberately does not do

No schema versions are claimed here (v26 is in flight; R1's remaining waves
and R2 claim their numbers in ROADMAP.md at dispatch time, per the
single-writer rule). No Owner-side work is ordered (`owner/` is the
collaborator's area; §5's catalog rows and any Care/relay gate are handovers).
No Clubs work is implied. And nothing here should be re-read as current after
v26 lands without re-running the §3 verification greps — this repo's
documented failure mode is stale missing-feature claims, and this document
becomes one the day the modifiers wave merges.
