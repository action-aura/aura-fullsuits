# Phase 9R — Deployment Architecture (M1)

Date: 2026-08-04. Versions below checked live against official sources on
this date (see Sources), not assumed from training data.

## Decision: monolithic host, no containers, no Kubernetes

Owner is one Flask application plus one PostgreSQL database plus a scheduler
process. There is no evidence of a workload that needs container
orchestration: no stated multi-team deployment cadence, no stated need for
independent scaling of sub-components, no existing containerization in the
repo. `requirements/owner-server.txt` already pins `gunicorn==22.0.0` — the
codebase was already heading toward a plain WSGI-server-on-a-host model, not
a container model.

Docker is still worth using for one narrow purpose — packaging the app +
its exact dependency set reproducibly for deployment — but Docker Compose
running on a single host is sufic if used; it is not required to reach a
professional deployment, and this ADR does not mandate it. See
`architecture-decision-record.md` for the containers-vs-direct-host
trade-off actually weighed.

Kubernetes is explicitly rejected: no real scale evidence exists (no
production traffic yet — there is no production), and the operational
overhead (cluster management, secrets, ingress controllers, etc.) would cost
more than the whole rest of Phase 9R combined for a single-app, single-
database, initial-pilot workload. Revisit only if real capacity data (M22)
later shows a single host is insufficient — not before.

## Target architecture

```
Internet
   |
   v
DNS  (A/AAAA records -> one public IP)
   |
   v
Caddy  (reverse proxy: automatic HTTPS, HTTP->HTTPS redirect, security headers,
        request limits, proxies to the app on localhost only)
   |
   v
Gunicorn  (N sync/gthread workers running the Flask app; bound to 127.0.0.1,
           never directly internet-facing)
   |
   v
PostgreSQL  (dedicated least-privilege application role; local Unix socket
             or localhost TCP -- not exposed beyond the host's own network
             namespace)
   |
   v
Encrypted external backups -> object storage (separate account/bucket from
                               the app, versioned, off-host)

Plus, alongside the app:
  - one dedicated scheduler process (systemd service, NOT one per Gunicorn
    worker) for the Phase 9.5E report-snapshot scheduler
  - structured JSON logs -> local file, shipped to an external log/metrics
    sink for M14
  - a deployment user with a scoped SSH key, no shared root login for CI/CD
```

## Selected components and the versions checked live today

| Component | Selection | Version (checked 2026-08-04) | Why |
|---|---|---|---|
| OS | Ubuntu LTS | **26.04 LTS** ("Resolute Raccoon"), supported to April 2031 | Current LTS, 5-year support window, matches this project's Windows-dev/Linux-prod split already implicit in `requirements/` (no Windows-only server deps) |
| Reverse proxy / TLS | **Caddy** | latest stable (check at deploy time) | Automatic HTTPS with no separate certbot cron/renewal-hook to operate — for a two-person team this removes an entire class of "cert expired silently" incidents. Nginx remains a valid choice if the owner already has Nginx operational experience; Caddy is the default recommendation here specifically because it minimizes ongoing ops burden, not because Nginx is deficient |
| Application server | **Gunicorn** | Bump from the currently pinned `22.0.0` to a current `26.x` at M5 implementation time (re-check exact patch version then) | Already the project's own chosen WSGI server (see `requirements/owner-server.txt`); sync/gthread worker model fits Flask + SQLAlchemy without an async rewrite |
| Database | **PostgreSQL** | **17.x** (current stable minor, e.g. 17.10 as of this check) | PostgreSQL 18 is the newest major, but 17 is the more conservative choice for a first production deployment: broader managed-provider availability, one full release cycle of real-world hardening, still receives full support for years. 18 is an acceptable alternative if the chosen managed provider only offers 18; either is "currently supported" — 14 is explicitly excluded (EOL November 2026) |
| Database driver | `psycopg[binary]==3.2.1` | already pinned | No change needed; re-verify current version at M4 implementation |
| Containers | None required; Docker optional for reproducible packaging only | — | See ADR |
| Object storage | Provider-dependent (S3-compatible interface recommended for portability) | — | Selection blocked on `external-dependency-register.md` #6 |
| Backup encryption | Age or GPG, applied client-side before upload | current stable | Selection finalized in `backup-policy.md` (M12) |

## Trust boundaries (summary — full detail in `network-and-trust-boundaries.md`)

1. **Internet → Caddy**: untrusted. Everything arriving here is adversarial
   by default.
2. **Caddy → Gunicorn**: trusted, but only because it's loopback-only
   (`127.0.0.1`) — Gunicorn never binds a public interface.
3. **Gunicorn → PostgreSQL**: trusted, least-privilege application role,
   never the database superuser.
4. **App → object storage / backup destination**: trusted, credentials
   scoped to exactly the buckets/prefixes needed, never broad account
   access.
5. **Deployment pipeline → server**: trusted, scoped SSH/API credential,
   never the same credential a human uses interactively.

## Data flows in scope for Phase 9R

- Remote employee/session auth (login, MFA, session cookie)
- CRM/commercial/expense/reporting reads and writes (existing Phase 9.5E
  functionality, unchanged — just now reachable remotely)
- Remote licensing: activation, refresh, suspension, revocation (bounded
  metadata only — see `remote-licensing-contract.md`)
- Product release publication and private authorized downloads (metadata +
  short-lived signed URLs, artifact bytes served from object storage, never
  proxied through the app process for large files if avoidable)
- Backup egress (database dump + release metadata -> external storage)
- Observability egress (logs/metrics -> external sink)

Explicitly **not** a data flow anywhere in this architecture: Retail/Clinic
operational data (products, inventory, sales, patient records, local
invoices, local databases) — there is no ingestion path for any of it in the
existing domain model, and Phase 9R adds none.

## Failure modes and scaling limits (initial, revisited in M22)

- Single app server = single point of compute failure until real capacity
  data justifies more. Acceptable for a controlled pilot; documented, not
  hidden.
- Single PostgreSQL instance = single point of data failure, mitigated by
  external backups (M12) and a tested restore path (M13), not by
  replication (no evidence yet that a pilot needs it).
- Scheduler runs as one dedicated process, not per-worker — a scheduler
  crash pauses report snapshots but does not take down the web app; app
  restart does not duplicate scheduler work (see `scheduler-topology.md`,
  M5).
- Certificate renewal failure = degraded (existing cert still serves until
  it actually expires) not immediate outage, given Caddy's retry-on-failure
  behavior — still monitored and alerted (M14) rather than assumed safe.

## Single-host risk — stated plainly

Caddy, Gunicorn, the scheduler, and (in the cheapest configuration)
PostgreSQL all run on infrastructure that can be as small as one or two
hosts. This is **not high availability**, and this document does not
describe it as such:

- **Host loss stops the application and database together** if they share a
  host, or stops the application alone (with the database surviving) if
  they're split across two hosts as recommended in ADR-3 — either way,
  there is no automatic failover to a second live instance.
- **Local disk loss is unrecoverable except through backups.** Nothing about
  this architecture protects data that exists only on the production host's
  disk — recovery depends entirely on the external backup being real,
  current, and restorable (M12/M13).
- **Maintenance causes service interruption.** OS patching, PostgreSQL minor
  upgrades, and app deploys all involve a brief outage window in this
  topology — there is no rolling-update capacity across redundant nodes.
- **Scaling is vertical before horizontal.** The first response to load is a
  bigger instance, not more instances, until M22 capacity data says
  otherwise.
- **PostgreSQL recovery depends entirely on verified external backups** —
  not on replication, not on a standby, because neither exists in this
  architecture.

This architecture can still legitimately **PASS for a controlled pilot**,
but only once, concretely:

- backups are genuinely external (off the production host) — M12
- restore has actually been tested, not assumed — M13
- RPO and RTO are stated numbers, not aspirations — M12/M13
- disk, memory, database, and certificate-expiry alerts exist and have been
  exercised — M14
- rollback is documented and has a real procedure, not "redeploy and hope" —
  M17
- this limitation is disclosed to the pilot customer, not hidden — M25

Until every one of those is true, this topology should be described exactly
as what it is: a single-host (or two-host) deployment suitable for a small,
monitored, controlled pilot — never as "highly available," "redundant," or
"enterprise-grade infrastructure."

## Operational ownership

Single operator (the project owner) for now — this is stated explicitly
rather than assumed, since several later milestones (on-call runbooks,
incident response) are written differently for a one-person team than a
24/7 SRE rotation.

## Sources

- [Ubuntu 26.04 LTS release notes](https://documentation.ubuntu.com/release-notes/26.04/)
- [Ubuntu release cycle](https://ubuntu.com/about/release-cycle)
- [PostgreSQL 18.4, 17.10, 16.14, 15.18, and 14.23 released](https://www.postgresql.org/about/news/postgresql-184-1710-1614-1518-and-1423-released-3297/)
- [PostgreSQL | endoflife.date](https://endoflife.date/postgresql)
- [Gunicorn changelog](https://docs.gunicorn.org/en/stable/news.html)
- [Nginx vs Caddy in 2026](https://privatedevops.com/articles/nginx-vs-caddy-2026-reverse-proxy-comparison)
