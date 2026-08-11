# Phase 9 — Staging Architecture

## Existing deployment baseline (found, not reinvented)

`owner/Dockerfile` and `owner/docker-compose.yml` already exist (Phase 6). They already do the right
*shape* of thing: Gunicorn as the WSGI server, Postgres 17 in its own container, Alembic migrations +
RBAC/catalog/offline-policy seeding run before Gunicorn starts, signing keys on a dedicated named
volume (deliberately separate from the Postgres volume — a real, already-correct security decision
from Phase 6, not something to redo). This is the correct baseline to harden, not replace.

What it lacks for staging (all addressed by this phase, see Milestones 3-6):

- No TLS / reverse proxy — Gunicorn is bound directly to a host port.
- Postgres port `5432` is published to the host (`ports: - "5432:5432"`) — reachable beyond the compose
  network.
- Dev-grade fixed credentials (`aura_owner_dev`) hardcoded into `docker-compose.yml` itself, not an
  external secret.
- No non-root user in the image.
- No dedicated readiness endpoint (only a trivial `/healthz` liveness check with no dependency probes).
- No resource limits, no request-size/timeout configuration exposed.
- No log rotation, no monitoring/alerting.
- Single environment file, no environment separation between dev/test/staging.

## Chosen topology: single hardened host, Docker Compose (not Kubernetes)

Per the spec's own guidance and this project's actual scale (a handful of pilot installations, not
internet-scale): single-host Compose stack, not Kubernetes. Kubernetes would add operational complexity
without a corresponding benefit at this scale, and would delay the one thing that actually matters for
pilot readiness — a proven, recoverable, monitored single-node deployment.

```
INTERNET / approved pilot client
        │
        ▼
  Reverse proxy (Caddy) -- TLS termination, security headers, rate limiting
        │  (internal Docker network only)
        ▼
  Aura Owner (Gunicorn, non-root, internal port only)
        │  (internal Docker network only)
        ▼
  PostgreSQL 17 (internal Docker network only, no host port published)
```

Supporting, same host:
- `backup` — scheduled `pg_dump` + signing-key-metadata + config-inventory backup job, own container.
- `scheduler` — commercial scan scheduler (Milestone 9), own container, single-run locking.
- Prometheus + Alertmanager (or an equivalent lightweight pair) for monitoring, own containers,
  scraping Owner's `/metrics`-equivalent and Postgres exporter, internal network only.
- Log rotation via the container runtime's own logging driver + `logrotate` for file-based logs.

Caddy was chosen over Nginx for staging specifically because it does automatic TLS certificate
management (Let's Encrypt in a real deployment, an internal/self-signed CA in this local-only session)
with materially less hand-written TLS configuration to get wrong — appropriate for a small team
operating staging without a dedicated infra engineer. Nginx remains a documented, equally valid
alternative (see `architecture-decision-record.md`).

## Local-only substitution (this session)

No real domain or publicly-trusted CA is available. The staging stack described above is built and run
for real on this machine via Docker Compose, using a locally-generated CA and a `staging.local`-style
hostname resolved via the host's own hosts file — a real TLS trust boundary is demonstrated (Caddy
does terminate real TLS, headers are real, the reverse-proxy/app/db separation is real), but it is
**not** reachable from the public internet and is not backed by a publicly-trusted certificate. This is
recorded as NOT VERIFIED for the "real staging deployment" gate, not silently presented as equivalent.
