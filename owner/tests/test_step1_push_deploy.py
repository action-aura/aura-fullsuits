"""Step 1 (2026-10-25) -- the push sender is actually wired on the live path.

Same discipline as test_oracle_free_scheduled_ops_units.py: a timer that
calls a command the CLI does not have, or a variable config.py does not know
(OWNER_STRICT_CONFIG=true refuses to boot on it), fails here rather than
silently on the VM.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import click

OWNER = Path(__file__).resolve().parents[1]
ORACLE = OWNER / "deploy" / "oracle-free"


def _flask_argv_from_sender_script() -> list[str]:
    text = re.sub(r"\\\n\s*", " ", (ORACLE / "run_push_sender.sh").read_text(encoding="utf-8"))
    line = next(line for line in text.splitlines() if line.strip().startswith("exec docker compose"))
    words = shlex.split(line, posix=True)
    return words[words.index("app:create_app") + 1:]


def test_the_sender_script_calls_a_real_command_with_arguments_it_accepts(app):
    argv = _flask_argv_from_sender_script()
    assert argv == ["notifications", "send-pending", "--quiet-when-idle"]
    command = app.cli
    path = []
    while isinstance(command, click.Group) and argv and argv[0] in command.commands:
        path.append(argv[0])
        command = command.commands[argv.pop(0)]
    assert not isinstance(command, click.Group), path
    command.make_context(path[-1], argv)  # raises on an unknown option


def test_the_service_and_timer_are_templated_and_run_every_minute():
    service = (ORACLE / "systemd" / "aura-owner-push.service").read_text(encoding="utf-8")
    execstart = [line for line in service.splitlines() if line.startswith("ExecStart=")]
    assert execstart == ["ExecStart=/usr/bin/env bash __COMPOSE_DIR__/run_push_sender.sh"]
    assert "WorkingDirectory=__COMPOSE_DIR__" in service and "Type=oneshot" in service
    timer = (ORACLE / "systemd" / "aura-owner-push.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=60s" in timer and "OnBootSec=" in timer
    assert "[Install]" in timer and "WantedBy=timers.target" in timer


def test_bootstrap_installs_the_timer_only_when_a_key_is_set():
    text = (ORACLE / "bootstrap.sh").read_text(encoding="utf-8")
    block = text[text.index("# 7c."):text.index("cat <<'BANNER'")]
    assert 'if [ -n "$(env_value OWNER_PUSH_VAPID_PRIVATE_KEY)" ]' in block
    assert "aura-owner-push.service" in block and "enable --now aura-owner-push.timer" in block
    assert 's|__COMPOSE_DIR__|$COMPOSE_DIR|g' in block


def test_the_variables_are_known_to_config_and_documented_in_both_env_examples():
    from app.config import KNOWN_CONFIG_ENV_VARS

    for name in ("OWNER_PUSH_VAPID_PRIVATE_KEY", "OWNER_PUSH_VAPID_SUBJECT"):
        assert name in KNOWN_CONFIG_ENV_VARS
        for example in (OWNER / ".env.example", ORACLE / ".env.example"):
            lines = example.read_text(encoding="utf-8").splitlines()
            assert any(line.startswith(name + "=") for line in lines), (name, example)
    # The examples ship push OFF: an empty key, never a real-looking one.
    for example in (OWNER / ".env.example", ORACLE / ".env.example"):
        key_lines = [line for line in example.read_text(encoding="utf-8").splitlines()
                     if line.startswith("OWNER_PUSH_VAPID_PRIVATE_KEY=")]
        assert key_lines == ["OWNER_PUSH_VAPID_PRIVATE_KEY="]


def test_no_new_dependency_was_needed():
    """webpush.py is built on cryptography + pyjwt, both already pinned; the
    real-browser proof is what justified not adding pywebpush (+ aiohttp)."""
    requirements = (OWNER.parent / "requirements" / "owner-server.txt").read_text(encoding="utf-8").lower()
    assert "pywebpush" not in requirements and "aiohttp" not in requirements
    assert "cryptography==" in requirements and "pyjwt==" in requirements
