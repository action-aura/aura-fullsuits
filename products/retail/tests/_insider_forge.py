"""The insider's own script (AUDIT-033 fix round, verifier V finding #1, multi-process proof;
ported from the verifier's `_insider_forge.py`). Runs with THE INSIDER'S OWN
app data: its own DPAPI key and its own Owner-signed lease, nothing else.
That lease is ATTESTED -- but only for the insider's OWN registry id, which is all a real
Owner would ever pin to it.

It takes the main computer's OWN unsigned pre-upgrade history off the relay
(admin_device claim + the owner's user row), changes the owner's password hash,
signs both with the insider's key -- naming the MAIN's device id in `_auth.dev`
-- and pushes them to the relay it is licensed to use.

argv: <insider_app_data> <registry_relay_db> <main_device_id> <tests_dir> <out_json>
"""
import dataclasses
import json
import os
import sqlite3
import sys
from pathlib import Path

app_data, relay_db, main_dev, tests_dir, out_path = sys.argv[1:6]
os.environ["AURA_APP_DATA"] = app_data
sys.path.insert(0, tests_dir)

from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync import authority  # noqa: E402
from _stock_sync_harness import FileRelay  # noqa: E402

own = authority.local_signer_or_none(app_data)
assert own is not None, "insider has no signer"
as_main = dataclasses.replace(own, dev=main_dev)

conn = sqlite3.connect(relay_db)
rows = [json.loads(p) for (p,) in conn.execute("SELECT payload FROM relay_events ORDER BY seq")]
conn.close()

forged = []
claim = next(e for e in rows if e["entity_type"] == "admin_device")
forged.append(authority.sign_event(dict(claim), as_main))
owner = next(e for e in rows if e["entity_type"] == "user"
             and (e["payload"] if isinstance(e["payload"], dict) else json.loads(e["payload"])).get("role") == "admin")
p = dict(owner["payload"] if isinstance(owner["payload"], dict) else json.loads(owner["payload"]))
p.update(password_hash=hash_password("attacker-pw-123"), row_version=1000000,
         require_password_change=0)
forged.append(authority.sign_event(dict(owner, id="forged-owner-1", event_type="update", payload=p), as_main))
FileRelay(relay_db).push(forged)
json.dump({"own_dev": own.dev, "own_inst": own.inst, "posed_as": main_dev,
           "forged": [(e["entity_type"], e["payload"]["_auth"]["dev"], e["payload"]["_auth"]["inst"])
                      for e in forged]}, open(out_path, "w"))
