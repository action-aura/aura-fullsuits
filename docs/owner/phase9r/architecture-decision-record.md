# Phase 9R — Architecture Decision Record (M1)

## ADR-1: Monolithic host vs. microservice decomposition

**Decision: monolithic host.**

Considered: splitting licensing/distribution from the CRM/commercial/
reporting app into separate services.

Rejected because: there is one team, one deployment cadence, one database
that every subsystem already shares (`owner_*` tables plus the licensing
tables live in the same PostgreSQL instance today), and zero evidence any
part of the app needs independent scaling. Decomposition would add network
calls, service-to-service auth, and independent deployment coordination for
no measurable benefit at this scale. Revisit only if M22 capacity data shows
one component saturating the host while others sit idle.

## ADR-2: Direct host deployment vs. Docker vs. Kubernetes

**Decision (reversed 2026-08-04, see `phase9-reconciliation.md`): Docker
Compose, per Phase 9's already-real prior decision; Kubernetes rejected.**

Originally recorded here as "direct host preferred, Docker optional" —
written without having yet discovered `docs/owner/phase9/` and
`deploy/staging/`. Phase 9 ("secure staging and pilot readiness") already
made this exact decision for real: a working, internally consistent
`docker-compose.staging.yml`, a real `Caddyfile`, and four systemd units
(`aura-owner-scheduled-ops`, `aura-owner-backup`, each with a `.timer`)
built around `docker compose run`/`up`. Per the owner's explicit
confirmation, Phase 9R extends that real prior artifact rather than
overriding it with a fresh preference — see `phase9-reconciliation.md` for
the full discovery and reasoning. Kubernetes remains rejected for the same
reason as before: no real scale evidence, and the operational cost for a
single-app single-database pilot would dwarf the rest of this phase.

## ADR-3: Managed PostgreSQL vs. self-managed on the same host

**Decision: deferred to the owner's provider choice, with a stated
default of self-managed on a separate small instance (not co-located with
the app server) if no managed option is chosen.**

Managed PostgreSQL (e.g. a provider's hosted Postgres product) removes
backup/patching/failover operational burden at a real dollar cost premium
over self-managed. Self-managed on its own instance (not the same box as the
app) keeps the app server's compromise blast radius from directly including
the database's disk, while staying cheap. Self-managed *on the same box* as
the app is explicitly discouraged even though it's the cheapest option: it
collapses the trust boundary between "app process compromised" and
"database compromised" into one box with one set of OS-level defenses. Final
choice depends on the owner's budget (`production-cost-model.md`) and is not
forced here.

## ADR-4: Reverse proxy — Caddy vs. Nginx

**Decision: Caddy, with Nginx as an acceptable substitute if the owner has
existing Nginx operational experience.**

For a single operator with no dedicated ops team, Caddy's built-in
automatic-HTTPS (no separate certbot install, cron job, or renewal hook to
maintain) removes an entire, historically common failure mode: a forgotten
certificate renewal silently taking the whole site down. Nginx has a larger
ecosystem and marginally better performance under very high concurrency —
not a relevant differentiator at this project's expected pilot scale (M22).

## ADR-5: Application server — Gunicorn sync/gthread workers, not async

**Decision: Gunicorn with `gthread` workers (already the pinned dependency),
not an async framework/server rewrite (e.g. Uvicorn/ASGI).**

The existing Flask app is written synchronously against SQLAlchemy's sync
engine throughout Phase 5–9.5E. Rewriting to async to use an ASGI server
would touch every request handler for no functional gain at this traffic
scale, and risks reintroducing bugs into 972 already-passing tests for zero
stated requirement. `gthread` workers give enough I/O concurrency per worker
to avoid one slow request blocking the whole worker, without an async
rewrite.

## ADR-6: Rate limiting backend — shared store required, not in-process

**Decision: rate-limit state must live in a shared, multi-worker-visible
store (PostgreSQL-backed, consistent with the existing activation
rate-limiting design from Phase 6 — see
`docs/owner/phase6/activation-protocol-threat-model.md` threat #3) — not
in-process memory.**

The moment Gunicorn runs more than one worker (it will, per
`application-server-topology.md`), in-process rate limiting becomes
per-worker, not per-deployment — an attacker gets N free attempts, one per
worker, before any limit engages. Phase 6 already solved this correctly for
license-key brute-forcing using a Postgres-backed limiter; Phase 9R extends
the same pattern to login, MFA, and every other rate-limited surface in M7,
rather than introducing a second, weaker mechanism.

## ADR-7: Object storage — S3-compatible interface, provider deferred

**Decision: require an S3-compatible API for private artifacts and backups,
defer the specific provider to the owner's choice.**

Coding against the S3 API (not a provider-specific SDK) keeps the option
open between AWS S3, Backblaze B2, Cloudflare R2, or a self-hosted MinIO,
without a code change if the owner later switches for cost reasons — real
2026 pricing in `production-cost-model.md` shows this matters (Backblaze B2
is roughly 3x cheaper per GB than AWS S3 standard storage).

## ADR-8: Signed leases over a shared bearer token

**Decision: every installation gets its own signed credential (Ed25519,
consistent with the existing Phase 6/7 signing infrastructure), never one
shared token per license.**

Already effectively decided by the existing licensing threat model (Phase 6
threat #3: "one shared bearer token reused across a customer's devices" is
explicitly a threat to defend against, not a design to adopt). Phase 9R's
signed-lease work (M9) extends the existing per-installation credential
model to remote network conditions; it does not change the model.
