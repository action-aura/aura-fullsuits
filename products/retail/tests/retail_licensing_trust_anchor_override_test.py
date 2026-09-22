"""Aura Retail -- LICENSING_TRUST_ANCHOR_PATH field-recovery override
(retail-hardware-viewports security task, 2026-09-22).

See config.py's "Trust-anchor path override" block for the full security
reasoning this pins. Short version: the trust anchor decides which Owner
signing keys this till will ever accept a licence from, so it is the root
of trust for the whole commercial model -- unlike OWNER_LICENSING_BASE_URL
or OWNER_LICENSING_CA_BUNDLE_PATH (already env-overridable), an attacker who
controls this value alone does not need a real Owner or a real certificate;
they can mint their own keypair and self-issue a free licence. The override
therefore requires TWO exact-string env vars, not one
(AURA_LICENSING_TRUST_ANCHOR_PATH + AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_
CONFIRM=1), and is loudly logged whenever it fires or is refused.

Per this repo's ENGINEERING.md ("prove both directions of anything that
both denies and allows" -- a fix with an allow/deny half is half-tested by
default because the deny half looks obvious): this file tests the DENY half
(confirm missing/wrong -> override refused, default path used) at least as
hard as the ALLOW half, and the report for this task includes real mutation
output showing each direction actually catches a regression, not just that
the happy path passes.

One file per process (matches every other *_config_test.py in this
directory) -- run standalone:
    pytest products/retail/tests/retail_licensing_trust_anchor_override_test.py -v
"""
import importlib
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_trust_anchor_override_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
_ENV_VARS = ("AURA_LICENSING_TRUST_ANCHOR_PATH", "AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM")
for _var in _ENV_VARS:
    os.environ.pop(_var, None)

import config  # noqa: E402


def teardown_module(module):
    for _var in _ENV_VARS:
        os.environ.pop(_var, None)
    importlib.reload(config)
    shutil.rmtree(DATA, ignore_errors=True)


def _reload_with(**env):
    """Re-import config.py with these env values, exactly the way a real
    process boot would read them. None unsets the variable entirely (the
    genuine out-of-the-box state), never the same thing as an empty string."""
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return importlib.reload(config)


# ── the pure decision function: resolve_licensing_trust_anchor_path ───────
# No reload/import machinery needed for these -- config.py's docstring says
# it is pure (no I/O, no environment reads inside it), mirroring
# site_relay_should_start's own testability story.

def test_unset_override_returns_default_path_unchanged():
    """The no-regression half: nothing set at all must behave exactly as it
    did before this feature existed -- the bundled path, untouched."""
    path, active, refused = config.resolve_licensing_trust_anchor_path(
        "/bundled/trust_anchor.json", "", ""
    )
    assert path == "/bundled/trust_anchor.json"
    assert active is False
    assert refused is None


def test_path_alone_without_confirm_is_refused():
    """THE SECURITY-CRITICAL DENY HALF. A bare AURA_LICENSING_TRUST_ANCHOR_
    PATH with no confirmation must NEVER take effect -- this is the one
    variable in config.py's whole override family that is allowed to change
    which signing keys are trusted, so a single stray env var must not be
    enough."""
    path, active, refused = config.resolve_licensing_trust_anchor_path(
        "/bundled/trust_anchor.json", "/evil/trust_anchor.json", ""
    )
    assert path == "/bundled/trust_anchor.json", "must fall back to the bundled path, not the offered one"
    assert active is False
    assert refused is not None and "AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM" in refused


@pytest.mark.parametrize("near_miss", ["true", "TRUE", "yes", "on", "  1  ", "1 ", "0", "2", ""])
def test_near_miss_confirm_values_refuse_the_override(near_miss):
    """Same exact-string discipline as AURA_SITE_RELAY_ENABLED's '1'/'0'
    check (config.py's own precedent): a near-miss confirms nothing. A
    typo, a copy-pasted 'true' from some other product's env convention, or
    an accidentally-empty value must all degrade to REFUSED, never to
    silently accepted."""
    path, active, refused = config.resolve_licensing_trust_anchor_path(
        "/bundled/trust_anchor.json", "/evil/trust_anchor.json", near_miss
    )
    assert active is False, f"{near_miss!r} must not confirm the override"
    assert path == "/bundled/trust_anchor.json"


def test_path_with_exact_confirm_is_honored():
    """THE ALLOW HALF -- this repo's engineering standard says the allow
    half is the one most likely to be under-tested, since the deny half
    looks like the obvious thing to check. Confirmed hardest here: an exact
    '1' confirmation, paired with a real offered path, must actually work,
    or the whole point of building a field-recovery mechanism is lost."""
    path, active, refused = config.resolve_licensing_trust_anchor_path(
        "/bundled/trust_anchor.json", "/ops/recovered_trust_anchor.json", "1"
    )
    assert path == "/ops/recovered_trust_anchor.json"
    assert active is True
    assert refused is None


def test_empty_override_path_is_inert_even_with_confirm_set():
    """A confirm flag with no path to go with it must do nothing -- there is
    no override to confirm. Guards against a deployment script that always
    sets the confirm flag defensively without checking whether a path was
    actually supplied."""
    path, active, refused = config.resolve_licensing_trust_anchor_path(
        "/bundled/trust_anchor.json", "", "1"
    )
    assert path == "/bundled/trust_anchor.json"
    assert active is False
    assert refused is None


# ── environment -> config.py wiring (needs the reload machinery) ──────────

def test_default_boot_uses_bundled_path_with_no_env_set():
    """No-regression check at the real import-time wiring level, not just
    the pure function: a totally clean boot (both env vars unset) must
    resolve to the same commercial_runtime-relative path this file always
    used, and must not be flagged as overridden."""
    cfg = _reload_with(AURA_LICENSING_TRUST_ANCHOR_PATH=None, AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM=None)
    assert cfg.LICENSING_TRUST_ANCHOR_PATH.replace("\\", "/").endswith(
        "commercial_runtime/licensing_contracts/trust_anchor.json"
    )
    assert cfg.LICENSING_TRUST_ANCHOR_OVERRIDE_ACTIVE is False


def test_confirmed_override_takes_effect_at_boot():
    cfg = _reload_with(
        AURA_LICENSING_TRUST_ANCHOR_PATH="/ops/recovered_trust_anchor.json",
        AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM="1",
    )
    assert cfg.LICENSING_TRUST_ANCHOR_PATH == "/ops/recovered_trust_anchor.json"
    assert cfg.LICENSING_TRUST_ANCHOR_OVERRIDE_ACTIVE is True


def test_confirm_alone_without_path_is_inert_at_boot():
    """Wiring-level counterpart of test_empty_override_path_is_inert_even_
    with_confirm_set -- that one calls the pure function directly with an
    explicit empty string, which would not notice a regression in HOW
    config.py reads the env var itself (e.g. os.environ.get's own default
    silently becoming non-empty). This exercises the real
    os.environ.get('AURA_LICENSING_TRUST_ANCHOR_PATH', ...) call: an
    operator who sets only the confirm flag (defensively, or a stale value
    left over from a previous incident) with no path must still boot on the
    bundled anchor, unchanged."""
    cfg = _reload_with(AURA_LICENSING_TRUST_ANCHOR_PATH=None, AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM="1")
    assert cfg.LICENSING_TRUST_ANCHOR_OVERRIDE_ACTIVE is False
    assert cfg.LICENSING_TRUST_ANCHOR_PATH.replace("\\", "/").endswith(
        "commercial_runtime/licensing_contracts/trust_anchor.json"
    )


def test_unconfirmed_override_is_refused_at_boot():
    cfg = _reload_with(
        AURA_LICENSING_TRUST_ANCHOR_PATH="/ops/recovered_trust_anchor.json",
        AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM=None,
    )
    assert cfg.LICENSING_TRUST_ANCHOR_OVERRIDE_ACTIVE is False
    assert cfg.LICENSING_TRUST_ANCHOR_PATH.replace("\\", "/").endswith(
        "commercial_runtime/licensing_contracts/trust_anchor.json"
    )


def test_active_override_is_logged_loudly_at_error_level(caplog):
    """"Logged loudly when used" is part of this override's security
    argument (config.py's own comment: "it can never be silent"). Proven
    here by actually capturing the log record, not by reading the source
    and assuming the logging call fires."""
    with caplog.at_level(logging.ERROR, logger="config"):
        _reload_with(
            AURA_LICENSING_TRUST_ANCHOR_PATH="/ops/recovered_trust_anchor.json",
            AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM="1",
        )
    assert any(
        "LICENSING TRUST ANCHOR OVERRIDDEN" in rec.message and rec.levelno == logging.ERROR
        for rec in caplog.records
    ), f"expected a loud ERROR-level override log; got {[(r.levelname, r.message) for r in caplog.records]}"


def test_refused_override_is_logged_at_warning_level(caplog):
    """A path offered without confirmation is not silent either -- an
    operator who typos the confirm variable should see why their override
    had no effect, not have to guess."""
    with caplog.at_level(logging.WARNING, logger="config"):
        _reload_with(
            AURA_LICENSING_TRUST_ANCHOR_PATH="/ops/recovered_trust_anchor.json",
            AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM=None,
        )
    assert any(
        "OVERRIDE_CONFIRM" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), f"expected a WARNING-level refusal log; got {[(r.levelname, r.message) for r in caplog.records]}"
