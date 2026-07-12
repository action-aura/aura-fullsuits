# Security

## Reporting

Report suspected vulnerabilities privately to the Action Aura team before any public disclosure. Do not open a public issue with exploit details.

## Baseline controls carried over from Action Aura Enterprise

Retail and Clinic inherit the following hardening already done in the source repo (do not regress these during extraction — see `docs/migration/risk-register.md` R1):

- Per-installation randomly generated secret key (`commercial_runtime/security/app_secret.py`), never a hardcoded/shared literal.
- Password hashing via PBKDF2-HMAC-SHA256 with legacy SHA-256 upgrade-on-login path.
- Multi-tenant session scoping by `company_id`/`tenant_id` — never trusted from client-supplied payload alone; resolved from authenticated session/registry state.
- Failed-login lockout (5 attempts / 15 min) and audit logging on auth events.

## Deny-by-default rules for this project

- Every externally callable API (Owner Control Center and client-installation APIs) must validate authentication, authorization, tenant scope, request schema, and rate limits before doing anything else.
- Owner-dashboard authentication and customer-installation authentication are separate credential systems. An installation token must never grant Owner dashboard access, and an owner session must never be embedded in a customer application.
- No debug mode, no stack traces returned to clients, in production builds.
- HTTPS-only in production; no cleartext traffic, no disabled certificate validation.
- No hidden remote access, remote database browsing, backdoors, universal admin credentials, or remote shell access. Remote diagnostics require explicit customer approval, are time-limited, audited, and revocable.

## Known open items

See `docs/migration/risk-register.md` for specific flagged issues (R6: stack trace disclosure in a source-repo endpoint; R7: a likely f-string bug producing a constant secret in an unrelated source-repo file, not ported into this project).
