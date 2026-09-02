# Is Aura Retail sellable? — status, with how each line was checked

Written 2026-09-02. **Every line says how it was verified.** Where something
was not run, it says so rather than inheriting confidence from a green test
suite — this cycle produced a till that displayed US dollars in a dinar shop
while 43 JS suites, 113 Python files and 238 Android tests were all passing,
so "the tests pass" is not evidence that a thing works.

Legend:

- **RUN** — the real artefact was executed and the result observed.
- **TESTED** — covered by a suite that was executed, but the artefact itself
  was not driven end to end.
- **UNVERIFIED** — not checked this cycle. Not the same as broken.
- **BLOCKED** — needs the owner, or hardware/credentials not present here.

---

## The money path

| Item | State | How |
|---|---|---|
| Server computes every persisted total | **TESTED** | `retail_pricing_test.py`; `create_sale` ignores any client-submitted total (AUDIT-002/003) |
| Desktop till preview matches server pricing | **TESTED** | `retail_pricing_parity_test.py` — 17 scenarios through BOTH implementations, 4 mutations caught |
| Jordanian dinar renders at 3 decimals | **RUN** | Driven at 390/768/1440; POS totals, chart axes and cash-tendered step all measured after the dollar bug |
| **Phone till QUOTES a different number than it CHARGES** | **RUN — DEFECT** | Android preview is `sell_price × qty`: no tax, no discount, **no promotions**. Server applies all three. Cashier reads one price, receipt prints another. Recorded in ROADMAP; the fix is a product decision |
| Promotions resolve identically on both clients | **TESTED** | The parity test above. Android is NOT covered — it has no promotion logic at all |

## Licensing and the commercial model

| Item | State | How |
|---|---|---|
| Device limit enforced | **TESTED** | Server-side, `owner/tests/test_commercial_ops_add_devices.py`, `DEVICE_LIMIT_REACHED` |
| Unlicensed install is read-only | **TESTED** | `test_pre_activation_states_deny_new_mutation`; documented in CLAUDE.md |
| `POST /api/admin/employees` has NO licence guard | **RUN — GAP** | Zero `require_license_capability` in `onboarding_routes.py`. A restricted licence can still add staff. Shared with Clinic, so the fix needs a scoping decision |
| `EXTRA_DEVICE` add-on is sellable | **RUN — NOT READY** | Exists in the catalog but sits at `PLANNED`, not `AVAILABLE`. This is the 50 JOD one-time fee |
| Licence activation end to end, on a clean machine | **UNVERIFIED** | Needs the clean-machine rehearsal in `go-live-runbook.md` Part 2 |

## Sync (multi-device)

| Item | State | How |
|---|---|---|
| Outbox, cursors, quarantine, pruning | **TESTED** | Owner suite; `owner/app/sync/pruning.py` has its own tests |
| `flask sync prune-events` scheduled | **BLOCKED** | Code and tests exist. Nothing schedules it. Needs a systemd timer on the droplet |
| Two real devices converging on one shop | **UNVERIFIED** | Never exercised with two physical installs |
| Business day is configurable | **RUN** | Was unreachable — no client could set it, so every install bucketed on each writing device's own clock. Now a card in Admin Center, proved against the real route |

## The three surfaces

| Item | State | How |
|---|---|---|
| Desktop web | **RUN** | 60 screenshots, three widths, both languages. Clipping/overflow findings 106 → 2 |
| Phone web | **RUN** | Rail replaced by a five-slot tab bar and a More sheet; driven at 390px, sheet opens, rows navigate, no page errors |
| Android | **RUN** | On a real Mi Note 10. Launch 2.9s. Emulator figures (12.7s) were environment, not the app |
| Owner Control Center | **RUN** | 26 screenshots of the whole surface; clipped/overflow findings 0 |
| Desktop and phone read as one product | **IN PROGRESS** | The accent already matches (`#F43F5E` on both). Type, density and surfaces do not yet |
| Android Sign In contrast | **RUN — DEFECT** | 2.81:1 measured on device, below the 3.0 floor. Cause not isolated; the obvious fix computes worse |

## Feature parity

Android is missing **seven** things the desktop has — counted, not estimated
(20 Android routes vs 19 web nav destinations, different sets):

WhatsApp reports · Email notifications · Promotions · Branches management ·
Audit log · Stock accuracy · Exceptions queue

Two things believed missing are actually present: **Employees** (a real route,
gated on `RetailSession.isAdmin`) and **Settings**.

## Reachability — features with no doorway

| Surface | State | How |
|---|---|---|
| Retail | **TESTED, list empty** | `retail_route_reachability_test.py`. Six defects found and closed this cycle; `KNOWN_GAPS` is now empty |
| Owner | **TESTED, 12 open** | `owner/tests/test_route_reachability.py`. 402 routes; 12 complete backends with no UI — including `customers.update`, three lead actions, and `subscriptions.correct_payment` |

## Operations — all BLOCKED on the owner

- Droplet bring-up and the clean-machine rehearsal (`go-live-runbook.md` Parts 1–2)
- `EXTRA_DEVICE` priced and set `AVAILABLE`
- A systemd timer for `flask sync prune-events`
- A real WhatsApp number — the configured one is Meta **test tier** and can only
  message pre-verified recipients
- The demo Owner subscription was CANCELLED as of 2026-08-17; re-confirm before demoing
- iOS needs a macOS host

---

## The honest answer

**Not yet sellable to a paying customer**, and the blockers are short:

1. The phone till quoting a different number than it charges. A cashier reading
   a wrong price aloud is a trust failure, not a cosmetic one.
2. Employee creation ignoring the licence, which undercuts the per-device model
   the whole commercial design rests on.
3. Nothing has ever been installed on a clean machine and activated end to end.
   Every other line above is about code that works; this is the one about the
   product being deliverable.

**Pilotable with a shop you can babysit** — yes, once (1) is decided, provided
that shop runs no promotions and has no VAT configured, which is the only
condition under which the phone's quote matches its charge today.
