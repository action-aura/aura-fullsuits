# Phase 9R — Network and Trust Boundaries (M1)

## Ports and network paths

| Path | Port | Exposure | Notes |
|---|---|---|---|
| Internet → Caddy | 443 (HTTPS), 80 (redirect only) | Public | Only two ports the firewall allows inbound from the internet |
| Caddy → Gunicorn | app port, e.g. 8000 | Loopback (`127.0.0.1`) only | Gunicorn never binds `0.0.0.0`; firewall additionally denies external access to this port as defense in depth |
| Gunicorn → PostgreSQL | 5432 | Loopback or private network only | If DB is on a separate host, connection crosses a private network with TLS required (`sslmode=require` minimum, `verify-full` preferred once a CA is in place) — never the public internet in plaintext |
| App → object storage | 443 (HTTPS) | Public (provider's API endpoint) | Outbound only; credentials scoped to specific bucket/prefix |
| Deployment pipeline → server | 22 (SSH) or provider API (443) | Restricted to CI/CD source IPs where the provider supports IP allowlisting | Scoped deploy key, not an interactively-used human credential |
| Operator → server (admin access) | 22 (SSH) | Restricted, key-based only, no password auth | Separate from the deployment credential |
| Health check (external monitor → app) | 443, specific path (e.g. `/health/live`) | Public but unauthenticated-safe (no sensitive data in response) | See `observability-and-alerting.md` |

## Trust zones

```
┌─────────────────────────────────────────────────────────────┐
│ ZONE 0 — Internet (untrusted)                                │
│   anonymous clients, synthetic test clients, attackers        │
└───────────────────────┬───────────────────────────────────────┘
                         │ HTTPS only, 443
┌───────────────────────▼───────────────────────────────────────┐
│ ZONE 1 — Edge (Caddy)                                          │
│   terminates TLS, enforces security headers, request limits,   │
│   forwards only to loopback                                    │
└───────────────────────┬───────────────────────────────────────┘
                         │ loopback, unencrypted (same host, same kernel)
┌───────────────────────▼───────────────────────────────────────┐
│ ZONE 2 — Application (Gunicorn workers + scheduler)             │
│   authenticates requests, enforces authorization/RBAC,          │
│   owns all business logic and licensing decisions                │
└───────────────────────┬────────────────────┬───────────────────┘
                         │ TLS if cross-host  │ HTTPS
┌───────────────────────▼───────┐  ┌──────────▼──────────────────┐
│ ZONE 3 — Data (PostgreSQL)     │  │ ZONE 4 — External services   │
│   least-privilege app role,    │  │   object storage, backup     │
│   no superuser app connection  │  │   destination, monitoring,   │
│                                 │  │   deployment credential store │
└─────────────────────────────────┘  └───────────────────────────┘
```

## What crosses each boundary, and what doesn't

- **Zone 0 → Zone 1**: arbitrary HTTP requests. Nothing here is trusted.
  Every request is subject to TLS, security headers, and rate limiting
  before it reaches Zone 2.
- **Zone 1 → Zone 2**: only what Caddy forwards, over loopback. Trusted
  because it's not reachable from outside the host — not because the proxy
  "sanitizes" anything (it doesn't parse business logic).
- **Zone 2 → Zone 3**: SQL over a least-privilege role. The application
  connection can read/write the tables it owns and nothing that would let it
  create roles, alter other databases, or bypass row-level protections the
  schema defines. See `production-postgresql.md` (M4).
- **Zone 2 → Zone 4**: outbound only, narrow credentials, narrow purpose
  (upload a backup, upload a release artifact, emit a log line, emit a
  metric). Zone 4 services never initiate a connection into Zone 2 or Zone 3.
- **What never crosses any boundary in this system**: Retail/Clinic
  operational data. There is no code path, no table, no API contract
  anywhere in this architecture that accepts products, inventory, sales,
  patient records, local invoices, or local customer records from a client.
  The remote licensing contract (M8) is explicitly bounded to license/
  installation/entitlement metadata — see `remote-licensing-contract.md`.

## Secrets, by zone

| Secret | Lives in | Never appears in |
|---|---|---|
| App/session secret, MFA encryption key | Zone 2, loaded from the host's secret mechanism at process start | Git, Docker image layers, logs, error messages |
| Database credentials | Zone 2 (app) and Zone 3 (server), least-privilege role only | Zone 1 (Caddy config has no DB awareness) |
| License-signing private key | Zone 2 only, key-identifier-addressable for rotation | Zone 4 (never uploaded anywhere except the documented signing-key recovery backup procedure, M3) |
| Object-storage credentials | Zone 2 only | Frontend JS, templates, client-visible responses |
| Deployment credential | CI/CD runner only, injected at deploy time | The server's own filesystem outside the deploy step, git history |

## Failure-mode boundary behavior

- Zone 1 down (Caddy crashed): whole site down, but Zone 2/3 data integrity
  unaffected — restart Caddy, nothing to repair.
- Zone 2 down (all Gunicorn workers crashed): Caddy serves a clean 502, not
  a stack trace; database untouched.
- Zone 3 down (PostgreSQL unreachable): app fails closed on requests needing
  the DB (which is nearly all of them) rather than serving stale/incorrect
  data; health/readiness reflects this (`observability-and-alerting.md`).
- Zone 4 down (object storage unreachable): download authorization and
  backup uploads fail explicitly; does not silently corrupt or skip.

This document will be re-verified against the *actual* deployed topology
once M18 (remote staging) exists — right now it describes the intended
architecture from M1, not yet a live-verified one.
