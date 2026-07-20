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

a = Analysis(
    [os.path.join(DESKTOP, 'launcher_retail.py')],
    pathex=[ROOT, BACKEND],
    binaries=[],
    datas=[
        (FRONTEND, os.path.join('products', 'retail', 'frontend')),
    ],
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
