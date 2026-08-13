# Phase 9R — Production Cost Model (M1)

Real pricing checked live 2026-08-04 (sources below), not estimated from
training data. No provider is selected or purchased here — this is a
planning estimate for the owner to decide against, per
`external-dependency-register.md` #11.

## Baseline monthly estimate (small, single-pilot scale)

| Item | Option | Est. monthly cost (USD) |
|---|---|---|
| App server VPS (2 vCPU / 4 GB) | Hetzner CPX22/CX32-class | ~$8–10 |
| App server VPS (2 vCPU / 4 GB) | DigitalOcean Basic Droplet, for comparison | ~$24 |
| PostgreSQL | Self-managed on a second small VPS of similar spec | ~$8–10 |
| PostgreSQL | Managed Postgres (provider-dependent, typically 2–4x self-managed) | ~$25–50+ |
| Domain registration | Typical `.com`/`.io`-class TLD | ~$1–3/mo amortized (annual purchase, ~$12–40/yr) |
| DNS | Usually bundled free with registrar or a free-tier DNS provider | $0 |
| TLS certificates | Caddy's built-in Let's Encrypt automation | $0 |
| Object storage (private artifacts + backups), light usage (tens of GB) | Backblaze B2 (~$0.007/GB/mo storage, egress mostly free under normal use) | ~$1–5 |
| Object storage, AWS S3 standard, for comparison | ~$0.023/GB/mo storage, plus real egress charges | ~3x the B2 estimate, more at any real download volume |
| Monitoring/alerting | Free tier of most external uptime/metrics tools is realistic at pilot scale | $0–15 |
| Backup storage (separate from #artifacts, or shared bucket with prefix separation) | Included in the object-storage line above if same provider | — |

**Estimated realistic floor for a controlled pilot: roughly $20–35/month**
(cheapest-viable provider choices: Hetzner VPS + self-managed Postgres on a
second small Hetzner box + Backblaze B2 + free-tier monitoring + a cheap
domain).

**Estimated comfortable mid-range: roughly $60–100/month** (managed
Postgres instead of self-managed, a paid monitoring tier, headroom on the
app server).

## What drives cost up from here

- Managed PostgreSQL instead of self-managed (biggest single lever)
- Real download volume against a provider that charges egress (AWS S3 vs.
  Backblaze B2 — a genuinely large cost difference at any real distribution
  volume, per the B2 pricing above)
- A second app-server instance for redundancy (not recommended until M22
  capacity data justifies it)
- A paid monitoring/alerting tier once free-tier limits (data retention,
  alert count) are exceeded

## What this estimate deliberately excludes

- The owner's own time/labor
- One-time setup costs (domain purchase is amortized above, not a separate
  line)
- Anything dependent on a provider not yet chosen (managed object storage
  from the same provider as the VPS, reserved-instance discounts, etc.)
- Formal third-party security assessment/penetration testing (not part of
  this phase's scope — see M23 note on not claiming certification)

## Recommendation

Start at the low end (Hetzner + self-managed Postgres + Backblaze B2) for
the controlled pilot. It is the smallest real commitment that satisfies
every PASS requirement in this phase (real domain, real HTTPS, real remote
Postgres, real external backup) without over-provisioning for traffic that
doesn't exist yet. Move individual line items to managed/paid tiers only
when M22's actual capacity data or M13's actual restore-drill experience
shows a concrete reason to.

## Sources

- [DigitalOcean vs. Hetzner Cloud 2026](https://betterstack.com/community/guides/web-servers/digitalocean-vs-hetzner/)
- [Hetzner Cloud Review 2026: pricing](https://betterstack.com/community/guides/web-servers/hetzner-cloud-review/)
- [Backblaze cloud storage pricing](https://www.backblaze.com/cloud-storage/pricing)
- [Backblaze B2 vs Amazon S3 pricing 2026](https://next3offload.com/blog/backblaze-b2-vs-amazon-s3/)
