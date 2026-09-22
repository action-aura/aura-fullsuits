# Infrastructure — measured state, 2026-08-20 (the hosts measured here no longer exist)

Everything here was measured on the live hosts of the time, not inferred from
config. **That hosting account has since been abandoned entirely** — access
was revoked and both hosts were destroyed — so the inventory, IPs and cost
figures below are historical record only, not a place to go back to. See
`docs/ops/free-hosting-owner.md` for the current hosting plan.

## The AI assistant's actual topology (as it was)

At the time, the desktop and Android assistants both called a small hosted
Ollama instance reverse-proxied through Caddy on port 11434. Caddy gated it on
an `Authorization: Bearer …` match — which is why an unauthenticated probe
returned **401, not a connection failure**; the service was healthy.

Models present: `phi3.5:3.8b` (2.2 GB) and `phi3:mini` (2.2 GB).

The bearer token lived on that host and had to match `AURA_AI_BEARER_TOKEN`,
which is baked into the APK at build time. There is currently no AI endpoint
for the app to point at.

**Correction, 2026-08-30 (still true, independent of the host).** This paragraph used to end: *"Nothing in the build
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

## Cost — historical

That host ran roughly $60/month total (~$12 for the 2 GB Owner box, ~$48 for
the 8 GB AI box), with headroom measured at 7941 MB total / 710 MB used / 7230
MB available at idle — Ollama sits at ~39 MB RSS when idle and only pulls the
model into memory to serve a request, so idle usage understated the real
number, which is the ~2.2 GB model plus working memory during inference. A
smaller box was considered and rejected: it would have left too little margin
for concurrent requests or a larger model, and resizing moves the host's IP,
which breaks any DNS name baked into a shipped client — a coordinated change,
not a slider drag, whichever host this ends up on. Current cost planning lives
in `docs/owner/phase9r/production-cost-model.md` and
`docs/ops/free-hosting-owner.md`.

## Model choice — the "better LLM, no more resources" question

Asked directly by the product owner: is there a better model that does not cost
more resources? Three separate questions hide in that one, and they have
different answers.

**Stated up front, because this document's own convention is to separate
measured from believed: none of the model-quality claims below were measured on
this hardware.** Everything about host sizing, memory and cost above WAS
measured on the live hosts of the time. The comparison below is a recommendation to test,
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

This is the answer to the question as asked: same host, same memory, same
$0 additional cost, `ollama pull` and a config change. **It is also the only
option on this page that does not require a decision from anyone about data
leaving the shop.** If only one thing is done, do this one.

### 2. The latency problem is not a model problem

The AI host had 4 vCPU and no GPU, so every token was generated on CPU.
Roughly 30 seconds to a full answer is a property of that, not of which 3B
model is loaded — a better 3B will answer better in about the same time. Nobody
waits 30 seconds at a till. Whatever else happens, the assistant should not sit
in any path a cashier is blocked on.

### 3. Hosted, and why it is a decision rather than an optimisation

A small hosted model would answer in one to three seconds instead of thirty,
cost fractions of a cent per question, and remove the recurring cost that a
small dedicated AI host exists to pay for — cheaper AND faster AND better,
on every axis this document can measure.

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

**What this does NOT change:** any future host swap still moves the AI
endpoint's address, which breaks whatever hostname is baked into a shipped
client. That is a coordinated change, not a slider drag, on any host this
ends up on.

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

Not a hosting issue, but it belongs with the measured state because it is a
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

The pre-deploy backup paths recorded here (`/opt/aura-owner.bak-*`) lived on
the host described at the top of this document, which no longer exists —
there is nothing to roll back to on it today. Any future host needs its own
rollback convention; see `docs/ops/free-hosting-owner.md`.
