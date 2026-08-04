# Import Parser and Platform Decision (M5.8.2)

Real, evidence-based decision per format, made against this codebase's
actual constraints — not a generic survey.

## Real constraints checked before deciding

- `shared/build.gradle.kts`: no Robolectric dependency anywhere
  (grepped, zero matches) — `androidUnitTest` runs on the plain JVM with
  **no Android framework classes available**. Any code touching
  `android.content.Context`, `android.database.sqlite.SQLiteDatabase`,
  or similar real Android framework APIs cannot be exercised by a unit
  test on this host; only real-device/emulator instrumented tests could
  (and M5.7.12 already established none exist on this host).
- `androidMain`'s own real dependency set (`shared/build.gradle.kts`):
  `app.cash.sqldelight:android-driver` only — no `sqlite-driver` (JDBC).
  Android's runtime DOES include `java.util.zip` natively (it is part of
  the Android platform's own core libraries, not a JVM-desktop-only
  addition) — safe to use directly in `androidMain`.
- `kotlinx-serialization-json:1.7.3` is already a real `commonMain`
  dependency (`shared/build.gradle.kts` line 41) — a genuinely shared
  KMP JSON parser already exists in this project, no new dependency
  needed for JSON.
- No XLSX/ZIP/SQLite-reading library of any kind is a dependency yet
  anywhere in this module.

## Per-format decision

### CSV — Option A, genuinely shared KMP

Real, hand-written character-level parser in `commonMain`. CSV is plain
text with no binary container format — no platform library is needed at
all, and hand-rolling it in pure Kotlin gives full control over every
security requirement (M5.8.5: BOM, quoted delimiters, embedded
newlines, field-length bounds, null-byte rejection) without depending on
any third-party parser's own security posture.

### JSON — Option A, genuinely shared KMP

`kotlinx.serialization.json` (already a real dependency) parses into a
`JsonElement` tree in `commonMain` — genuinely shared, well-maintained,
actively used by JetBrains. Depth/size/shape limits (M5.8.6) are
enforced by walking the resulting `JsonElement` tree with explicit
bounds, not by the parser itself (which has no built-in depth limit) —
this is the real reason `ImportLimits.MAX_JSON_DEPTH` must be enforced
in this codebase's own decoder logic, not assumed the library handles it.

### XLSX — Option B, platform-specific decoder (`androidMain`)

**Real reasoning**: XLSX is a ZIP container of XML parts. No mature,
actively-maintained pure-KMP (commonMain, Kotlin/Native-compatible) ZIP+
XML+XLSX-semantics library exists as a dependency in this project today,
and adding one now — sight-unseen, for a security-critical decoder — is
a real risk this milestone does not have grounds to accept without
evaluation the checkpoint does not ask for. Android's own runtime already
includes `java.util.zip` (`ZipInputStream`, real, part of the platform,
not a "JVM-only parser" in the sense the checkpoint warns against — it
is available on every real Android device) plus a lightweight
hand-written XML-parts reader is sufficient for the narrow slice of
XLSX actually needed here (shared-strings table + one worksheet's cell
values — not full spreadsheet fidelity).

**Real, disclosed limitation**: the byte-level XLSX decoding logic lives
in `androidMain` and therefore **cannot be exercised by a unit test on
this host** (no Robolectric, no device/emulator, `java.util.zip` itself
is real and available on the JVM this host runs tests on, but the
`android.content.Context`-free parts CAN actually be tested — see
`import-xlsx-security.md` for the real, achieved split between what is
and is not testable here).

**iOS**: contract-only this milestone. A real iOS XLSX decoder (likely
via `Foundation`'s `NSFileManager`/`libzip` or a Kotlin/Native ZIP
binding) is explicitly left to a future milestone with real macOS/Xcode
access, per the checkpoint's own "leave execution to macOS" instruction.

### SQLite — Option B, platform-specific decoder (`androidMain`)

**Real reasoning**: reading an arbitrary, untrusted, user-supplied
SQLite file safely (read-only, no trigger/view/virtual-table execution)
is a real Android-native concern.
`android.database.sqlite.SQLiteDatabase.openDatabase(path, null,
SQLiteDatabase.OPEN_READONLY)` is the correct, real, platform-provided
API for exactly this — opening a foreign SQLite file read-only, with the
platform's own hardened SQLite build, not a JDBC/desktop driver
smuggled into production Android code (which M5.7.12 already
established is explicitly kept OUT of `androidMain`'s real dependency
graph, and this milestone does not reverse that).

**Real, disclosed limitation**: this decoder cannot be exercised by a
unit test on this host either (real `android.database.sqlite` class,
unavailable without Robolectric or a device). The allowlist-checking,
row/table-count-limit, and `NormalizedTable`-shape logic ABOVE the raw
`SQLiteDatabase` calls is written against a small `SqliteRawReader`
interface (`commonMain`) specifically so that layer CAN be unit-tested
with a real, hand-written fake implementation of that interface,
matching real header/table/column shapes — this is the same "prove the
contract, disclose the untested platform edge" discipline
`reporting-android-driver-validation.md` already established for M5.7.

**iOS**: contract-only this milestone, same disclosure as XLSX.

## The shared contract every decoder must produce

Regardless of which format or which platform decodes it, every decoder
implementation returns the same `commonMain`-declared `NormalizedTable`
(headers + rows of plain strings, M5.8.1) — business rules (entity
detection, mapping, validation, duplicate policy, dry-run, commit)
consume only `NormalizedTable`, never a format-specific or
platform-specific type. This is the real mechanism that keeps business
logic platform-independent even though CSV/JSON decode in `commonMain`
and XLSX/SQLite decode in `androidMain` — verified by the `ImportDecoder`
interface (`commonMain`) accepting only `ImportSource`/`ImportLimits`
and returning `NormalizedTable`, with no format-specific type ever
crossing that boundary.
