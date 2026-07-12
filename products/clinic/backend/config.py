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

APP_VERSION = '0.1.0'

IS_STANDALONE = bool(getattr(sys, 'frozen', False)) or os.environ.get('AURA_STANDALONE') == '1'
