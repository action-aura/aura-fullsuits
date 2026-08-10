# Phase 9 Milestone 4 — Host Hardening

## Status: NOT VERIFIED against a real host (none available this session); baseline documented for
real deployment

No remote host exists this session. The controls below are the required baseline for whoever
provisions the real staging host, written as a checklist + reference commands, not aspirational prose.

## Required controls (real deployment)

| Control | Requirement | Verification method |
|---|---|---|
| OS | Ubuntu 22.04 LTS or newer (or an equivalent supported distro) | `lsb_release -a` |
| Automatic security updates | `unattended-upgrades` enabled, security repo only | `cat /etc/apt/apt.conf.d/50unattended-upgrades` |
| Deployment user | dedicated non-root `deploy` user, member of `docker` group only | `id deploy` |
| SSH | key-only auth, password auth disabled, root login disabled | `sshd -T \| grep -E 'passwordauthentication\|permitrootlogin'` both `no` |
| Firewall | default deny inbound, allow 443/80/22 (22 restricted to operator IP where practical) | `ufw status verbose` |
| Time sync | `systemd-timesyncd` or `chrony` active | `timedatectl status` -> `synchronized: yes` |
| Disk monitoring | alert before 85% full (Milestone 8) | Prometheus node-exporter `node_filesystem_avail_bytes` |
| Installed packages | minimal — Docker Engine, `curl`, `ufw`, `unattended-upgrades`, `fail2ban`; no desktop environment, no unused language runtimes | `apt list --installed \| wc -l` reviewed against a known-minimal baseline |
| Brute-force protection | `fail2ban` watching SSH and (via Caddy's access log) repeated 401/403 responses | `fail2ban-client status sshd` |

## PostgreSQL not publicly exposed

Enforced structurally, not just by convention: `docker-compose.staging.yml`'s `db` service has no
`ports:` mapping at all and sits on an `internal: true` Docker network with no route to the outside
world — there is no firewall rule that could accidentally leave it reachable, because the container
runtime itself never binds it to a host interface.

## Reverse proxy terminates HTTPS, Owner listens internally only

Enforced the same way: `owner` service has no `ports:` mapping; only `proxy` (Caddy) publishes 443/80.

## Real, local-only verification performed this session

- The local Windows development machine's own Postgres 17 (native service) was confirmed **not**
  reachable from outside `localhost`/the local network by construction — it is a Windows service bound
  per its own `postgresql.conf`/`pg_hba.conf`, not exposed through any port-forward or public interface
  this session touched (see `postgresql-hardening.md` for the actual `listen_addresses`/`pg_hba.conf`
  review performed).
- No firewall/SSH/host-OS control above could be verified against a real remote Linux host this
  session — recorded honestly as NOT VERIFIED, not assumed.
