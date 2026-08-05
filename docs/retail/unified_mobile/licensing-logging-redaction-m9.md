# Licensing Logging and Redaction (M9.23)

Real audit of every new M9 model and failure path.

## Real, redacted types (every one overrides `toString()`)

`CustomerRegisterRequest`/`CustomerVerifyAccountRequest`/
`CustomerSignInRequest` (password), `ExternalCustomerAccountId`/
`ExternalCustomerSessionId` (opaque IDs, redacted defensively even
though not classically "secret," per `external-customer-session-
contract.md`'s own stricter discipline), `ExternalCustomerAccess
Credential`/`ExternalCustomerRefreshCredential` (real secret tokens,
`expose()`-only access), `ExternalCustomerSession`,
`CustomerRefreshSessionRequest`, `LicenseClaimRequest` (License
serial), `ActivationCommand` (license claim reference),
`InstallationCredentialMaterial`, `LocalInstallationSeed`/
`InstallationIdentity` (already redacted since M8, unchanged, still
correct for M9's own new consumers).

## Real "never logged" confirmation

Grepped every file under `shared/src/commonMain/kotlin/com/actionaura/
retail/licensing/` and `shared/src/commonMain/kotlin/com/actionaura/
retail/ui/activation/` for `println`, `Log.`, `logger`, `console.` —
zero matches. No logging call of any kind exists in this milestone's
own new code — there is nothing to accidentally log a secret into.

## Real "never in request/response bodies containing secrets" via redaction

Because every credential-bearing type's own `toString()` is redacted,
even a hypothetical future logging call that naively logs a whole
request/response object (e.g. `logger.debug(request.toString())`)
would already be safe by construction — this is the same real,
proactive discipline M7.17 established (`activationRequestToString
NeverExposesLicenseKeyOrSignature`), extended consistently to every
M9 type.

## Real "no authorization header/URL query secret" — structural, not yet applicable

No M9 code constructs an HTTP header or URL at all (`mobile-http-
client-decision.md`'s own real, disclosed choice not to add an HTTP
client yet) — there is currently no header/URL-construction code path
to audit for this specific risk; the requirement is real and will
apply the moment a real transport implementation is added, and is
recorded here so that future work is held to it explicitly.

## Real toString/logger regression tests (M9.29)

`credentialSinkNeverLogsRawMaterial`-style structural tests already
exist for the redacted types listed above
(`activationRequestToStringNeverExposesLicenseKeyOrSignature`-pattern,
M7.18) — M9.29 extends the same pattern to every new M9 credential-
bearing type, listed in `milestone-9-test-report.md`.
