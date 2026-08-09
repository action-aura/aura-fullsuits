"""
Aura Retail -- one-click demo/presentation launcher.

Meant to sit next to AuraRetail.exe (in the same dist folder) and be
double-clicked instead of the app itself. Makes the whole thing portable:
points AURA_APP_DATA at a folder next to this launcher (not the machine's
per-user %LOCALAPPDATA%), so the app's database -- including whatever
license activation and demo seed data it was carrying -- travels with the
folder when it's copied to another machine, instead of starting fresh
there.

Not itself a Flask/webview app -- just resolves the portable data path and
execs the real AuraRetail.exe with it set, then exits. Compiled to its own
small .exe via PyInstaller (see build_demo_launcher.spec) so double-clicking
it on a machine with no Python installed still works.

Owner/sync connectivity (owner_config.txt, optional, plain text next to this
launcher) is read at RUN time, not baked into the compiled exe -- the venue's
actual LAN/IP is not knowable at build time, and the whole point of this file
is that fixing a wrong IP the night before Thursday means editing one text
file, not asking for a rebuild. Missing file or missing keys just means
"Owner not configured" (sync/licensing inert), the same safe default the app
already has everywhere else -- never a crash.

Expected owner_config.txt format (KEY=value, one per line, # comments ok):
    OWNER_URL=https://<owner-host>:5551
    CA_BUNDLE=owner-cert.pem
CA_BUNDLE is resolved relative to this launcher's own folder if not absolute.
"""
import os
import subprocess
import sys
import ctypes

HERE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, 'frozen', False) else __file__))
APP_EXE = os.path.join(HERE, 'AuraRetail', 'AuraRetail.exe')
DATA_DIR = os.path.join(HERE, 'AuraRetail_Data')
OWNER_CONFIG_FILE = os.path.join(HERE, 'owner_config.txt')


def _fail(message: str):
    ctypes.windll.user32.MessageBoxW(0, message, 'Aura Retail Demo', 0x10)
    sys.exit(1)


def _read_owner_config() -> dict:
    if not os.path.isfile(OWNER_CONFIG_FILE):
        return {}
    values = {}
    with open(OWNER_CONFIG_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()
    return values


def main():
    if not os.path.isfile(APP_EXE):
        _fail(
            f'AuraRetail.exe not found at:\n{APP_EXE}\n\n'
            'This launcher must sit next to the AuraRetail folder '
            '(same place they were both copied to).'
        )

    os.makedirs(DATA_DIR, exist_ok=True)
    env = os.environ.copy()
    env['AURA_APP_DATA'] = DATA_DIR
    env.setdefault('AURA_STANDALONE', '0')  # dev/demo mode: sample-data seeding path stays available

    owner_cfg = _read_owner_config()
    owner_url = owner_cfg.get('OWNER_URL', '')
    if owner_url:
        env['AURA_OWNER_LICENSING_URL'] = owner_url
        env['AURA_SYNC_RELAY_URL'] = owner_url
        ca_bundle = owner_cfg.get('CA_BUNDLE', '')
        if ca_bundle:
            if not os.path.isabs(ca_bundle):
                ca_bundle = os.path.join(HERE, ca_bundle)
            if os.path.isfile(ca_bundle):
                env['AURA_OWNER_LICENSING_CA_BUNDLE'] = ca_bundle
                env['AURA_SYNC_RELAY_CA_BUNDLE'] = ca_bundle
            else:
                _fail(
                    f'owner_config.txt points CA_BUNDLE at:\n{ca_bundle}\n\n'
                    'That file does not exist. Fix the path (or remove the '
                    'CA_BUNDLE line to run without Owner connectivity) and try again.'
                )

    try:
        subprocess.Popen([APP_EXE], env=env, cwd=os.path.dirname(APP_EXE))
    except Exception as exc:
        _fail(f'Could not start Aura Retail:\n{exc}')


if __name__ == '__main__':
    main()
