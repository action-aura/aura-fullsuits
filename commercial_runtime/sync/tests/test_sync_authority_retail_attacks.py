"""AUDIT-033 / S-AUTH fix round -- the verifier's retail-stream attacks, ported verbatim.

R1 (an insider posing as the main allocates a till) and R2 (an insider
acknowledges another till's release by speaking as it) are the retail-stream
faces of verifier finding #1. Each asserts the SECURE outcome, so a failing test
means the attack works: RED on fafaf7cc, GREEN once the signer's Owner lease must
name the registry device id the event claims. Driven through `apply_pull_result`
(the production pull entry point) and only helpers that existed at fafaf7cc.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority_retail_attacks.py -q
"""
from __future__ import annotations

import base64

import pytest

from commercial_runtime.sync import authority
from commercial_runtime.sync.tests.test_sync_authority_retail import Shop, _till_ev, _ack_ev


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return Shop(tmp_path, monkeypatch)


def impersonator(attacker, as_dev):
    key = attacker.key
    return authority.LocalSigner(
        licence=attacker.signer.licence, inst=attacker.inst, dev=as_dev,
        pk_b64=attacker.signer.pk_b64, lease=attacker.signer.lease,
        sign_fn=lambda data: base64.b64encode(key.sign(data)).decode("ascii"))


def test_R1_insider_posing_as_main_cannot_allocate_a_till(shop):
    ev = authority.sign_event(_till_ev(alloc_id="al-evil", terminal="term-insider"),
                              impersonator(shop.A, shop.M.dev))
    shop.pull([ev])
    assert shop.rows("SELECT id FROM till_allocations") == [], \
        f"till allocated by an insider posing as the main (parked={shop.parked()})"


def test_R2_insider_cannot_ack_another_tills_release_by_speaking_as_it(shop):
    """The victim till (term-B) has not had a signed event verified here yet
    (e.g. a phone whose Keystore signing is not live, or an old build)."""
    shop.pull([shop.world.sign(_till_ev(alloc_id="al-b", terminal="term-B", status="released"), shop.M)])
    assert [r["id"] for r in shop.rows("SELECT id FROM till_allocations")] == ["al-b"]
    ack = authority.sign_event(_ack_ev("al-b"), impersonator(shop.A, "term-B"))
    shop.pull([ack])
    acks = shop.rows("SELECT skey FROM retail_settings WHERE skey LIKE 'till_release_ack:%'")
    assert acks == [], f"release confirmed for someone else's till: {acks}"
