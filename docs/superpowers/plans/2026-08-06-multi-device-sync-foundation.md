# Multi-Device Sync Foundation + Category Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove a production-shaped multi-device data sync mechanism end to
end on Category: offline writes always succeed locally, push to a central
relay (built into Owner) the instant connectivity returns, other devices on
the same license pull and apply them automatically — tested locally over a
USB cable (desktop ↔ Android via `adb reverse`), architected so real cloud
deployment later is a config change, not a rewrite.

**Architecture:** Append-only event log (`owner_sync_events`) is the single
source of truth; `seq` (server-assigned) is the only ordering authority.
Each client (desktop Retail, mobile `aura-retail-unified`) queues local
writes in a `sync_outbox`, pushes immediately when online, pulls on a short
poll. Category IDs move from autoincrement integers to client-generated
UUIDs on both platforms. Auth replicates Owner's existing per-request
Ed25519 signature verification (`checkin.py`'s pattern) — no session object,
no new auth system; mobile gains real signing capability it never had.

**Tech Stack:** Owner: Flask + SQLAlchemy + Alembic (Postgres). Desktop
Retail: Flask + SQLite (`sqlite3` stdlib), `requests` for outbound HTTP.
Mobile: Kotlin Multiplatform, SQLDelight, new Ktor HTTP client dependency,
Tink for Ed25519 (matching the existing verifier's crypto library).

## Global Constraints

- License scoping (`license_id`) is **always** derived server-side from the
  verified device signature — never accepted as client input, on push or
  pull.
- Push is idempotent, keyed on the event's own client-generated `id` (UUID)
  — a retried request must never create a duplicate event with a new `seq`.
- `seq` is the only ordering authority. Client-supplied `created_at` is
  display/audit only, never used for conflict resolution.
- No hardcoded `127.0.0.1`/`localhost` in application logic on either
  client — the relay base URL is always a config value; local dev config
  supplies the loopback default, nothing else does.
- The wire-level `delete` event is a logical concept, not a literal storage
  instruction: desktop performs a real row delete, mobile performs
  `status = 'inactive'` (soft-archive) — each matches its own existing
  schema. Applying an incoming `delete` event must produce the same
  user-visible outcome on both platforms regardless of which local
  operation implements it.
- Every new local write (category create/update/delete on either platform)
  queues its outbox event in the **same local transaction** as the write
  itself — an event exists if and only if the write it describes actually
  committed.

---

### Task 1: Owner — `SyncEvent` model + migration

**Files:**
- Create: `owner/app/models/sync.py`
- Modify: `owner/app/models/__init__.py` (add the import so Alembic's
  autogenerate and `Base.metadata` see it — follow the exact pattern
  already used there for other models; open the file first to match
  existing import style)
- Create: `owner/migrations/versions/<generated>_sync_events_table.py`

**Interfaces:**
- Produces: `SyncEvent` SQLAlchemy model, table `owner_sync_events`, columns
  `id` (UUID PK, client-generated — NOT server `default=uuid.uuid4`, the
  caller supplies it), `seq` (server-assigned autoincrement bigint, unique,
  indexed), `license_id` (UUID, FK to `owner_licenses.id`), `device_id`
  (UUID, the installation id), `entity_type` (String, e.g. `'category'`),
  `entity_id` (UUID), `event_type` (String: `create`|`update`|`delete`),
  `payload` (JSONB, full row snapshot), `created_at` (client-supplied,
  display only), `received_at` (server `server_default=func.now()`,
  authoritative). Composite index on `(license_id, seq)`.
- Consumed by: Task 2's push/pull routes.

- [ ] **Step 1: Write the model**

```python
# owner/app/models/sync.py
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, BigInteger, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import Base
from app.models.base import TimestampMixin


class SyncEvent(Base, TimestampMixin):
    """Append-only log of business-data mutations shared across every
    device activated against the same license. `seq` (not `created_at`,
    which is client-supplied and untrusted for ordering) is the sole
    ordering authority every client cursors against."""

    __tablename__ = "owner_sync_events"

    # Client-generated (not server default) -- this IS the idempotency key
    # for POST /api/sync/v1/push: a retried push with the same id must be
    # a no-op, never a second row with a new seq.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, autoincrement=True, unique=True, nullable=False)
    license_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # create|update|delete
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    client_created_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        Index("ix_owner_sync_events_license_seq", "license_id", "seq"),
    )
```

Note: `TimestampMixin` already gives `created_at`/`updated_at` — here
`created_at` from the mixin becomes `received_at`'s job (server-stamped,
authoritative receipt time). Rename the mixin-provided column's usage
mentally as "received_at" in the plan's prose above; do not rename the
mixin itself. The client's own claimed timestamp is the separate
`client_created_at` field so the two are never confused.

- [ ] **Step 2: Register the model so Alembic sees it**

Open `owner/app/models/__init__.py`, find how other models (e.g. from
`licensing.py`) are imported, and add `from app.models.sync import SyncEvent`
following the exact same style (single-line import, alongside the others —
match whatever ordering/grouping convention the file already uses).

- [ ] **Step 3: Generate and clean up the migration**

```bash
cd owner
export OWNER_DATABASE_URL=<the dev database URL from your local .env>
python -m alembic revision --autogenerate -m "sync_events table"
```

Open the generated file in `owner/migrations/versions/`. Per this
codebase's convention (every existing migration is hand-adjusted after
autogenerate, not used verbatim), verify:
- `op.create_table("owner_sync_events", ...)` lists all columns above with
  correct types (`postgresql.UUID(as_uuid=True)`, `postgresql.JSONB`,
  `sa.BigInteger`, `sa.String`), FK/PK/unique constraints, and the
  composite index.
- `downgrade()` does `op.drop_index(...)` then `op.drop_table("owner_sync_events")`.

- [ ] **Step 4: Apply and verify**

```bash
python -m alembic upgrade head
```

Expected: no errors. Verify the table exists:
```bash
python -c "from app import create_app; from app.extensions import db; app = create_app(); app.app_context().push(); print(db.session.execute(db.text(\"SELECT to_regclass('owner_sync_events')\")).scalar())"
```
Expected: prints `owner_sync_events` (not `None`).

- [ ] **Step 5: Commit**

```bash
git add owner/app/models/sync.py owner/app/models/__init__.py owner/migrations/versions/
git commit -m "feat(owner): add SyncEvent model and owner_sync_events table"
```

---

### Task 2: Owner — sync push/pull routes

**Files:**
- Create: `owner/app/sync/__init__.py`
- Create: `owner/app/sync/routes.py`
- Modify: `owner/app/__init__.py` (register the new blueprint)

**Interfaces:**
- Consumes: `SyncEvent` (Task 1), `device_identity.verify_signature` and
  `Installation` model (existing, from `owner/app/licensing_service/`) —
  read `owner/app/licensing_service/checkin.py` in full before writing this
  task; the auth flow here must match it exactly (canonicalize body minus
  `signature`, look up `Installation` by `installation_id`, load its
  active device public key, verify, THEN resolve `license_id` via
  `installation.license`, reject with the same style of error/reason code
  the checkin flow uses on a bad signature).
- Produces: `POST /api/sync/v1/push`, `GET /api/sync/v1/pull` — consumed by
  Task 5 (desktop client) and Task 9 (mobile client).

- [ ] **Step 1: Write the blueprint**

```python
# owner/app/sync/__init__.py
from app.sync.routes import bp  # noqa: F401
```

```python
# owner/app/sync/routes.py
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.extensions import db
from app.models.sync import SyncEvent
from app.models.licensing import Installation  # confirm exact import path
                                                  # by reading checkin.py's
                                                  # own imports first
from app.licensing_service import device_identity
from app.licensing_service.canonicalize import canonicalize_bytes  # match
                                                  # checkin.py's actual
                                                  # import path exactly

bp = Blueprint("sync", __name__, url_prefix="/api/sync/v1")

_MAX_PUSH_BATCH = 200  # abuse-resistance bound per the spec's
                        # "reject unreasonably large batches" requirement


def _authenticate(body: dict):
    """Mirrors checkin.py's verify-then-resolve pattern exactly: look up
    the Installation, verify the Ed25519 signature over the canonicalized
    body, then resolve license_id via the relationship -- never from
    client input. Returns (installation, license_id) or raises a 401-style
    error the route handlers catch."""
    installation_id = body.get("installation_id")
    installation = db.session.get(Installation, installation_id) if installation_id else None
    if installation is None:
        raise PermissionError("UNKNOWN_INSTALLATION")
    public_key = device_identity.get_active_device_key(installation.id)
    if public_key is None:
        raise PermissionError("NO_ACTIVE_DEVICE_KEY")
    canonical_bytes = canonicalize_bytes({k: v for k, v in body.items() if k != "signature"})
    if not device_identity.verify_signature(public_key, canonical_bytes, body.get("signature", "")):
        raise PermissionError("INVALID_SIGNATURE")
    return installation, installation.license_id


@bp.route("/push", methods=["POST"])
def push():
    body = request.get_json(silent=True) or {}
    try:
        installation, license_id = _authenticate(body)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    events = body.get("events") or []
    if not isinstance(events, list) or len(events) > _MAX_PUSH_BATCH:
        return jsonify({"error": "INVALID_BATCH"}), 400

    stored = 0
    for ev in events:
        try:
            event_id = uuid.UUID(ev["id"])
        except (KeyError, ValueError, TypeError):
            return jsonify({"error": "INVALID_EVENT_ID"}), 400
        existing = db.session.get(SyncEvent, event_id)
        if existing is not None:
            continue  # idempotent no-op -- already stored, no new seq
        db.session.add(SyncEvent(
            id=event_id,
            license_id=license_id,
            device_id=installation.id,
            entity_type=ev["entity_type"],
            entity_id=uuid.UUID(ev["entity_id"]),
            event_type=ev["event_type"],
            payload=ev["payload"],
            client_created_at=datetime.fromisoformat(ev["created_at"]),
        ))
        stored += 1
    db.session.commit()
    return jsonify({"stored": stored, "received": len(events)})


@bp.route("/pull", methods=["GET"])
def pull():
    body = request.get_json(silent=True) or {}
    # NOTE: GET with a signed body is unusual but matches this
    # feature's need to authenticate the pull the same way as push --
    # if Owner's existing conventions elsewhere pass signed auth via a
    # header instead of a JSON GET body, follow that convention instead;
    # confirm against checkin.py / the external API's actual request
    # shape before finalizing this route.
    try:
        installation, license_id = _authenticate(body)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    since = int(request.args.get("since", "0"))
    rows = db.session.execute(
        select(SyncEvent)
        .where(SyncEvent.license_id == license_id)
        .where(SyncEvent.seq > since)
        .where(SyncEvent.device_id != installation.id)
        .order_by(SyncEvent.seq)
        .limit(500)
    ).scalars().all()

    return jsonify({
        "events": [{
            "id": str(r.id), "entity_type": r.entity_type, "entity_id": str(r.entity_id),
            "event_type": r.event_type, "payload": r.payload,
            "created_at": r.client_created_at.isoformat(), "seq": r.seq,
        } for r in rows],
        "cursor": rows[-1].seq if rows else since,
    })
```

- [ ] **Step 2: Register the blueprint**

Read `owner/app/__init__.py`'s existing `api_external` conditional
registration block (`EXTERNAL_API_ENABLED` gate + `csrf.exempt`) and add
the sync blueprint the same way — behind its own flag (e.g.
`SYNC_API_ENABLED`) or folded into the existing external-API flag if that
better matches this codebase's actual config surface; read the existing
block first and match its exact shape, including the `csrf.exempt(...)`
call.

- [ ] **Step 3: Verify blueprint registration**

```bash
cd owner
export OWNER_EXTERNAL_API_ENABLED=true   # or the sync-specific flag chosen above
flask --app app:create_app run --port 5551
```
In another terminal: `curl -s -X POST http://127.0.0.1:5551/api/sync/v1/push -H "Content-Type: application/json" -d '{}'` — expect a `401` with `UNKNOWN_INSTALLATION`, not a 404 or 500 (confirms routing + auth path both execute).

- [ ] **Step 4: Commit**

```bash
git add owner/app/sync/ owner/app/__init__.py
git commit -m "feat(owner): add sync push/pull routes, signed-request auth matching checkin.py"
```

---

### Task 3: Desktop Retail — schema (sync tables + Category UUID migration)

**Files:**
- Modify: `products/retail/backend/database/schema.py`

**Interfaces:**
- Produces: `sync_outbox` table (`id` UUID PK — the event id, `entity_type`,
  `entity_id`, `event_type`, `payload` JSON text, `created_at`), a
  single-row `sync_cursor` table (`id INTEGER PRIMARY KEY CHECK (id = 1)`,
  `last_seq INTEGER NOT NULL DEFAULT 0`) so there's always exactly one
  cursor row to `UPDATE`. Migrates `categories.id` from
  `INTEGER PRIMARY KEY AUTOINCREMENT` to `TEXT PRIMARY KEY` (UUID strings),
  updates `products.category_id`'s type/FK to match.
- Consumed by: Task 4 (routes write to `sync_outbox`), Task 5 (sync client
  reads/writes `sync_outbox`/`sync_cursor`).

- [ ] **Step 1: Add the new tables to the schema-creation SQL**

In `schema.py`, alongside the existing `CREATE TABLE IF NOT EXISTS categories` statement, add:

```sql
CREATE TABLE IF NOT EXISTS sync_outbox (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
```

- [ ] **Step 2: Write the Category-UUID migration and bump the schema version**

`RETAIL_SCHEMA_VERSION` is currently `1` (line 31) with a no-op
`migrate_fn` wired into `ensure_schema_version` (lines 292-296). Bump to
`2` and replace the no-op with:

```python
def _migrate_categories_to_uuid(conn):
    import uuid as _uuid
    rows = conn.execute("SELECT id FROM categories").fetchall()
    id_map = {row["id"]: str(_uuid.uuid4()) for row in rows}
    conn.execute("ALTER TABLE categories RENAME TO categories_old")
    conn.execute("""
        CREATE TABLE categories (
            id TEXT PRIMARY KEY,
            company_id INTEGER DEFAULT 1,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for old_id, new_id in id_map.items():
        row = conn.execute("SELECT * FROM categories_old WHERE id=?", (old_id,)).fetchone()
        conn.execute(
            "INSERT INTO categories (id, company_id, name, description, created_at) VALUES (?,?,?,?,?)",
            (new_id, row["company_id"], row["name"], row["description"], row["created_at"]),
        )
    conn.execute("DROP TABLE categories_old")

    conn.execute("ALTER TABLE products RENAME COLUMN category_id TO category_id_old")
    conn.execute("ALTER TABLE products ADD COLUMN category_id TEXT REFERENCES categories(id)")
    for old_id, new_id in id_map.items():
        conn.execute("UPDATE products SET category_id=? WHERE category_id_old=?", (new_id, old_id))
    conn.execute("ALTER TABLE products DROP COLUMN category_id_old")
```

Wire this into the `ensure_schema_version(...)` call's `migrate_fn`
argument in place of the current no-op `lambda c: None`, guarded so it only
runs when migrating from version 1 (read `ensure_schema_version`'s actual
signature first — confirm whether `migrate_fn` receives the *from* version
so this can be conditional, or whether a version check belongs inside
`_migrate_categories_to_uuid` itself before it does anything).

- [ ] **Step 3: Verify against a real pre-existing database**

```bash
cd products/retail/backend
cp -r "$LOCALAPPDATA/AuraRetail" /tmp/retail-backup-before-migration 2>/dev/null || true
```
Then, against an **isolated copy** of a database that has real pre-existing
category/product rows (never the real `%LOCALAPPDATA%\AuraRetail` directly
— copy it to a scratch location and point `AURA_APP_DATA` at the copy):
```bash
AURA_APP_DATA=/tmp/retail-migration-test ../../../.venv/Scripts/python.exe -c "
from database.schema import init_retail
init_retail()
"
```
Then inspect: `sqlite3 /tmp/retail-migration-test/retail.db "SELECT id, name FROM categories LIMIT 5"` — expect UUID-looking `id` values (36-char strings with dashes), not small integers. `SELECT category_id FROM products WHERE category_id IS NOT NULL LIMIT 5` — expect those to be the same UUID strings, correctly repointed.

- [ ] **Step 4: Commit**

```bash
git add products/retail/backend/database/schema.py
git commit -m "feat(retail): add sync_outbox/sync_cursor tables, migrate categories.id to UUID"
```

---

### Task 4: Desktop Retail — Category update/delete routes + outbox instrumentation

**Files:**
- Modify: `products/retail/backend/api/retail_api.py`

**Interfaces:**
- Consumes: `sync_outbox` table (Task 3).
- Produces: `PUT /categories/<id>`, `DELETE /categories/<id>` (new — did
  not exist before). All three category mutation routes (existing
  `create_category` plus the two new ones) now also write to
  `sync_outbox`.
- Consumed by: Task 5 (the outbox this task fills is what the push loop
  drains).

- [ ] **Step 1: Add the outbox-write helper**

```python
import uuid as _uuid
import json as _json
from datetime import datetime, timezone

def _queue_sync_event(cur, entity_type, entity_id, event_type, payload):
    """Must be called with the SAME cur/conn as the row write it
    describes, before that transaction's commit() -- see Global
    Constraints in the plan this came from."""
    cur.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), entity_type, str(entity_id), event_type,
         _json.dumps(payload), datetime.now(timezone.utc).isoformat()),
    )
```

- [ ] **Step 2: Instrument `create_category`, matching the existing function's exact style**

```python
@retail_bp.route('/categories', methods=['POST'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def create_category():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Category name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    new_id = str(_uuid.uuid4())
    cur.execute("INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?)",
                (new_id, cid, data['name'], data.get('description', '')))
    _queue_sync_event(cur, 'category', new_id, 'create', {
        'id': new_id, 'company_id': cid, 'name': data['name'], 'description': data.get('description', ''),
    })
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'id': new_id}})
```

(Note the `id` column is now a caller-supplied UUID string, not
`cur.lastrowid` — matches Task 3's schema change.)

- [ ] **Step 3: Add the new update route**

```python
@retail_bp.route('/categories/<string:category_id>', methods=['PUT'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def update_category(category_id):
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Category name required'}), 400
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute("UPDATE categories SET name=?, description=? WHERE id=? AND company_id=?",
                (data['name'], data.get('description', ''), category_id, cid))
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Category not found'}), 404
    _queue_sync_event(cur, 'category', category_id, 'update', {
        'id': category_id, 'company_id': cid, 'name': data['name'], 'description': data.get('description', ''),
    })
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})
```

- [ ] **Step 4: Add the new delete route**

```python
@retail_bp.route('/categories/<string:category_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('retail')
@require_license_capability("retail.product.create", restricted_mode_allowlist=RETAIL_RESTRICTED_ALLOWLIST)
def delete_category(category_id):
    cid = _cid()
    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM categories WHERE id=? AND company_id=?", (category_id, cid))
    if cur.rowcount == 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Category not found'}), 404
    _queue_sync_event(cur, 'category', category_id, 'delete', {'id': category_id})
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})
```

- [ ] **Step 5: Manual verification**

Run the backend locally (isolated `AURA_APP_DATA`), then:
```bash
curl -s -X POST http://127.0.0.1:5000/api/sub/retail/categories -H "Content-Type: application/json" -b cookies.txt -d '{"name":"Test Cat"}'
```
(using an authenticated session cookie from a prior login) — confirm the
response's `id` is a UUID string, then:
```bash
sqlite3 "$AURA_APP_DATA/retail.db" "SELECT * FROM sync_outbox"
```
Expect exactly one row, `event_type='create'`, `payload` containing the
same UUID and name. Repeat for PUT and DELETE against that same id, confirm
`sync_outbox` accumulates 3 rows total.

- [ ] **Step 6: Commit**

```bash
git add products/retail/backend/api/retail_api.py
git commit -m "feat(retail): add category update/delete routes, queue sync events on all category writes"
```

---

### Task 5: Desktop Retail — sync relay client + background loop

**Files:**
- Create: `commercial_runtime/sync/__init__.py`
- Create: `commercial_runtime/sync/relay_client.py` (mirrors
  `commercial_runtime/licensing_contracts/client.py`'s `_request` pattern —
  read that file first)
- Create: `commercial_runtime/sync/sync_service.py` (mirrors
  `commercial_runtime/licensing_contracts/checkin_scheduler.py`'s
  `start()`/`stop()`/`_schedule_next()` threading.Timer shape — read that
  file first)
- Modify: `products/retail/backend/config.py` (new `SYNC_RELAY_BASE_URL`
  setting, following the exact `OWNER_LICENSING_BASE_URL` pattern)
- Modify: `products/retail/backend/app.py` (start the sync service
  alongside the existing licensing blueprint registration)

**Interfaces:**
- Consumes: Owner's `/api/sync/v1/push`/`/pull` (Task 2), retail's
  `sync_outbox`/`sync_cursor` tables (Task 3), the existing
  `WindowsDpapiDeviceIdentityProvider.sign()` (unchanged, reused as-is per
  the approved spec — do not modify `device_identity.py`), the existing
  `LicenseStateRepository`'s `owner_installation_id` field.
- Produces: a running background sync loop, started once from `app.py`'s
  `init_app()`.

- [ ] **Step 1: Write the relay client**

Mirror `LicensingClient._request`'s exact retry/backoff/timeout/TLS-verify
behavior (`commercial_runtime/licensing_contracts/client.py:160-208`) for
two methods:

```python
class SyncRelayClient:
    def __init__(self, base_url, signer, installation_id, timeout_seconds, verify_tls):
        self._base_url = base_url.rstrip("/")
        self._signer = signer
        self._installation_id = installation_id
        self._timeout_seconds = timeout_seconds
        self._verify_tls = verify_tls

    def _signed_body(self, extra: dict) -> dict:
        body = {"installation_id": self._installation_id, **extra}
        canonical = canonicalize_bytes(body)  # reuse the same
                                                # canonicalization helper
                                                # licensing_contracts already
                                                # uses -- do not reimplement
        body["signature"] = self._signer.sign(canonical)
        return body

    def push(self, events: list[dict]) -> dict:
        body = self._signed_body({"events": events})
        return self._request("POST", "/api/sync/v1/push", json=body)

    def pull(self, since: int) -> dict:
        body = self._signed_body({})
        return self._request("GET", f"/api/sync/v1/pull?since={since}", json=body)

    # _request: copy client.py's _request implementation verbatim in
    # structure (retry on _RETRYABLE_STATUS_CODES, exponential backoff
    # with jitter, timeout=self._timeout_seconds, verify=self._verify_tls,
    # raise its own SyncRelayError subclass on non-retryable failure)
```

- [ ] **Step 2: Write the sync service (outbox drain + pull loop)**

```python
class SyncService:
    def __init__(self, relay_client, get_conn):
        self._client = relay_client
        self._get_conn = get_conn
        self._timer = None
        self._stopped = threading.Event()

    def push_once(self):
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM sync_outbox ORDER BY created_at").fetchall()
        if not rows:
            conn.close(); return
        events = [{
            "id": r["id"], "entity_type": r["entity_type"], "entity_id": r["entity_id"],
            "event_type": r["event_type"], "payload": json.loads(r["payload"]),
            "created_at": r["created_at"],
        } for r in rows]
        result = self._client.push(events)  # raises on failure -- caller's
                                              # retry/backoff in _request
                                              # already handled it; a raise
                                              # here means genuinely offline
        conn.execute("DELETE FROM sync_outbox WHERE id IN ({})".format(
            ",".join("?" * len(rows))), [r["id"] for r in rows])
        conn.commit(); conn.close()

    def pull_once(self):
        conn = self._get_conn()
        cursor_row = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
        result = self._client.pull(cursor_row["last_seq"])
        for ev in result["events"]:
            self._apply_event(conn, ev)
        conn.execute("UPDATE sync_cursor SET last_seq=? WHERE id=1", (result["cursor"],))
        conn.commit(); conn.close()

    def _apply_event(self, conn, ev):
        if ev["entity_type"] != "category":
            return  # only category is in scope for this sub-project
        p = ev["payload"]
        if ev["event_type"] in ("create", "update"):
            conn.execute(
                "INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description",
                (p["id"], p["company_id"], p["name"], p.get("description", "")),
            )
        elif ev["event_type"] == "delete":
            conn.execute("DELETE FROM categories WHERE id=?", (p["id"],))

    def run_once(self):
        try:
            self.push_once()
            self.pull_once()
        except Exception:
            pass  # offline or relay error -- next scheduled tick retries;
                   # never let a sync failure crash the app

    def start(self, interval_seconds=10):
        self._stopped.clear()
        self._schedule_next(interval_seconds)

    def stop(self):
        self._stopped.set()
        if self._timer:
            self._timer.cancel()

    def _schedule_next(self, interval_seconds):
        if self._stopped.is_set():
            return
        def _tick():
            try:
                self.run_once()
            finally:
                self._schedule_next(interval_seconds)
        self._timer = threading.Timer(interval_seconds, _tick)
        self._timer.daemon = True
        self._timer.start()
```

`push_once` must also be called **immediately** (not just on the 10s tick)
right after any route in Task 4 successfully queues an event — this is
what makes "push the instant connectivity returns" real rather than
approximate. Add a call to a module-level `sync_service.push_once()`
(best-effort, swallow exceptions) at the end of `_queue_sync_event`'s call
sites in `retail_api.py`, or expose a lightweight "nudge" the routes call
after commit.

- [ ] **Step 3: Add config**

```python
# config.py, alongside the OWNER_LICENSING_* block
SYNC_RELAY_BASE_URL = os.environ.get('AURA_SYNC_RELAY_URL', '')
SYNC_RELAY_TIMEOUT_SECONDS = float(os.environ.get('AURA_SYNC_RELAY_TIMEOUT_SECONDS', '10'))
SYNC_RELAY_VERIFY_TLS = True if getattr(sys, 'frozen', False) else (
    os.environ.get('AURA_SYNC_RELAY_INSECURE') != '1'
)
```

- [ ] **Step 4: Wire startup in `app.py`**

In `init_app()`, alongside the existing licensing blueprint construction
(reuse the same `WindowsDpapiDeviceIdentityProvider`/`LicenseStateRepository`
instances already built there — do not construct a second device identity),
construct a `SyncRelayClient` + `SyncService` and call `.start()` only if
`SYNC_RELAY_BASE_URL` is non-empty (empty = sync inert, matching the
existing `OWNER_LICENSING_BASE_URL` "empty means off" convention).

- [ ] **Step 5: Manual verification (local relay, no cable yet — that's Task 11)**

Run Owner locally (Task 2's verification steps), run retail backend with
`AURA_SYNC_RELAY_URL=http://127.0.0.1:5551` and a real activated license
(needed for the signature to verify). Create a category via the API,
confirm within ~10s (or immediately, if the nudge from Step 2 fired) that
`sync_outbox` is empty and Owner's `owner_sync_events` table has one row
matching it (`psql` or a quick Python check against `OWNER_DATABASE_URL`).

- [ ] **Step 6: Commit**

```bash
git add commercial_runtime/sync/ products/retail/backend/config.py products/retail/backend/app.py
git commit -m "feat(retail): add sync relay client + background push/pull loop"
```

---

### Task 6: Mobile — SQLDelight schema (sync tables + Category id migration)

**Files:**
- Modify: `shared/src/commonMain/sqldelight/com/actionaura/retail/db/Catalog.sq`
- Create: `shared/src/commonMain/sqldelight/com/actionaura/retail/db/Sync.sq`
- Modify: `shared/src/commonMain/kotlin/com/actionaura/retail/data/CategoryRepository.kt`
  (interface: every `id: Long` parameter/return becomes `id: String`)
- Modify: `shared/src/commonMain/kotlin/com/actionaura/retail/data/sqldelight/SqlDelightCategoryRepository.kt`
- Modify: any UI/ViewModel call site passing a category id (grep
  `categoryRepository\.|CategoryUseCases` under `ui/category/` first — fix
  every call site the type change breaks; the compiler will find them all,
  but grep first so the scope is known before starting)

**Interfaces:**
- Produces: `sync_outbox`/`sync_cursor` SQLDelight tables and generated
  queries (mirrors Task 3's desktop shape). `categories.id` becomes `TEXT`
  (UUID string), generated client-side at insert time (no more
  `lastInsertRowId()`).
- Consumed by: Task 10 (outbox instrumentation), Task 9 (sync client reads
  the same tables).

- [ ] **Step 1: Migrate `Catalog.sq`'s categories table**

```sql
-- Catalog.sq: replace the existing CREATE TABLE categories block
CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL
);
CREATE INDEX categories_company_id ON categories(company_id);
CREATE UNIQUE INDEX categories_company_name ON categories(company_id, name) WHERE status = 'active';
```

Update `products.category_id` (currently `INTEGER REFERENCES categories(id)`)
to `TEXT REFERENCES categories(id)`, and every named query in this file
touching `categories.id`/`products.category_id` (`selectActiveCategories`,
`selectCategoryById`, `updateCategoryStatus`, `insertCategory`, any join in
`selectActiveProducts`/`selectLowStockProducts`) to use the `TEXT` type —
read the full file first, since SQLDelight generates typed Kotlin from
these signatures and every one must agree.

Change `insertCategory` from relying on `lastInsertRowId()` to accepting
the id as a parameter:
```sql
insertCategory:
INSERT INTO categories (id, company_id, name, description, status, created_at)
VALUES (?, ?, ?, ?, 'active', ?);
```

- [ ] **Step 2: Add the sync tables**

```sql
-- Sync.sq (new file)
CREATE TABLE syncOutbox (
    id TEXT NOT NULL PRIMARY KEY,
    entityType TEXT NOT NULL,
    entityId TEXT NOT NULL,
    eventType TEXT NOT NULL,
    payload TEXT NOT NULL,
    createdAt INTEGER NOT NULL
);

CREATE TABLE syncCursor (
    id INTEGER NOT NULL PRIMARY KEY,
    lastSeq INTEGER NOT NULL DEFAULT 0
);
INSERT INTO syncCursor (id, lastSeq) VALUES (1, 0);

selectOutbox:
SELECT * FROM syncOutbox ORDER BY createdAt;

insertOutboxEvent:
INSERT INTO syncOutbox (id, entityType, entityId, eventType, payload, createdAt)
VALUES (?, ?, ?, ?, ?, ?);

deleteOutboxEvent:
DELETE FROM syncOutbox WHERE id = ?;

selectCursor:
SELECT lastSeq FROM syncCursor WHERE id = 1;

updateCursor:
UPDATE syncCursor SET lastSeq = ? WHERE id = 1;
```

- [ ] **Step 3: Update `CategoryRepository` interface and implementation**

```kotlin
// CategoryRepository.kt
interface CategoryRepository {
    suspend fun listActive(companyId: Long): List<Category>
    suspend fun getById(companyId: Long, id: String): Category?
    suspend fun insert(companyId: Long, name: String, description: String?, nowEpochMillis: Long): Category
    suspend fun setActive(companyId: Long, id: String, active: Boolean)
}
```

`Category` data class's `id` field changes `Long` → `String`. In
`SqlDelightCategoryRepository.insert` (currently uses
`db.transactionWithResult { ...; lastInsertRowId() }`), generate the UUID
client-side before the insert:
```kotlin
override suspend fun insert(companyId: Long, name: String, description: String?, nowEpochMillis: Long): Category =
    gate.mutex.withLock {
        db.transactionWithResult {
            val newId = uuid4().toString()  // use whatever UUID generation
                                              // is already available in
                                              // commonMain (kotlin.uuid or
                                              // an existing util) -- check
                                              // for precedent before adding
                                              // a new dependency
            queries.insertCategory(newId, companyId, name, description, nowEpochMillis)
            Category(id = newId, companyId = companyId, name = name, description = description, status = "active")
        }
    }
```

- [ ] **Step 4: Fix call sites**

```bash
./gradlew.bat :shared:compileKotlinAndroid 2>&1 | grep -i "error:"
```
Fix every compile error this surfaces in `ui/category/` (navigation args
typed `Long` for category id become `String`) and `usecases/category/`.
Do not fix anything the compiler doesn't flag — this is a mechanical type
propagation, not a redesign.

- [ ] **Step 5: Verify**

```bash
./gradlew.bat :shared:compileKotlinAndroid :shared:testDebugUnitTest --tests "*Category*"
```
Expected: compiles clean, existing category repository/use-case tests
(`ProductInventoryRepositoryTest.kt` and category equivalents, if any —
confirm exact test file names by listing the test directory first) pass
after being updated for the `String` id type.

- [ ] **Step 6: Commit**

```bash
git add mobile/aura-retail-unified/shared/src/commonMain/sqldelight/com/actionaura/retail/db/Catalog.sq \
        mobile/aura-retail-unified/shared/src/commonMain/sqldelight/com/actionaura/retail/db/Sync.sq \
        mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/data/
git commit -m "feat(mobile): add sync tables, migrate categories.id to UUID string"
```

---

### Task 7: Mobile — Ed25519 device signing (net-new capability)

**Files:**
- Create: `shared/src/commonMain/kotlin/com/actionaura/retail/sync/DeviceSigner.kt`
  (expect declarations)
- Create: `shared/src/androidMain/kotlin/com/actionaura/retail/sync/DeviceSigner.android.kt`
- Modify: `shared/src/commonMain/kotlin/com/actionaura/retail/di/AuraAppContainer.kt`

**Interfaces:**
- Consumes: `GenerationalSecureMaterialStore` (existing, from
  `AuraAppContainer.kt:89-98` — read its actual interface before assuming
  it can store a raw keypair; it may need a small addition to store two
  blobs (private/public key) under known keys, or one combined blob you
  serialize/deserialize yourself. Do not modify
  `GenerationalSecureMaterialStore`'s own contract if avoidable — prefer
  wrapping it.
- Produces: `DeviceSigner` with `sign(message: ByteArray): ByteArray` and
  `publicKeyBytes(): ByteArray`, generating and persisting a keypair on
  first use.
- Consumed by: Task 8 (activation), Task 9 (sync client signs push/pull
  requests).

- [ ] **Step 1: Write the commonMain contract**

```kotlin
// sync/DeviceSigner.kt
interface DeviceSigner {
    suspend fun publicKeyBytes(): ByteArray
    suspend fun sign(message: ByteArray): ByteArray
}

expect class PlatformDeviceSigner(secureStore: GenerationalSecureMaterialStore) : DeviceSigner
```

- [ ] **Step 2: Write the Android actual, using Tink (matching the existing verifier's library)**

Read `SignedLeaseSignatureVerifier.android.kt` first — it already depends
on Tink's `Ed25519Verify`; this task adds the signing counterpart,
`Ed25519Sign`, from the same library (no new crypto dependency).

```kotlin
// sync/DeviceSigner.android.kt
actual class PlatformDeviceSigner actual constructor(
    private val secureStore: GenerationalSecureMaterialStore
) : DeviceSigner {
    private val KEY_ALIAS = "sync_device_ed25519_keypair"

    private suspend fun loadOrCreateKeypair(): Ed25519PrivateKeyManager /* or whatever
        Tink type the existing verifier code models its key handling on --
        match SignedLeaseSignatureVerifier.android.kt's actual Tink API
        usage exactly rather than guessing at Tink's surface here */ {
        val existing = secureStore.retrieve(KEY_ALIAS)
        if (existing != null) return deserializeKeypair(existing)
        val generated = KeysetHandle.generateNew(Ed25519PrivateKeyManager.rawEd25519Template())
        secureStore.store(KEY_ALIAS, serializeKeypair(generated))
        return generated
    }

    override suspend fun publicKeyBytes(): ByteArray {
        val keypair = loadOrCreateKeypair()
        return keypair.publicKeysetHandle.rawEd25519PublicKeyBytes() // exact
            // accessor depends on Tink's actual API -- confirm against
            // SignedLeaseSignatureVerifier.android.kt's own key-handling
            // code before finalizing
    }

    override suspend fun sign(message: ByteArray): ByteArray {
        val keypair = loadOrCreateKeypair()
        val signer = keypair.getPrimitive(PublicKeySign::class.java)
        return signer.sign(message)
    }
}
```

The exact Tink API calls (`serializeKeypair`/`deserializeKeypair`,
`rawEd25519PublicKeyBytes`) are placeholders for "whatever
`SignedLeaseSignatureVerifier.android.kt` already demonstrates working" —
this step's implementer must read that file's actual Tink usage first and
mirror it precisely; do not guess at Tink's API surface from general
knowledge, this codebase already has a working, tested example to copy.

- [ ] **Step 3: Wire into DI**

In `AuraAppContainer.kt`, alongside the existing `secureStore` construction,
add:
```kotlin
val deviceSigner: DeviceSigner = PlatformDeviceSigner(secureStore)
```

- [ ] **Step 4: Verify key persistence**

Write a small instrumented test (or a temporary debug log call during
manual app launch) confirming: call `publicKeyBytes()` twice across two
separate app process launches, get the same bytes both times (proves the
key persists in secure storage, isn't regenerated every launch — a
regenerated key would silently break Owner-side verification, since
Owner would have registered the OLD public key at activation).

- [ ] **Step 5: Commit**

```bash
git add mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/sync/DeviceSigner.kt \
        mobile/aura-retail-unified/shared/src/androidMain/kotlin/com/actionaura/retail/sync/DeviceSigner.android.kt \
        mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/di/AuraAppContainer.kt
git commit -m "feat(mobile): add Ed25519 device signing capability (net-new, Tink-backed)"
```

---

### Task 8: Mobile — real activation call (register device public key with Owner)

**Files:**
- Modify (or replace): the production `ExternalLicensingTransport`
  implementation — currently `DisabledProductionTransport.kt` always
  returns `TransportNotConfigured`. Read
  `shared/src/commonMain/kotlin/com/actionaura/retail/licensing/CheckInContracts.kt`
  first for the exact request/response shape already modeled.

**Interfaces:**
- Consumes: `DeviceSigner` (Task 7), Owner's existing
  `owner/app/licensing_service/activation.py` (unmodified — confirmed
  platform-agnostic during planning, needs no server-side change).
- Produces: a working activation call mobile can actually make, needed
  before Task 9's sync client has anything valid to sign requests with
  (Owner must have registered this device's public key before it will
  accept a signed sync request from it).

- [ ] **Step 1: Implement the real transport**

Read `ExternalLicensingTransport`'s full interface
(`licensing/transport/ExternalLicensingTransport.kt:28-41`) and
`CheckInContracts.kt`'s `CheckInRequest` shape, then write a Ktor-backed
implementation (this shares the Ktor dependency Task 9 also needs — add it
once, in this task, since activation must work before sync can be tested
end to end). Build the activation request body per
`owner/app/licensing_service/activation.py`'s documented required fields
(`contract_version`, `request_id`, `correlation_id`, `timestamp`, `nonce`,
`product_code`, `platform`, `app_version`, `installation_id`,
`device_public_key`, `device_public_key_algorithm`, `license_key`,
`signature`) — sign the canonicalized body (minus `signature`) with
`DeviceSigner.sign(...)` from Task 7, matching exactly how desktop's
`LicensingClient` builds its own signed requests (read
`commercial_runtime/licensing_contracts/client.py`'s activation call for
the canonicalization approach and mirror it in Kotlin).

- [ ] **Step 2: Verify against the locally-running Owner instance**

With Owner running locally (Task 2's setup) and a real test license
available, drive activation from the mobile app (a debug button, or a
temporary test harness call) and confirm: Owner's `owner_installations`
table (or equivalent) gets a new row with this device's actual public key
bytes matching what `DeviceSigner.publicKeyBytes()` produced.

- [ ] **Step 3: Commit**

```bash
git add mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/licensing/
git commit -m "feat(mobile): implement real device activation, registers Ed25519 public key with Owner"
```

---

### Task 9: Mobile — sync relay client (Ktor)

**Files:**
- Create: `shared/src/commonMain/kotlin/com/actionaura/retail/sync/SyncTransport.kt`
- Modify: `shared/build.gradle.kts` (add Ktor dependencies — if not
  already added in Task 8, add here: `ktor-client-core`,
  `ktor-client-content-negotiation`, `ktor-client-json` to `commonMain`;
  `ktor-client-okhttp` or `ktor-client-android` to `androidMain`)

**Interfaces:**
- Consumes: `DeviceSigner` (Task 7), Owner's `/api/sync/v1/push`/`/pull`
  (Task 2).
- Produces: `SyncTransport` with `push(events)`/`pull(since)`, used by
  Task 10's sync orchestration.

- [ ] **Step 1: Write the transport**

```kotlin
// sync/SyncTransport.kt
class SyncTransport(
    private val httpClient: HttpClient,
    private val baseUrl: String,  // config value -- see Step 2
    private val signer: DeviceSigner,
    private val installationId: String,
) {
    private suspend fun signedBody(extra: Map<String, Any?>): Map<String, Any?> {
        val body = mapOf("installation_id" to installationId) + extra
        val canonical = canonicalize(body)  // must produce byte-identical
                                              // output to the Python
                                              // canonicalize_bytes() the
                                              // relay verifies against --
                                              // this is the single most
                                              // important cross-language
                                              // correctness point in this
                                              // whole task; write a shared
                                              // test vector (same input,
                                              // same expected bytes) and
                                              // verify against the Python
                                              // side before trusting it
        val signature = signer.sign(canonical)
        return body + ("signature" to signature.encodeBase64())
    }

    suspend fun push(events: List<SyncEventDto>): PushResult {
        val body = signedBody(mapOf("events" to events))
        return httpClient.post("$baseUrl/api/sync/v1/push") {
            contentType(ContentType.Application.Json)
            setBody(body)
        }.body()
    }

    suspend fun pull(since: Long): PullResult {
        val body = signedBody(emptyMap())
        return httpClient.get("$baseUrl/api/sync/v1/pull") {
            parameter("since", since)
            contentType(ContentType.Application.Json)
            setBody(body)
        }.body()
    }
}
```

- [ ] **Step 2: Config for the relay base URL**

Add a `SyncConfig` (or extend an existing config object if one already
exists for licensing) holding `baseUrl: String`, sourced from build
config / a settings screen — never hardcoded. For local cable testing this
is `http://127.0.0.1:<port>` (reached via `adb reverse`); production is a
real `https://` host. Follow whatever existing pattern the licensing
transport config already uses for its own base URL, if one exists — check
before inventing a new config mechanism.

- [ ] **Step 3: The canonicalization cross-language test (critical — do not skip)**

Write a small test with a fixed, hand-constructed body (e.g.
`{"installation_id": "test-123", "events": []}`), compute the canonical
bytes in Kotlin, and compare against the same fixed body's canonical bytes
computed by the actual Python `canonicalize_bytes()` function (run it once
in a Python REPL against the identical dict, print the bytes). They must
match exactly, byte for byte — if they don't, no signature will ever
verify and every sync request will silently fail auth. This is the
highest-risk single point of failure in the entire mobile sync path.

- [ ] **Step 4: Commit**

```bash
git add mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/sync/SyncTransport.kt \
        mobile/aura-retail-unified/shared/build.gradle.kts
git commit -m "feat(mobile): add Ktor sync transport, verified canonicalization matches Owner's Python implementation"
```

---

### Task 10: Mobile — outbox instrumentation + sync orchestration loop

**Files:**
- Modify: `usecases/category/CategoryUseCases.kt` (or the repository layer
  — whichever already owns the transaction boundary per Task 6's findings)
- Create: `shared/src/commonMain/kotlin/com/actionaura/retail/sync/SyncOrchestrator.kt`
- Modify: `di/AuraAppContainer.kt` (wire up and start the orchestrator)

**Interfaces:**
- Consumes: `syncOutbox`/`syncCursor` queries (Task 6), `SyncTransport`
  (Task 9).
- Produces: category create/archive/reactivate now queue outbox events in
  the same transaction as the write; a coroutine loop pushes immediately
  after any local write and polls every ~10s.

- [ ] **Step 1: Instrument the repository's transaction**

In `SqlDelightCategoryRepository`, inside the same `db.transactionWithResult { ... }`
block Task 6 already touched for `insert`, add the outbox write using the
generated `insertOutboxEvent` query from Task 6's `Sync.sq`:
```kotlin
queries.insertOutboxEvent(
    uuid4().toString(), "category", newId, "create",
    Json.encodeToString(CategoryPayload(newId, companyId, name, description)),
    nowEpochMillis,
)
```
Do the same for `setActive` — `active = false` maps to `event_type = "delete"`
per the Global Constraints' documented semantics (mobile's soft-archive IS
this platform's "delete"); `active = true` (reactivate) maps to
`event_type = "update"` with the row's current full state.

- [ ] **Step 2: Write the orchestrator (coroutine-based, no existing precedent to follow — this is genuinely new)**

```kotlin
class SyncOrchestrator(
    private val transport: SyncTransport,
    private val database: AuraDatabase,
    private val gate: DatabaseWriteGate,
    private val scope: CoroutineScope,
) {
    private var pollJob: Job? = null

    suspend fun pushOnce() = gate.mutex.withLock {
        val outbox = database.syncQueries.selectOutbox().executeAsList()
        if (outbox.isEmpty()) return
        val events = outbox.map { it.toDto() }
        transport.push(events)  // throws on failure -- caller decides retry
        database.transaction {
            outbox.forEach { database.syncQueries.deleteOutboxEvent(it.id) }
        }
    }

    suspend fun pullOnce() = gate.mutex.withLock {
        val cursor = database.syncQueries.selectCursor().executeAsOne().lastSeq
        val result = transport.pull(cursor)
        database.transaction {
            result.events.forEach { applyEvent(it) }
            database.syncQueries.updateCursor(result.cursor)
        }
    }

    private fun applyEvent(ev: SyncEventDto) {
        if (ev.entityType != "category") return
        when (ev.eventType) {
            "create", "update" -> database.categoryQueries.upsertCategory(/* from ev.payload */)
            "delete" -> database.categoryQueries.updateCategoryStatus(ev.entityId, "inactive")
        }
    }

    fun start(pollIntervalMs: Long = 10_000) {
        pollJob = scope.launch {
            while (isActive) {
                try { pushOnce(); pullOnce() } catch (e: Exception) { /* offline -- retry next tick */ }
                delay(pollIntervalMs)
            }
        }
    }

    fun stop() { pollJob?.cancel() }

    /** Call this right after any local category write commits -- makes
     * "push the instant connectivity returns" real, not just eventual
     * within the poll interval. */
    fun nudge() { scope.launch { try { pushOnce() } catch (e: Exception) {} } }
}
```

`upsertCategory` needs a corresponding SQLDelight query added to
`Catalog.sq` (`INSERT ... ON CONFLICT(id) DO UPDATE ...`, matching Task 5's
SQLite equivalent) if it doesn't already exist as one — check before
assuming.

- [ ] **Step 3: Call `nudge()` after category writes, and start the orchestrator at app launch**

In `CategoryUseCases`, after a successful create/archive/reactivate,
call the orchestrator's `nudge()`. In `AuraAppContainer`'s construction,
call `syncOrchestrator.start()` once, guarded by the same "is a relay URL
configured" check Task 9's config introduced.

- [ ] **Step 4: Verify (still local, not cable yet — Task 11 covers the physical device)**

Run the Android app in an emulator pointed at `10.0.2.2:<port>` (the
emulator's alias for the host machine's `127.0.0.1`, standard Android
emulator networking — this is a valid way to verify the loop works before
involving a physical cable). Create a category, confirm it appears in
Owner's `owner_sync_events` table within the poll interval.

- [ ] **Step 5: Commit**

```bash
git add mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/sync/SyncOrchestrator.kt \
        mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/usecases/category/ \
        mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/di/AuraAppContainer.kt
git commit -m "feat(mobile): instrument category CRUD with sync outbox, add push/pull orchestration loop"
```

---

### Task 11: End-to-end verification over USB cable

**Files:** none — verification only, matching the approved spec's test
matrix exactly.

- [ ] **Step 1: Start Owner locally** (Task 2's run command).

- [ ] **Step 2: Start desktop Retail** pointed at Owner (`AURA_SYNC_RELAY_URL=http://127.0.0.1:5551`), with a real activated license.

- [ ] **Step 3: Connect the phone via USB**, enable USB debugging, authorize the fingerprint, confirm `adb devices`.

- [ ] **Step 4: `adb reverse tcp:5551 tcp:5551`** — tunnels the phone's `127.0.0.1:5551` to the desktop's Owner instance.

- [ ] **Step 5: Install and launch the mobile app** (`./gradlew.bat :androidApp:installDebug`), configured with the sync relay base URL `http://127.0.0.1:5551` (matching Task 9's config, now reachable over the cable per Step 4). Complete real activation (Task 8) against a real test license.

- [ ] **Step 6: Test matrix** (must all pass, not just "no console error" — confirm actual row content on both sides via each platform's DB/UI):
  - [ ] Create a category on desktop → appears on mobile within one poll interval.
  - [ ] Create a category on mobile → appears on desktop within one poll interval.
  - [ ] Edit a category on desktop → mobile reflects the new name/description.
  - [ ] Archive a category on mobile → desktop's category is gone (real delete, per the documented per-platform delete semantics).
  - [ ] Delete a category on desktop → mobile's category becomes inactive (soft-archive, not crash/error).
  - [ ] Unplug the USB cable, create a category on mobile (must succeed locally, offline), reconnect, confirm it pushes immediately (not waiting for the next poll tick) and appears on desktop.
  - [ ] Same offline test in the other direction (desktop offline — stop Owner or block the port — create a category, restart Owner, confirm desktop's outbox drains immediately on the next successful connection attempt).

- [ ] **Step 7: Record results.** If every item passes, this sub-project is done. If anything fails, document exactly what broke (which step, what you saw) rather than patching ad hoc — route it through the same fix-loop discipline used for the retail UI shell work.

## Residual gaps (explicitly out of scope for this sub-project, not silently dropped)

- Real cloud deployment of Owner — separate future task.
- Products, Customers, Suppliers, Sales, Inventory sync — follow-up
  sub-projects, each needing their own conflict model where money/stock is
  involved.
- iOS sync client — `DeviceSigner`/`SyncTransport` are structured with
  `expect`/`actual` specifically so an iOS actual can be added later
  without touching commonMain, but this plan only implements the Android
  actual.
- Persistent push channel (websocket/SSE) — deferred per the approved spec.
- Rate limiting on the relay endpoints — the plan bounds batch size
  (abuse-resistance) but does not add full rate limiting; noted in the
  spec as a deployment-time concern.
