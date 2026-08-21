"""
Aura Retail -- STRUCTURAL guard on the schema-v13 attribution stamp.

Schema v13 (database/schema.py::_migrate_add_identity_and_attribution_columns)
added two groups of columns that only ever get a value if the code that writes
the row puts one there:

  * `uid` on RETAIL_UID_TABLES -- the WIRE identity a peer device or Owner's
    relay names the row by.
  * `actor_user_uid` / `terminal_id` / `created_at_utc` on RETAIL_ACTOR_TABLES
    -- WHO rang it, on WHICH terminal, at WHAT real instant.

The failure mode these columns have is the nastiest kind: an INSERT that omits
them succeeds. No exception, no constraint violation, `PRAGMA integrity_check`
still says 'ok', the till prints the receipt, the dashboard totals are right.
The damage only surfaces months later, when somebody asks who processed a
refund and the answer is a column full of NULLs that cannot be reconstructed
from anything -- this device cannot prove, after the fact, who was standing at
it. That is precisely why v13 left history NULL rather than guessing.

So a behavioural test alone is not enough. `retail_attribution_stamp_test.py`
proves the writers that exist TODAY stamp correctly; this file proves that a
writer added TOMORROW cannot quietly skip it. It reads the source of every
module under products/retail/backend/, finds every INSERT statement, and holds
each one against the schema constants themselves.

WHY THIS IS STRUCTURAL AND NOT A CHECKLIST -- both halves are discovered, not
typed out here:

  * WHICH TABLES need which columns comes from importing
    schema.RETAIL_UID_TABLES / schema.RETAIL_ACTOR_TABLES. When a future
    migration adds a table to either tuple, every write site for that table
    starts failing this test on the same commit, with no edit here.
  * WHICH WRITE SITES exist comes from scanning the source tree. A brand-new
    route, helper or import handler is picked up the moment it is written.

Nobody has to remember to add anything. The one thing that IS enumerated --
_UNSTAMPED_BY_DESIGN -- is enumerated in the safe direction: forgetting to
maintain it makes this file go RED, never green.

Deliberately a pure source scan: no Flask app, no database, no temp dir. It
therefore also runs (and fails) on a machine where the app cannot start at
all, which is exactly when a structural guard is most useful.

Run:
    pytest products/retail/tests/retail_attribution_stamp_structural_test.py -v
"""
import re
import sys
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Imported, never re-typed. These same two tuples drive the migration itself
# (schema.py::_migrate_add_identity_and_attribution_columns), so this test and
# the DDL can never disagree about which table carries which column -- and a
# v15 that appends a table to either one automatically extends this guard.
from database.schema import RETAIL_ACTOR_TABLES, RETAIL_UID_TABLES  # noqa: E402

#: table -> the set of v13 columns any INSERT into it must supply.
REQUIRED_COLUMNS = {}
for _t in RETAIL_UID_TABLES:
    REQUIRED_COLUMNS.setdefault(_t, set()).add('uid')
for _t in RETAIL_ACTOR_TABLES:
    REQUIRED_COLUMNS.setdefault(_t, set()).update(
        {'actor_user_uid', 'terminal_id', 'created_at_utc'}
    )

#: Write sites that are knowingly NOT stamped, as {(relative_path, table): why}.
#:
#: This exists so the scan can cover the WHOLE backend tree -- including files
#: this phase does not own -- without either failing on someone else's code or
#: silently narrowing itself to the two files that were easy. An entry here is
#: a recorded decision with a reason, not a suppression: delete the write site
#: and the entry goes stale, add a new unstamped one and the test fails until
#: somebody writes down why.
_UNSTAMPED_BY_DESIGN = {
    ('database/schema.py', 'branches'):
        "_seed_retail() demo fixture -- see below",
    ('database/schema.py', 'sales'):
        "_seed_retail() demo fixture -- see below",
    ('database/schema.py', 'sale_items'):
        "_seed_retail() demo fixture -- see below",
    ('database/schema.py', 'inventory_movements'):
        "_seed_retail() demo fixture -- see below",
    # All four: database/schema.py::_seed_retail is the DEMO seeder (dev
    # first-boot, and api/retail_api.py::demo_seed, which is itself gated on
    # AURA_RETAIL_DEMO_MODE + company admin + a typed confirmation). Its rows
    # are invented history for a shop that does not exist, with invented
    # `cashier` names and back-dated `created_at` values -- there is no real
    # actor, no real terminal and no real instant to stamp, and inventing an
    # actor_user_uid for a fictional cashier is the same category of mistake
    # v13 refused to make when it left real history NULL rather than guessing.
    # The `uid` gap is a genuine (small) one: demo rows cannot travel over
    # sync. Left as-is because schema.py belongs to another agent this phase --
    # REPORTED rather than edited.
}

#: Matches `INSERT [OR ...] INTO <table> (<column list>)`.
#: DOTALL because several of these statements wrap the column list onto its
#: own line (api/import_api.py's inventory_movements write is written that way).
_INSERT_WITH_COLUMNS = re.compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+[\"'`\[]?(?P<table>[A-Za-z_][A-Za-z_0-9]*)[\"'`\]]?\s*\(",
    re.IGNORECASE,
)

#: Matches `INSERT [OR ...] INTO <table>` regardless of what follows, so the
#: scan can PROVE it did not miss a column-list-less form (`INSERT INTO t
#: SELECT ...`, or `INSERT INTO t VALUES (...)` relying on column order).
#: Either of those would sail straight past _INSERT_WITH_COLUMNS and take the
#: stamp with it, so they are refused outright rather than merely unmatched.
_INSERT_ANY = re.compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+[\"'`\[]?(?P<table>[A-Za-z_][A-Za-z_0-9]*)[\"'`\]]?",
    re.IGNORECASE,
)


def _match_paren(text, open_index):
    """Index just past the ')' matching the '(' at `open_index`.

    Quote-aware, not a naive counter: a column list is parenthesis-free but a
    VALUES list is not necessarily string-literal-free (`'completed'`,
    `'sale_out'` appear inline in these statements), and a ')' inside a quoted
    literal must not close the group. Returns None on an unbalanced run rather
    than guessing -- the caller turns that into a loud failure.
    """
    depth = 0
    quote = None
    i = open_index
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _split_top_level(text):
    """Split a comma-separated SQL fragment at depth 0, quote-aware."""
    parts, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == '(':
            depth += 1
            buf.append(ch)
        elif ch == ')':
            depth -= 1
            buf.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append(''.join(buf))
    return [p.strip() for p in parts if p.strip()]


class Insert:
    """One parsed INSERT site: where it is, what table, which columns."""

    def __init__(self, path, line, table, columns, values):
        self.path = path
        self.line = line
        self.table = table
        self.columns = columns
        self.values = values

    def __repr__(self):
        return f"{self.path}:{self.line} INSERT INTO {self.table}"


def _backend_sources():
    """Every .py module under products/retail/backend/, path-sorted so a
    failure message lists sites in a stable order."""
    return sorted(BACKEND_DIR.rglob('*.py'))


def _scan(path):
    """Parse every column-listed INSERT in one file."""
    src = path.read_text(encoding='utf-8')
    rel = path.relative_to(BACKEND_DIR).as_posix()
    found = []
    for m in _INSERT_WITH_COLUMNS.finditer(src):
        open_paren = src.index('(', m.end() - 1)
        close = _match_paren(src, open_paren)
        assert close is not None, f"unbalanced column list at {rel}:{src.count(chr(10), 0, m.start()) + 1}"
        columns = [c.strip().strip('"').strip("'") for c in
                   _split_top_level(src[open_paren + 1:close - 1])]

        values = None
        tail = src[close:close + 4000]
        vm = re.search(r"\bVALUES\s*\(", tail, re.IGNORECASE)
        if vm:
            v_open = close + tail.index('(', vm.end() - 1)
            v_close = _match_paren(src, v_open)
            if v_close is not None:
                values = _split_top_level(src[v_open + 1:v_close - 1])

        found.append(Insert(
            path=rel,
            line=src.count('\n', 0, m.start()) + 1,
            table=m.group('table'),
            columns=columns,
            values=values,
        ))
    return found


ALL_INSERTS = [ins for p in _backend_sources() for ins in _scan(p)]
STAMPED_INSERTS = [i for i in ALL_INSERTS if i.table in REQUIRED_COLUMNS]


def test_the_scanner_actually_found_the_known_write_sites():
    """A guard on the guard.

    Every assertion below is of the form "no INSERT violates X". If the regex
    silently stopped matching -- a refactor to an f-string, a query builder, a
    renamed directory -- that whole family of tests would pass over an empty
    list and report green while checking nothing. A structural test that can
    be defeated by matching zero things is worse than no test, because it
    reports safety it is not providing.

    The floor is deliberately generous and stated as a minimum, not an exact
    count: this file must not need editing every time somebody adds a route.
    """
    assert len(ALL_INSERTS) > 30, (
        f"the INSERT scanner found only {len(ALL_INSERTS)} statements across "
        f"{len(_backend_sources())} backend modules -- it has almost certainly "
        f"stopped matching, and every other test in this file is now vacuous"
    )
    tables = {i.table for i in STAMPED_INSERTS}
    for expected in ('sales', 'sale_items', 'returns', 'return_items',
                     'inventory_movements', 'cash_sessions', 'cash_movements',
                     'branches', 'payments'):
        assert expected in tables, (
            f"no INSERT into `{expected}` was found anywhere under "
            f"products/retail/backend/. Either the scanner is broken or the "
            f"writer moved -- both need looking at before this file is trusted"
        )


def test_no_insert_into_a_stamped_table_uses_a_column_less_form():
    """`INSERT INTO sales VALUES (...)` and `INSERT INTO sales SELECT ...` are
    refused outright.

    Not a style objection. Both forms bind values by COLUMN ORDER, and v13
    appended its columns to the end of tables that a real shop's history
    already lives in. A positional INSERT written against today's column order
    is silently wrong the next time any migration adds a column, and it also
    slips past the column-list scan below -- so the stamp check would report
    green on a statement it never actually read.
    """
    offenders = []
    for path in _backend_sources():
        src = path.read_text(encoding='utf-8')
        rel = path.relative_to(BACKEND_DIR).as_posix()
        for m in _INSERT_ANY.finditer(src):
            if m.group('table') not in REQUIRED_COLUMNS:
                continue
            rest = src[m.end():m.end() + 200].lstrip()
            if not rest.startswith('('):
                offenders.append(f"{rel}:{src.count(chr(10), 0, m.start()) + 1} "
                                 f"INSERT INTO {m.group('table')} {rest[:40]!r}")
    assert not offenders, (
        "these INSERTs into v13-stamped tables do not name their columns:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize('ins', STAMPED_INSERTS, ids=repr)
def test_every_insert_into_a_stamped_table_supplies_the_v13_stamp(ins):
    """THE test this file exists for.

    One case per write site, generated from the scan, so a new INSERT into
    sales / returns / inventory_movements / cash_sessions / cash_movements --
    or into any uid-bearing table -- shows up as its own named failure the
    moment it is written, naming the exact file:line and the exact columns it
    forgot.
    """
    required = REQUIRED_COLUMNS[ins.table]
    key = (ins.path, ins.table)
    present = {c.lower() for c in ins.columns}
    missing = sorted(c for c in required if c not in present)

    if key in _UNSTAMPED_BY_DESIGN:
        # Deliberately asserted, not skipped. If somebody stamps this site
        # properly the entry becomes a lie, and a stale exemption is how a
        # guard rots into decoration.
        assert missing, (
            f"{ins} is listed in _UNSTAMPED_BY_DESIGN "
            f"({_UNSTAMPED_BY_DESIGN[key]}) but now supplies the full v13 "
            f"stamp. Delete the exemption."
        )
        return

    assert not missing, (
        f"{ins} does not supply {missing}.\n"
        f"Schema v13 added these columns to `{ins.table}`; an INSERT that "
        f"omits them succeeds and leaves them NULL forever -- there is no "
        f"constraint, no error, and no way to reconstruct who/where/when "
        f"after the fact. Stamp them at the write site, or add "
        f"('{ins.path}', '{ins.table}') to _UNSTAMPED_BY_DESIGN with a "
        f"written reason."
    )


@pytest.mark.parametrize('ins', ALL_INSERTS, ids=repr)
def test_every_insert_binds_exactly_as_many_values_as_it_names_columns(ins):
    """Column count == VALUES count, for EVERY insert in the backend.

    Stamping v13 meant hand-extending sixteen existing INSERT statements, each
    with a column list and a positional VALUES list that have to stay in
    lockstep. Adding `uid` to one and forgetting its `?` in the other is the
    single most likely mistake in that edit, and SQLite's error for it
    ("N values for M columns") only fires when that code path actually
    executes -- so a write site with no test behind it stays broken until a
    real shop hits it.

    Scoped to ALL inserts, not just stamped ones: the check costs nothing and
    the same mistake is possible anywhere.
    """
    if ins.values is None:
        pytest.skip('no literal VALUES list (INSERT ... SELECT, or built dynamically)')
    assert len(ins.columns) == len(ins.values), (
        f"{ins} names {len(ins.columns)} columns but binds "
        f"{len(ins.values)} values.\n"
        f"  columns: {ins.columns}\n"
        f"  values : {ins.values}"
    )


def test_the_utc_stamp_is_never_written_from_local_wall_clock():
    """`created_at_utc` must come from an explicitly UTC-aware clock.

    This is the one column whose bug is invisible on the machine that writes
    it. `datetime.now().strftime('%Y-%m-%d %H:%M:%S')` is what `create_sale`
    and `create_return` deliberately write into `created_at` (local, so the
    dashboard buckets by local date -- there is a comment at each saying so),
    and it produces a string that looks exactly like a timestamp. Pointed at
    `created_at_utc` it would be wrong by this machine's offset, in a column
    whose entire purpose is to be the value two devices in two timezones can
    compare.

    Source-level because it has to hold on a UTC+0 CI box too, where the
    behavioural version of this assertion is vacuously true.

    Checks the FILES THIS PHASE OWNS: elsewhere in the backend
    `datetime.now()` is legitimate (`_now()`, `create_sale`'s `now_local`) and
    nothing else writes created_at_utc.
    """
    owned = [BACKEND_DIR / 'api' / 'retail_api.py', BACKEND_DIR / 'api' / 'import_api.py']
    for path in owned:
        src = path.read_text(encoding='utf-8')
        # Every place the column is handed a value is a helper call, never an
        # inline expression, so the whole surface is the helper's one return.
        for m in re.finditer(r"created_at_utc", src):
            line_no = src.count('\n', 0, m.start()) + 1
            line = src.splitlines()[line_no - 1]
            assert 'datetime.now()' not in line, (
                f"{path.name}:{line_no} appears to feed created_at_utc from a "
                f"naive local clock: {line.strip()!r}"
            )
        assert 'now_utc_iso' in src, (
            f"{path.name} writes v13 stamps but never references now_utc_iso -- "
            f"the one aware-UTC timestamp helper this suite writes *_at_utc "
            f"columns with (commercial_runtime/identity/user_accounts.py)"
        )
