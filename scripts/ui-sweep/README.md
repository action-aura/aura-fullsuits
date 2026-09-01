# UI sweep — photograph the product, then look at it

Boots a product for real, drives it with a real browser, and saves a
screenshot of every screen at several widths.

## Why this exists

Everything in this repo had been verified by reading code or running tests.
Nobody had looked at the running product. On 2026-09-01 the first sweep found
four defects in about an hour, while **43 JS suites, 113 Python files and 238
Android tests were all green**:

| Found | Why no test could see it |
|---|---|
| POS totals read `$0.00` beside a `Charge — JD 0.000` button | The spans were seeded with a literal `"$0.00"` in the template and only corrected by `_recalc()`, which runs on cart mutations. An empty cart never mutates, so the till **at rest** was in the wrong currency. |
| All four chart axes hardcoded `'$' + v` | The axis callback is data Chart.js owns. It is never a value this codebase renders into the DOM. |
| `step="0.01"` on cash tendered | Makes a fils amount a step mismatch on a three-decimal currency. Invisible in markup assertions. |
| The admin-device banner filled ~45% of a 390px screen, one word per line | A consequence of a flex **basis**, not of markup. Only a rendered layout shows it. |

The lesson worth keeping: **a money surface is not covered because the money
function is covered.** The formatter was correct and unit-tested throughout.

## Retail

```sh
UI_SHOT_DIR=ui-shots py -3.14 scripts/ui-sweep/retail_ui_sweep.py
```

Self-contained: creates a temp `AURA_APP_DATA`, seeds an active licence,
completes first-run onboarding through the real form, then walks every
sidebar destination at 1440 / 768 / 390.

Needs `playwright` plus `playwright install chromium` in the interpreter you
run it with.

## Owner

Owner runs on its own interpreter (`.venv-owner`) and Postgres, so the server
is a subprocess and the browser is driven from the system interpreter.

```sh
sh scripts/ui-sweep/owner_bootstrap.sh          # throwaway DB, migrations, seed, superadmin
py -3.14 scripts/ui-sweep/owner_ui_sweep.py
```

`owner_bootstrap.sh` uses its **own** database (`aura_owner_uishots`) and
drops it first. It deliberately does not touch `aura_owner_dev`, which carries
the demo customer, its subscriptions and the live licensing signing key.

Owner requires TOTP enrolment on first login with no skip — correct, and a
hard gate for automation. The sweep computes the code from the secret the
enrolment page prints (RFC 6238, stdlib only), against its own throwaway
account in its own throwaway database. It will **not** work against an account
that is already enrolled, because the secret is encrypted at rest and is only
ever shown once; re-run `owner_bootstrap.sh` for a fresh one.

## Reading the results

Screenshots are just evidence — the point is to open them. Two habits that
paid off immediately:

* **Compare a value against its neighbour.** The dollar/dinar bug is obvious
  the moment the totals column and the Charge button are in one frame, and
  invisible in any assertion that looks at one of them alone.
* **Count what repeats.** `owner_dupe_check`-style label counting turned "the
  dashboard feels noisy" into "eight KPI labels each render twice on one
  page", which is actionable.

## Gotchas that cost time

* Screenshot on a **selector**, never on `networkidle`. Both apps resolve i18n
  and a status fetch *after* networkidle, so every early frame came out blank
  and looked like a boot failure.
* Take screenshots with `animations="disabled"` and a bounded `timeout`, and
  let a failed frame be recorded rather than kill the sweep — one page with a
  running animation otherwise photographs nothing at all.
* Owner's login form posts to `/auth/login`; `GET /` only 302s there.
* Do not call `page.content()` while a navigation is in flight.
