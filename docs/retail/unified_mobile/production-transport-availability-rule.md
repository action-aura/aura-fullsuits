# Production Transport Availability Rule (M9.3)

Because Aura Owner's real external Customer/licensing API does not
exist yet (M8's own accepted findings, `OWNER-EXTERNAL-CUSTOMER-
IDENTITY-AND-LICENSING-BOUNDARY-SPEC.md`), production wiring uses an
explicit unavailable transport.

## A. Contract test transport (test source sets only)

`ContractFixtureTransport` (`commonTest`,
`fixture-transport-report.md`) — deterministic, fixture-backed,
covers success and every real failure case from M7/M8's own sanitized
fixtures. Never referenced from any `commonMain`/`androidMain` file —
proven, not merely asserted, by `productionWiringCannotResolveTheFixtureTransport`
(M9.29).

## B. Disabled production transport (production-safe)

`DisabledProductionTransport` (`commonMain`,
`external-licensing-transport-contract.md`) — every operation returns
`TransportOutcome.TransportNotConfigured`. Never returns fake License
success, never issues fake credentials, never issues fake signed
leases. This is the one, real, only implementation reachable from
production dependency injection in this milestone.

## C. Future HTTP transport (not implemented in M9)

The interface (`ExternalLicensingTransport`) and configuration
contract (`external-api-configuration-contract.md`) exist; real
execution remains disabled until endpoint schemas and server
implementation exist. M9 deliberately adds no HTTP client dependency
(`mobile-http-client-decision.md`) — no speculative production route
string exists anywhere in this branch.

## Real test proving release wiring cannot resolve the fixture transport

`DisabledProductionTransportTest.kt` / `LicensingTransportWiringTest.kt`
(M9.29) asserts the real production DI graph (`AuraAppContainer`
equivalent for licensing, when wired in M9.20's startup integration)
constructs `DisabledProductionTransport`, never
`ContractFixtureTransport` — a structural, compile-time-adjacent proof
(the fixture transport class lives in `commonTest`, is not even
visible from `commonMain`/`androidMain` compilation units, so it
cannot be referenced by production code even accidentally).
