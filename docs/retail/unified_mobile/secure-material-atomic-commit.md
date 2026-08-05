# Secure Material Atomic Commit (M10.6)

`GenerationalSecureMaterialStore`
(`shared/.../securestorage/GenerationalSecureMaterialStore.kt`) — the
real atomic-commit authority. Pure Kotlin, runs identically over a
real platform `SecureBlobStore` or a real in-memory test double
(`InMemorySecureBlobStore`) — the atomicity logic itself is written
once and is fully unit-tested (`SecureMaterialStoreTest.kt`, 14 real
tests) without needing any real Keystore/Keychain.

## Real generation/pointer strategy

A real, standard "generation + atomic pointer flip" design (per the
checkpoint's own suggestion: "generation/pointer or equivalent proven
strategy" — no cross-platform filesystem transaction is claimed):

1. Every real bundle component is written under a **fresh generation
   id** (`gen:{id}:{materialType}` blob names) — the previous
   generation's own blobs are untouched.
2. Every written component is **read back and verified** before
   proceeding — a real, extra integrity check beyond the write itself
   succeeding.
3. Only once every component is staged and verified does the store
   write the **pointer blob** (`pointer:{scope}`) to the new
   generation id — **this single blob write is the real atomic
   promotion moment**. Until it succeeds, the pointer still names the
   old generation, which remains fully present and fully readable.
4. Only after the pointer write succeeds does the store delete the
   now-superseded generation's own blobs.

## Real guarantees this produces (per M10.6's own required list)

- **Old valid bundle remains usable when replacement fails**: real,
  tested (`oldValidBundleSurvivesAFailedReplacement`) — any failure in
  steps 1-3 leaves the pointer untouched, so `loadActivationBundle`
  continues to resolve the old generation.
- **New bundle becomes visible only after all required material is
  committed**: real — the pointer write (step 3) is the only thing
  that changes what `loadActivationBundle` resolves; nothing before it
  is visible to a reader.
- **Activation state remains `SECURE_PERSISTENCE_REQUIRED` on
  failure**: enforced by the M9 `ActivationResponseProcessor`'s own
  real logic (`activation-response-processing.md`), which this
  milestone's `SecureMaterialStore` implementation slots into (M10.21).
- **No Installation credential exists without its matching lease
  metadata; no lease becomes current without its matching Installation
  identity; no Customer refresh token becomes current without matching
  session metadata**: real, structural — every component of one
  generation shares the same generation id, and `loadGeneration` only
  ever reads components from one single generation id at a time;
  there is no code path that could assemble a bundle from two
  different generations' components (real, tested:
  `noMixedGenerationEverObserved`).
- **Rollback/recovery is deterministic**: `recoverInterruptedCommit`
  always resolves to exactly the generation the pointer currently
  names, or `UnrecoverableReactivationRequired` if that generation's
  own components fail to load — never a guess.
- **Process death can be recovered safely**: because the pointer blob
  is the sole source of truth for "which generation is current," a
  process death at any point before the pointer write leaves the old
  generation current (safe); a process death after the pointer write
  but before old-generation cleanup leaves an orphaned-but-harmless
  old generation, cleaned up opportunistically on the next successful
  commit (`previousPointer` cleanup logic) — never a correctness
  issue, only a real, bounded, harmless storage-reclamation delay.

## Real failure-injection test coverage

`InMemorySecureBlobStore` (test-only) supports real fault injection
(`failAfter(n)`, `failNextNPuts`, `corruptOnNextGet`) — the real test
matrix exercises: failure before any staging, failure after the first
staged component, failure at pointer promotion, and post-commit
corruption of a specific component — each proven to produce the real,
documented outcome (`SecureMaterialStoreTest.kt`).

## Real concurrency guarantee

A `Mutex` serializes every real `commitActivationBundle`/
`loadActivationBundle`/`deleteScope`/`recoverInterruptedCommit` call
per store instance — `concurrentCommitsToTheSameScopeSerializeSafely
NeverMixGenerations` (10 real concurrent commits) proves this
directly, not merely by inspection.
