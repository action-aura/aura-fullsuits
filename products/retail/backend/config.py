"""
Aura Retail -- standalone backend configuration.

Trimmed extraction of Action Aura Enterprise's config.py: keeps the
BASE_DIR/AURA_APP_DATA/AURA_STANDALONE/SECRET_KEY resolution (unchanged
behavior, needed by database/schema.py, commercial_runtime/identity, and
commercial_runtime/security) and drops the `DOMAINS` dict (banking/
healthcare/education/manufacturing industries-demo verticals -- out of scope)
and `DEFAULT_USERS` (only used by the legacy domain-based demo login, which
is not ported -- see docs/migration/source-inventory.md #34, risk-register R12).
"""
import os
import sys

if os.environ.get('AURA_BUNDLE_DIR'):
    BASE_DIR = os.environ['AURA_BUNDLE_DIR']
elif getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_app_data = os.environ.get('AURA_APP_DATA', BASE_DIR)
DATABASE_DIR = os.path.join(_app_data, 'database')

from commercial_runtime.security.app_secret import get_or_create_secret_key
SECRET_KEY = get_or_create_secret_key(_app_data)

IS_DEMO_MODE = not os.path.exists(os.path.join(_app_data, 'config.json'))

APP_VERSION = '1.0.0-rc.5'
PRODUCT_CODE = 'AURA_RETAIL'

from commercial_runtime.backup.service import SCHEMA_VERSION
from core.retail.pricing import CALCULATION_VERSION

# Packaged customer build (frozen .exe, or Android via AURA_STANDALONE=1) is
# always STANDALONE -> no sample/seed data is inserted, every install starts clean.
IS_STANDALONE = bool(getattr(sys, 'frozen', False)) or os.environ.get('AURA_STANDALONE') == '1'

# Phase 7 -- Licensing & Activation Service client configuration (Part H).
# Empty by default: the app must fully function with no Owner configured at
# all (commercial_runtime/licensing_contracts/routes.py's /status route
# reports NOT_CONFIGURED rather than erroring). Never a hidden fallback URL --
# an empty string here means "the licensing routes are inert," not "use some
# default Owner instance." Changing this in a commercial build is meant to be
# an authorized configuration step, not an ordinary end-user text field.
OWNER_LICENSING_BASE_URL = os.environ.get('AURA_OWNER_LICENSING_URL', '')
# Dev-only escape hatch for a local Owner instance without a certificate --
# never set AURA_OWNER_LICENSING_INSECURE=1 in a commercial build. Phase 7V
# Part G: this was previously honored unconditionally, meaning an env var set
# on a customer machine could silently disable TLS verification for real
# activation traffic against a frozen .exe. A frozen build now always
# verifies TLS regardless of this variable; only unfrozen (source/dev) runs
# honor the escape hatch.
#
# AURA_OWNER_LICENSING_CA_BUNDLE is the correct way to reach an Owner
# instance on a private LAN with no publicly-trusted certificate (e.g. an
# on-prem/local-network deployment): a path to that specific Owner's CA/leaf
# certificate. `requests`' `verify=` parameter accepts a CA bundle file path
# exactly like it accepts a bool, so this still genuinely verifies -- against
# the one certificate an operator explicitly trusted -- rather than disabling
# verification. Deliberately honored even in a frozen build: naming one
# specific trust anchor is not the same risk as AURA_*_INSECURE globally
# turning verification off, so it does not need the same frozen-build floor.
OWNER_LICENSING_CA_BUNDLE_PATH = os.environ.get('AURA_OWNER_LICENSING_CA_BUNDLE', '')
OWNER_LICENSING_VERIFY_TLS = OWNER_LICENSING_CA_BUNDLE_PATH or (
    True if getattr(sys, 'frozen', False) else (
        os.environ.get('AURA_OWNER_LICENSING_INSECURE') != '1'
    )
)
OWNER_LICENSING_TIMEOUT_SECONDS = float(os.environ.get('AURA_OWNER_LICENSING_TIMEOUT_SECONDS', '10'))
# One shared trust anchor for both products (it names which Owner signing
# keys are trusted, not which product is asking) -- generated once by
# scripts/generate_trust_anchor.py and bundled inside the commercial_runtime
# package itself (not derived from BASE_DIR, which is products/retail/
# backend in dev mode, not the suite root -- resolving via the package's own
# __file__ is correct in both dev and frozen/PyInstaller builds, matching how
# commercial_runtime.identity/backup are already imported either way).
import commercial_runtime.licensing_contracts as _licensing_contracts_pkg
LICENSING_TRUST_ANCHOR_PATH = os.path.join(os.path.dirname(_licensing_contracts_pkg.__file__), 'trust_anchor.json')

# Part H: see products/clinic/backend/config.py's identical block for the
# full rationale. Set by android/aura-retail's main.py (AURA_PLATFORM=
# 'ANDROID') before it imports this app; absent on Windows.
LICENSING_PLATFORM = os.environ.get('AURA_PLATFORM', 'WINDOWS')
LICENSING_INTERNAL_SHARED_SECRET = os.environ.get('AURA_INTERNAL_SHARED_SECRET') or None

# Multi-device sync foundation (2026-08-06), Task 5 -- SyncRelayClient/
# SyncService configuration, following the exact OWNER_LICENSING_* pattern
# immediately above: empty base URL means the sync loop is never started at
# all (app.py's init_app() only calls SyncService.start() when this is
# non-empty), never a hidden default Owner instance.
#
# Launch-readiness (2026-09-03): AURA_SYNC_RELAY_URL had no way to reach a
# customer install at all -- the Inno Setup installer never sets it, there
# is no screen, no documented step, so a product sold on multi-device sync
# shipped with sync silently off. SYNC_RELAY_BASE_URL (further down, after
# validate_sync_relay_url() is defined) now falls back to whatever Owner
# told this device at activation time when no operator has set the env var
# -- see _resolve_effective_sync_relay_base_url()'s docstring for the full
# precedence rule. The env var read here is kept as its own name so that
# rule can see "was it actually set" separately from "what did we end up
# using".
_AURA_SYNC_RELAY_URL_ENV = os.environ.get('AURA_SYNC_RELAY_URL', '')
SYNC_RELAY_TIMEOUT_SECONDS = float(os.environ.get('AURA_SYNC_RELAY_TIMEOUT_SECONDS', '10'))
# Same frozen-build TLS-verification floor as OWNER_LICENSING_VERIFY_TLS: a
# frozen customer build always verifies TLS regardless of this env var; only
# an unfrozen (source/dev) run honors the insecure escape hatch.
#
# AURA_SYNC_RELAY_CA_BUNDLE mirrors AURA_OWNER_LICENSING_CA_BUNDLE above --
# a path to the relay's own certificate, honored even in a frozen build,
# for the same reason: it names one specific trust anchor rather than
# disabling verification.
SYNC_RELAY_CA_BUNDLE_PATH = os.environ.get('AURA_SYNC_RELAY_CA_BUNDLE', '')
SYNC_RELAY_VERIFY_TLS = SYNC_RELAY_CA_BUNDLE_PATH or (
    True if getattr(sys, 'frozen', False) else (
        os.environ.get('AURA_SYNC_RELAY_INSECURE') != '1'
    )
)


# Final-review Fix 2 (2026-08-07): scheme enforcement for the sync relay URL,
# the desktop counterpart of
# mobile/aura-retail-unified/.../sync/SyncRelayConfiguration.kt's `validate()`
# -- previously only the KMP client checked this, so desktop would happily
# push and pull a device-signed event stream over cleartext against any host
# an operator typed into AURA_SYNC_RELAY_URL. Same rule as the KMP version:
# `https://` for anything real, `http://` only for an explicit local
# development host. Loopback is the one genuinely safe cleartext case (it
# never leaves the machine) and is exactly how this is run in dev
# (`AURA_SYNC_RELAY_URL=http://127.0.0.1:5551`).
#
# Deliberately NOT a raise at import time: a misconfigured relay URL must
# disable SYNC, not prevent the whole Retail app from booting (the product
# is required to function fully with no Owner configured at all). app.py
# checks SYNC_RELAY_URL_PROBLEMS and refuses to start the sync loop while
# logging every problem, mirroring AuraAppContainer.kt's own
# "never started against a config that does not validate()" gate.
_LOCAL_DEV_HOSTS = ('127.0.0.1', 'localhost', '::1')


def validate_sync_relay_url(url, insecure_scheme_allowed_hosts=_LOCAL_DEV_HOSTS):
    """Returns a list of human-readable problems with `url` (empty == valid).

    An empty/unset url is "valid" here in the same sense an empty
    OWNER_LICENSING_BASE_URL is: it means "sync is not configured", which
    app.py already handles as "never start the loop", not as an error.
    """
    if not url:
        return []
    problems = []
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return [f"AURA_SYNC_RELAY_URL is not a parsable URL ({exc})."]

    scheme = (parts.scheme or '').lower()
    # `hostname` (not `netloc`) is already lowercased, port-stripped, and has
    # any `user:pass@` prefix removed by urlsplit -- so a URL like
    # `http://127.0.0.1@evil.example.com/` cannot masquerade as loopback.
    host = parts.hostname or ''

    if not scheme:
        problems.append("AURA_SYNC_RELAY_URL has no scheme; it must start with https:// (or http:// for a loopback dev relay).")
    elif scheme not in ('http', 'https'):
        problems.append(f"AURA_SYNC_RELAY_URL uses unsupported scheme '{scheme}://'; only https:// (or http:// for a loopback dev relay) is allowed.")
    elif scheme == 'http' and host not in insecure_scheme_allowed_hosts:
        problems.append(
            f"AURA_SYNC_RELAY_URL uses cleartext http:// against non-loopback host '{host}'. "
            f"Every sync push/pull body is device-signed business data; it must travel over https://. "
            f"http:// is only permitted for a local development relay ({'/'.join(insecure_scheme_allowed_hosts)})."
        )
    if not host:
        problems.append("AURA_SYNC_RELAY_URL has no host.")
    if '@' in parts.netloc:
        problems.append("AURA_SYNC_RELAY_URL must not embed credentials.")
    if parts.query:
        problems.append("AURA_SYNC_RELAY_URL must not embed a query string.")
    if parts.fragment:
        problems.append("AURA_SYNC_RELAY_URL must not embed a URL fragment.")
    return problems


def _discover_persisted_sync_relay_url():
    """Best-effort read of the sync_relay_base_url Owner handed this device
    at activation (see commercial_runtime/licensing_contracts/activation.py's
    ingest_activation_response(), which stores it exactly like
    owner_installation_id) -- the fallback half of the precedence rule in
    _resolve_effective_sync_relay_base_url() below.

    Deliberately defensive: config.py is imported at process start, and a
    truly fresh install -- or one that has simply never activated yet --
    has no licensing.db, no licensing_state table, or no row at all. None
    of that is an error; booting the whole app must never depend on
    licensing.db already existing. Any failure here (missing file, a
    locked/corrupt DB, a permissions problem) is swallowed and treated the
    same as "nothing discovered yet": empty string, same as an install
    that never activated.
    """
    try:
        from pathlib import Path as _Path
        from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
        repo = LicenseStateRepository(_Path(DATABASE_DIR) / 'subsystems' / 'licensing.db')
        record = repo.load()
        return (record.sync_relay_base_url or '') if record is not None else ''
    except Exception:
        return ''


def _resolve_effective_sync_relay_base_url(env_value, persisted_value):
    """Precedence rule for SYNC_RELAY_BASE_URL (launch-readiness, 2026-09-03):

    1. AURA_SYNC_RELAY_URL (an operator's explicit env var) ALWAYS wins
       when set -- a shop pointed at a private relay must never be
       silently redirected by whatever Owner said at activation time. This
       is checked first and short-circuits regardless of whether the env
       value itself is valid; an invalid explicit value was already
       surfaced via SYNC_RELAY_URL_PROBLEMS before this change and must
       keep behaving exactly the same way -- precedence, not validity,
       decides the winner.
    2. Otherwise, the value Owner persisted to licensing.db at activation
       -- but ONLY if it passes the EXACT SAME validate_sync_relay_url()
       check a typed env var gets. Owner's activation response is
       untrusted wire data (it travels over the network and is only as
       trustworthy as the assertion-verification step that accepted it);
       skipping this check here would let a tampered or simply wrong
       activation response silently redirect a shop's device-signed sales
       data to a hostile host with a WEAKER check than a human operator
       ever gets. A persisted value that fails validation is discarded
       outright -- sync stays off, exactly as if nothing had ever been
       configured. It is never surfaced as a "problem" to log either: it
       was never something an operator typed, so there is nothing for them
       to go fix.
    3. Otherwise, empty -- sync stays off, unchanged from every install
       before this change.
    """
    if env_value:
        return env_value
    if persisted_value and not validate_sync_relay_url(persisted_value):
        return persisted_value
    return ''


_PERSISTED_SYNC_RELAY_BASE_URL = _discover_persisted_sync_relay_url()
# Takes effect on the NEXT LAUNCH, and that is acceptable: config.py is
# read once at import time and app.py decides at import whether to build
# the sync service at all -- hot-starting sync mid-session (reacting to an
# activation that just happened in this same running process) is a larger
# change and is explicitly out of scope here. An operator who activates
# while the app is already running must restart it for sync to come on.
SYNC_RELAY_BASE_URL = _resolve_effective_sync_relay_base_url(_AURA_SYNC_RELAY_URL_ENV, _PERSISTED_SYNC_RELAY_BASE_URL)

SYNC_RELAY_URL_PROBLEMS = validate_sync_relay_url(SYNC_RELAY_BASE_URL)


# ── AI Assistant (Retail sidebar chat) ──────────────────────────────────────
# Proxies the sidebar "AI Assistant" button (see app-shell.js's `hasAI` /
# `sub-ai.js`'s SubAI module) to a small hosted LLM (phi3.5:3.8b as of
# 2026-08-13, see AURA_AI_MODEL_NAME below, behind an Ollama-compatible
# /api/generate endpoint). The URL defaults to the
# demo droplet since it's not secret and the feature is meaningless without
# it -- defaulting it on is what makes the button work out of the box instead
# of shipping another "looks wired, does nothing" control. The bearer token
# is a real credential and must NEVER have a hardcoded default (2026-08-12:
# an earlier version of this file did exactly that and got a live token
# committed to git history -- the token was rotated immediately after
# discovery, but the lesson stands). An unset token here means the /ai/chat
# route's request to the droplet gets a 401, which the route already treats
# as "AI assistant is temporarily unavailable" -- fails closed and gracefully,
# never a crash. Set AURA_AI_BEARER_TOKEN in the environment (see
# .env.example) to actually enable the feature.
AURA_AI_ENDPOINT_URL = os.environ.get('AURA_AI_ENDPOINT_URL', 'https://104-248-35-215.sslip.io/api/generate')
AURA_AI_BEARER_TOKEN = os.environ.get('AURA_AI_BEARER_TOKEN', '')
# 2026-08-12: bumped from 15s after a real, realistic prompt (verified via
# timed curl against the actual droplet, not assumed) took 36.5s -- the
# original 15s was benchmarked against a 2-token "Say OK" reply, not
# representative. retail_api.py's _AI_REPLY_MAX_TOKENS now caps the model's
# own output length too, so this is a safety margin on top of that cap, not
# the only mitigation.
AURA_AI_TIMEOUT_SECONDS = float(os.environ.get('AURA_AI_TIMEOUT_SECONDS', '45'))

# 2026-08-13: switched the deployed model from phi3:mini to phi3.5:3.8b after
# real, on-droplet benchmarking (same CPU-only 4vCPU/8GB droplet, same
# realistic "How do I add a new product?" prompt, same num_predict=150 cap
# used in production -- via direct curl against http://localhost:11434,
# not assumed). Measured generation throughput: phi3:mini ~6.6 tok/s vs phi3.5:3.8b
# ~8.5-9.4 tok/s (roughly 30-40% faster) at the SAME disk footprint (2.2GB)
# and the same resident-memory class (~3.9GB while loaded) -- a strict
# improvement, not a resize. Two larger 7B-class candidates (qwen2.5:7b,
# and qwen2.5:3b as a smaller/faster-hoped-for option) were also pulled and
# benchmarked on the same droplet; qwen2.5:7b was ~3.4 tok/s with a ~20s
# cold-load alone (would blow the 45s timeout on a full-length reply) and
# qwen2.5:3b was slower per-token than phi3.5:3.8b despite being smaller
# (5.96 tok/s) -- both were deleted from the droplet after benchmarking
# (`ollama rm`), not left installed. phi3:mini itself is deliberately left
# installed (not deleted) as an instant rollback -- set AURA_AI_MODEL_NAME
# back to 'phi3:mini' with no droplet changes needed if phi3.5:3.8b ever
# regresses in practice.
AURA_AI_MODEL_NAME = os.environ.get('AURA_AI_MODEL_NAME', 'phi3.5:3.8b')
