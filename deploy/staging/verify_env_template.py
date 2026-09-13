"""Does the documented staging template actually start the app?

============================================================================
STANDALONE SCRIPT. NOT A PYTEST FILE. DO NOT PUT IT IN owner/tests/.
============================================================================
owner/tests/conftest.py takes a session-level Postgres advisory lock in an
autouse fixture, so every test collected there needs a live database. This check
needs no database at all -- owner/app/config.py imports standalone -- and making
it depend on Postgres would mean it could not run on a machine that has not been
set up yet, which is exactly the machine this check exists to protect.

    python deploy/staging/verify_env_template.py

WHAT IT CATCHES, and it caught it the day it was written. An operator follows
the documented path: copy .env.staging.example to .env.staging, fill the blank
secrets, `docker compose -f docker-compose.staging.yml up -d`. The owner
container then raises ConfigError and never starts, because
docker-compose.staging.yml sets OWNER_ENV=staging, which makes
owner/app/config.py's validate() enforce rules the template said nothing about:

    OWNER_ALLOWED_HOSTS        default ""   -> refused, an empty allowlist
                                               accepts any Host header
    OWNER_TRUSTED_PROXY_COUNT  default "0"  -> refused, must be >=1 behind a proxy
    OWNER_BACKUP_TARGET_URL    default ""   -> refused, and must not look like a
                                               local path

None of the three was in the template, and compose's own `environment:` block
supplies only OWNER_ENV, OWNER_DATABASE_URL and OWNER_SIGNING_KEY_DIRECTORY.

HOW IT CHECKS. Not by comparing lists of variable names -- a list in a test is a
second copy that drifts the moment validate() grows a rule. It builds the
environment an operator would really have (template + compose's environment
block, with ${...} expanded) and then RUNS StagingConfig.validate() against it.
Whatever validate() requires today is what this enforces today.

The three secrets the template deliberately leaves blank are filled with
throwaway values, because "the operator has not pasted their secrets yet" is not
the failure this is looking for. They are listed explicitly below so that
distinction stays visible.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / "deploy/staging/.env.staging.example"
COMPOSE = ROOT / "docker-compose.staging.yml"

# Blank in the template on purpose: the operator generates these. Filling them
# with throwaway values isolates "the template forgot a variable" (what this
# checks) from "the operator has not filled their secrets yet" (not a defect).
# Long enough to clear validate()'s minimum-length and not-a-placeholder rules.
# Each is 40 characters. validate() enforces a 32-character minimum, and the
# first draft of this file used a 31-character pepper -- which this script
# promptly reported as a template failure. Generating them makes the length
# obvious instead of something to be counted by eye.
_FILLER = "AuraStagingTemplateCheckThrowawayValue00"
OPERATOR_FILLED = {
    "STAGING_DB_PASSWORD": _FILLER,
    "OWNER_SECRET_KEY": _FILLER,
    "OWNER_LICENSE_PEPPER": _FILLER,
}
assert all(len(v) >= 32 for v in OPERATOR_FILLED.values()), (
    "throwaway values must clear validate()'s 32-character minimum, or this "
    "script reports its own placeholders as a template defect")


def parse_env_file(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_compose_owner_environment(path, base):
    """The `environment:` mapping of the `owner:` service, with ${VAR} expanded.

    Deliberately scoped to the owner service: the db service also has an
    `environment:` block, and folding POSTGRES_* in here would misrepresent what
    the app itself receives.
    """
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^  owner:\s*$", text, re.M)
    if not m:
        raise SystemExit("could not find the `owner:` service in docker-compose.staging.yml")
    rest = text[m.end():]
    nxt = re.search(r"^  \w[\w-]*:\s*$", rest, re.M)
    block = rest[:nxt.start()] if nxt else rest

    env_m = re.search(r"^    environment:\s*$", block, re.M)
    if not env_m:
        return {}
    tail = block[env_m.end():]
    out = {}
    for line in tail.splitlines():
        if not line.strip():
            continue
        if not line.startswith("      "):
            break
        if ":" not in line:
            continue
        k, v = line.strip().split(":", 1)
        v = v.strip()
        v = re.sub(r"\$\{(\w+)\}", lambda mm: base.get(mm.group(1), ""), v)
        out[k.strip()] = v
    return out


def main():
    template = parse_env_file(ENV_EXAMPLE)
    # Anti-vacuity: a parser that silently returned {} would make validate()
    # fail for the wrong reason and this script would "catch" a problem that
    # does not exist.
    if len(template) < 5:
        raise SystemExit(f"parsed only {len(template)} keys from {ENV_EXAMPLE.name} -- parser is broken")

    env = dict(template)
    for key, value in OPERATOR_FILLED.items():
        if env.get(key):
            print(f"note: {key} is no longer blank in the template; using its value")
        else:
            env[key] = value

    compose_env = parse_compose_owner_environment(COMPOSE, env)
    if "OWNER_DATABASE_URL" not in compose_env:
        raise SystemExit("did not find OWNER_DATABASE_URL in the owner service's environment "
                         "block -- the compose parser is broken, not the template")
    env.update(compose_env)

    print(f"template keys      : {len(template)}")
    print(f"compose env keys   : {len(compose_env)}  ({', '.join(sorted(compose_env))})")
    print(f"operator-filled    : {', '.join(sorted(OPERATOR_FILLED))}")
    print(f"OWNER_ENV          : {env.get('OWNER_ENV')!r}\n")

    child = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'owner');\n"
         "from app.config import StagingConfig;\n"
         "StagingConfig.validate();\n"
         "print('validate() passed')"],
        cwd=str(ROOT),
        env={**os.environ, **env},
        capture_output=True, text=True, errors="replace",
    )
    out = (child.stdout + child.stderr).strip()
    if child.returncode == 0:
        print("PASS: the documented template starts the app")
        print(f"      {out.splitlines()[-1] if out else ''}")
        return 0

    print("FAIL: an operator following the documented path cannot start the owner container.")
    print("      Add whatever validate() names below to deploy/staging/.env.staging.example.\n")
    for line in out.splitlines()[-20:]:
        print("      " + line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
