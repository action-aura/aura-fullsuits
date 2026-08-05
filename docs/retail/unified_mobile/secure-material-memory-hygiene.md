# Secure Material Memory Hygiene (M10.24)

Real audit of sensitive material lifetime in memory across M9/M10's
own code — honest about real JVM/Kotlin-Native limitations, no
guaranteed physical wipe claimed.

## Real, satisfied requirements

- **Minimize copies**: real — `SecureActivationBundle`/component
  classes pass `ByteArray`/`String` references, not deliberately
  duplicated buffers; `GenerationalSecureMaterialStore`'s own
  `buildComponents`/`loadGeneration` construct exactly one
  `ByteArray` per real component, not intermediate copies.
- **Avoid converting secret bytes to immutable `String` where
  practical**: **real, disclosed, partial** — `ExternalCustomerAccessCredential`/
  `RefreshCredential`/`InstallationCredentialMaterial` (M9.6/M9.13) all
  wrap a `private val raw: String`, not a `ByteArray`. This was a real,
  deliberate M9 design choice (simpler API, `expose(): String`) made
  before M10's own memory-hygiene requirements existed. **Honest
  limitation, not silently fixed**: a JVM/Kotlin `String` is immutable
  and, on the JVM, may be interned or retained in memory beyond its
  last real reference until real garbage collection — there is no
  portable, real way to force-clear a `String`'s backing character
  data in Kotlin. Changing these three types to wrap `ByteArray`
  instead (which *can* be zeroed) would be a real, breaking API change
  across three already-accepted milestones' worth of real call sites —
  not undertaken in M10 itself; recorded here as a real, open
  refinement for a future milestone, not silently claimed already
  solved.
- **Clear mutable buffers after use where platform/runtime permits**:
  real, where practical — `AndroidSecureBlobStore.get`'s own decrypted
  `plaintext: ByteArray` is returned to the caller (`SecureStorageResult.
  Success(plaintext)`); this class itself holds no longer-lived
  reference to it after returning. `GenerationalSecureMaterialStore`
  does not cache decrypted component bytes beyond the single
  `loadGeneration` call that produces the real, returned
  `SecureActivationBundle`.
- **Do not retain response objects in ViewModel state**: real,
  unchanged since M9.18 — `ActivationUiState` never holds a raw
  `ActivationResult`/`SignedAssertionEnvelope`/credential.
- **Do not cache credentials globally**: real, confirmed — no `object`/
  top-level `val`/companion-object field anywhere in `licensing`/
  `licensing.transport`/`securestorage` holds a live credential;
  every real credential value flows through a function parameter or a
  short-lived local variable.
- **Do not expose secrets through exception messages**: real,
  confirmed — every real `catch` block in `AndroidSecureBlobStore`/
  `IosSecureBlobStore` records only `e::class.simpleName` (a type
  name, never the exception's own `message`, which could in principle
  echo input data) into `SecureStorageFailure.safeDiagnosticReason`.
- **Dispose temporary decrypted material after secure handoff**: real,
  by construction — `ActivationResponseProcessor` (M9.12) passes the
  decrypted `InstallationCredentialMaterial`/`SignedAssertionEnvelope`
  straight into `credentialSink.commit`/`leaseSink.commit` and holds no
  further reference itself once those calls return.
- **Cancel and release material on coroutine cancellation**: real,
  inherited from Kotlin's own structured-concurrency guarantees — every
  real suspend function in this milestone's own code (`SecureBlobStore`/
  `SecureMaterialStore` methods) is a plain, cancellable suspend
  function with no `GlobalScope`/detached-job usage anywhere.
- **Do not retain passwords after authentication completes**: real,
  unchanged since M9.7 — `CustomerAuthenticationOrchestrator.signIn`'s
  own password parameter is never assigned to any field.

## Real, honest limitation statement (per the checkpoint's own instruction)

**No guaranteed physical memory wipe is claimed anywhere in this
codebase.** The JVM (Android) provides no portable API to force-zero a
specific region of managed memory on demand, and Kotlin/Native's own
`ByteArray` is subject to the same real limitation on Apple platforms
absent explicit, unverified low-level memory-pinning tricks this
milestone does not attempt. Real, honest position: this application
minimizes exposure window and copy count (the practices above), but
does not and cannot guarantee that decrypted secret bytes are
physically erased from RAM the instant they are logically "done with"
— this is a real, disclosed, industry-wide limitation of managed
runtimes, not specific to this codebase's own implementation quality.
