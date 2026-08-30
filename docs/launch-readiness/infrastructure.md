# Infrastructure — measured state, 2026-08-20

Everything here was measured on the live hosts, not inferred from config.

## Droplets

| Name | IP | Size | Runs |
|---|---|---|---|
| `aura-retail-demo` | 161.35.219.243 | 2 GB / 1 vCPU, fra1 | Owner CC **production** (`aura-owner`, gunicorn on 127.0.0.1:5551), `aura-retail`, `/opt/downloads` (APK + Windows ZIP), and a dept-nav review instance |
| `aura-llm-demo` | 104.248.35.215 | 8 GB / 4 vCPU, fra1 | `ollama` behind `caddy`, **plus** a second dept-nav Owner instance and its Postgres |

## The AI assistant's actual topology

The desktop and Android assistants both call
`https://104-248-35-215.sslip.io/api/generate`, which is Caddy on
`aura-llm-demo` reverse-proxying `127.0.0.1:11434` (Ollama). Caddy gates it on
an `Authorization: Bearer …` match — which is why an unauthenticated probe
returns **401, not a connection failure**. The service is healthy.

Models present: `phi3.5:3.8b` (2.2 GB) and `phi3:mini` (2.2 GB).

The bearer token is in `/etc/caddy/Caddyfile` on that droplet. It must match
`AURA_AI_BEARER_TOKEN`, which is baked into the APK at build time.

**Correction, 2026-08-30.** This paragraph used to end: *"Nothing in the build
currently passes it, so a rebuilt APK gets 401 → 503 regardless of the app code
being correct."* That is no longer true, and it was the most alarming sentence
in this document, so it should not be left standing. `android/aura-retail/app/
build.gradle` now resolves the secret three ways — `-PaiBearerToken=…` on the
command line, `aiBearerToken=…` in `local.properties`, or the
`AURA_AI_BEARER_TOKEN` environment variable (the CI-secret path) — and when it
resolves to nothing the build prints a banner naming the exact consequence:
the assistant renders, accepts a question, and always fails with "AI assistant
is temporarily unavailable."

So the failure mode is now LOUD rather than silent. It is still a real failure:
a build with no token produces an APK whose assistant cannot work. The banner
means nobody ships one without having been told, which is the difference that
matters. The same mechanism covers the licensing and sync secrets, each with
its own consequence text — the licensing one is worth reading, because a blank
there does not weaken licensing, it removes it, and the resulting APK is the
product given away.

## Cost — and a correction

Roughly **$60/month**: ~$12 for the 2 GB box, ~$48 for the 8 GB box.

An earlier recommendation in this project was to downsize `aura-llm-demo` for
savings. **That advice was wrong** — it is the LLM host, and downsizing to 2 GB
would kill the AI assistant outright.

Measured headroom on `aura-llm-demo`: 7941 MB total, **710 MB used**, 7230 MB
available at idle. Ollama sits at ~39 MB RSS when idle and only pulls the model
into memory to serve a request, so idle usage is misleadingly low — the number
that matters is the ~2.2 GB model plus working memory during inference.

So a **4 GB droplet (~$24/month, saving ~$24)** is plausible for `phi3.5:3.8b`
with roughly 1.5 GB left for the OS, Caddy, Postgres and gunicorn. It is not
generous: concurrent requests, or ever moving to a larger model, would not fit.
Downsizing also **changes the droplet's IP**, which breaks the `sslip.io`
hostname baked into `config.py:193` and therefore every already-installed
client. That makes it a coordinated change, not a slider drag — do it before
shipping installs, or not at all.

Recommendation: leave it at 8 GB until launch traffic is understood. The saving
is small relative to the risk of the assistant becoming the thing that is
"not working" again.

## Model choice — the "better LLM, no more resources" question

Asked directly by the product owner: is there a better model that does not cost
more resources? Three separate questions hide in that one, and they have
different answers.

**Stated up front, because this document's own convention is to separate
measured from believed: none of the model-quality claims below were measured on
this hardware.** Everything about droplet sizing, memory and cost above WAS
measured on the live hosts. The comparison below is a recommendation to test,
not a result. It would be settled by an afternoon: pull the candidate
alongside the incumbent on the same box, run the same twenty real questions
through both — including Arabic ones — and compare.

### 1. A same-footprint swap

The incumbent is `phi3.5:3.8b` (2.2 GB quantised). Several 3B-class instruct
models released since occupy the same ~2 GB and are generally stronger at
instruction-following and, specifically, **substantially stronger at Arabic** —
the phi family's weakest area, and the one that matters most here, because this
product ships an Arabic/RTL interface for the Jordanian market. A shopkeeper
asking the assistant a question in Arabic is not an edge case for this product,
it is the main case.

This is the answer to the question as asked: same droplet, same memory, same
$0 additional cost, `ollama pull` and a config change. **It is also the only
option on this page that does not require a decision from anyone about data
leaving the shop.** If only one thing is done, do this one.

### 2. The latency problem is not a model problem

`aura-llm-demo` has 4 vCPU and no GPU, so every token is generated on CPU.
Roughly 30 seconds to a full answer is a property of that, not of which 3B
model is loaded — a better 3B will answer better in about the same time. Nobody
waits 30 seconds at a till. Whatever else happens, the assistant should not sit
in any path a cashier is blocked on.

### 3. Hosted, and why it is a decision rather than an optimisation

A small hosted model would answer in one to three seconds instead of thirty,
cost fractions of a cent per question, and remove the $48/month that the 8 GB
droplet exists to pay for — cheaper AND faster AND better, on every axis this
document can measure.

**The axis it cannot measure is the one that decides it.** The assistant is
retrieval-augmented: answering "how did we do last week" means sending real
sales figures off the premises. For some buyers that is unremarkable. For a
co-operative, a foundation, or any institution with a data-residency rule, it
is disqualifying — and those are named buyers for this product.

So it cannot be a default. It can be a per-company opt-in, off unless switched
on, exactly the shape e-invoicing and licensing enforcement already use: an
install that never enables it must not pay a byte for it. That also makes the
two options above complementary rather than competing — the local model stays
the floor for shops that will not send data out, and the hosted one is the
upgrade for shops that will.

**What this does NOT change:** downsizing or replacing the droplet still moves
its IP, which still breaks the `sslip.io` hostname baked into `config.py` and
therefore every already-installed client. That is a coordinated change, not a
slider drag — the same caveat the cost section above records, and it applies
just as much to switching the assistant off a droplet as to shrinking one.

## Redundant deployments worth reconciling

There are **two** dept-nav review instances:

- `deptnav-161-35-219-243.sslip.io` on the retail droplet
- `deptnav.104-248-35-215.sslip.io` on the LLM droplet (active since 2026-08-19)

Since the dept-nav UI was merged into the production lineage and deployed to
`owner.actionaura.me` on 2026-08-20, **both are now redundant**. Neither costs
extra money — they fit inside droplets already paid for — but each runs a
Postgres and a gunicorn, and each is another internet-facing login surface for
a product that has not launched yet.

**Decision, 2026-08-20 — owner's call: KEEP BOTH. Neither is to be retired.**

Retiring the LLM-droplet instance was briefly considered and then explicitly
reversed by the owner before anything was changed. Nothing was stopped,
deleted, or reconfigured — the only commands run against it were read-only
health probes. Recorded here so a later cleanup pass does not "helpfully"
remove it: **`deptnav.104-248-35-215.sslip.io` stays.**

Both instances cost no extra money — they fit inside droplets already paid
for. The standing trade-off, for the record, is that each runs its own Postgres
and gunicorn and is another internet-facing login surface for a product that
has not launched yet; that is accepted.

Note that the surviving review instance's database is on a **different Alembic
graph** than production's, so it cannot be used to rehearse a production
migration without being rebuilt first.

## Deployments

### 2026-08-20 — Wave 2 Owner CC fixes → production

28 files (9 Python, 1 new static script, 13 templates, 5 translation artifacts).
No new third-party dependencies: the one new module, `app/metrics_contracts.py`,
imports only `dataclasses` and `decimal`.

The headline reason to ship this promptly was not the metric fixes — it was that
**"Void expense" had no confirmation dialog in production**. CSP had silently
killed the inline `onsubmit`, so a destructive financial action had lost its
only guard. That is now restored.

Verified after restart, in this order:
1. `py_compile` on every deployed Python module — clean.
2. Service active, and `journalctl` free of tracebacks / ImportError / SyntaxError.
3. HTTP smoke: the new `auto-submit.js` serves 200 (the restored filters are
   dead without it), and health, login, the dashboard CSS, `confirm.js`, the
   brand mark, the APK download and both licensing endpoints all still 200.
4. All 15 changed templates compiled through the **live app's own Jinja
   environment** — these pages sit behind login, so a missing include or a bad
   `url_for` would otherwise surface only when a real user opened the page.

Also compiled `.mo` catalogs were shipped, not just `.po` — the `.po` alone has
no runtime effect, so a translation-only deploy that forgets the `.mo` silently
does nothing.

## Development machine — leaked processes, measured 2026-08-20

Not a droplet issue, but it belongs with the measured state because it is a
standing drag on the machine every build and test run shares.

| PID pair | Started | What | CPU burned |
|---|---|---|---|
| 29204 / 19544 | 2026-08-12 | Owner app, `app.run(port=5551)` | small |
| 21676 / 24936 | 2026-08-18 | Owner app, `app.run(port=5551)` **again** | ~8 CPU-hours |
| 25620 / 43424 | 2026-08-17 | `aura-retail-demo/scripts/watch_and_build_exe.py` | ~2.5 CPU-hours |
| 9540 / 36152 | 2026-08-15 | `enhance_server.py` from an agent job tmp dir | small |

Two Owner dev servers cannot both hold port 5551, so one of the pair has been
failing to bind and retrying since 2026-08-18 — that is where the eight CPU-hours
went. The exe watcher's 2.5 CPU-hours is the same shape of problem.

Deliberately **not** killed: the watcher rebuilds executables, so a kill during a
build leaves a corrupt artifact, and the dev servers belong to the operator's own
session rather than to any agent. Flagged for the owner to clear. The VS Code
jedi language server in the same process list is legitimate and should stay.

Worth a `Stop`/`SessionEnd` hook that sweeps `app.run(port=5551)` strays, since
this has now accumulated across at least three separate days.

## Rollback points

| What | Where |
|---|---|
| Owner CC production, pre-UI-deploy | `/opt/aura-owner.bak-20260820` on 161.35.219.243 |
| Owner CC production, pre-Wave-2-deploy | `/opt/aura-owner.bak-20260820b` on 161.35.219.243 |
| Previous published APK | `/opt/downloads/AuraRetail-2026-08-17.apk` |
