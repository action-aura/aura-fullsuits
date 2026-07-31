# Phase 8V-P6 — Raw Wire Evidence (redacted excerpts)

Captured by the same real WSGI middleware used in Phase 8V-P5, wrapping the actual Owner Flask app's
`wsgi_app`, scoped to `/api/licensing/v1/*`, `license_key`/`signature`/`password`/`totp_secret`/
`recovery_code` redacted in-memory before any disk write. 7 real exchanges this session: 1
`service-info`, 2 `signing-keys`, 4 `check-ins`. Source temp file deleted at cleanup after this excerpt.

## The four real check-ins, in order (the core evidence for Scenario 3 and Scenario 5)

1. **Baseline** (`2026-07-31T05:30:29Z`, assertion before this doc's numbering) -- subscription
   `ACTIVE`, device `Active`. Confirms the rebuilt app behaves identically to before for the normal
   case.
2. **Restriction** (assertion `5d73b60b-51df-4d00-b5b1-feac5236f762`) --
   `subscription_status: "EXPIRED"`, `license_status: "ACTIVE"`,
   `offline_policy.hard_expiry_behavior: "WARN_ONLY"`, `commercial_grace_end: null`. Device:
   `Restricted`. See `scenario3-commercial-enforcement-final.md`.
3. **Extension-covered** (assertion `aafd637d-716d-4174-881b-fcbcde834caa`) --
   `subscription_status: "EXPIRED"` (unchanged), `offline_policy.emergency_extension_allowed: true`,
   `emergency_extension_until: "2026-07-31T08:38:28.108817+03:00"`. Device: `Active`. See
   `scenario5-emergency-extension-functional-final.md`.
4. **Post-expiry** (assertion `a742e845-b702-41ab-9131-01d7ea767431`) --
   `subscription_status: "EXPIRED"` (unchanged), `offline_policy.emergency_extension_allowed: false`,
   `emergency_extension_until: null`. Device: `Restricted` again.

No patient, invoice, appointment, or any Clinic domain field appears in any of the 4 check-ins' request
or response bodies -- every present field belongs to the licensing-contract vocabulary already
documented in Phase 8V-P5's `final-android-data-boundary.md`, whose allowed-field list this session's
new `commercial_grace_end` field was already part of (it shipped in Part W, this session only started
consuming it). No full license key appears in any of the 4 check-ins (none carry a `license_key` field
at all -- only the activation protocol does, and no activation occurred this session on this
installation, which was already activated).

## Scope disclosure

Same limitation as Phase 8V-P5: only `check-ins`/`service-info`/`signing-keys` traffic exists. Late
renewal, past-due-specific (as opposed to direct EXPIRED), device replacement, plan downgrade, temporary
exception, and stale-assertion traffic were not generated this session (see the corresponding NOT
VERIFIED scenario docs).
