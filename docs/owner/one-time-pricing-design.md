# One-time / perpetual pricing — options, costs, and a recommendation

**Status:** DESIGN + DECISION document. No source file changed. Written
2026-08-31 against `feat/launch-readiness` (worktree
`ci-hardening-w0.3-continue`). **Revises** `docs/owner/packages-and-issuance-
design.md` (the annual design, same author) in response to two owner
decisions recorded in ROADMAP.md and one direct request:

1. **Device fee: 50 JOD ONE-TIME** (ROADMAP 2026-08-30, overriding that
   document's per-year recommendation — his call, taken).
2. **"Can you present another one time payment plans?"** — a request for
   perpetual-licence options, which §A.5 of the annual design had explicitly
   declined to offer ("Annual only at launch; no perpetual SKU"). This
   document does what that section refused to do, properly costed, and says
   at the end whether the refusal should stand.

All prices JOD. Conversion used throughout: 1 USD = 0.709 JOD (peg), so
$48/month = 408 JOD/yr, $12/month = 102 JOD/yr, $60/month = 510 JOD/yr.

---

## 0. Verified facts this design stands on

Each was checked by reading code this session (or is cited to the commit that
traced it). They are not background — every pricing conclusion below leans on
at least one of them.

**0.1 Licences issued today never expire — and which way that cuts.**
Traced in commit 55bb4b1: the product enforces term via `subscription_status`,
which only leaves ACTIVE via `run_expiry_scan`, which selects
`Subscription.end_date.isnot(None)` (`owner/app/commercial_ops/expiry_scan.py:70`)
— and no creation path ever sets an end date; both term columns are nullable
with no default.

- Under the **annual** model this is a revenue defect: every licence sold is
  accidentally perpetual, invisible to the expiry scan and the whole Part W
  term machinery. The annual design's §B.4 minimal-issuance path exists
  partly to fix it (it finally sets term dates).
- Under a **one-time** model it is the correct behaviour, already
  implemented and already load-tested by every licence issued so far. A
  perpetual SKU needs *zero* new expiry code — it needs the §B.4 fix to
  become **conditional**: set `end_date` for ANNUAL plans, deliberately
  leave it NULL for ONE_TIME plans. One `if` on `plan.billing_model`
  turns the defect into the feature.
- The catalog already anticipates this: `billing_model` enumerates
  `ONE_TIME/MONTHLY/ANNUAL/PILOT/CUSTOM`
  (`owner/app/models/catalog.py:116`). A perpetual plan is a catalog row,
  not a schema change.

**0.2 The default offline policy never restricts on time alone.**
`standard-v1` (`owner/app/licensing_service/offline_policy.py`): check-in
every 24h, 14-day offline grace, warning from day 4 offline, and
`hard_expiry_behavior="WARN_ONLY"`. The product-side evaluator
(`commercial_runtime/licensing_contracts/policy_evaluator.py`) is explicit:
when grace is fully exhausted under WARN_ONLY it returns `GRACE_PERIOD`, not
`RESTRICTED` — "commercial mutation is never blocked by time alone."
`RESTRICTED` arrives only via a *signed* commercial fact: the last verified
assertion carrying `subscription_status` EXPIRED / CANCELLED / SUSPENDED, or
PAST_DUE beyond `commercial_grace_end`. Consequences:

- A perpetual licence under the default policy behaves exactly as a
  perpetual buyer would demand: **even if Owner's servers vanish forever,
  the install nags and keeps selling.** Vendor-death-proof by existing
  default, not by new work.
- The stricter posture exists in the same enum
  (`RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA`) and is assignable
  **per-licence** (`assign_policy`, audited, MFA-context). So "perpetual
  licences warn forever, subscription licences restrict after grace" is
  expressible entirely in the existing policy table — no new enum member,
  no product change.
- Honest corollary for the annual model: today an annual customer who
  simply stays offline forever also never restricts (their last assertion
  says ACTIVE, and WARN_ONLY never times out). If annual terms are ever
  meant to bite offline evaders, assign the RESTRICT policy to annual
  licences. That is an annual-model gap, noted here because this document
  is where the difference between the two licence shapes gets decided.

**0.3 The sync relay has no commercial gate.** `owner/app/sync/routes.py`
verifies device signature, timestamp, nonce, and rejects only on
`installation.status` (suspended/deactivated/replaced, line 164–172). It
never consults `subscription_status`, term, or any entitlement. Today, any
activated device pushes and pulls forever. Any option that says "sync stops
when services lapse" **needs new enforcement** — named in §6, small,
Owner-side only.

**0.4 "Updates" are not remotely enforced at all.** Release-channel
authorization exists Owner-side (`releases/distribution.py`) but no
product-side auto-updater consumes it; updates are manual installers
(annual design §C.1, re-confirmed). "Stops receiving updates" is therefore
enforced by *not handing over the next installer* — human-enforced, zero
build. This makes an updates-gated option cheap to run honestly.

**0.5 E-invoicing costs the vendor nothing recurring and never touches
Owner.** Submission goes install→JoFotara directly, credentials on the
install, own outbox (annual design §C.3 — the offline line). So e-invoicing
*submission* can be included in a perpetual licence at zero marginal cost.
What cannot be promised perpetually is **compliance maintenance**: when ISTD
changes the spec, the fix arrives as an update, and updates are a service.
Every option below words this carefully rather than promising "JoFotara
forever."

**0.6 The AI assistant is a hard-recurring cost.** The LLM droplet is
$48/month = 408 JOD/yr for all customers combined
(`docs/launch-readiness/infrastructure.md`, measured), before any scaling.
At the already-decided 60 JOD/yr add-on price, seven subscribers cover the
box. **AI can never be inside any one-time price** — a perpetual right to a
service with a perpetual cost and zero recurring revenue is strictly
underwater from customer one. Every option below keeps AI annual-only.
(Enforcement: the `ai_assistant_enabled` entitlement + one decorator
argument, already specified in the annual design §A.6.2.)

**0.7 Check-ins are near-free and must continue regardless of model.** One
signed HTTPS POST per device per 24h (`checkin_scheduler.py`,
`standard-v1`). They are the channel for suspend/revoke (fraud, chargeback),
device management, and the grace ladder. A perpetual licence still checks in;
it just never hears "expired."

---

## 1. The cost side, quantified — where pure perpetual stops paying for itself

The tension, stated as arithmetic rather than gesture. **Assumptions, all
stated, all adjustable:**

| Assumption | Value | Basis |
|---|---|---|
| Fixed infrastructure, today | 510 JOD/yr ($60/mo) | Measured, infrastructure.md. Of which 408 is the AI droplet — funded separately by the AI add-on (0.6), leaving **~102 JOD/yr** (Owner box) as the base the licence business must carry. |
| Marginal infra per served customer | 2–4 JOD/yr | Check-ins ≈ nothing; relay = small JSON batches (≤500 rows/call) well inside droplet bandwidth; Postgres growth + a droplet upsize step (~+102 JOD/yr per ~$12 tier) every few hundred customers. |
| Support per active customer, steady state | 2–3 h/yr × 5–8 JOD/h loaded | Jordanian support-tech cost ~600–800 JOD/mo fully loaded; WhatsApp-based support. Year 1 is higher (onboarding); steady state after. → **10–24 JOD/yr, call it ~18** |
| Compliance/updates engineering | amortised, not per-customer | Real but lumpy (a JoFotara spec change is a fixed cost whenever it lands); funded by whatever recurring line exists. |
| Serving cost per **entitled** customer/yr | **~20–25 JOD** | 18 support + 2–4 infra. |
| Serving cost per **lapsed** customer/yr (relay off, no support entitlement) | **~1–2 JOD** | Check-ins only. |
| Post-JoFotara-wave sales rate | 10–20 new sales/yr | The mandate is a one-time adoption spike (annual design §A.3); assume sales decay after it. This is the assumption that decides everything — flagged as such. |

**The pure-perpetual solvency line.** Under pure perpetual (Option A), every
customer ever sold is entitled forever. Year-k economics:

    revenue(k)  = P × s          (price × new sales that year — nothing else)
    cost(k)     = F + c × N(k)   (fixed base + serving cost × entire installed base)

With P = 650 JOD (the price A would need, §2.A), c = 22 JOD/yr, F = 102 JOD/yr:

    solvent while  N  ≤  (650 × s − 102) / 22  ≈  29.5 × s − 4.6

**The installed base may never exceed ~30× the annual sales rate.** N only
grows and s only shrinks after the mandate wave, so the lines are guaranteed
to cross:

| Sales rate s (per yr) | Max solvent installed base N |
|---|---|
| 30 (wave peak) | ~880 |
| 15 (post-wave) | ~438 |
| 10 | ~290 |
| 5 (mature) | ~143 |
| 0 (product sunset / sales pause) | **0 — every active customer is a loss** |

Concretely: **a pure perpetual model stops covering its own serving cost at
roughly 300–450 customers** on the post-wave sales assumption — a base the
JoFotara wave alone could build in its first two years. And that table
already *excludes* AI (0.6) and prices the fixed base at today's single small
droplet. This is not a pricing problem fixable with a bigger P: P = 1,000
moves the crossing to ~45× s, it does not remove the crossing. The only
structural fix is to put recurring costs behind a recurring line — which is
Options B and C.

**The mirror-image number:** under B/C, a lapsed customer costs ~1–2 JOD/yr
(check-ins only, by construction). One year of Care at 90 JOD funds ~4
customer-years of entitled serving cost. The solvency question stops
existing, at any customer count — that is the entire argument of this
document in one sentence.

---

## 2. The options

Three shapes were mandated for consideration; all three are here, priced.
One is rejected, one is runner-up, one is recommended.

### Option A — Pure perpetual: pay once, everything forever — **REJECTED**

- **Customer pays:** Shop 650 JOD once (Chain 1,500 once). Nothing ever
  again. Devices 50 JOD one-time each beyond 2 (decided). The price must be
  ~3.5× annual because it has to pre-fund an unknown number of serving
  years — which makes it *worse* against the annual sticker in the exact
  market segment (small shops) most sensitive to the first number.
- **Years 2, 3, 5:** identical to year 1 — licence active, updates supplied,
  relay on, support answered, forever. (AI still excluded per 0.6 — which
  already breaks the "everything" promise and has to be explained at the
  point of sale.)
- **Cost to serve / break-even:** §1. Solvent only while installed base
  ≤ ~30× annual sales; guaranteed insolvent when the mandate wave ends or
  sales pause. The ROADMAP already flags the one-time device fee's
  20-till-hypermarket margin worry — Option A is that worry applied to
  *every* line of the price list, unboundedly.
- **Enforcement:** the only honest virtue: **zero new enforcement.** Never
  set `end_date` (today's behaviour), default WARN_ONLY policy, done. The
  never-expire fact (0.1) is fully "the correct behaviour already
  implemented" here.
- **Why rejected, in his numbers, not principle:** every relay byte, support
  message, and JoFotara spec-change fix from 2027 to forever is paid for
  out of money collected in 2026. At 15 sales/yr the business is underwater
  from roughly customer 440, and *each additional sale after that makes the
  hole deeper* — the perverse case where growth is loss. A one-time device
  fee on top (his decision, kept) compounds it: the largest installs
  generate the most perpetual load with the least perpetual revenue.

### Option B — Perpetual licence + optional annual services, unbundled — runner-up

The classic on-premise shape: the licence and the services are two visibly
separate purchases from day one.

- **Customer pays:** Shop licence **380 JOD once**; "Aura Care" services
  **90 JOD/yr, optional from day 1** (Chain: 900 once + 210/yr). Devices
  50 JOD one-time each (slot, forever) + Care scales per device (§3).
- **Care covers:** updates (features + JoFotara compliance), the sync
  relay, support (WhatsApp, business hours), and eligibility to buy the AI
  add-on (which remains its own 60/yr line, per 0.6).
- **Year 2, 3, 5 — with Care:** as annual model. **Without Care (ever, or
  lapsed):** licence stays ACTIVE forever (0.1); software runs and sells
  forever, WARN_ONLY (0.2); no updates; relay off at lapse (§6 gate) so a
  multi-device shop keeps selling on every till but the tills stop
  converging; no support; no AI. Data, backup, export: untouched, always
  (§5).
- **Cost to serve:** entitled customer ~20–25 JOD/yr funded by 90 Care;
  lapsed customer ~1–2 JOD/yr funded by the licence margin. Solvent at any
  N. Break-even on the *licence* price itself: 380 covers the ~40–50 JOD
  year-1 onboarding-heavy serving cost plus sales cost with wide margin.
- **Enforcement:** the §6 build list (relay gate + conditional term-setting
  + ONE_TIME plan row). Updates/support human-enforced (0.4).
- **Why runner-up and not winner:** unbundling invites the worst buyer
  path — skip Care from day 1, never experience the service, then hit the
  first JoFotara spec change or dead till with no update channel, no
  support relationship, and a grievance ("I paid 380 and it broke"). In a
  WhatsApp-reputation market that grievance is expensive out of all
  proportion to the 90 JOD not collected. The fix is exactly Option C.

### Option C — Perpetual licence with 12 months of Care included, renewable — **RECOMMENDED**

Same machinery as B; different bundling, and the bundling is the point. One
sticker price that includes the first year of everything; Care renewal is a
choice made *after* a year of experiencing what Care is.

- **Customer pays once:** **"Aura Retail Shop — One": 450 JOD** — perpetual
  licence, 2 devices, and 12 months of Aura Care (updates + compliance +
  sync relay + support). **Chain — One: 1,050 JOD** (perpetual, 2 devices,
  12 months Care at Chain service level — same wave-C honesty constraint as
  the annual design: don't sell the comparison screen before pinning
  exists).
- **Recurring, optional:** **Care renewal 90 JOD/yr Shop / 210 JOD/yr
  Chain** (± per-device scaling, §3). AI add-on 60 JOD/yr, Care-holders
  only. WhatsApp alerts 40 JOD/yr, Care-holders only (it needs Meta
  onboarding labour — a service by definition).
- **Year 2 (renewed):** indistinguishable from an annual customer.
  **Year 2 (not renewed):** licence ACTIVE forever, sells forever, nag-free
  (no ladder ever fires commercially — subscription_status is ACTIVE and
  stays so, 0.1); frozen at last installed version; relay off — single-till
  shops (most of the market) feel *nothing* except no updates; multi-till
  shops keep selling per-till but stop converging; no support; no AI.
  **Year 3, 5:** same, indefinitely. Re-joining Care later is allowed
  (price: current-year Care, no back-payment — punishing returners loses
  more than it collects; state this in the policy so support doesn't invent
  it ad hoc).
- **Customer arithmetic vs the annual plan (Shop):** 450 up front vs 180/yr.
  Never-renewer breaks even against annual at 2.5 years. Faithful renewer:
  cumulative 450/540/630/720/810 vs annual 180/360/540/720/900 — crossover
  in year 4, cheaper forever after. Early years favour the vendor, long run
  favours the customer — the correct shape for a product funding its own
  launch.
- **Cost to serve / break-even:** identical mechanics to B. Year-1 serving
  (~40–50 JOD incl. onboarding) is inside the 450 with room; every
  subsequent entitled year is inside the 90; every lapsed year costs ~1–2
  JOD carried by licence margin. **There is no customer count at which C
  goes underwater** — that is the designed property, not a happy accident.
- **Enforcement:** §6 list, same as B.

### The shape deliberately not offered: "perpetual, updates forever, cloud never"

A fourth shape exists in the wild (own the software + all updates forever,
but no cloud services). Rejected without pricing: updates are the channel
JoFotara compliance rides on, and promising them forever recreates Option
A's unfunded perpetual obligation in its most expensive form (engineering
time), while being unenforceable anyway (0.4 — we'd be promising, not
gating). Compliance work must sit behind the recurring line.

---

## 3. The device meter under one-time pricing

The owner decided **50 JOD one-time per additional device**; that decision
is kept, and it actually *fits* a perpetual licence better than it fit the
annual one — the slot and the licence now have the same lifetime shape, and
`device_limit` enforcement at activation (`DEVICE_LIMIT_REACHED`,
server-side, Ed25519-bound) doesn't know or care about billing periods.

What must not be kept is the ROADMAP's flagged consequence — 20 tills paying
once while loading the relay forever — and under B/C it doesn't have to be:
**the slot is one-time; the *service on the slot* is annual.** Care scales
with devices:

    Care (Shop)  = 90 JOD/yr  covering 2 devices, + 10 JOD/yr per additional device
    Care (Chain) = 210 JOD/yr covering 2 devices, + 10 JOD/yr per additional device

The 20-till hypermarket: 450 + 18×50 = 1,350 one-time, then Care
90 + 180 = 270/yr — which now actually funds the relay traffic and support
load those 20 devices generate. If the per-device Care line is rejected as
too fiddly, the fallback is three flat Care tiers (2 devices: 90; 3–5: 130;
6+: 220). Either is an Owner catalog value, no product code.

Under Option A the one-time device fee has no such counterweight — every
extra device is perpetual load with zero recurring revenue, which is one of
the reasons A is rejected rather than repriced.

**Non-renewers and devices:** a lapsed customer's already-activated devices
keep working (0.2 — activation is the online moment; enforcement reads the
local snapshot). Whether a lapsed customer may activate *new* slots they
already paid for: yes — the slot was sold one-time and activation is
licence-machinery, not Care-machinery. They simply won't converge with the
other tills until Care resumes. This keeps the promise clean: everything
bought once works forever; everything ongoing is Care.

---

## 4. Offline policy — perpetual vs subscription, answered from the code

Read before answering, per the brief (`offline_policy.py`,
`policy_evaluator.py`):

- **A perpetual licence still checks in** every 24h (0.7) and keeps all
  Owner levers that protect *the vendor* (suspend/revoke for fraud or
  chargeback — signed decisions override every timer,
  `policy_evaluator.py`) and *the customer* (device replace/release).
- **Unreachable for a long time, perpetual:** WARNING from day 4 offline,
  GRACE from day 14 — and then, because `standard-v1` is WARN_ONLY,
  **GRACE_PERIOD indefinitely: nagging, never restricting.** The signed
  ladder cannot produce RESTRICTED for a licence whose subscription_status
  is ACTIVE — and a perpetual subscription is ACTIVE forever (0.1). This is
  exactly the behaviour a perpetual buyer is owed, including the extreme
  case: Owner's droplet dies permanently and the shop keeps selling. Put
  that sentence in the sales material — no local competitor can say it.
- **Unreachable for a long time, subscription (annual):** identical today —
  which is arguably too generous (0.2's corollary: offline-forever evades
  expiry). If the owner wants annual terms to bite offline, assign annual
  licences the `RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA` policy at
  issuance. **So yes — the answer differs between perpetual and
  subscription, and the difference is expressed per-licence in the existing
  policy table** (`assign_policy`), zero new enum members, zero product
  change. Proposed rule, one line in the issuance path: ONE_TIME plans →
  `standard-v1` (WARN_ONLY); ANNUAL plans → a new `annual-v1` policy row
  with RESTRICT behaviour and the same timings.
- What lapsing Care does **not** do: it never touches licence state. A
  lapsed-Care perpetual install is ACTIVE_ONLINE every day it can reach
  Owner. The only thing that changes at lapse is the relay answering 400
  with a distinct reason code (§6) and humans not answering support. The
  grace ladder is licence machinery and stays out of the Care fight
  entirely — mixing them would violate the doctrine in §5.

---

## 5. What a non-renewing customer KEEPS and LOSES — the doctrine table

Consistent with the codebase's own settled philosophy: restricted installs
go read-only and preserve data, never hostage
(`RETAIL_RESTRICTED_ALLOWLIST`, `DATA_PRESERVED_FAMILY`) — and a lapsed
perpetual customer is *better* than restricted: they are a licensed owner.

| | KEEPS (forever, offline-proof) | LOSES (until Care resumes) |
|---|---|---|
| Licence | ACTIVE — never expires, never restricts, never re-verified against payment (0.1, 0.2) | — |
| Selling | Sales, returns, cash sessions, stock, promotions, reports, AR/AP, permissions — on every paid device | — |
| Data | All of it: history, backup, restore, export — unconditional, the same promise the trial and RESTRICTED states already make | — |
| E-invoicing | Submission keeps working (install→JoFotara direct, 0.5) | Compliance *updates* when ISTD changes the spec — stated at sale, not discovered |
| Software | The version they have, forever | New versions, fixes, features |
| Multi-device | Every till sells independently | Convergence: relay off, tills diverge until Care resumes (outbox buffers; §6.4 caps it) |
| Support | — | WhatsApp support, entirely |
| AI / WhatsApp alerts | — (annual add-ons, Care-holders only) | — |

The one sentence for the sales page: *"You own it. Stop paying and it keeps
working and your data stays yours — you just stop getting updates, sync
between tills, and our help."* Every clause in that sentence is enforced by
machinery named in this document or explicitly by humans.

---

## 6. What must be BUILT — named, and nothing else

All Owner-side items are the collaborator's area — handed over as spec, not
a work order for this branch.

1. **The relay Care gate (the only new machine).** `owner/app/sync/routes.py`
   currently gates on `installation.status` only (0.3). Add, after the
   existing signature verification resolves the licence: deny push/pull
   with reason code `SYNC_SERVICES_LAPSED` when the licence's Care is
   lapsed. Smallest honest representation of "Care until": a nullable
   `care_until` date on `Subscription` (Alembic migration — owner/ uses
   Alembic, this is not the products' PRAGMA world), set by issuance
   (+12 months) and by a "renew Care" action; NULL means never-had /
   perpetual-Option-A grandfather = allowed, mirroring the reserved
   "absent = unenforced" contract so nothing regresses for existing
   licences. Deliberately **not** an entitlement flag: entitlements ride
   the signed assertion into the product, and the product must NOT consume
   this (a product-side sync gate would put Owner reachability adjacent to
   the sale path — §C.3 territory). The gate lives only where the service
   lives: the relay.
2. **Conditional term-setting in the minimal issuance path** (annual design
   §B.4, amended): ANNUAL plan → set `start_date`/`end_date` (fixing 0.1's
   defect); ONE_TIME plan → leave `end_date` NULL *on purpose*, set
   `care_until`. Also assign the offline policy per §4 (standard-v1 vs
   annual-v1). One branch, one new policy row.
3. **Catalog rows:** the two ONE_TIME plans (`billing_model="ONE_TIME"` —
   value already enumerated, 0.1) with `effective_date` set (the §0.5
   unsellable-plan trap from the annual design applies here too), Care as
   a renewable line — the renewal-request pipeline
   (`commercial_ops/renewal_requests.py`) is the existing machinery for
   "customer pays again, extend a date," and extending `care_until` is a
   strictly simpler variant of what it already does to terms. Fold into
   the `seed-commercial-packages` command already specified in the annual
   design §A.6.1.
4. **Product-side, small:** the relay client must treat
   `SYNC_SERVICES_LAPSED` as a *quiet, persistent* condition — surface
   "sync paused — services expired" in the sync status UI, back off to a
   slow retry, and **cap the local outbox** (or prune acknowledged-locally
   segments) so a never-renewing multi-device shop doesn't grow an
   unbounded queue for a relay that will never answer. Without this, §5's
   "keeps working forever" quietly accumulates a disk-space debt.
5. **Explicitly NOT built:** any licence-expiry behaviour for ONE_TIME
   plans (the whole point), any updates-enforcement machinery (0.4 —
   human-enforced until an auto-updater exists), any product-side Care
   awareness beyond item 4, AI inside any one-time price (0.6).

Items 1–3 are days of Owner-side work on existing patterns. Nothing here
touches the products' schema version or the sync migration in flight.

---

## 7. Does the annual design survive? Yes — as the other track, and here is the honest split

This document does **not** replace the annual design; it retracts one
sentence of it (§A.5's "no perpetual SKU") and keeps the rest. The two
tracks sell to different buyers the annual design itself identified (§A.3):

- **Annual (180/420)** stays on the price list as the low-commitment door:
  the shop that balks at 450 up front, the JoFotara-driven buyer who wants
  the cheapest compliant year, pilots converting gently. Jordanian SMBs
  "distrust auto-renewing subscriptions but accept annual licences with
  renewal" — that finding stands.
- **One (450/1,050)** is the answer to the buyer this market genuinely
  contains in numbers: the shop owner whose mental model is the legacy
  local POS quote — *thousands of JOD one-time plus maintenance* — and who
  hears "subscription" as "rent." Option C is exactly the shape that buyer
  already understands and already pays for maintenance under; we are
  pricing the familiar model sharply, not educating the market into a new
  one. That is the strongest argument *for* one-time pricing here, and it
  is market-specific, not principled.
- Same catalog, same meter (devices, 50 one-time, identical across
  tracks), same Care service delivered to both (annual holders have Care
  by definition). Two price columns, one product, one support motion.
- Risk named rather than hidden: **track cannibalisation.** A faithful
  renewer pays less on One from year 4 (§2.C). That is acceptable — money
  earlier is worth more to a launching product, and the 2.5-year
  break-even protects against buy-once-and-churn — but the two prices must
  be set together forever after: keep One ≈ 2.5× annual and Care ≈ 0.5×
  annual whenever either moves.

---

## 8. Recommendation

**Offer Option C alongside the existing annual plans. Do not offer pure
perpetual (Option A) at any price.**

- **Shop — One: 450 JOD** one-time (perpetual licence, 2 devices, 12 months
  Care) · Care renewal 90 JOD/yr + 10/yr per device beyond 2.
- **Chain — One: 1,050 JOD** one-time · Care renewal 210 JOD/yr + 10/yr per
  extra device. (Same wave-C shipping honesty as the annual Chain tier.)
- **Devices: 50 JOD one-time each** — unchanged, the owner's decision, and
  a better fit under One than it was under annual because Care carries the
  recurring load (§3).
- **Annual Shop 180 / Chain 420** stay as the second track (§7).
- **AI 60/yr and WhatsApp alerts 40/yr remain annual add-ons for
  Care-holders only** — never inside a one-time price (0.6).

Why C and not A, in his numbers: A's revenue stops while ~20–25 JOD/customer
of annual serving cost never does, so it is solvent only while the installed
base stays under ~30× the yearly sales rate — roughly 300–450 customers on
post-mandate sales assumptions, a ceiling the launch wave itself could
breach, after which every sale deepens the loss (§1). C sells the *same
perpetual ownership feeling* — pay once, own it forever, vendor-death-proof
by the existing WARN_ONLY default, data never hostage — while placing every
cost that recurs (relay, support, compliance updates, AI) behind a 90 JOD
line the customer can decline without losing their shop's history or their
ability to sell. It is the model the Jordanian market already understands
from legacy POS vendors, priced against them aggressively; it needs days of
Owner-side work (§6), no product schema change, and it converts the
never-expiring-licence finding from a defect to be fixed into the exact
behaviour one track is selling — with one `if` on `billing_model` keeping
the other track honest.

**Decisions only the owner can make (compact):**
1. Sign off 450 / 1,050 / 90 / 210 and the +10/yr-per-device Care scaling
   (or pick the flat Care tiers in §3).
2. Confirm the compliance-update caveat wording for lapsed customers (§5's
   e-invoicing row) — it will be read back to us the first time ISTD
   changes the spec.
3. Confirm re-joining Care is current-year-price, no back-pay (§2.C).
4. Accept that annual licences get the RESTRICT offline policy while One
   licences keep WARN_ONLY (§4) — this is the one place the two tracks are
   deliberately treated differently by the machinery.

---

*Verification note, per house rules: 0.1 is cited to commit 55bb4b1's trace
plus `expiry_scan.py:70` re-read this session; 0.2–0.7 were each verified by
reading the named files this session (`offline_policy.py`,
`policy_evaluator.py`, `state_machine.py`, `checkin_scheduler.py`,
`owner/app/sync/routes.py`, `commercial_runtime/sync/relay_client.py`,
`owner/app/commercial_ops/assertion_fields.py`, `owner/app/models/
catalog.py:116`, `docs/launch-readiness/infrastructure.md`). The support-cost
and post-wave sales-rate figures in §1 are assumptions, labelled as such in
their table — they are the two numbers to re-run this arithmetic with when
real figures exist. Another agent is editing `products/retail/frontend`
concurrently; nothing in this document depends on files in that tree.*
