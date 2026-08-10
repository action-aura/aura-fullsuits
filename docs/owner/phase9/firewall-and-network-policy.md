# Phase 9 — Firewall and Network Policy

## Status: NOT VERIFIED against a real host (documented policy for real deployment)

## Required inbound rules (real deployment)

```
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   # restrict to operator's known IP with `ufw allow from <IP> to any port 22` where practical
ufw allow 80/tcp   # ACME HTTP-01 challenge / TLS redirect only
ufw allow 443/tcp
ufw enable
```

No other inbound port is opened. `5432` (Postgres) is never opened — it is not reachable at the host
firewall layer even before considering that Docker itself never publishes it (`network-and-trust-
boundaries.md`), a deliberate two-layer control.

## fail2ban jails (real deployment)

- `sshd` — standard.
- A custom jail watching Caddy's access log for repeated `401`/`403`/`429` from the same source IP
  against `/auth/login`, complementing Owner's own application-level login rate limiting (Phase 4/8) —
  not a replacement for it.

## Outbound policy

No outbound restriction beyond what Docker's `internal: true` network already enforces for the `db`
service (no route out at all). The `owner` and `backup` containers need outbound access only for
package installation at build time (not at runtime) and, if a real off-host backup copy target is
configured, to reach that target over an encrypted channel.

## This session's actual network posture (local, honest)

This machine's own Postgres 17 service and the local dev Owner process were both bound to `localhost`/
the local network only for every test performed this phase — never exposed to a public interface,
never tested against a real firewall. No real host firewall exists to configure or verify this session.
