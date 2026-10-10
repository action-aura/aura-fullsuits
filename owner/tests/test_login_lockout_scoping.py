"""Login lockout scoping (live-deploy audit 2026-10-10, finding L1).

The old lock keyed on the email alone, so anyone who knew the super admin's
address could lock them out from anywhere. It is now three locks: the
(email, IP) pair, the email across all IPs (50/hour) and the IP across all
emails (30/15 min).

Why the assertions look the way they do (ENGINEERING 1): every "locked" check
logs in with the CORRECT password, because the lock is checked before the
password -- a 401 with the right password can only come from the guard. Every
"locked" check is paired with a positive control (one step below the
threshold, or another source, still logs in -- 302), so a test that passes
because the whole login is broken cannot hide.
"""
from __future__ import annotations

import re

from app.extensions import db_session
from app.security.ratelimit import record_attempt, record_reset_request
from tests.conftest import get_csrf, make_staff

PW = "Sup3r-Str0ng-Pass!"
BAD = "wrong-password-1A!"
LOCKED_MSG = "Too many attempts -- try again later."


def _login(client, email, ip, password=PW):
    csrf = get_csrf(client.get("/auth/login", environ_base={"REMOTE_ADDR": ip}).get_data(as_text=True))
    return client.post(
        "/auth/login", data={"csrf_token": csrf, "email": email, "password": password},
        environ_base={"REMOTE_ADDR": ip},
    )


def _fail(app, email, ip, n=1):
    with app.app_context():
        for _ in range(n):
            record_attempt(email, ip, success=False, reason="invalid_credentials")


def _shape(resp):
    """(status, body) with the per-session CSRF token removed -- it is the only
    legitimately varying byte in the page."""
    return resp.status_code, re.sub(rb'value="[^"]*"', b'value=""', resp.data)


def test_failures_from_one_ip_do_not_lock_the_admin_out_from_another(app, client, seeded):
    make_staff(app, "admin@x.com")
    for _ in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        assert _login(client, "admin@x.com", "1.1.1.1", BAD).status_code == 401
    attacker = _login(client, "admin@x.com", "1.1.1.1")           # right password, still refused
    assert attacker.status_code == 401 and LOCKED_MSG.encode() in attacker.data
    # the DoS is gone: the real admin, on another address, gets in
    assert _login(app.test_client(), "admin@x.com", "2.2.2.2").status_code == 302


def test_fifty_failures_spread_over_fifty_ips_lock_that_email_everywhere(app, client, seeded):
    make_staff(app, "admin@x.com")
    for i in range(49):
        _fail(app, "admin@x.com", f"10.0.{i}.1")
    assert _login(client, "admin@x.com", "9.9.9.9").status_code == 302     # 49: still fine
    _fail(app, "admin@x.com", "10.0.99.1")                                  # the 50th
    locked = _login(app.test_client(), "admin@x.com", "8.8.8.8")
    assert locked.status_code == 401 and LOCKED_MSG.encode() in locked.data


def test_thirty_failures_from_one_ip_across_emails_lock_that_ip(app, client, seeded):
    make_staff(app, "admin@x.com")
    for i in range(29):
        _fail(app, f"victim{i}@x.com", "7.7.7.7")
    assert _login(client, "admin@x.com", "7.7.7.7").status_code == 302     # 29: still fine
    _fail(app, "victim29@x.com", "7.7.7.7")                                 # the 30th
    locked = _login(app.test_client(), "admin@x.com", "7.7.7.7")
    assert locked.status_code == 401 and LOCKED_MSG.encode() in locked.data
    # only that IP is refused
    assert _login(app.test_client(), "admin@x.com", "6.6.6.6").status_code == 302


def test_the_locked_response_is_byte_identical_for_all_three_lock_kinds(app, seeded):
    make_staff(app, "admin@x.com")
    # kind 1: pair
    _fail(app, "admin@x.com", "1.1.1.1", app.config["LOGIN_MAX_ATTEMPTS"])
    # kind 2: email across IPs (different IP each, so no pair is locked)
    make_staff(app, "wide@x.com")
    for i in range(50):
        _fail(app, "wide@x.com", f"10.1.{i}.1")
    # kind 3: IP across emails
    make_staff(app, "other@x.com")
    for i in range(30):
        _fail(app, f"v{i}@x.com", "3.3.3.3")
    pair = _login(app.test_client(), "admin@x.com", "1.1.1.1")
    email_wide = _login(app.test_client(), "wide@x.com", "4.4.4.4")
    ip_wide = _login(app.test_client(), "other@x.com", "3.3.3.3")
    assert _shape(pair)[0] == 401 and LOCKED_MSG.encode() in pair.data
    # `pair` is exactly today's locked response (same route, same message);
    # the new kinds must not differ from it by a single byte.
    assert _shape(pair) == _shape(email_wide) == _shape(ip_wide)


def test_a_success_from_one_ip_clears_only_that_pairs_counter(app, client, seeded):
    make_staff(app, "admin@x.com")
    n = app.config["LOGIN_MAX_ATTEMPTS"]
    _fail(app, "admin@x.com", "5.5.5.5", n)         # B: locked
    _fail(app, "admin@x.com", "4.4.4.4", n)         # A: locked...
    with app.app_context():
        record_attempt("admin@x.com", "4.4.4.4", success=True)   # ...then A succeeds, newer than B's failures
    assert _login(client, "admin@x.com", "4.4.4.4").status_code == 302          # A cleared
    still = _login(app.test_client(), "admin@x.com", "5.5.5.5")
    assert still.status_code == 401 and LOCKED_MSG.encode() in still.data      # B untouched


def test_password_reset_requests_never_count_toward_the_ip_lock(app, client, seeded):
    """The forgot-password throttle stores success=False rows with an ip_address
    in the same table; they must not lock an IP out of logging in."""
    make_staff(app, "admin@x.com")
    with app.app_context():
        for i in range(40):
            record_reset_request(f"someone{i}@x.com", "7.7.7.7")
    assert _login(client, "admin@x.com", "7.7.7.7").status_code == 302
