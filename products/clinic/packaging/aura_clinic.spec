# -*- mode: python ; coding: utf-8 -*-
#
# Aura Clinic -- Windows desktop build spec -> dist/AuraClinic/AuraClinic.exe
#
# Mirrors products/retail/packaging/aura_retail.spec -- see that file's
# comments for the full rationale (self-contained, not delegating to the
# source monolith's aura_enterprise.spec).
#
# Build (from the aura-fullsuits repo root):
#   pyinstaller products/clinic/packaging/aura_clinic.spec --noconfirm

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), '..', '..', '..'))
BACKEND = os.path.join(ROOT, 'products', 'clinic', 'backend')
FRONTEND = os.path.join(ROOT, 'products', 'clinic', 'frontend')
DESKTOP = os.path.join(ROOT, 'products', 'clinic', 'desktop')

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

a = Analysis(
    [os.path.join(DESKTOP, 'launcher_clinic.py')],
    pathex=[ROOT, BACKEND],
    binaries=[],
    datas=[
        (FRONTEND, os.path.join('products', 'clinic', 'frontend')),
    ] + _licensing_datas,
    hiddenimports=[
        'flask', 'flask_cors', 'werkzeug', 'waitress',
        'commercial_runtime.identity.mt_auth',
        'commercial_runtime.identity.auth_routes',
        'commercial_runtime.identity.onboarding_routes',
        'commercial_runtime.identity.registry_db',
        # registry.db is shared with Retail, so its v3 migration modules ship
        # in this bundle too -- see the identical entry (and its reasoning) in
        # products/retail/packaging/aura_retail.spec. Clinic is otherwise out
        # of scope for that change; omitting these would mean a Retail-side
        # migration silently breaking the Clinic executable's first launch.
        'commercial_runtime.identity.account_schema',
        'commercial_runtime.identity.user_accounts',
        'commercial_runtime.security.app_secret',
        'commercial_runtime.security.passwords',
        'commercial_runtime.security.audit',
        'commercial_runtime.security.modes',
        'commercial_runtime.backup.service',
        'commercial_runtime.backup.routes',
        'commercial_runtime.launcher_support',
        'api.clinic_api',
        'database.schema',
        # docs/einvoicing/phase1/ -- Jordan JoFotara e-invoicing. See
        # products/retail/packaging/aura_retail.spec's identical block for
        # why these are listed explicitly rather than left to PyInstaller's
        # static analysis.
        'core.clinic.einvoice_adapter',
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
    name='AuraClinic',
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
    name='AuraClinic',
)
