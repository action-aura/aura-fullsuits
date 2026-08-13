# Phase 9.5A Milestone 18 — Mobile Authentication ADR

## Decision: short-lived opaque access token + rotating refresh token (Option B)

## Options compared

**A. Reuse browser session cookies.** Rejected: cookies require a cookie jar and `Secure`/`SameSite`
semantics that don't map cleanly onto native mobile HTTP clients, and — a real, concrete finding from
Phase 9's own capacity testing (`docs/owner/phase9/capacity-and-resilience-results.md`) — this
codebase's `SESSION_COOKIE_SECURE=True` already causes real friction for non-browser HTTP clients over
anything but a fully-TLS-correct connection. A mobile app is exactly a non-browser client.

**B. Short-lived opaque access token + rotating refresh token.** Chosen. Standard first-party mobile
pattern (matches, e.g., what a native Android/iOS app talking to its own backend typically does):
access token short-lived (e.g. 15 min) and sent as `Authorization: Bearer`, never a cookie; refresh
token longer-lived, single-use, rotated on every use, detects reuse (a replayed old refresh token
revokes the whole session chain — a real signal of token theft).

**C. Another established pattern (e.g. OAuth2 device-code flow, WebAuthn-only).** Rejected as
over-engineered for a first-party, single-organization internal app with a known, small user base
(employees + two management accounts) — device-code flow solves a "third-party app, no shared trust"
problem this system doesn't have.

## Why not reuse the product-license device identity (Ed25519 device keypair)

Explicitly a separate security domain per the governing instruction. The product licensing device
identity (Phase 6/8, `DevicePublicKey`/Ed25519) proves *which physical device* is running a licensed
product installation — a commercial-enforcement concept. Employee mobile login proves *which human* is
using the Aura Owner Mobile app — an identity/authorization concept. Conflating them would mean, e.g., a
compromised employee phone could be mistaken for a licensing-relevant device, or a device replacement
(Phase 8's own real, tested workflow) would have unintended employee-session side effects. Kept
structurally separate: no shared table, no shared key material, no shared trust store.

## Real infrastructure reused

`StaffUser` remains the one account; `StaffSession` (existing: `token_hash`, `session_version_at_login`,
`expires_at`, `mfa_verified_at`, `revoked_at`/`reason`) is extended (additive columns) to also represent
a mobile refresh-token session, rather than building a parallel session table — see
`mobile-session-contract.md` for the exact extension.

## What this phase builds vs. defers

Real: the schema extension (additive columns on `StaffSession`, a new `refresh_token_hash` +
`refresh_token_family_id` + `access_token_last_issued_at`), and the full request/response contract
(`mobile-session-contract.md`). **Not built this phase**: the actual `/api/operations/v1/auth/*` route
implementations — per the governing instruction's own explicit boundary ("do not implement the full
mobile authentication stack unless explicitly needed to validate foundational schema/contracts"), and
because no mobile client exists yet to consume it. The schema is validated instead via real unit tests
of the token-family/rotation/reuse-detection *logic* in isolation (Milestone 22/24), proving the design
is implementable without building the full stack prematurely.
