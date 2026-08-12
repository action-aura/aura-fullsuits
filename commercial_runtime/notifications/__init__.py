"""Outbound email -- shared product-side domain (feat/email-outbox-foundation).

Mirrors commercial_runtime/einvoicing's shape deliberately -- see that
package's __init__.py and CLAUDE.md's "E-invoicing" section, which names it
as the reference pattern for any future "must talk to an external
authority/channel reliably" feature. Imported by both Retail and Clinic
Windows backends directly (same import convention as einvoicing and
licensing_contracts). Nothing in this package imports from `products/`.

Named `notifications`, not `email`, on purpose: this is the first of
potentially several outbound channels (CLAUDE.md's "What's genuinely
missing" list also names SMS/WhatsApp/push as zero-not-partial) and Python's
own stdlib already owns the bare `email` package name (email.mime.text is
used internally by smtp_client.py) -- a sibling module called `email`
importable as `commercial_runtime.email` would not collide with the stdlib
package under normal absolute-import resolution, but the name is confusing
enough on a repo this size (raw sqlite3, several sys.path.insert() calls in
test bootstraps) that avoiding it entirely was judged safer than proving it
is fine today and hoping it stays fine.

Default OFF, in the same two-layer sense as OWNER_LICENSING_BASE_URL, NOT
einvoicing's three-layer kill-switch scheme: unlike a tax authority
integration, there is no safe shared demo credential to default to for
someone else's SMTP relay, so the FIRST gate is simply "has an operator
configured AURA_SMTP_HOST at all" (see settings.py::is_enabled and
smtp_client.py's module docstring) -- an install that has never set any
AURA_SMTP_* env var is bit-for-bit unaffected by this package: no thread
starts, no table gains a row beyond empty CREATE TABLE IF NOT EXISTS shells,
no request behaves differently. The second, per-company gate
(`email_settings.enabled`) exists for the same reason einvoicing's per-
company `enabled` setting exists: an operator may run SMTP for one company
on a multi-tenant install without turning it on for every company sharing
that installation.
"""
