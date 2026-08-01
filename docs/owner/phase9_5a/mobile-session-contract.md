# Phase 9.5A — Mobile Session Contract

## `StaffSession` extension (additive columns)

```
refresh_token_hash (nullable — NULL for a browser-cookie session, set for a mobile session),
refresh_token_family_id (UUID, nullable — all refresh tokens descended from one login share a family;
  reuse of any non-current token in a family revokes the whole family),
access_token_last_issued_at (timestamptz, nullable),
platform (WEB | ANDROID | IOS, default WEB — extends the existing session concept rather than
  building ANDROID/IOS-specific business rules per Non-Negotiable Principle 1's "do not create
  separate Android and iOS business rules")
```

No password is ever stored beyond the existing `StaffUser.password_hash` (Argon2id, unchanged); no
token value is ever stored in plaintext — only `refresh_token_hash` (same HMAC/hash discipline already
used for `StaffSession.token_hash`).

## Flow (design contract, not routed this phase)

1. **Login**: `POST /api/operations/v1/auth/mobile/login` — email + password + `platform`. On success
   with `mfa_required=false`: issues access token (15 min) + refresh token (new `StaffSession` row,
   new `refresh_token_family_id`). With `mfa_required=true`: returns `MFA_REQUIRED` + a short-lived MFA
   challenge token (not a full session yet).
2. **MFA challenge**: `POST /api/operations/v1/auth/mobile/mfa-verify` — challenge token + TOTP code.
   On success: issues the real access+refresh token pair (same as step 1's success path).
3. **Refresh**: `POST /api/operations/v1/auth/mobile/refresh` — refresh token. Validates hash match,
   `revoked_at IS NULL`, not expired. Issues a NEW access token + NEW refresh token, invalidates the
   presented one (rotation). **Reuse detection**: if the presented refresh token's hash doesn't match
   the *current* one on record for that family (i.e., an old, already-rotated-away token is replayed),
   the entire `refresh_token_family_id` is revoked immediately — this is the concrete signal of
   token theft the ADR's "detects reuse" claim refers to, not just a stated intention.
4. **Logout**: `POST /api/operations/v1/auth/mobile/logout` — revokes the current session's refresh
   token family only (not every session on every device — a user logging out on one phone shouldn't
   log them out everywhere, unless...).
5. **Revoke one session** / **revoke all employee sessions**: management action
   (`security_sessions.revoke`), same underlying `StaffSession.revoked_at` mechanism used for the
   existing `disable_staff()` flow — extended (not duplicated) to also revoke mobile refresh-token
   families.
6. **Account suspension/termination**: `EmployeeProfile.employment_status -> SUSPENDED/TERMINATED`
   revokes every `StaffSession` for that `StaffUser` (existing real mechanism, Milestone 4) — including
   every mobile refresh-token family, automatically, no separate mobile-specific revocation code path
   needed.

## Required properties (all satisfied by this design)

MFA-capable (step 2); device/session registration (each login creates a distinct `StaffSession` row,
listable); revocable (per-session or all); short access lifetime (15 min); rotating refresh (every
`refresh` call issues a new one); reuse detection (family-wide revocation on replay); logout;
force-logout by admin; suspension/termination cascade; multiple concurrent authorized devices (each its
own `StaffSession` row, no artificial single-device limit); audited
(`MOBILE_LOGIN`/`MOBILE_TOKEN_REFRESHED`/`MOBILE_REFRESH_REUSE_DETECTED`/`MOBILE_LOGOUT` — Milestone 23);
secure storage is a client-side requirement (Android Keystore/iOS Keychain), stated here as the
contract's own expectation for whichever later phase builds the client; no password in this flow
beyond the one initial login call; no token in logs (the existing structured-logging redaction,
Phase 9, already strips `signature`/token-shaped base64 blobs — extended with a `refresh_token`/
`access_token` field-name pattern, Milestone 23's own concern); no token in a URL (every token is a
request body/header value, never a query string); no indefinite bearer token (15 min hard cap, no
"remember me forever" option).

## App version metadata, rate limits, error codes

`app_version` accepted on login (stored on `StaffSession`, informational — no enforced minimum version
gate this phase, schema-ready for one later). Rate limits: login attempts reuse the exact existing
`LoginAttempt`/lockout mechanism (Phase 4), not a new one. Error codes: `AUTHENTICATION_REQUIRED`,
`MFA_REQUIRED`, `INVALID_CREDENTIALS`, `ACCOUNT_SUSPENDED`, `REFRESH_TOKEN_INVALID`,
`REFRESH_TOKEN_REUSE_DETECTED` (new, mobile-specific — added to Milestone 19's catalog).
