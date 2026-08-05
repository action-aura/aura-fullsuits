# Mobile Licensing Network Audit (M9.1)

Real audit of every existing networking-adjacent component in the
codebase relevant to a future licensing transport, across the legacy
Android Retail app and the current Unified Mobile shared module.

## Legacy Android Retail app — real, existing `OwnerClient`

`android/aura-retail/app/src/main/java/com/actionaura/retail/licensing/
OwnerClient.kt` (mirrored, with the same class name and shape, in
`android/aura-clinic`).

- **HTTP library**: OkHttp 4.12.0 (`android/aura-retail/app/build.gradle:178`),
  a plain, synchronous `OkHttpClient` — not coroutine-native, not KMP-
  compatible (JVM-only).
- **JSON**: Gson 2.11.0 converter (`build.gradle:177`) — same JVM-only
  constraint. Critically, `OwnerClient` deliberately does **not**
  parse-then-reserialize the response for signed calls — it returns
  the raw response body string verbatim, because Gson's generic
  `Map<String, Any?>` deserialization silently converts every JSON
  integer to `Double` (real finding, confirmed via physical Phase 7V-A
  testing — a genuinely valid signature would verify as invalid after
  a round-trip). Real, important lesson for M9.2/M9.12: any future
  shared response-decoding layer must decode directly into typed data
  classes (as M7.17's `kotlinx.serialization` models already do), never
  through a generic `Map`, or risk the identical corruption class.
- **Base URL configuration**: `OwnerClientConfig.baseUrl`, plain
  constructor parameter — no environment/build-variant wiring visible
  in this file (configuration source not audited further, out of this
  file's own scope).
- **Timeout behavior**: `timeoutSeconds = 10` default, applied to both
  connect and read timeout.
- **Retry behavior**: real, deliberate, bounded — `maxRetries = 4`,
  exponential backoff with 25% jitter (`retryBaseBackoffMillis = 1000`,
  `retryMaxBackoffMillis = 30000`), retries only on `429/500/502/503/504`
  plus `SocketTimeoutException`/generic `IOException`. **Never** retries
  a TLS failure (`SSLException` thrown immediately, not retried —
  explicit real comment: retrying could mask a real MITM attempt).
  Honors a real `Retry-After` header.
- **Nonce/idempotency**: a fresh nonce (24 random bytes,
  `SecureRandom`) and fresh signature are generated on **every**
  attempt, including retries — explicit, real, tested rationale: a
  retried identical nonce would be rejected as `NONCE_REUSED` by
  Owner's own real replay protection if the earlier attempt actually
  landed and only the response was lost (a real failure mode confirmed
  via physical device testing).
- **TLS**: relies on OkHttp's platform default trust store — no custom
  trust-all mode, no certificate pinning, found in this file.
- **Certificate/proxy handling**: not customized in this file (platform
  default).
- **Error parsing**: maps to a small, real, closed set of local
  exception types (`NetworkError`, `MalformedResponseError`), each
  carrying a `reasonCode` string aligned with `commercial_runtime/
  licensing_contracts/reason_codes.py`'s own `LOCAL_REASON_CODES`
  vocabulary already audited in M7.15.
- **Logging**: none found in this file — no request/response body
  logging present.
- **Connectivity detection**: none — relies purely on the real
  exception outcome of the attempted call.
- **Cancellation**: none — this is a blocking, synchronous call
  (`http.newCall(...).execute()`), not coroutine-cancellable.
- **Android lifecycle interaction**: none in this file — presumably
  invoked from a background thread/worker by its own caller (caller
  not audited here).
- **Current activation calls**: `activate`/`checkIn`/`deactivate`/
  `fetchSigningKeys`/`fetchServiceInfo` — a real, complete mirror of
  the M7-audited external API surface
  (`remote-licensing-api-contract-map.md`).
- **Local Flask / loopback / Chaquopy**: real, explicit design —
  this class's own docstring states every response is "UNTRUSTED until
  handed to the embedded Python backend's `/_internal/sync-*` routes
  for independent re-verification" — i.e. the legacy Android app
  embeds a local Python runtime (Chaquopy) that re-verifies Owner's
  signature locally. **This is exactly the loopback/Chaquopy pattern
  M6's own checkpoint already prohibited for Unified Mobile** ("No
  Chaquopy/local Flask introduced" — M6 gate, still true; M9 does not
  introduce it either).
- **Release/version calls**: `fetchServiceInfo` covers real Owner
  service metadata; no dedicated release-manifest call found in this
  file (matches M7.14's own finding that `product-version-check` lives
  in a separate, non-live prototype route).

## Unified Mobile shared module — current real state

`shared/build.gradle.kts` — **no HTTP client dependency of any kind
exists yet** (grepped for `ktor`/`okhttp`/`retrofit`/`http` — zero
matches in `commonMain`, `androidMain`, or anywhere else in this
file). Real, current dependencies relevant to this audit:
`kotlinx-coroutines-core:1.9.0`, `kotlinx-serialization-json:1.7.3`,
`kotlinx-datetime:0.6.1` — all real KMP-compatible, already used by
M7.17/M8's own contract models. `androidx.security:security-crypto:
1.1.0-alpha06` is present in `androidMain` (added by an earlier
milestone in preparation for the M10 secure-storage work) — not used
by anything yet, and M9 does not begin using it (out of scope, per
M9's own explicit "do not implement Android secure storage" rule).

## Reusable shared components (real, already exist)

- `kotlinx.serialization.json.Json` — the real, existing, proven
  decoder for every M7.17/M8 contract model. M9's transport layer must
  decode directly into these typed models, mirroring the legacy
  client's own hard-learned lesson about avoiding generic-`Map`
  round-tripping.
- `LicensingError`/`ServerReasonCode`/`LocalReasonCode` (M7.15) — the
  real, closed error vocabulary M9.17's error mapping must reuse
  verbatim, not reinvent.
- `ResolvedDevicePolicy`, `ActivationRequest`/`ActivationResult`,
  `SignedAssertionEnvelope` (M7.17/M8) — the real, existing typed
  payload/response shapes M9's orchestration layer must construct
  and consume, not redefine.

## Android-only / obsolete / insecure findings

- OkHttp + Gson (legacy `OwnerClient`) is Android-only — cannot be
  reused in `commonMain` for iOS compatibility (M9.26's own
  requirement).
- The legacy Chaquopy-embedded-Python re-verification pattern is
  explicitly not carried into Unified Mobile (already prohibited since
  M6).
- No insecure behavior (trust-all TLS, hardcoded credentials, cleartext
  URL) was found in `OwnerClient.kt` itself — its real design is
  already careful (TLS failures never retried, license key cleared
  after use, fresh nonce per attempt). These are real, good patterns
  M9's own shared orchestration should preserve conceptually, not
  discard as "the old, insecure way."

## Missing `commonMain` authority (the real gap M9 fills)

No shared, KMP-compatible transport interface, retry policy,
connectivity abstraction, or orchestration layer exists anywhere in
`commonMain` today — every real networking concept currently exists
only in the Android-only, JVM-only legacy client. M9.2 onward define
the shared replacement; the legacy `OwnerClient`'s own real, tested
behaviors (retry-on-5xx-and-429, never-retry-TLS-failure, fresh-nonce-
per-attempt, honor-Retry-After, typed-not-generic decoding) are the
real evidence basis for M9.15's retry policy and M9.2's transport
contract — not invented from scratch.

## iOS compatibility

Nothing in the current legacy client or current shared module
constrains iOS compatibility either way — the legacy client is simply
inapplicable (Android-only), and the shared module currently has zero
networking code to migrate. M9's own new code is designed KMP-first
from the start (M9.26 governs this explicitly).

## Migration target

M9 does not migrate `OwnerClient.kt` itself — it builds a new,
`commonMain`-native transport boundary informed by its real, audited
behavior. The legacy client remains untouched (this branch never
modifies `android/aura-retail`/`android/aura-clinic`, per every prior
milestone's own standing constraint).
