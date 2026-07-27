# Phase 8V-P — Final Data-Boundary Report (Part O data-boundary half)

Extends `real-traffic-evidence.md`'s forbidden-content sweep (real captured traffic, clean) plus
Phase 8V's own structural surface sweep (`docs/owner/phase8v/phase8v-data-boundary-report.md`,
unchanged by this session's commits — no new Owner UI surface or template was added in Phase 8V-P,
only the activation.py fix, the permission-seed sync, and the trust-anchor regeneration, none of
which touch any response/template field set).

## Net-new this session

- Real activation/check-in/renewal/expiry/revival/pilot-conversion/device-replacement/downgrade
  traffic, five real license/subscription lifecycles, two real installed products — all clean (see
  `real-traffic-evidence.md`).
- `DEVICE_ALREADY_REGISTERED` (new public reason code, this session's fix): a bare status string,
  carries no data at all, structurally impossible to leak anything.

## Result: zero prohibited-data findings, real and structural evidence both.
