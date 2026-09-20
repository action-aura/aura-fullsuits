"""The retail and registry `sync_cursor`/`sync_outbox` tables must not drift.

WHY THIS FILE EXISTS
`commercial_runtime/identity/registry_sync_schema.py` states in its own module
docstring that its tables mirror retail's "verbatim -- same columns, same
defaults, same `PRIMARY KEY CHECK (id = 1)` shape". That was true when it was
written. Nothing asserted it afterwards.

Retail schema **v31** added `sync_cursor.relay_url` to retail.db. The registry
copy did not follow, and the result was not theoretical:

    GET /api/registry-sync/_internal/cursor
    sqlite3.OperationalError: no such column: relay_url   -> HTTP 500

Found in logcat on a real phone 2026-09-20, then reproduced against a fresh
database. `products/retail/backend/app.py` registers
`make_sync_internal_blueprint` TWICE -- once at `/api/sync` on the retail
connection, once at `/api/registry-sync` on the registry one. ONE route
implementation, TWO schemas: the moment they diverge, the second breaks.

The 500 was the loud half. The quiet half is worse, and
retail_sync_cursor_relay_binding_test.py spells it out: without relay
binding, a cursor left from a relay the device no longer talks to asks the
new relay for events past a sequence number meaningless there, and the device
"silently stops receiving data forever, with no error anywhere". The registry
stream is the one carrying `users`/`user_permissions` -- whether a cashier
created on one device can sign in on another.

Registry schema **v8** closed it. This file stops it reopening.

WHY IT READS SOURCE RATHER THAN BUILDING TWO DATABASES
Building a real retail.db pulls in the whole product (env, migration chain,
dozens of tables) for a question about two of them, while registry's needs a
single function call -- so a live comparison would be lopsided AND slow. What
matters is the declared column set on each side, and both sides declare it in
exactly two shapes: a `CREATE TABLE IF NOT EXISTS`, and guarded
`ALTER TABLE ... ADD COLUMN` steps. Both are extracted below.

STATED PLAINLY: this compares DECLARATIONS, not two live databases. It cannot
catch a migration that is written but never wired into a version chain. What
it does catch is the drift that actually happened -- a column added on one
side and not the other -- and it catches it at the source, before anything
ships.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RETAIL_SCHEMA = ROOT / "products/retail/backend/database/schema.py"
REGISTRY_SCHEMA = ROOT / "commercial_runtime/identity/registry_sync_schema.py"

SHARED_TABLES = ("sync_cursor", "sync_outbox")


def _strip_py_comments(src: str) -> str:
    """Blank out `#` comments and triple-quoted blocks, preserving newlines.

    Both files discuss these tables at length in prose -- this very
    repository's habit -- and the registry docstring now quotes the exact
    `ALTER TABLE sync_cursor ADD COLUMN relay_url TEXT` it performs. Scanning
    raw text would read the documentation as a declaration, the same trap
    that has already bitten two other guards in this suite.
    """
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))

    # ONLY the leading module docstring, plus `#` comments.
    #
    # Blanking EVERY triple-quoted block deletes the schema this test exists
    # to read: both files keep their real SQL inside triple-quoted strings
    # handed to execute()/executescript(). The first version of this file did
    # that and reported "no CREATE TABLE for sync_cursor found" -- a scanner
    # that had thrown away its own subject.
    #
    # The module docstring is where the prose lives, and it matters here
    # because this repo documents a defect beside its fix:
    # registry_sync_schema.py's docstring now quotes the very
    # `ALTER TABLE sync_cursor ADD COLUMN relay_url TEXT` it performs, which
    # would otherwise be read as a second declaration.
    src = re.sub(r'\A\s*(?:[rubRUB]{0,2})"""[\s\S]*?"""', blank, src)
    src = re.sub(r"\A\s*(?:[rubRUB]{0,2})'''[\s\S]*?'''", blank, src)
    src = re.sub(r"#[^\n]*", blank, src)
    return src


def _declared_columns(path: Path, table: str) -> set:
    """Every column the file declares for `table`: those in its
    CREATE TABLE, plus any added by an ALTER TABLE ... ADD COLUMN."""
    src = _strip_py_comments(path.read_text(encoding="utf-8"))

    head = re.search(
        rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{table}\s*\(",
        src, re.I)
    assert head, f"{path.name}: no CREATE TABLE for {table} found"

    # BALANCED SCAN, not a non-greedy regex. `(.*?)\)` stops at the FIRST
    # closing paren, which inside
    #     id INTEGER PRIMARY KEY CHECK (id = 1),
    #     last_seq INTEGER NOT NULL DEFAULT 0
    # is the one closing CHECK -- so the body was truncated after `id` and
    # `last_seq` silently vanished. Both files truncated identically, so the
    # equality assertion below PASSED while comparing two wrong answers.
    # That is this suite's recurring failure shape (the pass condition being
    # satisfied by the bug), caught here only by printing what was extracted
    # instead of trusting a green run.
    body_start = head.end()
    depth = 1
    i = body_start
    while i < len(src) and depth:
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
        i += 1
    assert depth == 0, f"{path.name}: unbalanced parentheses in {table}'s CREATE TABLE"
    body = src[body_start:i - 1]

    cols = set()
    depth = 0
    field = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.add(field)
            field = ""
        else:
            field += ch
    cols.add(field)

    names = set()
    for raw in cols:
        raw = raw.strip()
        if not raw:
            continue
        first = raw.split()[0]
        # Skip table-level constraints, which are not columns.
        if first.upper() in {"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"}:
            continue
        names.add(first)

    for m in re.finditer(
            rf"ALTER\s+TABLE\s+{table}\s+ADD\s+COLUMN\s+(\w+)", src, re.I):
        names.add(m.group(1))

    return names


@pytest.mark.parametrize("table", SHARED_TABLES)
def test_retail_and_registry_declare_the_same_columns(table):
    retail = _declared_columns(RETAIL_SCHEMA, table)
    registry = _declared_columns(REGISTRY_SCHEMA, table)

    # ANTI-VACUITY, and it is not decoration: an earlier version of the
    # extractor truncated both CREATE TABLE bodies at the `)` closing
    # `CHECK (id = 1)`, losing `last_seq` on BOTH sides -- so the equality
    # assertion passed while comparing two wrong answers. Two equally broken
    # readings are equal. Naming the base columns each table must contain is
    # what makes that impossible to repeat; a count alone would not have
    # caught it, because the truncated sets still had two entries.
    BASE = {
        "sync_cursor": {"id", "last_seq"},
        "sync_outbox": {"id", "entity_type", "entity_id", "event_type", "payload"},
    }[table]
    for label, got in (("retail", retail), ("registry", registry)):
        missing = BASE - got
        assert not missing, (
            f"extractor lost {sorted(missing)} from {label}'s {table} "
            f"(got {sorted(got)}). It is misreading the schema, so any "
            "comparison below would be between two wrong answers.")

    assert retail == registry, (
        f"{table} has drifted between the two databases.\n"
        f"  retail   : {sorted(retail)}\n"
        f"  registry : {sorted(registry)}\n"
        f"  only in retail   : {sorted(retail - registry)}\n"
        f"  only in registry : {sorted(registry - retail)}\n\n"
        "products/retail/backend/app.py registers make_sync_internal_blueprint "
        "TWICE -- /api/sync on the retail connection and /api/registry-sync on "
        "the registry one -- so one route implementation runs against both of "
        "these schemas. When they differ, the shared SQL raises "
        "'no such column' against whichever database is missing it, which is "
        "exactly how /api/registry-sync/_internal/cursor came to return HTTP "
        "500 on every install. Add the column to BOTH, each behind its own "
        "version bump."
    )


def test_relay_url_is_present_on_both_sides():
    """The specific column whose absence shipped the defect. The parity test
    above already fails if it is missing from one side, but it fails with a
    set diff; this names it, so the failure says what broke rather than only
    that something did."""
    for label, path in (("retail", RETAIL_SCHEMA), ("registry", REGISTRY_SCHEMA)):
        cols = _declared_columns(path, "sync_cursor")
        assert "relay_url" in cols, (
            f"{label}'s sync_cursor has no relay_url (columns: {sorted(cols)}). "
            "Without it, SyncService.ensure_cursor_matches_relay raises against "
            "this database, and the stream has no protection against a cursor "
            "left over from a different relay -- which does not error, it "
            "silently stops delivering data forever."
        )
