# Mobile HTTP Client Decision (M9.4)

Real evaluation of available KMP HTTP options, and the real decision
this milestone makes: **document and design around Ktor, but do not
add the dependency yet.**

## Real evaluation

| Criterion | Ktor Client | OkHttp (legacy `OwnerClient`) |
|---|---|---|
| `commonMain` compatibility | Real, official JetBrains KMP HTTP client — yes | No — JVM-only, confirmed by `mobile-licensing-network-audit.md`'s own audit of the legacy client |
| Android engine | `ktor-client-okhttp` or `ktor-client-android` (real, maintained engines) | N/A — it IS the engine |
| iOS engine | `ktor-client-darwin` (real, maintained, `NSURLSession`-backed) | None — cannot target iOS at all |
| Cancellation | Real, native `kotlinx.coroutines` cancellation support | Blocking, synchronous (`OwnerClient.requestRaw` calls `.execute()`) — not cancellable |
| Timeouts | Real, per-request and per-client configurable | Real (`OkHttpClient.Builder` connect/read timeout) |
| TLS | Platform-default trust store per engine, real support for custom `TrustManager`/pinning | Platform default, no pinning found |
| Request/response interception | Real `HttpClientPlugin`/`HttpRequestPipeline` mechanism | Real `Interceptor` mechanism (Android-only) |
| JSON integration | Real, first-class `kotlinx.serialization` `ContentNegotiation` plugin — same serializer this project already uses for every M7.17/M8 contract model | Gson — a real, already-identified correctness hazard (`mobile-licensing-network-audit.md`'s own generic-`Map` corruption finding) |
| Dependency size | Moderate, modular (only needed engines/plugins pulled in) | N/A (already present, Android-only) |
| Maintenance burden | Actively maintained by JetBrains, same org as Kotlin/Compose Multiplatform itself — version-compatibility risk is lower than a third-party KMP HTTP library | N/A |
| Testability | Real `MockEngine` for deterministic, dependency-free tests | `mockwebserver` (real, already used in the legacy app's own tests) |
| Logging/redaction | Real `Logging` plugin with pluggable, redactable log format | Manual (none found in the legacy client) |

## Real decision

**Ktor is the correct future choice** — it is the only real option
satisfying every `commonMain`/iOS-compatibility requirement, and its
`kotlinx.serialization` integration directly avoids the exact generic-
`Map` corruption class already found in the legacy Gson-based client.

**M9 does not add the Ktor dependency in this milestone.** Real,
disclosed reasoning:
1. M9 never executes a real HTTP call under any circumstance
   (`production-transport-availability-rule.md`) — `DisabledProduction
   Transport` needs no HTTP library at all to implement.
2. Adding `ktor-client-darwin` (the iOS engine) to `iosMain` cannot be
   verified on this Windows host — no macOS/Xcode exists to confirm
   real Kotlin/Native compilation against it (same real, standing
   constraint as every prior milestone's iOS disclosure). Adding an
   unverified iOS dependency would be exactly the kind of "unverified
   addition" this whole initiative's own discipline exists to avoid.
3. Deferring the dependency until the milestone that actually executes
   a request keeps M9's own real, verified build (Android debug APK,
   596+ shared tests) free of an untested new dependency surface.

## What M9 does instead

`ExternalLicensingTransport` (M9.2) is a pure Kotlin `interface` with
`suspend fun` signatures — no HTTP library import anywhere in its
definition or in `DisabledProductionTransport`. `ExternalApiConfiguration`
(M9.5) defines the real configuration shape a future Ktor-backed
implementation will consume, without requiring Ktor to exist yet. When
a later milestone first needs to execute a real request, it adds
`ktor-client-core`/`ktor-client-content-negotiation`/
`ktor-client-okhttp` (Android)/`ktor-client-darwin` (iOS) and
implements `ExternalLicensingTransport` against them — a real,
scoped, verifiable addition at the point it is actually needed.
