"""Serve Owner on 127.0.0.1:$OWNER_UI_PORT. Run with the .venv-owner python."""
import logging
import os
import sys
from pathlib import Path

OWNER = Path("owner").resolve()
os.chdir(OWNER)
sys.path.insert(0, str(OWNER))

os.environ.setdefault(
    "OWNER_DATABASE_URL",
    "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_uishots")
os.environ.setdefault("OWNER_SECRET_KEY", "uishots-local-only-not-for-production")
os.environ.setdefault("OWNER_LICENSE_PEPPER", "uishots-local-only-pepper")
# Deliberately NOT the external API: config.py refuses it without
# SESSION_COOKIE_SECURE, which needs HTTPS and is irrelevant to a UI sweep.
os.environ.pop("OWNER_EXTERNAL_API_ENABLED", None)

from app import create_app  # noqa: E402

logging.getLogger("werkzeug").setLevel(logging.ERROR)
app = create_app()

from werkzeug.serving import make_server  # noqa: E402

port = int(os.environ.get("OWNER_UI_PORT", "5651"))
server = make_server("127.0.0.1", port, app, threaded=True)
print("OWNER READY %d" % port, flush=True)
server.serve_forever()
