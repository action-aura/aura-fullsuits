# Phase 9 — Architecture Decision Record

## ADR-9.1: Harden the existing Compose stack rather than replace it

**Decision**: build `docker-compose.staging.yml` as a hardened evolution of the existing
`owner/docker-compose.yml`, reusing `owner/Dockerfile`'s Gunicorn entrypoint.

**Alternatives considered**: a from-scratch Kubernetes manifest set; a bare-metal (no container)
systemd-only deployment.

**Rejected because**: Kubernetes adds real operational surface (cluster lifecycle, ingress controller,
secrets provider, etc.) with no benefit at pilot scale (a handful of installations); a from-scratch
non-Docker deployment would throw away the already-correct Phase 6 decisions (signing-key volume
separation, Gunicorn entrypoint, migration-then-seed-then-serve ordering) for no reason.

## ADR-9.2: Caddy as the staging reverse proxy

**Decision**: Caddy for staging TLS termination and security headers.

**Alternative considered**: Nginx (explicitly suggested in the governing instruction as an equally
valid option).

**Chosen because**: automatic certificate management (real deployment: Let's Encrypt; this session:
internal CA) requires materially less hand-written config than Nginx + certbot, reducing the chance of
a staging-only TLS misconfiguration. Nginx remains fully compatible with the same topology if a future
operator prefers it — the reverse-proxy container is a single, swappable piece of the Compose stack.

## ADR-9.3: Single hardened host, not multi-host, for staging

**Decision**: one host runs proxy + Owner + Postgres + backup + monitoring via Compose.

**Rejected**: splitting Postgres onto a separate managed database service.

**Rationale**: at pilot scale, a managed DB service adds cost and a second trust/network boundary to
secure without a corresponding reliability benefit; the real restore-drill requirement (Milestone 7) is
easier to prove end-to-end on a single host. This is documented as a **migration-friendly** choice, not
a permanent one — the Compose file's `db` service can be pointed at a managed Postgres instance later
by changing the connection string alone.

## ADR-9.4: systemd timers over a distributed task queue for scheduled operations

**Decision**: use container-internal locking + a simple scheduler loop (or systemd timers on the host
where the deployment is host-native) for the commercial scans (Milestone 9), not Celery/RQ/a message
broker.

**Rationale**: the existing scans (`expiry_scan.py`, `device_slot_ops.scan_over_limit_licenses`, etc.)
are already idempotent, report-only, single-process functions — introducing a broker adds a new
component to secure and monitor for no functional gain at this scale.

## ADR-9.5: Local-only staging this session (not a deployment-approach decision, a resourcing one)

**Decision**: build and validate the entire stack above locally via Docker Compose; do not fabricate a
remote host. Recorded in `phase8-evidence-reuse-decision.md` and `phase9-baseline.md`. This is not an
architecture choice — the architecture above is designed to be deployed to a real remote host verbatim
(same Compose file, same images, different `.env.staging` values and DNS) — it is a statement of what
could actually be verified this session.
