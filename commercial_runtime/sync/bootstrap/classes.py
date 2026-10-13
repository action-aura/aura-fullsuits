"""Table classes: what a bootstrap copy does with every table of a product database.

A bootstrap copy is a copy of a LIVE shop database handed to a different
computer. Most of that database is the shop's business (products, sales,
ledger) and should arrive whole. Some of it is not the copy's to give:

  * who may sign in, what they may do, which computer is main, which till number
    a computer owns -- AUTHORITY. Copying it from a peer would let any peer hand
    a joining computer a staff list the main computer never signed
    (AUDIT-033/S-AUTH). It is dropped from the copy and comes back as signed
    events from the main computer.
  * this computer's own counters, queues, credentials and relay bookkeeping --
    LOCAL_CARRY. The PRODUCER's values must not arrive (they are the producer's:
    its next invoice number, its e-invoice credentials, its hub's site log), and
    the REQUESTER's must not be lost (its own numbering must not restart).
  * the producer's local logs and the outbox -- LOCAL_DROP. Not the requester's.

Every table is exactly ONE class; a table that is none is a table nobody decided
about, and the producer refuses to ship it (`UNCLASSIFIED_TABLE`) rather than
guess, because a wrong guess either leaks a producer's secrets or silently
drops shop data. The product's own test pins the same rule over a freshly
migrated database, so a new table cannot merge without a class.

The vocabulary and the data structures live here (shared, no product imports).
The MAP from table name to class is the product's:
`products/retail/backend/database/bootstrap_classes.py`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Mapping, Optional

SYNCED = "SYNCED"                # shop business data carried by sync events: copy as is
DERIVED = "DERIVED"              # computed from SYNCED rows (balances): copy as is
AUTHORITY = "AUTHORITY"          # signed by the main computer: dropped, re-announced signed
LOCAL_CARRY = "LOCAL_CARRY"      # this computer's own: producer's rows dropped, requester's kept
LOCAL_DROP = "LOCAL_DROP"        # producer-local logs/queues: dropped, requester's NOT carried
BLOCK_IF_ROWS = "BLOCK_IF_ROWS"  # like LOCAL_CARRY, and install is refused while the requester has an open row
ROW_LEVEL = "ROW_LEVEL"          # rows differ in class (retail_settings, by key prefix): see RowRule

TABLE_CLASSES = (SYNCED, DERIVED, AUTHORITY, LOCAL_CARRY, LOCAL_DROP, BLOCK_IF_ROWS, ROW_LEVEL)
ROW_CLASSES = (SYNCED, DERIVED, AUTHORITY, LOCAL_CARRY, LOCAL_DROP)

#: Classes whose rows are kept in the copy as the producer has them.
KEEP_IN_COPY = frozenset({SYNCED, DERIVED})
#: Classes whose rows come from the REQUESTER's old database at install.
CARRIED_FROM_REQUESTER = frozenset({LOCAL_CARRY, BLOCK_IF_ROWS})


@dataclass(frozen=True)
class RowRule:
    """Row-level classes for one table whose rows are not all alike.

    `prefix_classes` is checked in order against the `key_column` value; the
    first prefix that matches decides, otherwise `default_class`."""
    key_column: str
    prefix_classes: tuple          # ((prefix, class), ...)
    default_class: str

    def class_of_key(self, key: Optional[str]) -> str:
        text = "" if key is None else str(key)
        for prefix, cls in self.prefix_classes:
            if text.startswith(prefix):
                return cls
        return self.default_class

    def __post_init__(self):
        for _, cls in self.prefix_classes:
            if cls not in ROW_CLASSES:
                raise ValueError(f"row class {cls!r} is not one of {ROW_CLASSES}")
        if self.default_class not in ROW_CLASSES:
            raise ValueError(f"default row class {self.default_class!r} is not one of {ROW_CLASSES}")


@dataclass(frozen=True)
class BootstrapSpec:
    """Everything the shared producer/installer need to know about one product's
    database, supplied by the product."""
    product: str                                  # 'retail'
    db_name: str                                  # 'retail.db'
    table_classes: Mapping[str, str]
    schema_version: int                           # the version THIS build speaks
    row_rules: Mapping[str, RowRule] = field(default_factory=dict)
    #: BLOCK_IF_ROWS table -> SQL predicate that is true for a row that must
    #: block an install (an open cash session: the till's money is mid-count).
    block_where: Mapping[str, str] = field(default_factory=dict)
    cursor_table: str = "sync_cursor"
    outbox_table: str = "sync_outbox"

    def class_of(self, table: str) -> Optional[str]:
        return self.table_classes.get(table)

    def problems(self, conn: sqlite3.Connection) -> list:
        """Human-readable problems with this spec against a real database:
        tables with no class, classes for tables that do not exist is NOT a
        problem (a class map may name tables a given install never created),
        and structurally invalid entries. Empty list = every table decided."""
        out = []
        live = user_tables(conn)
        for table in live:
            cls = self.table_classes.get(table)
            if cls is None:
                out.append(f"{table}: no class")
        for table, cls in self.table_classes.items():
            if cls not in TABLE_CLASSES:
                out.append(f"{table}: unknown class {cls!r}")
            if cls == ROW_LEVEL and table not in self.row_rules:
                out.append(f"{table}: ROW_LEVEL but no RowRule")
            if cls != ROW_LEVEL and table in self.row_rules:
                out.append(f"{table}: has a RowRule but class is {cls}")
            if cls == BLOCK_IF_ROWS and table not in self.block_where:
                out.append(f"{table}: BLOCK_IF_ROWS but no block_where predicate")
        for table in self.block_where:
            if self.table_classes.get(table) != BLOCK_IF_ROWS:
                out.append(f"{table}: has a block_where but is not BLOCK_IF_ROWS")
        return out


def user_tables(conn: sqlite3.Connection) -> list:
    """Every ordinary table except SQLite's own bookkeeping, sorted."""
    return sorted(
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall())


def table_columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list:
    return [r[1] for r in conn.execute(f'PRAGMA {schema}.table_info("{table}")').fetchall()]
