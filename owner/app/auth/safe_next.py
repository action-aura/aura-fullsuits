"""Where a sign-in / re-authentication may send the person afterwards.

Lives in its own module (not in auth/routes.py) because two sides need the same
rule: the routes that REDIRECT TO the sign-in page (security/rbac.py builds the
``next`` it carries) and the routes that act on it after the password / MFA /
re-auth step (auth/routes.py). One rule, one place, so they cannot drift apart.
"""
from __future__ import annotations

from urllib.parse import unquote, urlsplit

from flask import request


def safe_next(value: str | None) -> str | None:
    """Only ever redirect to a same-site relative path. Rejects absolute
    URLs, protocol-relative '//host', and the backslash forms browsers
    (WHATWG URL parsing) fold into '/' -- a leading slash plus a backslash,
    or its percent-encoded form, both land on https://evil.com/ even though they start with a single '/'
    (review D SEC-06). Percent-decoded and re-checked so an encoded
    backslash or control character cannot slip through."""
    if not value or not value.startswith("/"):
        return None
    candidates = (value, unquote(value), unquote(unquote(value)))
    for cand in candidates:
        if "\\" in cand or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cand):
            return None
        if not cand.startswith("/") or cand.startswith("//"):
            return None
        parts = urlsplit(cand)
        if parts.scheme or parts.netloc:
            return None
    return value


def return_target() -> str | None:
    """The page to come back to after signing in / confirming identity, for the
    request being bounced right now: path AND query string (a link such as
    /contracts/seller?required=1 must not lose its query), and only for a GET.

    A POST-only action (publish, retire...) cannot be "come back to" -- the browser
    would re-ask the URL with GET and get a 405 -- so it returns None and the person
    lands on the dashboard; the page that links to such an action carries its own
    explicit ``next`` (the contract detail page does)."""
    if request.method not in ("GET", "HEAD"):
        return None
    target = request.path
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", "replace")
    return safe_next(target)
