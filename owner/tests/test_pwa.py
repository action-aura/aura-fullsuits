"""Step 1 (Aura Growth Plan) -- the Owner Control Center as an installable
web app: manifest, service worker, offline page, install hint.

The tests that matter most here are the cache-safety ones. Every Owner page
and JSON route is behind a login and carries money / customer / licence data;
a service worker that cached any of it would show stale figures as live and
leave a readable copy on the phone after logout. So the worker's safety is
proven two ways, and both are mutation-proven (see the commit message):

  1. test_precache_allow_list_* -- the ONLY list the worker ever caches is
     public /static/ files plus the offline page, checked against the real
     url_map, so the list cannot quietly grow to include a page.
  2. test_the_real_worker_* -- the REAL rendered /sw.js runs in Node
     (tests/pwa_sw_harness.js) against every route this app registers, as a
     page navigation and as a script fetch, online then offline; afterwards
     the cache must hold exactly the allow-list and nothing else, and every
     response must have come from where the policy says.

Node is required for (2) and for the install-hint tests, as it is for the
product test runner; CI provisions it. A machine without node fails loudly
unless AURA_ALLOW_MISSING_NODE=1 is set on purpose (same convention as
products/retail/tests/retail_attribution_i18n_test.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from tests.conftest import force_login, login_and_verify_mfa, make_staff

TESTS_DIR = Path(__file__).resolve().parent
OWNER_DIR = TESTS_DIR.parent
STATIC_DIR = OWNER_DIR / "app" / "static"
ORIGIN = "https://owner.test"

ARABIC_RE = re.compile("[؀-ۿ]")


# ----------------------------------------------------------------- helpers

def _node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("AURA_ALLOW_MISSING_NODE") == "1":
            pytest.skip("node is not on PATH and AURA_ALLOW_MISSING_NODE=1 was set explicitly")
        pytest.fail(
            "node is not on PATH: the service-worker safety tests run the real worker in Node. Install "
            "node, or set AURA_ALLOW_MISSING_NODE=1 to skip deliberately on a machine with no node."
        )
    return node


def _run_node(script: Path, payload: dict) -> dict:
    proc = subprocess.run(
        [_node(), str(script)], input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, f"{script.name} failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _png_has_alpha_channel(data: bytes) -> bool:
    color_type = data[25]
    return color_type in (4, 6)


def _sw_config_from_source(source: str) -> dict:
    match = re.search(r"^const PWA_CONFIG = (\{.*\});$", source, re.M)
    assert match, "PWA_CONFIG line not found in the served worker"
    return json.loads(match.group(1))


def _sample_path(rule: str) -> str:
    def repl(m):
        converter = (m.group(1) or "default").lower()
        return {
            "int": "1",
            "uuid": "00000000-0000-0000-0000-000000000001",
            "path": "a/b",
        }.get(converter, "sample")

    return re.sub(r"<(?:(\w+)(?:\([^)]*\))?:)?\w+>", repl, rule)


def _logged_in_super_admin(app, client):
    staff_id = make_staff(app, "pwa-super@example.com", super_admin=True)
    force_login(client, app, staff_id)
    return staff_id


# ---------------------------------------------------------------- manifest

def test_manifest_is_served_as_a_web_app_manifest_with_every_install_field(client):
    resp = client.get("/manifest.webmanifest?lang=en")
    assert resp.status_code == 200
    assert resp.mimetype == "application/manifest+json"
    assert resp.headers["Cache-Control"] == "no-cache"
    data = json.loads(resp.get_data(as_text=True))

    assert data["id"] == "/"
    assert data["name"] == "Aura Owner Control Center"
    assert data["short_name"] == "Aura Owner"
    assert data["start_url"] == "/"  # the dashboard (dashboard.index)
    assert data["scope"] == "/"
    assert data["display"] == "standalone"
    assert data["lang"] == "en" and data["dir"] == "ltr"
    # Theme colour comes from Owner's own tokens: the top bar is
    # --aura-color-surface-raised, the page --aura-color-surface-base.
    tokens = (STATIC_DIR / "css" / "tokens.css").read_text(encoding="utf-8")
    assert f"--aura-color-surface-raised: {data['theme_color']};" in tokens
    assert f"--aura-color-surface-base: {data['background_color']};" in tokens

    by_purpose = {(i["sizes"], i["purpose"]): i for i in data["icons"]}
    assert set(by_purpose) == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}
    for (sizes, _purpose), icon in by_purpose.items():
        icon_resp = client.get(icon["src"])
        assert icon_resp.status_code == 200, icon["src"]
        assert icon_resp.mimetype == "image/png"
        width, height = _png_size(icon_resp.get_data())
        assert f"{width}x{height}" == sizes, f"{icon['src']} is {width}x{height}, manifest says {sizes}"
    # A maskable icon is cropped to any shape by the launcher: it must be
    # full-bleed, with no transparent corners to show through.
    maskable = client.get(by_purpose[("512x512", "maskable")]["src"]).get_data()
    assert not _png_has_alpha_channel(maskable) or _corner_is_opaque(maskable)


def _corner_is_opaque(png: bytes) -> bool:
    import zlib

    width, height = _png_size(png)
    pos, idat = 8, b""
    while pos < len(png):
        length = struct.unpack(">I", png[pos:pos + 4])[0]
        kind = png[pos + 4:pos + 8]
        if kind == b"IDAT":
            idat += png[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    channels = 4 if png[25] == 6 else 2
    # Row 0, filter byte then pixel 0: with filter types 0/1/2/3/4 the first
    # pixel's alpha is stored raw (no left/up neighbour for pixel 0, row 0).
    first_pixel = raw[1:1 + channels]
    return first_pixel[-1] == 255


def test_manifest_in_arabic_uses_the_catalog_name_and_right_to_left(client):
    data = json.loads(client.get("/manifest.webmanifest?lang=ar").get_data(as_text=True))
    assert data["lang"] == "ar"
    assert data["dir"] == "rtl"
    # The catalog's existing Arabic product name (brand kept Latin, per the
    # translation style guide that also exempts "Aura Owner" itself).
    assert data["name"] == "مركز تحكم Aura Owner"
    assert data["short_name"] == "Aura Owner"
    assert ARABIC_RE.search(data["description"])
    # Same app identity in both languages: switching language must never look
    # like a second installed app.
    english = json.loads(client.get("/manifest.webmanifest?lang=en").get_data(as_text=True))
    assert data["id"] == english["id"] == "/"


def test_manifest_with_an_unknown_or_missing_language_falls_back_to_english(client):
    for query in ("?lang=xx", "?lang=../../etc", ""):
        data = json.loads(client.get("/manifest.webmanifest" + query).get_data(as_text=True))
        assert data["lang"] == "en", query
        assert data["name"] == "Aura Owner Control Center", query


def test_apple_touch_icon_is_an_opaque_180_square(client):
    resp = client.get("/static/img/brand/apple-touch-icon.png")
    assert resp.status_code == 200
    data = resp.get_data()
    assert _png_size(data) == (180, 180)
    assert not _png_has_alpha_channel(data) or _corner_is_opaque(data)


# ----------------------------------------------------------- service worker

def test_service_worker_is_served_from_the_root_as_javascript_and_never_http_cached(client):
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert resp.mimetype == "text/javascript"
    # Revalidated on every check, so a deploy reaches phones on the next page load.
    assert resp.headers["Cache-Control"] == "no-cache"
    # Served from "/", its natural scope is the whole site: no
    # Service-Worker-Allowed header is needed (or sent) to register scope "/".
    assert "Service-Worker-Allowed" not in resp.headers
    assert resp.headers["Content-Security-Policy"].startswith("default-src 'self'")


def test_served_worker_embeds_exactly_the_server_side_policy(app, client):
    from app.pwa.routes import CACHE_PREFIX, build_sw_config

    served = _sw_config_from_source(client.get("/sw.js").get_data(as_text=True))
    with app.test_request_context():
        expected = build_sw_config()
    assert served == expected
    assert served["cachePrefix"] == CACHE_PREFIX
    assert re.fullmatch(r"[0-9a-f]{16}", served["version"])


def test_precache_allow_list_is_only_public_static_files_and_the_offline_page(app):
    """Guard 1 of 2. The allow-list is the ONE list the worker ever caches.
    Every entry must be a real file under app/static/ (public: served to
    anyone, no login) or the offline page -- and must not resolve to any
    other route in the real url_map, so a page or JSON route can never be
    smuggled in under any name."""
    from werkzeug.exceptions import MethodNotAllowed, NotFound

    from app.pwa.routes import build_sw_config

    with app.test_request_context():
        config = build_sw_config()
    adapter = app.url_map.bind("owner.test")

    assert config["precache"].count(config["offlineUrl"]) == 1
    assert config["offlineUrl"] == "/offline"
    for path in config["precache"]:
        try:
            endpoint, _args = adapter.match(path, method="GET")
        except (NotFound, MethodNotAllowed):
            pytest.fail(f"precache entry {path!r} is not a GET route at all")
        if path == config["offlineUrl"]:
            assert endpoint == "pwa.offline"
            continue
        assert endpoint == "static", f"precache entry {path!r} is the {endpoint} route, not a static file"
        assert path.startswith("/static/"), path
        assert (STATIC_DIR / path[len("/static/"):]).is_file(), path
        assert "?" not in path and "#" not in path, path

    # And the list is the real shell, not an accidentally-empty one: the
    # stylesheets/scripts every page loads, the icons, and the offline page.
    for required in ("/static/css/tokens.css", "/static/css/shell.css", "/static/js/pwa.js",
                     "/static/img/brand/action-aura-mark.svg", "/static/img/brand/icon-192.png"):
        assert required in config["precache"], required


def test_cache_version_changes_when_a_cached_file_changes(app, monkeypatch, tmp_path):
    """No manual version bump on deploy: the version is a hash of what is cached."""
    from app.pwa import routes as pwa_routes

    asset = tmp_path / "a.css"
    asset.write_bytes(b"body { color: red; }")
    monkeypatch.setattr(pwa_routes, "_static_precache_files", lambda: [("/static/a.css", asset)])
    with app.test_request_context():
        before = pwa_routes.build_sw_config()["version"]
        asset.write_bytes(b"body { color: blue; }")
        after = pwa_routes.build_sw_config()["version"]
        again = pwa_routes.build_sw_config()["version"]
    assert before != after
    assert after == again


def _scenarios(app, config):
    """Every route the app registers, as the browser would hit it: a page
    navigation and a script fetch for every GET route (plus a query-string
    variant), and the real method as a form navigation for every non-GET
    route -- plus the static files themselves and a few adversarial URLs."""
    scenarios = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        path = _sample_path(rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            if method == "GET":
                for mode in ("navigate", "cors"):
                    scenarios.append({"id": f"{mode} GET {path}", "url": path, "mode": mode, "method": "GET"})
                    scenarios.append({"id": f"{mode} GET {path}?page=2", "url": path + "?page=2",
                                      "mode": mode, "method": "GET"})
            else:
                scenarios.append({"id": f"navigate {method} {path}", "url": path, "mode": "navigate", "method": method})
    # Device-facing APIs are only registered when OWNER_EXTERNAL_API_ENABLED is
    # on; the worker's answer must not depend on that.
    for path in ("/api/licensing/v1/activate", "/api/sync/v1/pull", "/api/v1/anything"):
        scenarios.append({"id": f"cors GET {path} (external api)", "url": path, "mode": "cors", "method": "GET"})
    for path in config["precache"]:
        if path == config["offlineUrl"]:
            continue
        scenarios.append({"id": f"no-cors GET {path}", "url": path, "mode": "no-cors", "method": "GET"})
        scenarios.append({"id": f"no-cors GET {path}?v=1", "url": path + "?v=1", "mode": "no-cors", "method": "GET"})
    scenarios.append({"id": "no-cors GET /static/not-precached.json", "url": "/static/not-precached.json",
                      "mode": "no-cors", "method": "GET"})
    scenarios.append({"id": "cross-origin static", "url": "https://evil.example/static/css/tokens.css",
                      "mode": "no-cors", "method": "GET"})
    scenarios.append({"id": "cors POST precached path", "url": "/static/css/tokens.css", "mode": "cors",
                      "method": "POST"})
    return scenarios


def test_the_real_worker_caches_nothing_but_the_allow_list_across_every_route(app, client, tmp_path):
    """Guard 2 of 2: behaviour, not source text. The real rendered /sw.js is
    installed, activated, then fed every route this app has -- online, then
    with the network down -- and must:
      * end with its cache holding EXACTLY the allow-list (no page, no JSON);
      * fetch the allow-list with no cookies and bypassing the HTTP cache;
      * send every page navigation to the network, and show the offline page
        only when the network fails;
      * serve allow-listed files from its cache (offline proves it);
      * not intercept anything else at all (API, POST, query strings,
        other origins);
      * delete its own older cache versions and leave other caches alone."""
    source = client.get("/sw.js").get_data(as_text=True)
    config = _sw_config_from_source(source)
    sw_path = tmp_path / "sw.js"
    sw_path.write_text(source, encoding="utf-8")
    scenarios = _scenarios(app, config)
    assert len(scenarios) > 400, "the route sweep is suspiciously small -- did url_map iteration break?"

    result = _run_node(TESTS_DIR / "pwa_sw_harness.js", {
        "swPath": str(sw_path), "origin": ORIGIN, "cachePrefix": config["cachePrefix"], "scenarios": scenarios,
    })

    cache_name = config["cachePrefix"] + config["version"]
    allow = {ORIGIN + p for p in config["precache"]}
    assert set(result["caches"]) == {cache_name, "unrelated-cache"}, (
        "activate must delete our older versions and nothing else: " + repr(sorted(result["caches"]))
    )
    cached = set(result["caches"][cache_name])
    assert cached == allow, (
        "the worker cached something outside the allow-list (an authenticated page or JSON would show up "
        f"here): extra={sorted(cached - allow)[:10]} missing={sorted(allow - cached)[:10]}"
    )
    assert result["caches"]["unrelated-cache"] == [ORIGIN + "/unrelated"]

    install = result["installRequests"]
    assert {r["url"] for r in install} == allow
    assert all(r["credentials"] == "omit" for r in install), install
    assert all(r["cache"] == "reload" for r in install), install
    assert result["skipWaiting"] and result["claimed"]

    precached_static = {p for p in config["precache"] if p != config["offlineUrl"]}
    offline_body = "NETWORK:" + config["offlineUrl"]
    for s in scenarios:
        online, offline = result["online"][s["id"]], result["offline"][s["id"]]
        is_same_origin = not s["url"].startswith("http")
        if s["method"] != "GET" or not is_same_origin:
            assert not online["intercepted"] and not offline["intercepted"], s
        elif s["mode"] == "navigate":
            assert online == {"intercepted": True, "body": "NETWORK:" + s["url"], "status": 200}, (s, online)
            assert offline["intercepted"] and offline["body"] == offline_body, (s, offline)
        elif s["url"] in precached_static:
            assert online["intercepted"] and online["body"] == "NETWORK:" + s["url"], (s, online)
            assert offline["intercepted"] and offline["body"] == "NETWORK:" + s["url"], (s, offline)
        else:
            assert not online["intercepted"] and not offline["intercepted"], (s, online, offline)


# ------------------------------------------------------------ offline page

def test_offline_page_needs_no_login_and_is_bilingual_with_arabic_right_to_left(client):
    resp = client.get("/offline")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-cache"
    html = resp.get_data(as_text=True)
    assert '<section lang="ar" dir="rtl">' in html
    assert '<section lang="en" dir="ltr">' in html
    assert "أنت غير متصل بالإنترنت" in html
    assert "You are offline" in html
    assert "<script" not in html  # CSP script-src 'self' -- and nothing needs one
    assert 'href="/"' in html  # "Try again" is a plain navigation


def test_offline_page_is_byte_identical_for_a_signed_in_manager_and_a_stranger(app, client, seeded):
    """The offline page is cached on the phone and shown to whoever holds it,
    so nothing about the person who was signed in may reach it."""
    anonymous = client.get("/offline").get_data()
    _logged_in_super_admin(app, client)
    assert client.get("/").status_code == 200  # really signed in
    signed_in = client.get("/offline").get_data()
    assert signed_in == anonymous
    text = signed_in.decode("utf-8")
    assert "Test Staff" not in text and "pwa-super@example.com" not in text
    assert "csrf" not in text.lower()


# ------------------------------------------------------- pages and shell

def _assert_pwa_head(html: str, lang: str):
    assert f'<link rel="manifest" href="/manifest.webmanifest?lang={lang}">' in html
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in html
    assert '<meta name="mobile-web-app-capable" content="yes">' in html
    assert '<meta name="apple-mobile-web-app-status-bar-style" content="default">' in html
    assert '<meta name="apple-mobile-web-app-title" content="Aura Owner">' in html
    assert '<link rel="apple-touch-icon" href="/static/img/brand/apple-touch-icon.png">' in html
    assert '<meta name="theme-color" content="#FFFFFF" media="(prefers-color-scheme: light)">' in html
    assert '<meta name="theme-color" content="#1B1F29" media="(prefers-color-scheme: dark)">' in html
    assert '<script src="/static/js/pwa.js" data-sw-url="/sw.js" data-sw-scope="/"></script>' in html
    # Registration stays in the external file (CSP: no inline script).
    assert "serviceWorker.register" not in html


def _assert_install_hint_markup(html: str):
    assert re.search(r'<div class="aura-install-hint" id="aura-install-hint" role="region" aria-label="[^"]+" hidden>', html)
    assert re.search(r'data-install-variant="android" hidden>', html)
    assert re.search(r'data-install-variant="ios" hidden>', html)
    assert 'data-install-action="install" hidden>' in html
    assert 'data-install-action="dismiss"' in html


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_sign_in_page_carries_the_manifest_ios_tags_and_install_hint_in_both_languages(client, lang):
    client.get(f"/locale/{lang}")
    html = client.get("/auth/login").get_data(as_text=True)
    assert f'<html lang="{lang}"' in html
    _assert_pwa_head(html, lang)
    _assert_install_hint_markup(html)
    if lang == "ar":
        assert "ثبّت Aura Owner على هذا الهاتف" in html
        assert "إضافة إلى الشاشة الرئيسية" in html


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_dashboard_carries_the_manifest_ios_tags_and_install_hint_in_both_languages(app, client, seeded, lang):
    _logged_in_super_admin(app, client)
    client.get(f"/locale/{lang}")
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    _assert_pwa_head(html, lang)
    _assert_install_hint_markup(html)


def test_install_hint_is_hidden_by_css_inside_the_installed_app():
    """Second, script-independent guarantee: in standalone the hint is
    display:none regardless of what pwa.js decides."""
    css = (STATIC_DIR / "css" / "shell.css").read_text(encoding="utf-8")
    block = re.search(
        r"@media \(display-mode: standalone\), \(display-mode: fullscreen\), \(display-mode: minimal-ui\) \{\s*"
        r"\.aura-install-hint \{ display: none !important; \}\s*\}", css)
    assert block, "standalone-mode hide rule for .aura-install-hint is missing from shell.css"
    assert ".aura-install-hint[hidden]" in css


_IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1"
_DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"

_HINT_CASES = [
    {"id": "android_installable", "fireBeforeInstall": True},
    {"id": "android_inside_installed_app", "fireBeforeInstall": True, "standaloneMedia": True},
    {"id": "iphone_safari", "ua": _IPHONE_UA, "platform": "iPhone"},
    {"id": "iphone_inside_installed_app", "ua": _IPHONE_UA, "platform": "iPhone", "iosStandalone": True},
    {"id": "ipad_reporting_as_mac", "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
     "platform": "MacIntel", "maxTouchPoints": 5},
    {"id": "dismissed_before_on_this_device", "fireBeforeInstall": True, "dismissedBefore": True},
    {"id": "iphone_dismissed_before", "ua": _IPHONE_UA, "platform": "iPhone", "dismissedBefore": True},
    {"id": "storage_blocked", "fireBeforeInstall": True, "storage": "throws"},
    {"id": "dismiss_click", "fireBeforeInstall": True, "click": "dismiss"},
    {"id": "install_click", "fireBeforeInstall": True, "click": "install"},
    {"id": "desktop_not_installable", "ua": _DESKTOP_UA, "platform": "Win32", "maxTouchPoints": 0},
]


def test_install_hint_behaviour_on_each_kind_of_phone():
    """The REAL static/js/pwa.js against a fake browser, per case."""
    result = _run_node(TESTS_DIR / "pwa_hint_harness.js", {
        "jsPath": str(STATIC_DIR / "js" / "pwa.js"), "cases": _HINT_CASES,
    })
    for case_id, r in result.items():
        assert "error" not in r, (case_id, r.get("error"))
        assert r["registered"] == [{"url": "/sw.js", "scope": "/"}], (case_id, r["registered"])

    def shown(case_id, variant):
        r = result[case_id]
        assert r["hintHidden"] is False, (case_id, r)
        assert r["androidHidden"] is (variant != "android"), (case_id, r)
        assert r["iosHidden"] is (variant != "ios"), (case_id, r)
        assert r["installHidden"] is (variant != "android"), (case_id, r)

    def hidden(case_id):
        assert result[case_id]["hintHidden"] is True, (case_id, result[case_id])

    shown("android_installable", "android")
    assert result["android_installable"]["prevented"] is True
    shown("iphone_safari", "ios")
    shown("ipad_reporting_as_mac", "ios")
    shown("storage_blocked", "android")  # blocked storage degrades to "may show again", never a crash

    hidden("android_inside_installed_app")
    hidden("iphone_inside_installed_app")
    hidden("dismissed_before_on_this_device")
    hidden("iphone_dismissed_before")
    hidden("desktop_not_installable")
    # When we are not showing our own hint, the browser's default install UI is left alone.
    assert result["android_inside_installed_app"]["prevented"] is False
    assert result["dismissed_before_on_this_device"]["prevented"] is False

    hidden("dismiss_click")
    assert result["dismiss_click"]["stored"] == "1"
    hidden("install_click")
    assert result["install_click"]["prompted"] is True
    assert result["install_click"]["stored"] == "1"
    assert result["android_installable"]["stored"] is None


# ------------------------------------------------------- CSP and sessions

def _csp(resp) -> dict[str, list[str]]:
    directives = {}
    for part in resp.headers["Content-Security-Policy"].split(";"):
        bits = part.split()
        if bits:
            directives[bits[0]] = bits[1:]
    return directives


@pytest.mark.parametrize("path", ["/sw.js", "/manifest.webmanifest", "/offline", "/auth/login"])
def test_existing_csp_already_allows_the_worker_and_manifest_and_was_not_loosened(client, path):
    csp = _csp(client.get(path))
    # worker-src falls back to child-src, then script-src; manifest-src to
    # default-src. Both resolve to 'self' -- same-origin /sw.js and
    # /manifest.webmanifest are allowed without adding a directive.
    worker = csp.get("worker-src") or csp.get("child-src") or csp.get("script-src") or csp["default-src"]
    manifest = csp.get("manifest-src") or csp["default-src"]
    assert worker == ["'self'"]
    assert manifest == ["'self'"]
    assert csp["script-src"] == ["'self'"], "script-src must stay 'self' only -- no 'unsafe-inline', no hosts"
    assert all("*" not in v for values in csp.values() for v in values)


def test_real_login_with_mfa_still_works_next_to_the_worker_routes(app, client, seeded):
    """Sessions, CSRF and MFA are untouched by the worker (it never sees a
    POST), and the public worker routes neither need nor disturb a session."""
    make_staff(app, "pwa-mfa@example.com", super_admin=True, mfa=True)
    for path in ("/sw.js", "/manifest.webmanifest?lang=en", "/offline"):
        assert client.get(path).status_code == 200
    resp = login_and_verify_mfa(client, "pwa-mfa@example.com")
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    assert client.get("/").status_code == 200
    for path in ("/sw.js", "/manifest.webmanifest?lang=en", "/offline"):
        assert client.get(path).status_code == 200
    assert client.get("/").status_code == 200  # still signed in
