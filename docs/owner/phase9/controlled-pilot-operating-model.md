# Phase 9 Milestone 15 — Controlled Pilot Operating Model

No real customer is onboarded this phase — this defines the model for when a real pilot is explicitly
approved, after all Phase 9 gates pass and Milestones 12-14's real infrastructure gaps are closed.

## Recommended initial scope

- **One** pilot customer, **one** product (Retail *or* Clinic, not both simultaneously — matches
  Phase 8V-P7's own precedent of not doubling operational complexity before it's proven at 1x).
- One primary device, up to 2 additional devices (matching the real `device_limit`/exception mechanics
  already proven in Phase 8).
- Manually recorded payment (no payment gateway — forbidden this phase and Phase 10).
- Manually approved license, issued by a named internal Super Admin/Sales operator.
- Named internal support owner (a real person, not a shared inbox, for the pilot's duration).
- Explicit start/end date, explicit support hours (business hours only — a supervised pilot, not
  24/7 on-call for a single customer).

## Pilot stages

1. **Internal staging acceptance** — this phase's own Milestone 12 evidence, extended once real
   infrastructure exists.
2. **Controlled tester installation** — an internal team member installs the real staging-connected
   build (Milestone 13) first, using this same installation guide, before any external user does.
3. **Synthetic-data validation** — repeat the real activation/check-in/renewal/restriction cycle
   (already proven methodology, Phase 8V-P7/P9) against the real staging deployment with synthetic
   data only.
4. **Customer environment readiness** — confirm the pilot customer's actual device meets the product's
   real requirements (OS version, disk space) — a real, simple checklist, not built this phase (no
   real customer yet).
5. **Written pilot acceptance** — the pilot customer and the internal pilot owner both sign off in
   writing before any real operational data enters the system. This is the explicit gate between
   "technical readiness" and "real business use" — nothing in this codebase enforces it technically;
   it is a real human process control.
6. **Limited real operation** — real license, real (small) payment record, real customer data entered
   manually by the internal operator (never by the pilot customer directly against a public signup —
   forbidden this phase).
7. **Daily review** — see `daily-pilot-review-template.md`.
8. **Pilot completion or rollback** — either a written extension/conversion decision, or offboarding
   (`pilot-offboarding-checklist.md`).

## Operational workflows (real Owner functionality already proven, Phase 8 — reused, not rebuilt)

Customer creation, subscription creation, manual payment record, license issuance, installation
approval, device replacement, renewal, past-due handling, emergency extension — all real, existing,
tested Owner features from Phase 8. This phase adds the *supervision structure* around them (the
staging environment, the manual approval gate, the daily review habit), not new product code.

## Support request / incident / backup / offboarding / data export

See `incident-response-plan.md`, `pilot-offboarding-checklist.md`, `backup-policy.md`, and the current
product contract's real Backup & Restore feature (Phase 8V-P9's `export-contract-decision.md` — Export
remains NOT IN CURRENT PRODUCT CONTRACT; a pilot customer's data-export responsibility is fulfilled via
the existing real Backup & Restore feature, not a promise of a feature that doesn't exist).
