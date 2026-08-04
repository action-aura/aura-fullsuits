# Import Android Adapter Validation (M5.8.20)

Real, per-adapter validation status — same disclosure discipline
already established for reporting's Android driver
(`reporting-android-driver-validation.md`): every real claim below is
either a real, executed JVM test or an explicit "compile-only, not
device-verified" disclosure, never blurred together.

## Corrected finding: `AndroidXlsxImportDecoder` IS real, JVM-unit-testable

`import-xlsx-security.md` originally stated this class "cannot be
exercised by a unit test on this host." Real, re-checked evidence this
milestone: its only imports are `java.util.zip.*` and `java.io.*` —
real, plain JVM standard library, not `android.content`/
`android.database`. It never touches a real Android framework class.
That original claim was true in spirit (no device/emulator exists on
this host) but overly conservative in practice — this class needed no
device at all. Corrected here rather than left standing, matching this
session's own "no silent overwrite of a prior wrong claim" discipline.

Real proof: `AndroidXlsxImportDecoderTest.kt` (4/4), driving the real
class with a `FakeImportSource` (a plain, commonMain-interface fake,
no Android dependency) and real, hand-built ZIP byte arrays
(`java.util.zip.ZipOutputStream`):
- A real minimal, valid XLSX decodes to the expected `NormalizedTable`
  (header row 1, one data row 2).
- A real non-ZIP byte stream is rejected as `MalformedContent`, not a
  crash.
- A real ZIP missing the worksheet part is rejected safely (proves the
  real `entryMetas.filter{...}.sorted().first()` call, which would
  throw `NoSuchElementException` if unguarded by
  `XlsxSecurityPolicy.checkEntries`'s own "at least one worksheet"
  check running first, never reaches that unguarded state).
- A real, genuinely-inflating repetitive payload (2,000,000 bytes of
  `'A'`, real DEFLATE compression) is rejected under a tightened real
  `maxUncompressedSizeBytes` limit — a true zip-bomb shape, not a
  hand-faked header (the standard `ZipOutputStream` API itself enforces
  real size/CRC consistency for `STORED` entries, so a declared-vs-
  actual mismatch cannot be constructed through it — confirmed by a
  real `ZipException` when the first attempt tried exactly that).

## Confirmed, genuinely untestable: `AndroidSqliteImportDecoder`/`AndroidSqliteRawReader`

Real imports: `android.content.Context`, `android.database.sqlite.SQLiteDatabase`
— real Android framework classes. No Robolectric dependency exists in
this project (confirmed by `grep` during M5.8.8), and no
`adb`/emulator exists on this host (confirmed by direct command
during M5.7). This claim stands, unlike the XLSX one — the real
decision pipeline these classes delegate to (`SqliteImportPipeline`,
via the `SqliteRawReader` interface boundary) is fully proven in
`commonTest` with a `FakeSqliteRawReader`; only the real byte-level
`SQLiteDatabase.openDatabase`/temp-file lifecycle is unverified here.

## New this milestone: `PickedFileImportSource`

Real, pure `commonMain` bridge from the already-established
`platform.FilePicker` contract (`PickedFile(name, bytes)`, defined at
M2, unrelated to Import Center at the time) to `ImportSource`. Fully
tested from `commonTest` — `PickedFileImportSourceTest.kt` (2/2), no
Android dependency at all (`import-android-adapter-validation.md`
disclosure: `FilePicker.pickFile` itself returns the whole file already
buffered, so this class's own size check runs AFTER that buffering,
not before — see `PickedFileImportSource`'s own KDoc for the full real
disclosure).

## Real, disclosed scope NOT built this milestone

- **No real Android `FilePicker` implementation** (e.g. Storage Access
  Framework `ACTION_OPEN_DOCUMENT` + `ContentResolver`). Building it
  requires real `Activity`/`Intent` result-callback wiring — UI-adjacent
  work the checkpoint's own "do not begin full Compose Import screens"
  instruction places out of scope for M5.8. The `FilePicker` interface
  contract already exists (M2); a real implementation is deferred to
  the milestone that actually builds the Import screen.
- **iOS adapter contract — defined, not implemented** (per the
  checkpoint's own explicit "define but do not execute" instruction,
  and this session's standing rule never to claim iOS build/run
  evidence from this Windows host, no macOS/Xcode available): the real
  iOS analogue would be a `UIDocumentPickerViewController` (or
  `.fileImporter` in SwiftUI) producing a security-scoped `URL`, read
  via `NSFileCoordinator`/`Data(contentsOf:)` inside
  `startAccessingSecurityScopedResource()`/`stopAccessingSecurityScopedResource()`
  bracketing, then wrapped in the exact same `ImportSource` contract
  `PickedFileImportSource` already proves works for Android's
  `PickedFile` shape — no `commonMain` contract change is anticipated,
  since `ImportSource`/`ImportFileDescriptor` are already fully
  platform-neutral. Not written as Kotlin/Native code this milestone —
  a design description only, matching the standing "no iOS build claims
  from this host" rule.
