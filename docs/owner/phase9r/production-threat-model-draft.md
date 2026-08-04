# Phase 9R — Production Threat Model (Draft, M0)

## Why this is a draft, and what it extends

The activation protocol already has a threat model
(`docs/owner/phase6/activation-protocol-threat-model.md`, 15 threats) that
explicitly scoped out network-layer threats and physical host compromise,
because at that point *"this service is not exposed publicly."* Phase 9R's
entire purpose is to remove that exemption — Owner becomes genuinely
internet-reachable. This draft's job is to cover exactly the surface that
exemption used to cover, plus the new surfaces Phase 9R introduces (private
distribution, external backups, a deployment pipeline, remote observability).
It is a *draft* because several mitigations below depend on the M1
architecture decision (not yet made) and real infrastructure (not yet
provisioned, per `infrastructure-availability-audit.md`); it will be
finalized once M1–M18 land.

Existing threat models this one builds on rather than repeats:
`docs/owner/phase5/owner-threat-model.md` (application-level auth/session/
RBAC threats), `docs/owner/phase6/activation-protocol-threat-model.md`
(licensing-protocol threats), `docs/owner/phase9_5a/data-isolation-threat-model.md`
(tenant/data-isolation threats), `docs/owner/phase9_5c/location-privacy-and-threat-model.md`.

## New surface 1 — Network exposure (closes the Phase 6 exemption)

| # | Threat | Planned mitigation | Milestone |
|---|---|---|---|
| N1 | Plaintext interception of licensing/session traffic | HTTPS-only, modern TLS config, HTTP→HTTPS redirect, HSTS after correctness is proven | M6 |
| N2 | Expired certificate silently breaks all clients | Automated renewal + certificate-expiry monitoring/alert | M6, M14 |
| N3 | DDoS / volumetric abuse against a single small server | Reverse-proxy request limits, distributed rate limiting, documented as a stated capacity limit rather than "solved" — full DDoS mitigation is out of scope for a controlled pilot | M6, M7, M22 |
| N4 | Host-header / forwarded-header spoofing to bypass origin checks or poison redirects | Explicit trusted-proxy configuration, host allowlist, no blind trust of `X-Forwarded-*` | M6, M7 |
| N5 | Open redirect via a crafted `next`/return URL | Redirect-target allowlist validation | M7 |
| N6 | Physical/host compromise of the production server | Least-privilege deployment credentials, no plaintext secrets on disk outside the secret store, audit logging of privileged actions — full physical security is provider-dependent and out of this project's control | M3, M16 |

## New surface 2 — Remote licensing API, network-exposed variant

Every Phase 6 threat (replay, brute force, race on device slots, tampered
responses, key exposure, enumeration, forged device keys, revoked-key reuse,
signing-key compromise, replay-store unavailability, oversized payloads,
assertion tampering) now additionally faces a real internet-routable
attacker, not a trusted local/LAN caller. Phase 9R re-validates each one
under that assumption (M8, M20, M21) rather than re-designing the mitigation
— the mechanisms already exist; what's unverified is their behavior under
real network conditions, real concurrency, and a real multi-worker
deployment (in particular: is rate limiting still enforced when it's no
longer one process? See M7, M21).

| # | Threat | Planned mitigation | Milestone |
|---|---|---|---|
| L1 | Cross-customer activation (attacker activates against someone else's license) | Existing license/customer binding re-verified against real remote requests | M8, M20 |
| L2 | Device-cap race under real concurrent remote clients | `SELECT ... FOR UPDATE` re-verified under genuine concurrent remote load, not just local tests | M21 |
| L3 | One shared bearer token reused across a customer's devices | Per-installation credential, no shared token, verified in the signed-lease design | M9 |
| L4 | Stale/replayed signed lease accepted after suspension or revocation | Bounded refresh deadline, documented offline-exposure window (explicitly not "instant" revocation) | M9 |

## New surface 3 — Product release and distribution (new in Phase 9R)

| # | Threat | Planned mitigation | Milestone |
|---|---|---|---|
| D1 | Public, permanent, unauthenticated artifact URL | Short-lived signed download URLs only, never a static public link | M11 |
| D2 | Download link reused past intended expiry or by a different customer | Server-side expiry + scope check on every download, not just at issuance | M11 |
| D3 | Path/object-key traversal to fetch an unrelated artifact | Server-resolved object key from validated (product, platform, release) tuple — never a client-supplied raw key | M11 |
| D4 | Tampered or substituted artifact accepted by the client | Published checksum, verified client-side | M11 |
| D5 | Unpublished/withdrawn release still downloadable | Publication-state check on every authorization, not just at release time | M11, M24 |

## New surface 4 — Backup, restore, and the deployment pipeline (new in Phase 9R)

| # | Threat | Planned mitigation | Milestone |
|---|---|---|---|
| B1 | Backup stored only on the production host (single point of failure) | External, physically separate backup destination required by policy | M12 |
| B2 | Backup archive contains plaintext secrets | Secrets excluded from ordinary backups; signing-key recovery material handled via a separate documented procedure | M3, M12 |
| B3 | Restore "succeeds" but data is silently wrong (never actually tested) | Isolated restore drill with row-count/checksum/audit-chain verification required before any backup is trusted | M13 |
| B4 | Uncontrolled/undocumented manual server edits drift from source control | Deployment pipeline is the only sanctioned path to production; drift is a documented incident condition | M16, M24 |
| B5 | Migration applied without a pre-migration backup or without a rollback plan | Backup-before-migration + explicit per-migration rollback classification required before any remote deploy | M17 |
| B6 | Secrets leaked via CI/CD logs | Secret redaction in pipeline output, no secret ever echoed | M16 |

## Explicitly out of scope for this threat model (matches the phase's own scope boundary)

Formal penetration-testing certification (not performed unless a real
independent assessment happens — this project does not claim one).
Multi-region/active-active threats (out of architecture scope per M1).
Kubernetes-specific threats (not adopted without real scale evidence).
Threats to JoFotara, General Ledger, Payment Gateway, or the Aura Owner
mobile app — none of that exists yet in this codebase.

## Status

**Draft.** Finalized once M1 (architecture), M6 (edge/TLS), M8/M9 (licensing/
lease hardening), M11 (distribution), and M12/M13 (backup/DR) land with real
implementation evidence to check each mitigation against.
