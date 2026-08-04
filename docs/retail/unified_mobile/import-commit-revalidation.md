# Import Commit Revalidation (M5.8.14)

Real bounded commit token + server-side revalidation — never trusts a
client-submitted count. Proven by `ImportCommitRevalidatorTest.kt` (9/9).

## Token shape (`ImportCommitToken`)

`dryRunId, sourceHash, mappingVersion, schemaVersion, companyId,
branchId, idempotencyKey, nonce, issuedAtEpochMillis, expiresAtEpochMillis`
— every field is a copy of the real dry-run's own value at issuance
time (`ImportCommitTokenFactory.issue`). The token carries no counts
(no `plannedInserts` etc.) — commit logic (M5.8.15/16) reads counts
only from the re-fetched real `ImportDryRun`, never the token.

## Revalidation is 8 real, ordered checks (`ImportCommitRevalidator.revalidate`)

1. Dry-run exists for `(token.dryRunId, token.companyId)` → else `DryRunNotFound`
2. Not already consumed → else `CommitConflict`
3. Not expired (token OR dry-run) → else `DryRunExpired`
4. `sourceHash` matches → else `SourceMismatch`
5. `mappingVersion`/`schemaVersion` match → else `SchemaMismatch`
6. `companyId` matches → else `AccessDenied`
7. `branchId` matches → else `SourceMismatch`
8. `commitEligible == true` → else `CommitConflict`

Every check compares the token's claimed field against the REAL,
independently re-fetched dry-run — never the reverse.

## Cross-company token: proven to fail at lookup, not later

`aTokenClaimingADifferentCompanyThanTheRealDryRunNeverSucceeds` proves
a token issued from a dry-run belonging to company 2 but queried with
company 1 resolves to `DryRunNotFound` — the company-scoped
`getDryRun(id, companyId)` lookup itself returns nothing, so a
mismatched company never reaches the field-by-field checks at all.
This is a structurally stronger guarantee than "checked and rejected
afterward."

## Real, disclosed limitation

Token integrity (tamper detection on the token's own bytes, e.g. a
signature/MAC) is NOT implemented this milestone — the token is a
plain data class, not a signed/encrypted artifact. This is acceptable
because every real security-relevant field is independently
re-verified against durable storage per check above; a maliciously
edited token can only ever narrow what happens (fail a check) never
widen it (no check trusts the token's value without confirming it
against the real stored dry-run). `ImportAccessContext` (M5.8.19,
`DEFERRED_TO_MILESTONES_7_TO_10`) will own real actor-identity binding.
