# Phase 9 — Network and Trust Boundaries

## Boundaries

1. **Public/internet <-> reverse proxy**: only port 443 (and 80 for redirect/ACME challenge in a real
   deployment) crosses this boundary. TLS terminates here. This is the only boundary a real pilot
   client ever crosses.
2. **Reverse proxy <-> Owner**: internal Docker network only, plain HTTP (TLS already terminated),
   never published to the host's public interface.
3. **Owner <-> PostgreSQL**: internal Docker network only. `docker-compose.staging.yml` (Milestone 3)
   does not publish Postgres's `5432` to the host at all — a change from the existing dev
   `docker-compose.yml`, which does. This is the single most important hardening delta from the
   existing dev compose file.
4. **Owner <-> signing-key volume**: local Docker named volume, never network-attached, never inside
   the Postgres data directory (Phase 6 decision, retained).
5. **Backup job <-> PostgreSQL / signing-key metadata**: internal network / local volume mount, backup
   artifacts written to a separate volume, never inside the Postgres data volume itself.
6. **Monitoring <-> Owner/Postgres**: internal network, scrapes metrics endpoints only, no
   customer-domain data ever crosses this boundary (Owner holds none — see the Owner data boundary
   below).
7. **SSH <-> host**: key-only, restricted to the operator's own IP in a real deployment (recorded as a
   requirement in `firewall-and-network-policy.md`; not enforceable in this local session since there
   is no real host).

## Owner data boundary (unchanged from Phase 8, reaffirmed for staging)

Aura Owner's database schema contains no table for patient records, appointments, prescriptions,
diagnoses, Clinic notes, Clinic invoice line detail, Retail sale/receipt lines, Retail stock
quantities, suppliers, or Retail customer records — confirmed structurally in Phase 8V-P7
(`android-data-boundary-final.md`) and unchanged since (no schema migration touched this boundary).
Nothing in this phase adds a new data path across this boundary; monitoring/logging (Milestone 8) is
explicitly designed to not capture customer-domain data (see `logging-and-redaction-policy.md`).

## Trust model

- Client (Windows/Android) trusts Owner via the existing signed trust-anchor manifest mechanism
  (Phase 6/8), not TLS-certificate pinning and not trust-on-first-use. Staging uses its own signing
  authority (Milestone 5) — clients built against staging trust only the staging key, never
  production's.
- Operator trusts the host via SSH key auth only (no password auth in a real deployment).
- Reverse proxy trusts Owner via the internal Docker network boundary alone (no additional
  application-layer trust needed inside that boundary in this topology).
