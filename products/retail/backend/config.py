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

APP_VERSION = '1.0.0-rc.3'
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
OWNER_LICENSING_VERIFY_TLS = True if getattr(sys, 'frozen', False) else (
    os.environ.get('AURA_OWNER_LICENSING_INSECURE') != '1'
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
