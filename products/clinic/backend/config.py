"""
Aura Clinic -- standalone backend configuration.

Identical structure to products/retail/backend/config.py (see that file's
docstring for the rationale) -- trimmed from Action Aura Enterprise's
config.py: BASE_DIR/AURA_APP_DATA/AURA_STANDALONE/SECRET_KEY resolution only,
no DOMAINS dict, no DEFAULT_USERS.
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
PRODUCT_CODE = 'AURA_CLINIC'

from commercial_runtime.backup.service import SCHEMA_VERSION

IS_STANDALONE = bool(getattr(sys, 'frozen', False)) or os.environ.get('AURA_STANDALONE') == '1'

# Phase 7 -- Licensing & Activation Service client configuration (Part H).
# See products/retail/backend/config.py's identical block for the full
# rationale (empty-by-default, never a hidden fallback URL, dev-only TLS
# escape hatch, trust anchor resolved via the package's own __file__ rather
# than BASE_DIR since BASE_DIR is products/clinic/backend, not the suite root).
OWNER_LICENSING_BASE_URL = os.environ.get('AURA_OWNER_LICENSING_URL', '')
# Phase 7V Part G: AURA_OWNER_LICENSING_INSECURE is a source/dev-only escape
# hatch for testing against a local Owner instance without a real cert. A
# frozen (PyInstaller-packaged) commercial build must never honor it -- an
# environment variable set on a customer machine (accidentally, or by a
# well-meaning support technician debugging connectivity) must not be able to
# silently disable TLS verification for real activation traffic. Frozen
# builds always verify_tls=True regardless of what the env var says.
OWNER_LICENSING_VERIFY_TLS = True if getattr(sys, 'frozen', False) else (
    os.environ.get('AURA_OWNER_LICENSING_INSECURE') != '1'
)
OWNER_LICENSING_TIMEOUT_SECONDS = float(os.environ.get('AURA_OWNER_LICENSING_TIMEOUT_SECONDS', '10'))

import commercial_runtime.licensing_contracts as _licensing_contracts_pkg
LICENSING_TRUST_ANCHOR_PATH = os.path.join(os.path.dirname(_licensing_contracts_pkg.__file__), 'trust_anchor.json')

# Part H: which device-identity provider app.py wires up, and whether the
# Kotlin-facing /_internal/sync-* routes are registered at all. Set by
# android/aura-clinic's main.py (AURA_PLATFORM='ANDROID') before it imports
# this app -- absent on Windows, where WindowsDpapiDeviceIdentityProvider and
# this process's own direct Owner calls are always correct. On Android the
# actual device key + Owner HTTP calls live in Kotlin (Part U); this shared_
# secret is generated fresh per process by ServerBootstrap.kt and threaded
# in the same way AURA_APP_DATA already is -- never hardcoded, never
# persisted, only valid for this process's lifetime.
LICENSING_PLATFORM = os.environ.get('AURA_PLATFORM', 'WINDOWS')
LICENSING_INTERNAL_SHARED_SECRET = os.environ.get('AURA_INTERNAL_SHARED_SECRET') or None
