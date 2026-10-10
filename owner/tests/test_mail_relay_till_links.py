"""The till's mailed links must pass Owner's relay link validator (F1a).

Owner's relay only accepts links on loopback / private / link-local IPs or
'localhost'. The till builds links from request.host_url, which can be a
machine name (http://SHOP-PC:5001/); verification._till_link_base rewrites that
to http://127.0.0.1:<port>/ (plan D7). These tests run the REAL till builders
and feed what they produced to the REAL Owner validator (relay._link), so a
drift on either side turns this red.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from commercial_runtime.identity import verification  # noqa: E402

from app.mail import relay  # noqa: E402
from app.releases.distribution import DownloadAuthorizationRejected  # noqa: E402


class _Sink:
    def __init__(self):
        self.mails = []

    def enqueue(self, **kw):
        self.mails.append(kw)
        return True


@pytest.fixture()
def till(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE secure_links (id TEXT, company_id TEXT, token_hash TEXT, email_target TEXT, "
                 "expires_at TEXT, purpose TEXT, created_at TEXT, is_used INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE users (id TEXT, company_id TEXT, email TEXT, status TEXT)")
    conn.execute("INSERT INTO users VALUES ('u1','co-1','boss@shop.example','active')")
    sink = _Sink()
    monkeypatch.setattr(verification, "_mail_sink", sink)
    return conn, sink


def _link_in(mail):
    return next(w for w in mail["body_text"].split() if w.startswith("http"))


@pytest.mark.parametrize("base,expected_prefix", [
    ("http://SHOP-PC:5001/", "http://127.0.0.1:5001/"),
    ("http://shop-pc.local:5001/", "http://127.0.0.1:5001/"),
    ("http://192.168.1.20:5001/", "http://192.168.1.20:5001/"),
    ("http://localhost:5001/", "http://localhost:5001/"),
    ("http://127.0.0.1:5001/", "http://127.0.0.1:5001/"),
    ("http://8.8.8.8:5001/", "http://127.0.0.1:5001/"),
])
def test_verification_link_is_rebuilt_on_loopback_unless_already_local_and_passes_owners_validator(till, base, expected_prefix):
    conn, sink = till
    assert verification.send_verification_email(conn, company_id="co-1", user_email="boss@shop.example", base_url=base) == "queued"
    link = _link_in(sink.mails[0])
    assert link.startswith(expected_prefix) and "/#verify-email/" in link
    assert relay._link(link) == link            # the real Owner validator accepts it


@pytest.mark.parametrize("base,expected_prefix", [
    ("http://SHOP-PC:5001/", "http://127.0.0.1:5001/"),
    ("http://192.168.1.20:5001/", "http://192.168.1.20:5001/"),
    ("http://localhost:5001/", "http://localhost:5001/"),
])
def test_password_reset_link_gets_the_same_treatment(till, base, expected_prefix):
    conn, sink = till
    verification.request_password_reset(conn, email="boss@shop.example", base_url=base)
    link = _link_in(sink.mails[0])
    assert link.startswith(expected_prefix) and "/#reset-password/" in link
    assert relay._link(link) == link


def test_the_unrewritten_machine_name_link_is_what_owner_would_refuse():
    """Control: proves the rewrite is needed, i.e. the validator really does
    refuse a hostname link (so the tests above are not passing vacuously)."""
    with pytest.raises(DownloadAuthorizationRejected):
        relay._link("http://SHOP-PC:5001/#verify-email/" + "a" * 32)
