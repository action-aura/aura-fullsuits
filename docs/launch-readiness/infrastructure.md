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
`AURA_AI_BEARER_TOKEN`, which is baked into the APK at build time via
`-PaiBearerToken`. Nothing in the build currently passes it, so a rebuilt APK
gets 401 → 503 regardless of the app code being correct.

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

## Rollback points

| What | Where |
|---|---|
| Owner CC production, pre-UI-deploy | `/opt/aura-owner.bak-20260820` on 161.35.219.243 |
| Owner CC production, pre-Wave-2-deploy | `/opt/aura-owner.bak-20260820b` on 161.35.219.243 |
| Previous published APK | `/opt/downloads/AuraRetail-2026-08-17.apk` |
