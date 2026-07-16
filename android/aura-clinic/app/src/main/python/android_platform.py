"""
Android platform abstraction — storage path layout ONLY.

This is the mobile counterpart to the desktop path logic in aura_core. It does not
contain any business logic; it just maps the Android app's private files directory
(passed in from Java) to the canonical bundle/data sub-paths the shared core expects.

Layout under the app's private filesDir (sandboxed, wiped on uninstall):
    <filesDir>/bundle/   read-only web/app assets extracted from the APK
                         (static/, templates/, config.json) — re-extracted on update
    <filesDir>/data/     writable: database/, logs/, uploads/, config.json
"""

import os

BUNDLE_SUBDIR = 'bundle'
DATA_SUBDIR = 'data'


def resolve(files_dir: str) -> dict:
    """Given the Android app's private filesDir, return {'bundle','data'} absolute
    paths and ensure the writable data dir exists. The bundle dir is created/filled
    by the Java AssetInstaller before the server starts."""
    bundle = os.path.join(files_dir, BUNDLE_SUBDIR)
    data = os.path.join(files_dir, DATA_SUBDIR)
    os.makedirs(data, exist_ok=True)
    return {'bundle': bundle, 'data': data}
