# Phase 9 — Pilot Onboarding Checklist

- [ ] Written pilot acceptance signed (both parties).
- [ ] Internal pilot owner named and confirmed available for the pilot's duration.
- [ ] Customer record created in Owner (`create_customer` — real, existing Phase 8 function).
- [ ] Subscription created and transitioned to `ACTIVE`.
- [ ] Payment manually recorded (real amount, real date, real method — no card data stored, matching
      the forbidden-items list).
- [ ] License issued, plaintext key delivered to the customer through a secure, logged channel (not
      email in plaintext if avoidable — matches `private-artifact-distribution.md`'s access-logging
      principle).
- [ ] Product installed by the customer (or, for the very first pilot, by the internal team on the
      customer's behalf) using `pilot-installation-guide.md`.
- [ ] Real activation confirmed (Owner shows the installation as `ACTIVE`).
- [ ] Real first check-in confirmed.
- [ ] Support contact information given to the customer, matching `support-escalation-matrix.md`.
- [ ] Backup responsibility explained to the customer (who is responsible for their own local product
      backups vs what Owner backs up — see `backup-policy.md`'s explicit Owner/product-data boundary).
- [ ] First daily review scheduled (`daily-pilot-review-template.md`).
- [ ] Pilot end date confirmed and calendared.
