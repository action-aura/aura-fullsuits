"""Owner -> product, for real: the signed lease Owner issues for a trial is fed to
the PRODUCT's own evaluator (commercial_runtime in the feat/trial-lock worktree,
read-only), and the product must lock the till when the trial is over and unlock it
when the lease after payment arrives.

Why this exists: Owner can have every field "right" and still not lock anything, if
what it puts in the lease (term_end, subscription_status) is not what the product
reads. Each side's own tests pass on their own; only this one runs the product's
verifier + evaluator on Owner's actual signed bytes.

The product code is imported in a SUBPROCESS rooted at the trial-lock worktree, so
this file's own interpreter keeps Owner's modules and nothing of the product's is
imported into it. Skipped (loudly) when that worktree is not present on this machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.conftest import build_checkin_body, jordan_today
from tests.test_customer_onboarding import _world, kyc, onboard  # noqa: F401 -- kyc is a fixture
from tests.test_trial_conversion import _activate, confirm, sell

PRODUCT_ROOT = os.environ.get(
    "AURA_TRIAL_LOCK_WORKTREE",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "trial-lock")),
)

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(PRODUCT_ROOT, "commercial_runtime", "licensing_contracts", "policy_evaluator.py")),
    reason=f"product worktree with the trial lock not found at {PRODUCT_ROOT} (set AURA_TRIAL_LOCK_WORKTREE)",
)

SCRIPT = textwrap.dedent('''
    import json, sys, tempfile
    from datetime import datetime
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from commercial_runtime.licensing_contracts.assertion_verifier import verify_assertion
    from commercial_runtime.licensing_contracts.policy_evaluator import evaluate
    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
    from commercial_runtime.licensing_contracts.trusted_time import new_anchor

    case = json.load(sys.stdin)
    store = OwnerTrustStore(Path(tempfile.mkdtemp()) / "trust.json")
    store.bootstrap_from_anchor({"keys": [{"key_id": case["key_id"], "public_key": case["public_key"], "algorithm": "ed25519"}]})
    real_now = datetime.fromisoformat(case["verify_at"])
    verified = verify_assertion(
        case["envelope"], trust_store=store, expected_product_code="AURA_RETAIL", expected_platform="WINDOWS",
        expected_installation_id=case["installation_id"], expected_device_key_fingerprint=case["fingerprint"],
        trusted_now=real_now,
    )
    at = datetime.fromisoformat(case["evaluate_at"])
    state = evaluate(evidence=verified.evidence, anchor=new_anchor(at), local_wall_clock_now=at,
                     last_successful_checkin_at=at, last_checkin_attempt_ok=True)
    print(json.dumps({"state": state.name, "term_end": str(verified.evidence.term_end),
                      "status": verified.evidence.subscription_status}))
''')


def product_state(app, envelope, *, installation_id, key_pair, evaluate_at):
    from app.licensing_service import device_identity
    from app.extensions import db_session
    from app.models.licensing_service import SigningKey
    from tests.conftest import public_key_b64

    with app.app_context():
        key = db_session.execute(select(SigningKey).where(SigningKey.status == "ACTIVE")).scalars().one()
        case = {
            "envelope": envelope, "key_id": key.key_id, "public_key": key.public_key, "installation_id": installation_id,
            "fingerprint": device_identity.fingerprint_of(public_key_b64(key_pair)),
            "verify_at": datetime.now(timezone.utc).isoformat(), "evaluate_at": evaluate_at.isoformat(),
        }
    done = subprocess.run([sys.executable, "-c", SCRIPT, PRODUCT_ROOT], input=json.dumps(case), capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_product_locks_an_ended_trial_and_unlocks_it_when_the_paid_lease_arrives(app, client, seeded, kyc, signing_key):
    w = _world(app)
    trial = onboard(app, w)
    activated, key_pair = _activate(client, trial.license_key)
    lease_trial = activated["signed_assertion"]
    installation_id = activated["installation_id"]
    amman = timezone(timedelta(hours=3))
    day = jordan_today()
    term_end = day + timedelta(days=6)  # a 7-day trial: day D .. D+6, the end day counts
    during = datetime.combine(day + timedelta(days=3), datetime.min.time(), tzinfo=amman) + timedelta(hours=12)
    last_second = datetime.combine(term_end, datetime.min.time(), tzinfo=amman) + timedelta(hours=23, minutes=59)
    after = datetime.combine(term_end + timedelta(days=1), datetime.min.time(), tzinfo=amman) + timedelta(minutes=1)

    states = {name: product_state(app, lease_trial, installation_id=installation_id, key_pair=key_pair, evaluate_at=at)
              for name, at in (("during", during), ("last_second", last_second), ("after", after))}
    # Owner's lease carries the date the product reads...
    assert states["during"]["term_end"] == term_end.isoformat() and states["during"]["status"] == "PILOT"
    # ...and the product sells through the whole last day, then locks.
    assert states["during"]["state"] != "TRIAL_ENDED" and states["last_second"]["state"] != "TRIAL_ENDED"
    assert states["after"]["state"] == "TRIAL_ENDED"

    # The customer pays; a manager confirms; the shop's next check-in carries the paid lease.
    confirm(app, w, sell(app, w, trial))
    body = build_checkin_body(key_pair, installation_id=installation_id)
    resp = client.post("/api/licensing/v1/check-ins", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 200, resp.get_json()
    unlocked = product_state(app, resp.get_json()["signed_assertion"], installation_id=installation_id, key_pair=key_pair, evaluate_at=after)
    assert unlocked["status"] == "ACTIVE" and unlocked["state"] != "TRIAL_ENDED"
    # and the very same moment is still locked for an UNPAID shop (the lock was not just disabled)
    assert product_state(app, lease_trial, installation_id=installation_id, key_pair=key_pair, evaluate_at=after)["state"] == "TRIAL_ENDED"
