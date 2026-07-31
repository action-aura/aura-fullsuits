# Phase 9 Milestone 8 — Observability Architecture

## Real gap found and closed

No structured or redacted logging existed anywhere in `owner/app/` before this phase (grep found a
single ad-hoc `logging.*` call in one file). Built `owner/app/observability/logging_config.py`: every
log record — request logs and any application `logging.*` call — is emitted as structured JSON
(`timestamp`, `severity`, `service`, `message`, `correlation_id`, plus safe optional fields
`reason_code`/`operator_id`/`installation_ref`/`license_ref`), and every message is redacted before
emission using the same substring-based approach already proven in
`commercial_runtime/licensing_contracts/events.py`'s `FORBIDDEN_DETAIL_MARKERS` (Phase 8), extended
here to a real regex pass since log lines are free text, not a structured dict with named fields to
refuse outright. A correlation ID is assigned per request (`X-Correlation-Id`, generated or passed
through from an upstream proxy) and returned in the response header — real end-to-end request
tracing, not aspirational.

A real duplicate-emission bug was found and fixed while smoke-testing this against a live Owner
process: `app.logger` propagates to the root logger by default, and both had the same handler
attached, so every log line was emitted twice. Fixed with `app.logger.propagate = False`.

## What this phase did NOT build

A live metrics-scraping stack (Prometheus + node/postgres exporters + Alertmanager) — the architecture
doc (`staging-architecture.md`) specifies it as part of the real staging topology, but Docker Engine is
not installed on this machine (see `deployment-packaging.md`), so no container-based monitoring stack
was run this session. What *was* verified is real and load-bearing on its own: structured, redacted
logs (the actual data an external log aggregator would ingest) and the `/health/ready` endpoint (the
actual thing a real Prometheus `blackbox_exporter` or an uptime check would poll) — both real,
tested, working.

## Real deployment target (documented, not built this session)

- `node-exporter` + `postgres-exporter` containers on the `internal` Docker network, scraped by
  `prometheus`, alerting via `alertmanager` — all four as additional services in
  `docker-compose.staging.yml`, none published to a host port (Grafana/Prometheus UI reachable only via
  an SSH tunnel or a separate authenticated route, never public).
- Caddy's own access log (JSON-structured, matches Owner's own log shape) feeds the same log pipeline.

## What is monitored (see `alert-catalog.md` for full detail)

Process availability, readiness status, DB connectivity, migration mismatch, signing-key readiness,
preflight failure, HTTP error rate, latency, rate-limit events, authentication/MFA failures, activation/
check-in/reconciliation/scan failures, backup success/failure, disk/memory/CPU, process restarts, TLS
certificate expiry, audit-chain verification failure.
