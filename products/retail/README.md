# Aura Retail

POS and retail management: a Flask backend (raw `sqlite3`, no ORM), a vanilla-JS
frontend (no framework, no build step), shipped as a Windows desktop app and
embedded in the Android app via Chaquopy.

This file is the map. It answers three questions — **where do I go**, **how do I
run and test it**, **how do I install it** — and nothing else. Architecture,
conventions and the state of the product live in the repo-root `CLAUDE.md`,
`DESIGN.md` and `ROADMAP.md`.

Every command below has been run. Where something is unverified, it says so.

---

## Where things are

```
products/retail/
├── backend/          the Flask app and all business logic
│   ├── app.py          entry point — START HERE (see "Run it" below)
│   ├── api/            retail_api.py: every HTTP route the product has
│   ├── core/           business logic (pricing, tax, notification hooks)
│   ├── database/       schema.py: versioned migrations (PRAGMA user_version)
│   └── security/       runtime key material, GENERATED — see "Local files" below
├── frontend/         vanilla JS/CSS/HTML, served directly by Flask
│   ├── index.html      the shell; app-shell.js + subsystem-retail.js are the app
│   ├── css/            main.css holds the design tokens and all five themes
│   ├── locales/        en.json / ar.json — every user-visible string
│   ├── brand/          logo and brand assets
│   └── lib/, fonts/
├── desktop/          launcher_retail.py — the pywebview desktop window
├── packaging/        PyInstaller spec + Inno Setup installer script
├── tests/            200 suites — see tests/README.md for the index
└── docs/             retail-specific notes (most docs live in /docs at the root)
```

The two frontend files that matter are `app-shell.js` (navigation, session,
theming — the chrome around every screen) and `subsystem-retail.js` (every
retail screen: POS, products, reports, transfers…). They are large on purpose;
there is no bundler.

---

## Run it

From `products/retail/backend`:

```bash
python app.py
```

Then open <http://127.0.0.1:5000/>.

Two environment variables control it, and the second one matters more than it
looks:

| Variable | Effect |
|---|---|
| `PORT` | Port to listen on. Default `5000`. |
| `AURA_APP_DATA` | **Where the shop's databases and key material are written.** Point it at an empty directory to get a genuinely fresh install. |
| `AURA_STANDALONE` | Runs as a standalone product rather than inside the larger monorepo layout. |

The exact command verified for this document, which is also the one to use when
you want a clean install to poke at without touching your existing data:

```bash
AURA_STANDALONE=1 PORT=5099 AURA_APP_DATA=/some/empty/dir python app.py
# GET /api/health -> 200 {"status":"ok"}
# GET /            -> 200, serves the app shell
```

It serves with `waitress` when installed and falls back to the Flask dev server.

> **Do not start it with `flask --app app run`.** `app.py` documents this in its
> own comments: that imports the module-level `app` object without ever calling
> `init_app()`, so the process comes up looking perfectly healthy and is not.
> `python app.py` is the one correct way.

The desktop window (pywebview, with Edge/Chrome `--app` and then the default
browser as fallbacks):

```bash
python products/retail/desktop/launcher_retail.py
```

### The thing that confuses everyone first

**A fresh install is READ-ONLY, not unlocked.** With no licence activated,
`POST /sales` returns `403 LICENSE_INACTIVE` — you cannot ring a sale or open a
cash drawer. Reads, returns and customer payments are on a deliberate
allowlist. This is the licence gate working as designed, not a bug, and it is
pinned by tests. Activate a licence at `/static/licensing.html`.

---

## Test it

Canonical runner — this is what CI runs, and it covers both the Python and the
Node suites, one subprocess per file:

```bash
python products/run_all_tests.py retail
```

A single suite:

```bash
<venv>/Scripts/python.exe -m pytest products/retail/tests/<name>_test.py -q
node products/retail/tests/<name>_test.js
```

**Rule: one test FILE per pytest process.** These suites boot a Flask app at
import time, so running several in one process poisons them (AUDIT-010). The
canonical runner already does this correctly; only hand-rolled `pytest` calls
get it wrong.

> **Do not reorganise `tests/` into subdirectories.** The runner discovers suites
> with a **non-recursive** glob (`discover()` in `products/run_all_tests.py`, and
> `SUITES` lists directories explicitly). Moving files into subfolders makes them
> silently stop running while the suite still reports green — a cleanup that
> quietly deletes your coverage. `tests/README.md` indexes them instead.

`tests/README.md` groups all 200 suites by area with one line each, so you can
find the right one without grepping.

---

## Install / package (Windows)

Two commands from the repo root. Both were run end to end on 2026-09-08 and the
installed copy was launched and verified:

```powershell
<venv>\Scripts\python.exe -m PyInstaller products/retail/packaging/aura_retail.spec --noconfirm
"C:\Users\<you>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" products\retail\packaging\aura_retail_setup.iss
```

Produces `dist/AuraRetail/AuraRetail.exe`, then
`dist/installers/AuraRetail-Setup-<version>.exe`.

Inno Setup is a separate download and is not part of the Python environment. The
PyInstaller step alone gives you a runnable `AuraRetail.exe` without it.

---

## Local files you will see that are NOT in the repository

None of these are committed, all are ignored, and none is a leaked secret:

| Path | What it is |
|---|---|
| `backend/security/secret.key` | Generated at first boot. Local only. |
| `backend/database/subsystems/*.db` | Local databases, including `licensing.db`. |
| `__pycache__/` | Python bytecode. |

Do not commit them and do not copy them between machines — **the licence is
device-bound**, so a copied `licensing.db` will not make another machine
licensed, and copying databases around is how two installs end up disagreeing
about the same shop.

---

## When something looks wrong

- **Every mutation returns 403** — no licence is activated. See the read-only
  note above; this is the expected state of a fresh install.
- **Tests pass but the screen is broken** — run the product. This repo has a
  documented history of green suites over broken UI; `tests/README.md` says
  which suites actually render screens.
- **A migration error mentioning a table that should exist** — you may be running
  against a database touched by a different branch's schema version. Mixed
  schema state produces misleading errors that do not reproduce on a clean
  install; point `AURA_APP_DATA` at an empty directory to get one.
