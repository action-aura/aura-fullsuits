# Phase 9 Milestone 4 — TLS and Security Headers

## Real finding and fix this session

`owner/app/security/headers.py` already applied a solid header set on every response (CSP scoped to
`'self'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
a restrictive `Permissions-Policy`) — but HSTS only fired when `app.config["ENV"] == "production"`.
Phase 9 introduces a real `staging` environment that also only ever serves over real TLS (Caddy
termination) — under the old condition, staging would never get HSTS despite genuinely qualifying.
Fixed: HSTS now fires for `ENV in ("production", "staging")`. No existing test covered HSTS at all
(gap); `owner/tests/test_security_headers.py` (4 new tests) now covers baseline headers in every
environment, HSTS present for both `production` and `staging`, and HSTS correctly **absent** in
`development` (plain HTTP — an HSTS header there would be actively wrong, telling a browser to force
HTTPS against a server that doesn't speak it).

## TLS policy (real deployment)

- TLS 1.2 minimum, TLS 1.3 preferred — Caddy's default cipher/protocol policy already meets this
  without additional configuration (verified against Caddy's own documented defaults, not
  independently re-implemented).
- Certificate: Let's Encrypt (automatic, via Caddy) for a real public domain. This local-only session
  uses Caddy's `tls internal` local-CA mode instead — a real TLS handshake, real header injection,
  **not** a publicly-trusted certificate; recorded as NOT VERIFIED for "publicly trusted TLS", not
  presented as equivalent.
- Certificate renewal: automatic (Caddy's own ACME client handles this in a real deployment; nothing
  for this project to build).

## CSP is not aspirational

`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;
frame-ancestors 'none'; form-action 'self'; base-uri 'self'` was already in place before this phase and
already correctly scoped (`script-src 'self'` only — no `unsafe-inline` for scripts). This phase did not
weaken it. Per the governing instruction's own warning ("do not break required Owner UI functionality
through an untested CSP"), no CSP change was made without evidence — none was needed.

## Real evidence this session

`owner/tests/test_security_headers.py`, 4/4 passing, run against the real local Owner app factory (not
mocked): CSP/`X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy` present in every environment;
HSTS present for `production` and `staging`; HSTS absent for `development`.
