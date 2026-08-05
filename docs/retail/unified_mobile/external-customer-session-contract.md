# External Customer Session Contract (M9.6)

`ExternalCustomerSessionContracts.kt` — real, shared external Customer
session model, structurally separate from Aura Owner employee sessions
(`StaffUser`/`StaffSession`), local Retail user sessions, Installation
credentials, and signed License leases
(`owner-internal-vs-customer-boundary.md`'s own real, cited
distinction).

## Real models

`ExternalCustomerAccountId`/`ExternalCustomerSessionId` — opaque,
`toString()`-redacted wrappers (never a raw string floating through
presentation code). `ExternalCustomerAccessCredential`/
`ExternalCustomerRefreshCredential` — real secret-material classes;
`expose()` is the only way to read the raw value, and `toString()`/
`equals()`/`hashCode()` never leak it. `CustomerSessionExpiry`.
`ExternalCustomerSession` — the real, immutable snapshot, itself
`toString()`-redacted.

## Real, closed `CustomerAuthenticationState`

`NotConfigured`, `SignedOut`, `Authenticating`,
`VerificationRequired`, `Authenticated`, `Refreshing`, `Expired`,
`Revoked`, `Disabled`, `Locked`, `TransportUnavailable`, `Error` —
every state the checkpoint's own M9.6 list names, modeled as a real
sealed interface (not a bare enum, since `VerificationRequired`/
`Authenticated`/`Error` each carry real associated data).

## Credential discipline (real, enforced, not aspirational)

- Redacted from `toString` — every credential-carrying type overrides
  it.
- Never appear in `UiState` — enforced downstream in
  `activation-presentation-contract.md`'s own `ActivationUiState`
  shape, which never embeds `ExternalCustomerAccessCredential`/
  `ExternalCustomerRefreshCredential` directly.
- Never logged — no `println`/logger call exists anywhere in this
  package (M9.23 adds the explicit regression test).
- Never placed in navigation arguments — `AuraRoute` (M6) types never
  reference these classes.
- Never written to business SQLDelight — no `.sq` file references
  these types (confirmed by inspection — the licensing package has no
  SQLDelight schema of its own).
- Remain behind the protected `CustomerSessionCredentialSink`
  abstraction (M9.13) for any future persistence — M9 defines the
  interface only; M10 implements real secure storage.

## Real request/result shapes

`CustomerRegisterRequest`, `CustomerVerifyAccountRequest`,
`CustomerSignInRequest`, `CustomerSignInResult`,
`CustomerRefreshSessionRequest`, `CustomerSignOutRequest` — every
password-carrying request overrides `toString()` to redact the
password field.
