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

Not removed here: tearing down a running service is destructive and reversible
only by redeploying, so it wants an explicit decision rather than a cleanup
sweep. Recommendation: keep **one** review instance as a staging target for the
UI redesign work, retire the other.

Note that the surviving review instance's database is on a **different Alembic
graph** than production's, so it cannot be used to rehearse a production
migration without being rebuilt first.

## Rollback points

| What | Where |
|---|---|
| Owner CC production, pre-UI-deploy | `/opt/aura-owner.bak-20260820` on 161.35.219.243 |
| Previous published APK | `/opt/downloads/AuraRetail-2026-08-17.apk` |
