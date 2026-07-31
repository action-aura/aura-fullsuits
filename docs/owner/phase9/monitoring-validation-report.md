# Phase 9 Milestone 8 — Monitoring Validation Report

## Real, verified this session

- Structured JSON logging: real, tested (`owner/tests/test_observability_logging.py`, 7/7), confirmed
  end-to-end against a live local Owner process (real log lines captured and inspected, including
  catching and fixing a real duplicate-emission bug).
- Redaction: real, tested — 5 distinct secret-shape categories confirmed redacted, one control test
  confirms no over-redaction of ordinary log text.
- Correlation IDs: real, confirmed present on every response (`X-Correlation-Id` header) and inside the
  corresponding structured log line.
- `/health/ready` as the underlying signal for the top P0 alerts (Owner down, DB unreachable, migration
  mismatch, signing key missing, pepper misconfigured): real, tested (Milestone 3), and separately
  confirmed live against the real staging database (Milestone 6/7).

## NOT VERIFIED this session

A live Prometheus/Alertmanager/exporter stack, scrape configuration, or firing-alert test — Docker
Engine is not installed on this machine, and no remote host exists to deploy one to. The alert
*definitions* (`alert-catalog.md`) are real design work mapped directly to real, already-verified
signals; the alerting *system* itself was not stood up or exercised.

## What would close this gap in a real deployment

Add `prometheus`, `alertmanager`, `node-exporter`, `postgres-exporter` services to
`docker-compose.staging.yml` (all on the `internal` network, none published to a host port), point
Prometheus at Owner's `/health/ready` (as a blackbox probe) and the two exporters, load
`alert-catalog.md`'s thresholds as real Prometheus alerting rules, and run one real fire-and-resolve
test per P0 alert before considering monitoring itself production-ready for a pilot.
