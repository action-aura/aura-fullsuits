"""Lead discovery -- the spending cap (Step 0, 2026-10-25).

Every Leads Finder search is one billed Google Places Text Search request.
These tests prove the cap that sits in front of it, against the real
Postgres test database and with Google faked at the transport (the one
function that would open a socket), so a test can COUNT the calls Google
would have billed rather than only look at an outcome:

* the (N+1)th search of a Jordan day / month is refused BEFORE the transport
  is called (call count stays N);
* concurrent searches on separate threads and DB connections never exceed
  the cap (the counter is an atomic conditional upsert, not read-then-write);
* the day resets at midnight in Amman (21:00 UTC), not at UTC midnight;
  the month on the 1st in Amman;
* the nightly `commercial housekeeping` purge of old rate-limit rows cannot
  reset a month's count halfway through the month;
* reaching the cap writes ONE audit row per Jordan day, not one per click;
* the Leads Finder screen shows what is left, in Arabic too;
* the API key never reaches a log line, a response, the audit log or the
  counter table.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import date, datetime, timezone

import pytest

from app.leads import discovery as discovery_module
from app.leads.discovery import LeadDiscoveryError, build_provider
from tests.conftest import force_login, get_csrf, make_staff

SECRET_KEY = "AIzaSy-THIS-IS-A-FAKE-KEY-THAT-MUST-NEVER-LEAK"

OCT_25_NOON_AMMAN = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)  # 12:00 Amman


def _config(daily=3, monthly=1000, key=SECRET_KEY, provider="google_places"):
    return {
        "LEAD_DISCOVERY_PROVIDER": provider,
        "GOOGLE_PLACES_API_KEY": key,
        "LEAD_DISCOVERY_TIMEOUT_SECONDS": 5.0,
        "LEAD_DISCOVERY_DAILY_CAP": daily,
        "LEAD_DISCOVERY_MONTHLY_CAP": monthly,
    }


class CountingTransport:
    """Stands in for discovery._urllib_http_post -- the only code that would
    reach Google. Counts every call (thread-safe) so a test can assert "no
    billed request happened", not merely "an error was shown"."""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, url, headers, body, timeout):
        with self._lock:
            self.calls += 1
        payload = {"places": [{"id": "places/p1", "displayName": {"text": "Acme"}, "internationalPhoneNumber": "+962700000001"}]}
        return 200, json.dumps(payload).encode("utf-8")


@pytest.fixture()
def transport(monkeypatch):
    fake = CountingTransport()
    # build_provider() resolves the default transport at construction time
    # (`http_post or _urllib_http_post`), so patching the module global is
    # what every real code path -- including the route -- picks up.
    monkeypatch.setattr(discovery_module, "_urllib_http_post", fake)
    return fake


def _pin_clock(monkeypatch, moment: datetime):
    monkeypatch.setattr("app.leads.discovery_budget.utcnow", lambda: moment)


def _search(config):
    return build_provider(config).search("pharmacies", "Irbid, Jordan")


def _counter_rows():
    from sqlalchemy import select

    from app.extensions import db_session
    from app.models.licensing_service import RateLimitCounter

    db_session.rollback()  # read what is committed, not this session's snapshot
    rows = db_session.execute(select(RateLimitCounter)).scalars().all()
    return {row.bucket_key: row.count for row in rows}


def _cap_audit_rows():
    from sqlalchemy import select

    from app.extensions import db_session
    from app.models.audit import AuditLog

    db_session.rollback()
    return db_session.execute(
        select(AuditLog).where(AuditLog.action_code == "LEAD_DISCOVERY_CAP_REACHED")
    ).scalars().all()


# -- 1. the cap refuses the (N+1)th search before any HTTP call ----------------

def test_daily_cap_refuses_the_next_search_without_calling_google(app, transport, monkeypatch):
    _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)
    with app.app_context():
        config = _config(daily=3)
        for _ in range(3):
            assert len(_search(config)) == 1
        assert transport.calls == 3

        with pytest.raises(LeadDiscoveryError) as exc_info:
            _search(config)
        assert exc_info.value.code == "DAILY_CAP_REACHED"
        # THE assertion: the refused search never reached the transport.
        assert transport.calls == 3
        assert _counter_rows()["lead_discovery:day:2026-10-25"] == 3


def test_monthly_cap_refuses_even_with_daily_room_and_does_not_burn_a_daily_slot(app, transport, monkeypatch):
    _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)
    with app.app_context():
        config = _config(daily=50, monthly=2)
        _search(config)
        _search(config)
        with pytest.raises(LeadDiscoveryError) as exc_info:
            _search(config)
        assert exc_info.value.code == "MONTHLY_CAP_REACHED"
        assert transport.calls == 2
        counts = _counter_rows()
        assert counts["lead_discovery:month:2026-10"] == 2
        # The day increment taken before the month refused is rolled back --
        # a refused search must not eat into today's budget either.
        assert counts["lead_discovery:day:2026-10-25"] == 2


def test_an_invalid_query_costs_no_budget(app, transport, monkeypatch):
    _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)
    with app.app_context():
        with pytest.raises(LeadDiscoveryError) as exc_info:
            build_provider(_config(daily=1)).search("", "Irbid")
        assert exc_info.value.code == "INVALID_QUERY"
        assert transport.calls == 0
        assert _counter_rows() == {}


def test_a_lowered_cap_takes_effect_on_the_next_search(app, transport, monkeypatch):
    """An admin who lowers the cap below today's count mid-day must stop
    searches at once -- the conditional update compares against the cap in
    force now, not the one the row was created under."""
    _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)
    with app.app_context():
        for _ in range(3):
            _search(_config(daily=10))
        with pytest.raises(LeadDiscoveryError):
            _search(_config(daily=2))
        assert transport.calls == 3


# -- 2. concurrent callers cannot exceed the cap --------------------------------

def test_concurrent_searches_never_exceed_the_cap(app, transport, monkeypatch):
    """Twelve threads, each on its own scoped session (= its own pooled
    Postgres connection), released together by a barrier against a cap of
    five. A read-then-write counter lets several threads all see "4 < 5" and
    all pass; the conditional upsert serializes them on the row lock."""
    from app.extensions import db_session

    _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)
    threads_n, cap = 12, 5
    config = _config(daily=cap)
    barrier = threading.Barrier(threads_n)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def worker():
        with app.app_context():
            try:
                barrier.wait(timeout=30)
                _search(config)
                result = "ok"
            except LeadDiscoveryError as exc:
                result = exc.code
            except Exception as exc:  # noqa: BLE001 -- surfaced in the assertion below
                result = f"error:{type(exc).__name__}:{exc}"
            finally:
                db_session.remove()
            with outcomes_lock:
                outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert sorted(outcomes) == sorted(["ok"] * cap + ["DAILY_CAP_REACHED"] * (threads_n - cap)), outcomes
    assert transport.calls == cap
    with app.app_context():
        assert _counter_rows()["lead_discovery:day:2026-10-25"] == cap
        # Seven threads were refused at once; still exactly one audit row.
        assert len(_cap_audit_rows()) == 1


# -- 3. reset at Jordan midnight, not UTC midnight -----------------------------

def test_day_resets_at_amman_midnight_which_is_21_00_utc(app, transport, monkeypatch):
    with app.app_context():
        config = _config(daily=2)
        _pin_clock(monkeypatch, datetime(2026, 10, 25, 20, 59, 59, tzinfo=timezone.utc))  # 23:59:59 Amman
        _search(config)
        _search(config)
        with pytest.raises(LeadDiscoveryError):
            _search(config)

        _pin_clock(monkeypatch, datetime(2026, 10, 25, 21, 0, 0, tzinfo=timezone.utc))  # 00:00:00 Amman, Oct 26
        assert len(_search(config)) == 1
        assert transport.calls == 3
        counts = _counter_rows()
        assert counts["lead_discovery:day:2026-10-25"] == 2
        assert counts["lead_discovery:day:2026-10-26"] == 1


def test_day_does_not_reset_at_utc_midnight(app, transport, monkeypatch):
    with app.app_context():
        config = _config(daily=1)
        _pin_clock(monkeypatch, datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc))  # 01:00 Amman, Oct 26
        _search(config)
        _pin_clock(monkeypatch, datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc))   # new UTC date, 04:00 Amman, still Oct 26
        with pytest.raises(LeadDiscoveryError) as exc_info:
            _search(config)
        assert exc_info.value.code == "DAILY_CAP_REACHED"
        assert transport.calls == 1


def test_month_resets_on_the_first_in_amman(app, transport, monkeypatch):
    with app.app_context():
        config = _config(daily=50, monthly=1)
        _pin_clock(monkeypatch, datetime(2026, 10, 31, 20, 59, 59, tzinfo=timezone.utc))  # Oct 31 23:59:59 Amman
        _search(config)
        with pytest.raises(LeadDiscoveryError) as exc_info:
            _search(config)
        assert exc_info.value.code == "MONTHLY_CAP_REACHED"

        _pin_clock(monkeypatch, datetime(2026, 10, 31, 21, 0, 0, tzinfo=timezone.utc))  # Nov 1 00:00 Amman
        assert len(_search(config)) == 1
        assert _counter_rows()["lead_discovery:month:2026-11"] == 1
        assert transport.calls == 2


def test_housekeeping_purge_cannot_reset_the_month_mid_month(app, transport, monkeypatch):
    """`commercial housekeeping --apply` runs every night on the live server
    and deletes rate-limit rows whose window_start is more than a day old.
    A month counter stamped with the month's START would be deleted on the
    2nd and the month's budget would silently start again every night."""
    from datetime import timedelta

    from app.licensing_service import ratelimit

    with app.app_context():
        config = _config(daily=50, monthly=2)
        first = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
        _pin_clock(monkeypatch, first)
        _search(config)
        _search(config)

        later = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
        ratelimit.purge_old_counters(older_than=later - timedelta(days=1))  # exactly what housekeeping passes

        _pin_clock(monkeypatch, later)
        with pytest.raises(LeadDiscoveryError) as exc_info:
            _search(config)
        assert exc_info.value.code == "MONTHLY_CAP_REACHED"
        assert transport.calls == 2


# -- 4. one audit row per Jordan day when the cap is reached -------------------

def test_cap_reached_is_audited_once_per_jordan_day(app, transport, monkeypatch):
    with app.app_context():
        config = _config(daily=1)
        _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)
        _search(config)
        for _ in range(3):
            with pytest.raises(LeadDiscoveryError):
                _search(config)
        rows = _cap_audit_rows()
        assert len(rows) == 1
        assert rows[0].result == "REJECTED"
        assert rows[0].after_state_redacted["cap"] == "DAILY"
        assert rows[0].after_state_redacted["limit"] == 1

        _pin_clock(monkeypatch, datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc))
        _search(config)
        with pytest.raises(LeadDiscoveryError):
            _search(config)
        assert len(_cap_audit_rows()) == 2


# -- 5. the kill switch --------------------------------------------------------

@pytest.mark.parametrize(
    "config",
    [
        _config(provider=""),                 # key set, flag not set
        _config(key=""),                      # flag set, no key
        _config(daily=0),                     # a zero cap switches it off
        _config(monthly=0),
        _config(daily=-5),
        _config(daily="lots"),                # garbage never means "unlimited"
        _config(daily=True),                  # bool is an int in Python; never a cap
    ],
)
def test_discovery_is_off_and_calls_nothing_unless_flag_key_and_caps_are_all_valid(app, transport, config):
    from app.leads.discovery import is_discovery_enabled

    with app.app_context():
        assert is_discovery_enabled(config) is False
        with pytest.raises(LeadDiscoveryError) as exc_info:
            _search(config)
        assert exc_info.value.code == "NOT_CONFIGURED"
        assert transport.calls == 0


def test_missing_cap_settings_fall_back_to_the_safe_defaults_never_unlimited(app, transport, monkeypatch):
    from app.leads.discovery import DEFAULT_DAILY_CAP, DEFAULT_MONTHLY_CAP, discovery_caps

    config = {"LEAD_DISCOVERY_PROVIDER": "google_places", "GOOGLE_PLACES_API_KEY": SECRET_KEY}
    assert discovery_caps(config) == (DEFAULT_DAILY_CAP, DEFAULT_MONTHLY_CAP) == (100, 1000)


def test_shipped_config_defaults_are_the_low_caps(app):
    from app.leads.discovery import discovery_caps

    assert discovery_caps(app.config) == (100, 1000)
    # Blank in an env file (a copied example left empty) is the default too.
    assert discovery_caps({"LEAD_DISCOVERY_DAILY_CAP": " ", "LEAD_DISCOVERY_MONTHLY_CAP": ""}) == (100, 1000)
    assert discovery_caps({"LEAD_DISCOVERY_DAILY_CAP": "2.5"}) is None
    assert discovery_caps({"LEAD_DISCOVERY_DAILY_CAP": "7", "LEAD_DISCOVERY_MONTHLY_CAP": "70"}) == (7, 70)


# -- 6. the screen: remaining budget, refusal message (English + Arabic) -------

def _sales_with_profile(app, client, email):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": f"EMP-{uuid.uuid4().hex[:6]}",
             "full_name": "Budget Tester", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_id,
        )
    force_login(client, app, staff_id)
    return staff_id


def _enable(app, daily, monthly=1000):
    app.config.update(_config(daily=daily, monthly=monthly))


def _post_search(client):
    csrf = get_csrf(client.get("/leads/discover").get_data(as_text=True))
    return client.post("/leads/discover/search", data={"csrf_token": csrf, "segment": "pharmacies", "area": "Irbid"})


def test_screen_shows_remaining_budget_and_refuses_at_the_cap_with_429(app, client, seeded, transport, monkeypatch):
    _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)
    _sales_with_profile(app, client, "budget1@example.com")
    _enable(app, daily=2, monthly=1000)

    page = client.get("/leads/discover").get_data(as_text=True)
    assert "Searches left today: 2 of 2" in page
    assert "this month: 1000 of 1000" in page

    assert _post_search(client).status_code == 200
    second = _post_search(client)
    assert second.status_code == 200
    assert "Searches left today: 0 of 2" in second.get_data(as_text=True)

    refused = _post_search(client)
    assert refused.status_code == 429
    body = refused.get_data(as_text=True)
    # (the apostrophe in "Today's" is HTML-escaped, so match the rest)
    assert "limit of map searches has been reached. It resets at midnight, Jordan time." in body
    assert transport.calls == 2
    # Button is disabled once nothing is left, so the next click is not even possible.
    assert re.search(r'<button type="submit"[^>]*disabled', body)


def test_monthly_refusal_message_and_arabic_translation(app, client, seeded, transport, monkeypatch):
    _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)
    _sales_with_profile(app, client, "budget2@example.com")
    _enable(app, daily=50, monthly=1)

    assert _post_search(client).status_code == 200
    refused = _post_search(client)
    assert refused.status_code == 429
    assert "It resets on the 1st of next month, Jordan time." in refused.get_data(as_text=True)

    client.get("/locale/ar?next=/leads/discover")
    refused_ar = _post_search(client)
    assert refused_ar.status_code == 429
    body_ar = refused_ar.get_data(as_text=True)
    assert "It resets on the 1st of next month" not in body_ar
    assert "تم بلوغ حد بحث الخرائط لهذا الشهر" in body_ar
    assert "المتبقي من عمليات البحث اليوم" in body_ar
    assert transport.calls == 1


# -- 7. the key never leaks ----------------------------------------------------

def test_api_key_never_reaches_logs_responses_audit_or_counters(app, client, seeded, monkeypatch, caplog):
    from sqlalchemy import select

    from app.extensions import db_session
    from app.models.audit import AuditLog

    _pin_clock(monkeypatch, OCT_25_NOON_AMMAN)

    def echoing_transport(url, headers, body, timeout):
        # A hostile/verbose transport failure that quotes the request back.
        raise ConnectionError(f"proxy refused request with X-Goog-Api-Key={headers['X-Goog-Api-Key']}")

    monkeypatch.setattr(discovery_module, "_urllib_http_post", echoing_transport)
    _sales_with_profile(app, client, "budget3@example.com")
    _enable(app, daily=1)

    caplog.set_level(logging.DEBUG)
    failed = _post_search(client)          # transport error, slot spent
    refused = _post_search(client)         # cap reached, audited
    form = client.get("/leads/discover")

    for resp in (failed, refused, form):
        assert SECRET_KEY not in resp.get_data(as_text=True)
    assert failed.status_code == 502
    assert refused.status_code == 429
    assert SECRET_KEY not in caplog.text
    with app.app_context():
        db_session.rollback()
        for row in db_session.execute(select(AuditLog)).scalars().all():
            assert SECRET_KEY not in json.dumps(
                [row.after_state_redacted, row.before_state_redacted, row.reason, row.entity_public_id], default=str
            )
        assert all(SECRET_KEY not in key for key in _counter_rows())

