"""
Aura FullSuits -- per-device login / "one Admin Device": local device
identity resolution (Phase 1, backend-only, this week).

CRITICAL: this module must be safe to import EAGERLY on Android (Chaquopy).
Do NOT import `commercial_runtime.licensing_contracts.device_identity` (or
anything that transitively imports it) and do NOT import `cryptography`
anywhere in this file. `device_identity.py` imports
`cryptography.hazmat...` at module scope, which is exactly what crashed
Android's `app.py` import with `ModuleNotFoundError('cryptography')` before
that provider was made lazy-imported (see products/retail/backend/app.py's
Part H comment, ~line 146, and products/clinic/backend/app.py's identical
block). This module instead reads the plaintext, non-secret metadata file
the licensing system already writes -- `device_key_meta.json` -- as plain
JSON, with zero dependency on the licensing package's Python code.

Nothing in this module is wired into any login path yet. `mt_auth.py`,
`auth_routes.py`, `onboarding_routes.py`, and both products' `config.py`
remain untouched this week; `binding_enforced()` below exists only as the
future gate for that wiring and currently has no caller. See
docs/superpowers/specs/2026-08-10-per-device-login.md for the full Phase 0
decision record this module implements (the company_id-mismatch refusal in
`resolve_local_device()` is option 0-a from that spec, §b).

Like device_registry.py, every function here takes an explicit `conn`
where a database is needed -- callers own connection lifecycle. Unlike
registry_db.py, this module does NOT resolve AURA_APP_DATA at import time:
`_resolve_app_data()` below is called fresh, at the top of every public
function, specifically to avoid the module-import-time-env-var-caching
anti-pattern documented in products/run_all_tests.py's module docstring
(lines 1-25) -- that doc explains how caching a filesystem/env-derived path
in a module-level constant breaks any process that imports this module more
than once against a different AURA_APP_DATA (multi-file pytest runs), even
though it's harmless in the single-import-per-process shape a real desktop
or Android process actually has.
"""
import json
import os
import threading
import uuid
from typing import Optional

from commercial_runtime.identity import device_registry

_ACTIVE_STATUS = "ACTIVE"
_TRUTHY_VALUES = {"1", "true", "yes", "on"}


class LocalDeviceStateCorruptError(Exception):
    """Raised when device/local_device.json exists but cannot be trusted.

    Mirrors licensing_contracts.device_identity.LocalStateCorruptError's
    "corrupt local state, don't paper over it" contract (see that class's
    docstring: callers must route this to an explicit error path, never to
    silent regeneration) -- but is defined locally here, rather than
    imported, because device_identity.py imports `cryptography` at module
    scope and this module must stay safe to import eagerly on Android. A
    fresh UUID here would silently create a second local-device identity
    for an install that already had one, which would show up downstream as
    a duplicate `devices` row -- exactly the failure mode this refusal
    exists to prevent.
    """


class DeviceCompanyMismatchError(Exception):
    """Raised when this install's existing local device row is bound to a
    DIFFERENT company_id than the one resolve_local_device() was called
    with.

    This is a deliberate refusal, not a bug to smooth over -- Phase 0
    decision option 0-a (docs/superpowers/specs/2026-08-10-per-device-login.md
    §b). Silently re-binding the device row to a new company_id would hide
    the exact tenant-fragmentation failure mode that spec's §a describes:
    two onboarding runs (or two physical devices) computing two different
    company_id values that should have been one tenant. Note that
    device_registry.upsert_local_device()'s UPDATE branch overwrites
    company_id unconditionally (unlike its COALESCE'd fields), so this
    check MUST run before upsert_local_device() is ever called -- calling
    upsert first and inspecting the result afterwards would already have
    performed the silent rebind this error exists to prevent.
    """


def _resolve_app_data() -> str:
    """Same fallback AURA_APP_DATA resolves to as registry_db.py's
    module-level `_app_data` (registry.db lives in the same
    commercial_runtime/identity/ directory, so the "two levels up from this
    file" fallback lands on the same suite-root default) -- except computed
    fresh on every call rather than cached at import time. See module
    docstring."""
    return os.environ.get("AURA_APP_DATA") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def local_device_fingerprint() -> Optional[str]:
    """Reads the licensing system's own public, non-secret device-key
    metadata file as plain JSON -- no import of device_identity.py, no
    cryptography involved at all, just `json.load` over a file that module
    already writes (see WindowsDpapiDeviceIdentityProvider._write_meta(),
    device_identity.py ~lines 199-211).

    Returns the sha256-hex `public_key_fingerprint` string, or None if:
      - the licensing directory / meta file doesn't exist yet (licensing
        was never configured or never activated on this device -- a fully
        supported, permanent state for an install with no
        OWNER_LICENSING_BASE_URL set, see products/retail/backend/config.py
        ~line 47's "the app must fully function with no Owner configured at
        all"), or
      - the file exists but can't be parsed / is missing the field, or
      - `status` is anything other than "ACTIVE" (i.e. "RESET_PENDING" or
        "REVOKED_LOCALLY" -- a key mid-rotation or revoked locally is not a
        trustworthy device identity to hand to the device registry).

    A parse failure is treated the same as "not present yet" (returns None)
    rather than raised: unlike local_device_uuid()'s local_device.json
    (this module's OWN state, see below), device_key_meta.json is owned and
    written by the licensing system -- this function is a read-only,
    best-effort consumer of someone else's file, not its authority. If it's
    unreadable, the correct behavior is to fall back to the local UUID
    identity, not to blow up device resolution for an install that may not
    even have licensing configured.
    """
    app_data = _resolve_app_data()
    meta_path = os.path.join(app_data, "licensing", "device_key_meta.json")
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("status") != _ACTIVE_STATUS:
        return None
    fingerprint = raw.get("public_key_fingerprint")
    if not fingerprint or not isinstance(fingerprint, str):
        return None
    return fingerprint


def local_device_uuid() -> str:
    """Fallback local device identity for when licensing is unconfigured --
    a fully supported, permanent shipping state (see
    products/retail/backend/config.py ~line 47 and
    local_device_fingerprint()'s docstring above; the same "invisible
    unless opted in" philosophy applies here as it does to licensing and
    e-invoicing).

    Generates a UUID exactly once with uuid.uuid4() and persists it to
    <AURA_APP_DATA>/device/local_device.json; every subsequent call on this
    install reads the persisted value back rather than generating a new
    one. If the file exists but cannot be read as a valid UUID record, this
    raises LocalDeviceStateCorruptError rather than silently regenerating a
    fresh UUID -- a silent regeneration here would create a second,
    disconnected local device identity for an install that already had one,
    which is exactly the kind of silent duplication device_registry.py's
    "one row per physical device, keyed by a UUID stable for the life of
    the install" invariant depends on not happening.
    """
    app_data = _resolve_app_data()
    device_dir = os.path.join(app_data, "device")
    uuid_path = os.path.join(device_dir, "local_device.json")

    if os.path.exists(uuid_path):
        try:
            with open(uuid_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            value = raw["device_uuid"]
            if not isinstance(value, str) or not value:
                raise ValueError("device_uuid field is empty or not a string")
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise LocalDeviceStateCorruptError(
                f"{uuid_path} exists but could not be read as a valid local device UUID "
                f"record ({exc!r}). Refusing to silently generate a replacement -- doing so "
                "would create a duplicate local device identity for this install. If this "
                "file is genuinely unrecoverable, it must be resolved deliberately, not "
                "papered over automatically."
            ) from exc

    os.makedirs(device_dir, exist_ok=True)
    new_uuid = str(uuid.uuid4())
    with open(uuid_path, "w", encoding="utf-8") as f:
        json.dump({"device_uuid": new_uuid}, f, indent=2)
    return new_uuid


_cache_lock = threading.Lock()
_cached_device: Optional[dict] = None


def resolve_local_device(conn, company_id: str, device_label: str = None, platform: str = None) -> dict:
    """Idempotent per-install device resolution -- the main entry point
    Wednesday's device_routes.py (out of scope this week) and, eventually,
    login enforcement will call.

    `company_id` is an explicit parameter (the caller's current session's
    company_id) rather than something this function derives on its own:
    device_context.py has no framework/session coupling, the same way
    device_registry.py takes an explicit `conn` rather than resolving one
    itself, and a single registry.db can host more than one company_id (see
    CLAUDE.md's multi-tenant-per-install note) so there is no single
    "the" company_id to read out of `conn` without being told which
    session's is meant.

    Identity resolution: the DB row's `id` (device_registry's primary key)
    is ALWAYS local_device_uuid() -- never local_device_fingerprint() --
    because device_registry.py's own schema docstring defines `devices.id`
    as "a local UUID stable for the life of the install", and
    upsert_local_device() has no support for migrating a row's primary key.
    If the fingerprint were used as the id whenever available, a device
    that resolves once before licensing activation (id=uuid) and again
    after (id=fingerprint) would silently get a SECOND row instead of
    having its first one updated -- exactly the "fingerprint appearing
    later must update the existing row" case this function is required to
    handle correctly. Using the persisted local UUID as the row identity
    on every call, and passing the fingerprint only as the (nullable,
    separately unique-indexed) device_fingerprint field, is what makes
    that update-not-duplicate behavior fall out naturally instead of
    needing special-case migration logic.

    company_id drift (Phase 0 decision 0-a): if a device row already
    exists for this install's local UUID and its stored company_id differs
    from the `company_id` argument, this raises DeviceCompanyMismatchError
    instead of proceeding -- see that class's docstring. This check runs
    BEFORE upsert_local_device() is called, since upsert's UPDATE branch
    would otherwise silently overwrite the mismatch.

    Caching: the resolved row is cached in a module-level variable, guarded
    by a lock, so repeated calls in the same process with the same
    company_id don't re-hit the database. A call with a DIFFERENT
    company_id than what's cached always falls through to a fresh
    resolution (and the mismatch check above), rather than trusting a
    stale cache entry -- that fall-through is what lets this function
    actually detect drift instead of masking it behind the cache.
    """
    global _cached_device
    with _cache_lock:
        if _cached_device is not None and _cached_device.get("company_id") == company_id:
            return _cached_device

        fingerprint = local_device_fingerprint()
        device_id = local_device_uuid()

        existing = device_registry.get_device(conn, device_id)
        if existing is not None and existing["company_id"] != company_id:
            raise DeviceCompanyMismatchError(
                f"Local device {device_id!r} is already registered under company_id "
                f"{existing['company_id']!r}, but this call resolved company_id "
                f"{company_id!r}. Refusing to silently re-bind -- see "
                "docs/superpowers/specs/2026-08-10-per-device-login.md (Phase 0 decision 0-a)."
            )

        row = device_registry.upsert_local_device(
            conn,
            device_id=device_id,
            company_id=company_id,
            device_label=device_label,
            platform=platform,
            device_fingerprint=fingerprint,
        )
        _cached_device = row
        return row


def binding_enforced() -> bool:
    """Reads AURA_DEVICE_BINDING_ENFORCED at call time. Returns False when
    unset, empty, or '0' (the default, and the state for this entire week
    per the standing instruction that enforcement stays OFF); True only for
    an actual truthy value. This is the (not-yet-wired) gate for
    login-enforcement to check once that wiring exists -- it has no caller
    yet."""
    value = os.environ.get("AURA_DEVICE_BINDING_ENFORCED", "")
    return value.strip().lower() in _TRUTHY_VALUES
