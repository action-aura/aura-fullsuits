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

a = Analysis(
    [os.path.join(DESKTOP, 'launcher_clinic.py')],
    pathex=[ROOT, BACKEND],
    binaries=[],
    datas=[
        (FRONTEND, os.path.join('products', 'clinic', 'frontend')),
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
        'api.clinic_api',
        'database.schema',
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
