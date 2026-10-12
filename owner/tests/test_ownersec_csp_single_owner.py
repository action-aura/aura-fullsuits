"""OWNER-SEC item 4 (review D SEC-05): the app's own CSP must be the only one.
Caddy's `header` block replaces the upstream header, and its weaker CSP broke
the MFA enrolment QR (an <img src="data:...">). Two guards: the proxy config
must not set CSP/Referrer-Policy, and the app's CSP on the real enrolment page
must allow data: images and carry form-action/base-uri."""
from __future__ import annotations

import pathlib
import re

from tests.conftest import get_csrf, make_staff

OWNER_DIR = pathlib.Path(__file__).resolve().parent.parent


def test_no_owner_caddyfile_overrides_csp_or_referrer_policy():
    caddyfiles = list((OWNER_DIR / "deploy").rglob("Caddyfile*"))
    assert caddyfiles, "expected at least the oracle-free Caddyfile"
    for path in caddyfiles:
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0].strip()
            assert not re.match(r"(?i)(content-security-policy|referrer-policy)\b", code), (path.name, line)


def test_enrolment_page_csp_allows_the_qr_data_image(app, client, seeded):
    make_staff(app, "csp-enrol@example.com", password="Correct-Password-1!", super_admin=True)  # mfa_required, not enrolled
    page = client.get("/auth/login")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post("/auth/login", data={"csrf_token": csrf, "email": "csp-enrol@example.com", "password": "Correct-Password-1!"})
    assert "/auth/mfa-enroll" in resp.headers["Location"]
    enrol = client.get("/auth/mfa-enroll")
    assert enrol.status_code == 200
    csp = enrol.headers["Content-Security-Policy"]
    img_src = re.search(r"img-src([^;]*)", csp).group(1)
    assert "data:" in img_src
    assert "form-action 'self'" in csp and "base-uri 'self'" in csp
    # The page really does embed the QR as a data: image (what the CSP must permit).
    assert 'src="data:image/svg+xml' in enrol.get_data(as_text=True)
