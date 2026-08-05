# External Licensing Transport Contract (M9.2)

One `commonMain` transport boundary: `ExternalLicensingTransport`
(`shared/.../licensing/transport/ExternalLicensingTransport.kt`).

## Real operation set

`register`, `verifyAccount`, `signIn`, `refreshCustomerSession`,
`signOut`, `claimLicense`, `activateInstallation`, `refreshLease`,
`listInstallations`, `deactivateInstallation`, `requestReplacement`,
`checkRelease` — each justified by an already-accepted M7/M8 contract
(`OWNER-EXTERNAL-CUSTOMER-IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md` for
the customer-session operations; `remote-licensing-api-contract-map.md`
for activation/lease/installation operations;
`mobile-release-version-contract.md` for `checkRelease`). No operation
was added without a real, cited contract behind it.

## Real result wrapper

Every operation returns `TransportOutcome<T>`
(`TransportOutcome.kt`) — a closed sealed type distinguishing:
`Success`, `BusinessRejection`, `AuthenticationRejection`,
`RateLimited`, `Timeout`, `NetworkFailure`, `TlsFailure`,
`MalformedResponse`, `UnsupportedContractVersion`, `Cancelled`,
`TransportNotConfigured`. No raw exception ever crosses this
boundary into presentation code.

## Real implementations (exactly three, per `production-transport-availability-rule.md`)

1. `DisabledProductionTransport` — the only one ever wired into
   release production DI; every operation returns
   `TransportNotConfigured`.
2. A `commonTest`-only fixture transport (`fixture-transport-report.md`).
3. No real HTTP implementation exists in M9 — deliberately deferred
   (`mobile-http-client-decision.md`'s own real, disclosed reasoning).

## Not invented

No URL, no auth header shape, no production endpoint string appears
anywhere in this interface or its real implementations — every method
signature is pure Kotlin types, no transport-specific detail leaks
into the contract itself.
