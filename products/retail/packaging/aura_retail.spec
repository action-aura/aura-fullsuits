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
# BUILD-VERIFIED 2026-08-30. This comment previously read "NOT YET BUILD-VERIFIED
# in this environment (no PyInstaller/Windows build toolchain run as part of this
# extraction)", which was true when written and had since become the most
# load-bearing stale claim in the repo -- it was read as "the desktop product may
# not package at all", which materially affected how close to sellable this suite
# looked.
#
# Measured, not assumed. PyInstaller 6.21.0 on Python 3.14, Windows, from the repo
# root exactly as the command above prescribes:
#
#     Building EXE from EXE-00.toc completed successfully.
#     Building COLLECT COLLECT-00.toc completed successfully.
#     Build complete!            -> dist/AuraRetail/AuraRetail.exe, 10.6 MB, exit 0
#
# What that does and does NOT establish, stated so the next reader does not
# over-read it the way the old comment was under-read:
#   * ESTABLISHED: the spec is valid, every hidden import and data file it names
#     resolves, and a complete one-folder distribution is produced.
#   * NOT established: that the exe RUNS correctly on a clean machine. It was not
#     launched here, and a dev box carries state a customer's will not. That is
#     step 2.2 of docs/release/go-live-runbook.md and it stays owed.
#   * NOT established: the Inno Setup installer (aura_retail_setup.iss). Inno Setup
#     is not installed on this dev machine, so setup.exe remains unbuilt and
#     unverified -- see the runbook.
#
# RUN-VERIFIED 2026-09-08. Both "NOT established" bullets above are now closed,
# and they are left standing rather than deleted so the sequence stays legible:
# packaging was proved first, running second, a month apart.
#
# The build was made from a `git archive HEAD` snapshot rather than the working
# tree, so no half-finished edit could get into it, and the exe was launched with
# every inherited AURA_* variable stripped and AURA_APP_DATA pointed at a
# directory that had never existed -- a dev box's leftover environment is exactly
# how a packaging defect hides.
#
#     launcher starting -> no secret key found, generating one
#     server on 127.0.0.1:5001, ready after 3 attempts, 5.87s
#     GET /api/version -> 200   GET / -> 200, 5939 bytes
#     retail.db migrated v0 -> v26, 46 tables, pre-migration backup taken
#     stderr: empty
#
# The migration chain is the part worth noting: ensure_schema_version() ran
# inside the frozen build and took its integrity-checked backup before touching
# anything, which is the behaviour the whole migration_safety module exists for
# and the thing most likely to be silently missing from a packaged app.
#
# The installer was then built (Inno Setup 6 is now installed) and exercised end
# to end: silent install exit 0, 920 files / 93.3 MB, the INSTALLED copy served
# GET / -> 200, silent uninstall exit 0, program files gone, business data kept.
#
# Still NOT established: behaviour on a machine that is not this one. Everything
# above removes the dev environment from the picture; it cannot remove the dev
# MACHINE. A second Windows box remains step 2.2's real remainder.

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

# retail-hardware-viewports (2026-09-24) -- owner_endpoint.json, the
# release-baked OWNER_LICENSING_BASE_URL (config.py's
# _resolve_owner_licensing_base_url()). Same "PyInstaller's static
# import-graph analysis cannot see a plain JSON data file, so it must be
# listed here explicitly or a commercial build silently ships without it"
# reasoning as trust_anchor.json immediately above -- an installed copy
# launched from the Start menu has no AURA_OWNER_LICENSING_URL environment
# variable and no screen to set one, so THIS is its only way to ever learn
# where Owner is and be able to activate.
#
# Bundled to the ROOT of the frozen bundle ('.', i.e. BASE_DIR /
# sys._MEIPASS at runtime) rather than into a subpackage the way
# trust_anchor.json is: config.py resolves it as
# `os.path.join(BASE_DIR, OWNER_ENDPOINT_FILENAME)`, a product-level build
# artifact, not commercial_runtime package data.
#
# Written by scripts/release/build_desktop_installer.ps1 to
# build/release/owner_endpoint.json immediately before this spec runs, and
# ALWAYS deleted again by that script's `finally` block afterward -- so it is
# gitignored and build-time-only, exactly like trust_anchor.json. A
# dev/source build run without that script simply has no such file, and
# config.py's own docstring is explicit that a missing baked file is the
# ordinary case, not an error: it resolves to '' silently, same as today.
_owner_endpoint = os.path.join(ROOT, 'build', 'release', 'owner_endpoint.json')
if os.path.exists(_owner_endpoint):
    _licensing_datas.append((_owner_endpoint, '.'))

# Fail-CLOSED, but only for a RELEASE build (AURA_RELEASE_BUILD=1, set by
# build_desktop_installer.ps1 around the PyInstaller invocation and always
# cleared again in that script's `finally` block -- never left set for an
# ordinary developer build).
#
# WHY THIS EXISTS: the published rc.7 installer shipped WITHOUT
# trust_anchor.json. This spec has only ever included it "when present" (see
# the comment above _trust_anchor), so a release cut without one still
# produced a complete, exit-0 build -- and that build could never verify a
# single Owner response, so it could never activate. Nothing here said so at
# build time; it was only discoverable by trying to activate a real install
# and watching it fail. A dev/source build (the variable unset) keeps
# today's lenient behavior on purpose -- both files are still genuinely
# optional there, and this spec must never require a release engineer's
# trust anchor or baked URL just to `pyinstaller ... --noconfirm` locally.
if os.environ.get('AURA_RELEASE_BUILD') == '1':
    _release_problems = []
    if not os.path.exists(_trust_anchor):
        _release_problems.append(f"trust_anchor.json missing at {_trust_anchor}")
    if not os.path.exists(_owner_endpoint):
        _release_problems.append(f"owner_endpoint.json missing at {_owner_endpoint}")
    if _release_problems:
        raise SystemExit(
            "AURA_RELEASE_BUILD=1 but this build is not release-ready: "
            + "; ".join(_release_problems)
            + ". A release build must never ship without a way to verify Owner's "
              "signed responses or a baked Owner licensing URL -- fix the inputs "
              "and re-run, or unset AURA_RELEASE_BUILD for a lenient dev/source build."
        )

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

# tzdata is DATA, not a module, so hiddenimports cannot carry it and a bare
# `import tzdata` in the spec would not help either -- `zoneinfo` reads the
# package's .tzif files off disk.
#
# Without this the packaged .exe has no timezone database at all: Windows ships
# none, so `zoneinfo.ZoneInfo('Asia/Amman')` raises and every shop that has
# configured a business timezone silently falls back to bucketing reports on
# whatever clock the device happens to hold. That is precisely the defect the
# business-date feature exists to fix, and it would announce itself only in a
# log line nobody reads.
#
# Measured on the build machine before adding it: `zoneinfo.TZPATH` is `()`.
# Same failure shape as the `cryptography` omission on the Android side --
# present in requirements, absent from the bundle, and only reproducible on a
# real install rather than in a source checkout.
_tzdata_datas = collect_data_files('tzdata')

a = Analysis(
    [os.path.join(DESKTOP, 'launcher_retail.py')],
    pathex=[ROOT, BACKEND],
    binaries=[],
    datas=[
        (FRONTEND, os.path.join('products', 'retail', 'frontend')),
    ] + _licensing_datas + _certifi_datas + _tzdata_datas,
    hiddenimports=[
        'flask', 'flask_cors', 'werkzeug', 'waitress',
        'commercial_runtime.identity.mt_auth',
        'commercial_runtime.identity.auth_routes',
        'commercial_runtime.identity.onboarding_routes',
        'commercial_runtime.identity.registry_db',
        # registry.db v3 (docs/launch-readiness/multi-device-design.md §6).
        # account_schema is imported INSIDE registry_db._migrate_registry_schema
        # rather than at module scope, and it runs during first-launch
        # migration -- i.e. before the server starts, on the one code path
        # where a missing module is not a degraded feature but a build that
        # cannot boot at all. Listed explicitly for the reason this file
        # already gives below: nothing in commercial_runtime.* is left to
        # PyInstaller's static analysis.
        'commercial_runtime.identity.account_schema',
        'commercial_runtime.identity.user_accounts',
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
