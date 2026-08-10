# Phase 9R — M6: Request Body Limits (Real Bug Found and Fixed)

## Extends, does not replace, Phase 9's TLS/edge work

Phase 9's `tls-and-security-headers.md`, `network-and-trust-boundaries.md`,
and `deploy/staging/Caddyfile` already cover TLS policy (Let's Encrypt via
Caddy, `tls internal` for the local-only session), security headers (HSTS
fixed for staging in Phase 9, CSP already scoped to `'self'`), and Docker
network isolation (`internal: true` network for the database). None of
that needed re-doing for 9.5A-E — the new CRM/commercial-sales/expenses
routes are all proxied generically through the same `reverse_proxy
owner:5000` directive; no route-specific Caddy configuration was ever
needed or is needed now.

## What M6 found instead: a real, previously-undetected bug

`app.config["MAX_CONTENT_LENGTH"]` — a Flask-**global** ceiling enforced by
Werkzeug before any view function runs — was set equal to
`MAX_REQUEST_BYTES` (64KB), a value sized specifically for the licensing
API's small JSON payloads (Phase 6). This silently capped **every** route
in the application at 64KB, including expense attachment uploads
(`EXPENSE_ATTACHMENT_MAX_BYTES` = 10MB, Phase 9.5E). Confirmed by direct
reproduction: a 200KB PDF upload through the real HTTP route (not a direct
Python call — every *existing* attachment test calls `upload_attachment()`
directly, bypassing the WSGI layer entirely, which is exactly why this went
undetected) returned a raw, unstyled `413 Request Entity Too Large` before
ever reaching the attachment-specific size/type validation.

## Fix

- `owner/app/config.py`: `MAX_CONTENT_LENGTH` decoupled from
  `MAX_REQUEST_BYTES`, now a separate `OWNER_MAX_CONTENT_LENGTH_BYTES`
  (default 12MB — covers the largest legitimate body, expense attachments,
  with headroom).
- `owner/app/api_external/routes.py`: added `_bounded_payload()`, a
  blueprint-scoped `before_request` hook that explicitly enforces the
  licensing API's own tighter `MAX_REQUEST_BYTES` (64KB) independently of
  the now-larger global ceiling — using the blueprint's pre-existing
  `errorhandler(413)` (which previously relied entirely on the global
  config to ever fire) for a consistently-formatted JSON error.
- `deploy/staging/Caddyfile`: added a `request_body { max_size 15MB }`
  directive as edge-level defense in depth, above the app's 12MB so
  multipart overhead never gets clipped at the proxy before the app's own
  check runs.

## Real test evidence

`owner/tests/test_phase9r_request_body_limits.py`, 4/4 passing, all through
the real HTTP route (not direct function calls):

```
test_200kb_attachment_upload_succeeds                                    PASSED
test_upload_over_the_real_10mb_limit_is_still_rejected_by_the_app         PASSED
test_oversized_licensing_payload_is_rejected_even_though_the_global_cap_is_larger  PASSED
test_normal_sized_licensing_request_is_not_affected                       PASSED
```

The second and third tests specifically prove neither limit regressed into
the other: attachments over 10MB are still rejected (by the app's own
informative check, not a raw 413), and licensing payloads over 64KB are
still rejected (by the blueprint's own explicit check) even though the
global ceiling is now much larger.

Broader regression sample re-run clean after this global-scope config
change: 63 tests across expense/attachment, activation/check-in protocol,
security headers, IDOR/security, and data-boundary suites — no regression.

## Disposition

**PASS for the repository-controlled portion.** Real edge/app-level request
body limits configured and tested. What remains **NOT VERIFIED**: this
Caddy configuration running against a real remote host and real client
uploads over an actual network — blocked on infrastructure per
`infrastructure-availability-audit.md`, same as the rest of M6.
