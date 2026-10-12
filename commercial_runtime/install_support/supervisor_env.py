"""The environment variables the launcher's restart supervisor hands its child
(INSTALL-1, verifier H1).

THE BUG THIS EXISTS FOR. The supervisor tells its child "you are the child, and
you may exit 75 after a restore" through environment variables. Everything the
child spawns inherits them -- including the update installer, and through it the
freshly installed AuraRetail.exe that Inno relaunches at the end. That new exe
saw "I am the child", skipped supervision, and ran as ONE process with the
75-exit still armed: the first restore after any in-app update (or rollback)
made the till vanish and never come back (measured: server down at 2.8 s, never
up again in 40 s, no process left).

So: whatever the child launches that is NOT itself a supervised child must get
an environment with these removed. Only the supervisor's own Popen of its child
may set them.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

CHILD_ENV = "AURA_LAUNCHER_CHILD"
RESTART_ENV = "AURA_RESTART_ON_RESTORE"

# Everything the supervisor sets for its child and nobody else may inherit.
SUPERVISOR_ENV_VARS = (CHILD_ENV, RESTART_ENV)


def scrubbed_env(environ: Optional[Mapping[str, str]] = None) -> dict:
    """A copy of the environment with every supervisor-internal variable removed
    (case-insensitively: Windows environment names are case-insensitive)."""
    environ = os.environ if environ is None else environ
    drop = {name.upper() for name in SUPERVISOR_ENV_VARS}
    return {k: v for k, v in environ.items() if k.upper() not in drop}
