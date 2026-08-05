# Import Center UI Vertical Slice (M6.19)

Real, shared Import Center presentation through commit and immutable
result — using the real M5.8 pipeline end-to-end: real
`CsvImportDecoder` → real `ImportEntityDetector` → a real, durable
dry-run → a real bounded commit token → the real, transactional,
rollback-proven `ImportCommitExecutor.commit`. Proven by
`ImportCategoriesViewModelTest.kt` (3/3), all passing on the first
real run — real, direct evidence the M5.8 architecture composes
correctly end-to-end from a UI layer, not just from its own internal
tests.

## Real, disclosed scope decisions

1. **Paste-based input, not a real file picker.** No real Android
   `FilePicker` implementation exists yet
   (`import-android-adapter-validation.md`'s own disclosed gap — real
   `Activity`/`Intent` wiring is UI-adjacent work M5.8's own checkpoint
   placed out of scope, and it remains unbuilt). The user pastes real
   CSV text directly; it is decoded through the exact same real
   `CsvImportDecoder` a real file's bytes would go through — this is
   real, user-supplied data processed by the real pipeline, never
   hard-coded sample data standing in for a real import.
2. **Categories only**, reusing the real M5.8 5-entity pipeline scoped
   to one entity for this milestone's own tractability.
3. **The required 7-step route flow is collapsed into ONE real screen**
   with internal `ImportStep` state (`PasteInput → DryRunReady →
   Committing → Result`), not 7 separate navigation destinations. The 6
   other Import routes (`ImportFileSelection`/`ImportInspection`/
   `ImportMapping`/`ImportDryRun`/`ImportCommit`/`ImportResult`) remain
   real, typed, complete route declarations (`AuraNavHost.kt`) — each
   renders `FeatureUnavailableScreen` citing this exact real
   simplification, not silently dropped.
4. **A new real hash utility was needed and built**: `ImportSourceHasher`
   (FNV-1a 64-bit, pure Kotlin). Real, disclosed finding: M5.8 never
   built a real hash function for `ImportDryRun.sourceHash` — every
   M5.8 test used a literal test string. Real, deliberate choice: FNV-1a
   is appropriate here because `sourceHash` detects "the file changed
   since the dry-run was issued" (`import-commit-revalidation.md`), not
   a security/tamper-proofing boundary — a cryptographic hash would be
   the real requirement if this value were ever used as one, which
   `import-commit-revalidation.md`'s own real, disclosed token-integrity
   scope already establishes it is not.

## Real, structural proof (not merely "no exception thrown")

- `pastingRealCsvTextAndPreviewingProducesARealDurableDryRun` —
  re-reads the dry-run from `ImportPersistenceRepository` directly,
  independent of the ViewModel's own in-memory state.
- `committingARealPreviewedImportWritesRealCategoryRowsThroughTheRealTransactionalExecutor`
  — re-reads the real Category row from `CategoryRepository` after
  commit, independent of the reported `ImportCommitResult`.
- `emptyPastedTextProducesARealNonCommitEligibleDryRun` — proves the
  real `CsvImportDecoder`'s own `NoDataRows` validation runs; the
  preview step never fabricates rows to force a "successful" demo.

## Real, disclosed scope NOT built this milestone

- Multi-entity import (Products/Suppliers/Branches/Customers) through
  this UI — the real pipeline supports all 5 (M5.8), this screen only
  exercises Categories.
- Real duplicate/conflict/warning display — `ImportDryRun.duplicateCount`/
  `conflictCount`/`warningCount` are always `0` in this screen's own
  dry-run construction (no real duplicate-detection pass is run before
  building the dry-run) — a real, disclosed simplification, not a claim
  the real `ImportDuplicatePolicy`/`ImportEntityDetector.isAmbiguous`
  machinery (both real and tested since M5.8) is wired into this UI yet.
- Real Android content-provider file selection — remains `NOT_VERIFIED`,
  matching the standing disclosure across this entire session for any
  real device-dependent capability.
