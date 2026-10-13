"""SYNC-BOOT Phase 1 proof, end to end: a registry stream that stalls while the
retail stream of the SAME computer races ahead must not lose its events to the
nightly prune.

THE HAZARD (read in the code, then reproduced here). One computer runs two sync
streams -- retail and registry -- over ONE installation id. Each keeps its own
cursor locally, but Owner used to keep ONE cursor per installation. The retail
stream pulls every event of the licence (it applies only the retail types, and
advances past the rest), so it drags that one Owner cursor to the end of the log
while the registry stream, which has not run, still has users/permissions to
receive. The production prune deletes up to the slowest cursor -- which, with one
cursor, no longer includes the registry stream -- and the registry stream later
finds the events it never applied gone.

THE SCENARIO (real Owner over HTTP, real retail apps in their own processes, the
real SyncRelayClient, the production prune command -- same machinery as
test_sync_late_join_after_prune_cross_stack.py, with `stream_labels` on so the
computers talk to Owner the way the shipped app does):

  1. A founds the shop and invites an accountant (registry events), pushes.
  2. B joins, pulls the REGISTRY stream once (it now holds the accountant), and
     pulls retail.
  3. A invites two more people (more registry events), pushes, and is fully
     caught up on both streams.
  4. B pulls RETAIL ONLY. Its retail cursor is at the end of the log; its
     registry cursor stalled at step 2.
  5. The production prune runs.
  6. B finally pulls the registry stream: it must receive EVERY event it missed.

Mutation proof (run for real): in owner/app/sync/routes.py pull(), force
`stream = LEGACY_STREAM` (collapse to one cursor) -> step 5 deletes everything B's
registry stream still needed, step 6 is refused with RESYNC_REQUIRED, and
`test_a_stalled_registry_stream_recovers_every_event_after_the_prune` goes RED.

Run (one Owner pytest process at a time -- shared test database):
    OWNER_TEST_DATABASE_URL=... AURA_RETAIL_PYTHON=<repo .venv python> \\
      .venv-owner/Scripts/python.exe -m pytest tests/test_sync_registry_stall_cross_stack.py -q
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from tests.conftest import make_license, make_staff
from tests.test_sync_late_join_after_prune_cross_stack import (
    _Computer, _OwnerOverHttp, _prune_exactly_as_production_does, run_accept_device, _DEVICE_PY, _DEVICE_TIMEOUT,
)

_LICENCE = "LIC-STALL-TEST-1"
_INVITEES_FIRST = ["first.accountant@stall.test"]
_INVITEES_LATER = ["later.one@stall.test", "later.two@stall.test"]


class _Labelled(_Computer):
    """A computer that syncs both streams over Owner with stream labels on."""

    def run(self, actions: list) -> list:
        assert self.installation_id, "computer used before it was activated"
        return run_accept_device(
            _DEVICE_PY, self.dir, self.dir / "_unused_relay.db", actions, timeout=_DEVICE_TIMEOUT,
            options={"owner_relay": {"base_url": self.owner.base_url, "installation_id": self.installation_id,
                                     "device_key_b64": self.key_b64},
                     "stream_labels": True, "registry_owner_relay": True},
        )


def _invite(a: _Labelled, login: dict, emails: list) -> None:
    results = a.run([login, *[
        {"op": "http", "method": "POST", "path": "/api/admin/employees",
         "json": {"email": e, "name": e.split("@")[0], "preset": "accountant"}} for e in emails
    ], {"op": "reg_push"}, {"op": "push"}])
    for r in results[1:1 + len(emails)]:
        assert r["status_code"] == 200, r


def _owner_rows(app, sql_filter):
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        return db_session.execute(select(SyncEvent.seq, SyncEvent.entity_type).where(*sql_filter)).all()


@pytest.fixture
def stalled_shop(app, client, seeded, signing_key, tmp_path, monkeypatch):
    for var in ("AURA_OWNER_LICENSING_URL", "AURA_SYNC_RELAY_URL", "OWNER_LICENSING_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AURA_SITE_RELAY_ENABLED", "0")
    monkeypatch.setenv("AURA_BACKUP_SCHEDULER", "0")
    # Registry events are SIGNED authority events (AUDIT-033): without a signing
    # identity A's invitations would leave A unsigned and B would park every one
    # in its quarantine instead of applying it. Same switch the registry
    # two-device tests use (see retail_s_devices_twodevice_test.py).
    monkeypatch.setenv("AURA_ACCEPT_SIGNED", "1")
    monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", _LICENCE)

    actor_id = make_staff(app, "sync-registry-stall@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=2, product_code="AURA_RETAIL")
    owner = _OwnerOverHttp(app)
    try:
        a, b = (_Labelled(tmp_path, n, owner) for n in ("a", "b"))
        a.activate(client, full_key)
        b.activate(client, full_key)

        # 1. A founds the shop and invites an accountant (registry events), pushes.
        [created, _lic_a] = a.run([{"op": "create_shop"},
                                   {"op": "activate_shared_licence", "license_public_id": _LICENCE}])
        login = {"op": "login", "email": created["email"], "password": created["password"]}
        _invite(a, login, _INVITEES_FIRST)

        # 2. B joins: same company, pulls the REGISTRY stream once, then retail.
        [boot, _lic_b] = b.run([{"op": "bootstrap"},
                                {"op": "activate_shared_licence", "license_public_id": _LICENCE}])
        b.run([{"op": "reg_exec", "sql": "UPDATE company_settings SET company_id=?", "params": [created["company_id"]]},
               {"op": "reg_exec", "sql": "UPDATE users SET company_id=?", "params": [created["company_id"]]}])
        [reg_pull_1, _] = b.run([{"op": "reg_pull"}, {"op": "pull"}])
        assert reg_pull_1["ok"] is True and not reg_pull_1.get("resync_required"), reg_pull_1
        first = b.run([{"op": "reg_select", "sql": "SELECT email FROM users"}])[0]["rows"]
        # HARNESS GUARD: the registry stream really delivers invitations to B.
        assert set(_INVITEES_FIRST) <= {r["email"] for r in first}, first

        # 3. A invites two more people; A is fully caught up on both streams.
        _invite(a, login, _INVITEES_LATER)
        # B authors one event AFTER all of A's. Owner's pull excludes a device's own
        # events, so a computer that only ever authors keeps its Owner cursor at 0
        # (see test_sync_late_join_after_prune_cross_stack.py); without B's event A
        # would receive nothing and the prune would have nothing it may delete.
        [_, made, _push] = b.run([{"op": "login", "email": boot["email"], "password": boot["password"]},
                                  {"op": "create_product", "name": "B Product", "sku": f"STALL-B-{uuid.uuid4().hex[:6]}",
                                   "price": 3.0}, {"op": "push"}])
        assert made["status_code"] == 200, made
        a.run([{"op": "pull"}, {"op": "reg_pull"}])

        # 4. B pulls RETAIL ONLY: retail cursor at the end of the log, registry stalled.
        b.run([{"op": "pull"}, {"op": "pull"}])

        yield {"app": app, "license_id": license_id, "a": a, "b": b}
    finally:
        owner.close()


def _b_cursors(shop) -> dict:
    with shop["app"].app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor

        rows = db_session.query(SyncDeviceCursor).filter(
            SyncDeviceCursor.installation_id == uuid.UUID(shop["b"].installation_id)).all()
        return {r.stream: r.last_acked_seq for r in rows}


def test_a_stalled_registry_stream_recovers_every_event_after_the_prune(stalled_shop):
    from app.models.sync import SyncEvent

    shop = stalled_shop
    app, license_id = shop["app"], shop["license_id"]

    # Owner knows B's two streams are in different places (the premise).
    cursors = _b_cursors(shop)
    assert set(cursors) == {"retail", "registry"}, cursors
    assert cursors["registry"] < cursors["retail"], cursors

    def count():
        with app.app_context():
            from app.extensions import db_session
            return db_session.execute(select(func.count()).select_from(SyncEvent).where(
                SyncEvent.license_id == license_id)).scalar_one()

    before = count()
    report = _prune_exactly_as_production_does(app)
    after = count()
    if not (report.get("dry_run") is False and after < before):
        pytest.fail(f"the production prune deleted nothing ({before} -> {after}, report {report}); "
                    "the scenario did not happen, so nothing below would mean anything")

    # The prune stopped at B's STALLED stream, not at B's retail stream.
    surviving = _owner_rows(app, [SyncEvent.license_id == license_id])
    assert min(s.seq for s in surviving) > cursors["registry"], (surviving, cursors)
    assert any(s.entity_type == "user" and s.seq > cursors["registry"] for s in surviving), surviving

    # 6. B finally runs the registry stream: refused would be the lost-history bug.
    [reg_pull, users] = shop["b"].run([{"op": "reg_pull"}, {"op": "reg_select", "sql": "SELECT email FROM users"}])
    assert reg_pull["ok"] is True and not reg_pull.get("resync_required"), (
        f"B's registry stream was refused after the prune ({reg_pull}): the prune deleted events it had not applied")
    held = {r["email"] for r in users["rows"]}
    assert set(_INVITEES_FIRST + _INVITEES_LATER) <= held, (held, "B's registry stream lost events to the prune")
