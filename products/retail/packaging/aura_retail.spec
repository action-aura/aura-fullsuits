# -*- mode: python ; coding: utf-8 -*-
#
# Aura Retail -- Windows desktop build spec -> dist/AuraRetail/AuraRetail.exe
#
# New file (not extracted) -- Action Aura Enterprise's aura_enterprise.spec bundles
# the entire multi-subsystem monolith (every subsystem's hidden imports/data files).
# Aura Retail is a much smaller, standalone product, so this spec is self-contained
# rather than delegating to the monolith's spec.
#
# Build (from the aura-fullsuits repo root):
#   pyinstaller products/retail/packaging/aura_retail.spec --noconfirm
#
# NOT YET BUILD-VERIFIED in this environment (no PyInstaller/Windows build
# toolchain run as part of this extraction) -- see docs/migration/retail-extraction-report.md.

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), '..', '..', '..'))
BACKEND = os.path.join(ROOT, 'products', 'retail', 'backend')
FRONTEND = os.path.join(ROOT, 'products', 'retail', 'frontend')
DESKTOP = os.path.join(ROOT, 'products', 'retail', 'desktop')

block_cipher = None

# Phase 7V Part F -- trust_anchor.json (scripts/generate_trust_anchor.py's
# output) is never picked up by PyInstaller's module-import analysis since
# it's plain JSON data, not a .py module -- it must be listed explicitly here
# or a commercial build silently ships with NO way to verify any Owner
# response, and every activation attempt fails. Gitignored, build-time-only
# (a release engineer runs generate_trust_anchor.py against the real Owner
# instance being released against before cutting this build), so only
# included when present -- a dev/source build without one is still valid.
_trust_anchor = os.path.join(ROOT, 'commercial_runtime', 'licensing_contracts', 'trust_anchor.json')
_licensing_datas = []
if os.path.exists(_trust_anchor):
    _licensing_datas.append((_trust_anchor, os.path.join('commercial_runtime', 'licensing_contracts')))

# certifi's cacert.pem is a data file, not a module -- PyInstaller's
# built-in hook-certifi.py (pyinstaller-hooks-contrib) is supposed to
# collect it automatically, but wasn't firing in this build environment:
# requests/utils.py's DEFAULT_CA_BUNDLE_PATH = certifi.where() resolves at
# import time to a path that only exists in the *build* venv, and without
# the data file bundled alongside it, every requests.Session() (Owner
# licensing/sync HTTP calls) crashes the whole app at import with
# FileNotFoundError before the server ever starts. Collected explicitly
# here instead of trusting hook auto-discovery -- same reasoning as the
# trust_anchor.json / einvoicing hiddenimports above.
from PyInstaller.utils.hooks import collect_data_files
_certifi_datas = collect_data_files('certifi')

a = Analysis(
    [os.path.join(DESKTOP, 'launcher_retail.py')],
    pathex=[ROOT, BACKEND],
    binaries=[],
    datas=[
        (FRONTEND, os.path.join('products', 'retail', 'frontend')),
    ] + _licensing_datas + _certifi_datas,
    hiddenimports=[
        'flask', 'flask_cors', 'werkzeug', 'waitress',
        'commercial_runtime.identity.mt_auth',
        'commercial_runtime.identity.auth_routes',
        'commercial_runtime.identity.onboarding_routes',
        'commercial_runtime.identity.registry_db',
        'commercial_runtime.security.app_secret',
        'commercial_runtime.security.passwords',
        'commercial_runtime.security.audit',
        'commercial_runtime.security.modes',
        'commercial_runtime.backup.service',
        'commercial_runtime.backup.routes',
        'commercial_runtime.launcher_support',
        'api.retail_api',
        'api.import_api',
        'database.schema',
        'core.retail.pricing',
        # docs/einvoicing/phase1/ -- Jordan JoFotara e-invoicing. Listed
        # explicitly, same as every other commercial_runtime.* package
        # above: the trust_anchor.json precedent in this repo (see
        # docs/licensing/phase7/) is a real prior instance of a
        # non-statically-discoverable module/asset silently missing from a
        # packaged build, so nothing here is left to PyInstaller's static
        # analysis alone.
        'core.retail.einvoice_adapter',
        'commercial_runtime.einvoicing.schema',
        'commercial_runtime.einvoicing.settings',
        'commercial_runtime.einvoicing.killswitch',
        'commercial_runtime.einvoicing.credentials',
        'commercial_runtime.einvoicing.sequence',
        'commercial_runtime.einvoicing.document',
        'commercial_runtime.einvoicing.ubl',
        'commercial_runtime.einvoicing.qr',
        'commercial_runtime.einvoicing.outbox',
        'commercial_runtime.einvoicing.audit',
        'commercial_runtime.einvoicing.worker',
        'commercial_runtime.einvoicing.routes',
        'commercial_runtime.einvoicing.providers.base',
        'commercial_runtime.einvoicing.providers.mock',
        'commercial_runtime.einvoicing.providers.direct_istd',
        'segno',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['pandas', 'eventlet', 'flask_socketio', 'python_socketio'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AuraRetail',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=(os.environ.get('AURA_SPEC_UPX', '1') == '1'),
    console=False,
    version=os.path.join(os.path.dirname(os.path.abspath(SPEC)), 'version_info.txt'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AuraRetail',
)
