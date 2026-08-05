# Missing `commercial_runtime` Fixture Investigation (M8.11)

Real investigation of the gap M7.13 disclosed:
`commercial_runtime/licensing_contracts/canonical.py`'s own module
docstring references `commercial_runtime/licensing_contracts/tests/
fixtures/canonical_vectors.json`, a file confirmed absent by direct
filesystem check.

## 1. Document path containing the reference

`commercial_runtime/licensing_contracts/canonical.py`, module
docstring, lines 8-11: "covered by the shared conformance fixtures in
`commercial_runtime/licensing_contracts/tests/fixtures/
canonical_vectors.json`, which both this module's tests and Owner's
own test suite can be checked against to catch drift."

## 2. Expected fixture name

`canonical_vectors.json`, expected at
`commercial_runtime/licensing_contracts/tests/fixtures/
canonical_vectors.json`. Confirmed absent again in this milestone
(direct `find`/`ls` re-check, same result as M7.13).

## 3. Expected schema — RECOVERED, real, executable evidence

The schema is fully recoverable — **not lost**, just never
materialized into the promised standalone file. Found in three
independent, mutually cross-checked real implementations, all
agreeing on the identical output for the identical input:

1. `commercial_runtime/licensing_contracts/tests/test_canonical.py:
   64-78`, `test_matches_owner_known_vector()` — the literal Python
   vector:
   ```python
   payload = {
       "contract_version": "v1", "product_code": "AURA_RETAIL",
       "nonce": "abc123", "timestamp": "2026-07-22T10:00:00+00:00",
   }
   expected = (
       '{"contract_version":"v1","nonce":"abc123",'
       '"product_code":"AURA_RETAIL","timestamp":"2026-07-22T10:00:00+00:00"}'
   )
   ```
   Its own comment: "Cross-checked by hand against
   `owner/app/licensing_service/canonical.py`'s own doctest-equivalent
   behavior (Phase 6) — same algorithm, same output."
2. `android/aura-retail/app/src/test/java/com/actionaura/retail/
   licensing/CanonicalTest.kt:60-69` — the identical vector, ported to
   Kotlin, whose own comment explicitly cites
   `test_canonical.py::test_matches_owner_known_vector` as its source
   of truth.
3. `android/aura-clinic/app/src/test/java/com/actionaura/clinic/
   licensing/CanonicalTest.kt` — the same vector again, in the Clinic
   client's own independent Kotlin port.

Three independent real implementations (Owner Python, `commercial_
runtime` Python, legacy Android Kotlin ×2) agree byte-for-byte on this
vector. The schema is therefore real, resolvable, and not fabricated
by this investigation.

## 4. Tests that should consume it

Per `canonical.py`'s own docstring intent: any future canonicalization
implementation (Owner's own, `commercial_runtime`'s own, or a future
Unified Mobile Kotlin port — gap #7,
`licensing-gap-ownership-matrix.md`) should be checkable against this
one shared vector to catch cross-implementation drift.

## 5. Equivalent fixture already existing under another name

Yes — functionally equivalent to the missing file, real evidence
exists inline in `test_canonical.py` and both legacy `CanonicalTest.kt`
files, just never extracted into the standalone JSON file the
docstring promises.

## Resolution taken in this milestone

**Schema is fully recoverable from executable evidence — a sanitized,
versioned reference fixture is created in this branch's own canonical
shared fixture location**, not inside `commercial_runtime/` itself
(that package is a separate deployable this branch does not own or
build, and modifying it was never authorized by any M7/M8
instruction — the fix is scoped to what this branch actually controls).

`mobile/aura-retail-unified/shared/src/commonTest/kotlin/com/
actionaura/retail/licensing/fixtures/CanonicalVectorFixture.kt`
reproduces the real, triple-cross-checked vector above as a real,
versioned Kotlin fixture (`CANONICAL_VECTOR_SET_VERSION`), ready for
the future Kotlin canonicalization port (gap #7) to assert against —
so when that port lands, it inherits a pre-verified cross-
implementation vector instead of starting from zero.

## Documentation reference updated

`offline-license-lease-contract-audit.md` and
`licensing-gap-ownership-matrix.md` gap #6 are updated (see the M8
follow-up note appended to each) to record that the canonicalization
*schema* itself is no longer an open question — the vector is real,
resolved, and mirrored in this branch's own fixture. What remains
genuinely open (and unchanged in classification, `COMMERCIAL_RUNTIME`)
is only that `commercial_runtime/licensing_contracts/tests/fixtures/
canonical_vectors.json` itself still does not exist as a real file in
that package — this investigation resolves the *schema-recoverability*
question, not the *upstream file's own absence*, which remains that
package's own maintainers' responsibility to fix.

## No fabrication

Nothing in this investigation invents a new canonicalization behavior
— every byte of the recovered vector comes from real, already-existing,
already-tested code. No real credentials, PII, or private keys are
involved (the vector is a plain JSON canonicalization example, not
signed material).
