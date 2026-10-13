"""AUDIT 2026-09-24 gap G2: expiry scan, reconcile, device-limit scan,
sync-event pruning, and report snapshots ran on NEITHER free-hosting path.
This suite proves the fix actually wires real, currently-registered Owner
CLI commands -- not commands that used to exist, or a typo'd path that
would silently no-op under `set -uo pipefail` (no `-e`, deliberately, so one
job's failure doesn't skip the rest -- see run_scheduled_ops.sh's own
header) as an "unknown command" click error, logged as FAILED but easy to
miss in a systemd journal nobody is watching in real time.

The command list is DERIVED from the live Flask app's registered
`app.cli.commands` tree (walked recursively), never a hand-typed list here
-- a hand-typed list would just be a second copy of the same claim the
systemd unit makes, and could drift from the real CLI the same way a
renamed/removed command would silently break the shell script without
breaking this test.

Adversarial verification 2026-09-26 added TestScheduledOpsArgumentsParse:
checking only the command PATH let `--apply` -> `--aply` and
`--period-type daily` -> `--period-type daly` both pass all four original
tests, and either typo would make that job fail every single night (a click
usage error, exit 2) -- the same "logged as FAILED in a journal nobody reads"
outcome this file exists to prevent. The new tests hand each invocation's
FULL argument list to the real Click command's own parser.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

import click

_ORACLE_FREE_DIR = Path(__file__).resolve().parents[1] / "deploy" / "oracle-free"
_RUN_SCHEDULED_OPS_SH = _ORACLE_FREE_DIR / "run_scheduled_ops.sh"
_SERVICE_UNIT = _ORACLE_FREE_DIR / "systemd" / "aura-owner-scheduled-ops.service"
_TIMER_UNIT = _ORACLE_FREE_DIR / "systemd" / "aura-owner-scheduled-ops.timer"


def _registered_cli_command_paths(app) -> set[tuple[str, ...]]:
    """Every command path registered on this app's `flask` CLI, e.g.
    ("commercial", "expiry-scan") or ("sync", "prune-events") -- walked
    from the live Click command tree, exactly as `flask <path...>` would
    need to be typed to reach it."""
    paths: set[tuple[str, ...]] = set()

    def _walk(command: click.Command, prefix: tuple[str, ...]) -> None:
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                _walk(sub, prefix + (name,))
        else:
            paths.add(prefix)

    _walk(app.cli, ())
    return paths


def _invoked_command_paths_from_run_scheduled_ops_sh() -> list[tuple[str, ...]]:
    """Parses every `run_job "<label>" <flask-args...>` call out of the real
    shell script (joining backslash line-continuations first), and reduces
    each call's argument list down to the leading non-flag, non-variable
    words -- the actual `flask <group> <command>` path, stripping trailing
    options like --apply/--period-type/--currency "$var"."""
    text = _RUN_SCHEDULED_OPS_SH.read_text(encoding="utf-8")
    joined = re.sub(r"\\\n\s*", " ", text)  # collapse `\`-continued lines

    calls = re.findall(r'run_job\s+"[^"]*"\s+([^\n]+)', joined)
    assert calls, "expected at least one run_job(...) invocation in run_scheduled_ops.sh -- none were parsed at all"

    paths: list[tuple[str, ...]] = []
    for call in calls:
        words: list[str] = []
        for token in call.split():
            if token.startswith("-") or "$" in token or token.startswith('"'):
                break
            words.append(token)
        assert words, f"could not extract a command path from run_job call: {call!r}"
        paths.append(tuple(words))
    return paths


class TestScheduledOpsUnitsReferenceRealCliCommands:
    def test_run_scheduled_ops_sh_parses_to_at_least_the_four_named_gap_jobs(self):
        """Sanity anchor on the parser itself: if this fails, the regex
        above stopped matching the real file shape and every other
        assertion in this class would be vacuously true."""
        invoked = _invoked_command_paths_from_run_scheduled_ops_sh()
        assert ("commercial", "expiry-scan") in invoked
        assert ("commercial", "device-limit-scan") in invoked
        assert ("sync", "prune-events") in invoked
        assert ("reports", "generate-scheduled") in invoked

    def test_the_sync_prune_runs_as_a_dry_run_only(self):
        """SYNC-JOIN stop-gap (2026-10-13): `sync prune-events --apply`
        deletes history a device activated later can never get back
        (measured: test_sync_late_join_after_prune_cross_stack.py). Until a
        bootstrap path exists the nightly job must stay a dry run; this
        fails the moment `--apply` comes back."""
        script = Path(__file__).resolve().parents[1] / "deploy" / "oracle-free" / "run_scheduled_ops.sh"
        live_lines = [l for l in script.read_text(encoding="utf-8").splitlines()
                      if "prune-events" in l and not l.lstrip().startswith("#")]
        assert live_lines, "the nightly sync prune (dry run) disappeared entirely"
        assert not any("--apply" in l for l in live_lines), live_lines

    def test_every_invoked_command_is_a_real_registered_cli_command(self, app):
        registered = _registered_cli_command_paths(app)
        invoked = _invoked_command_paths_from_run_scheduled_ops_sh()

        unknown = [path for path in invoked if path not in registered]
        assert not unknown, (
            f"run_scheduled_ops.sh invokes {unknown}, which do not match any currently-registered "
            f"`flask` CLI command. Registered commands: {sorted(registered)}"
        )

    def test_service_unit_execstart_runs_run_scheduled_ops_sh(self):
        service_text = _SERVICE_UNIT.read_text(encoding="utf-8")
        execstart_lines = [line for line in service_text.splitlines() if line.startswith("ExecStart=")]
        assert len(execstart_lines) == 1, f"expected exactly one ExecStart= line, found {len(execstart_lines)}"
        assert "run_scheduled_ops.sh" in execstart_lines[0]
        # Both this and WorkingDirectory must share the same templated
        # placeholder -- bootstrap.sh substitutes it in one pass; a mismatch
        # here would mean bootstrap.sh's `sed` only fixed one of the two.
        assert "__COMPOSE_DIR__" in execstart_lines[0]
        working_dir_lines = [line for line in service_text.splitlines() if line.startswith("WorkingDirectory=")]
        assert len(working_dir_lines) == 1
        assert "__COMPOSE_DIR__" in working_dir_lines[0]

    def test_timer_is_wired_to_the_matching_service_via_install_section(self):
        # Timer units associate to the service of the same base name by
        # systemd convention (no explicit Unit= needed) -- this just
        # confirms the file exists, parses as a timer, and is installable.
        timer_text = _TIMER_UNIT.read_text(encoding="utf-8")
        assert "[Timer]" in timer_text
        assert "OnCalendar=" in timer_text
        assert "[Install]" in timer_text
        assert "WantedBy=timers.target" in timer_text


# Stand-in for shell variables when parsing an invocation's arguments: the
# script only ever interpolates $report_currency (read from .env.oracle), so
# any string is a valid stand-in for Click's parser -- the variable's real
# value is checked at runtime by the script itself (REPORT_CURRENCY empty ->
# logged FAILED, exit 1).
_SHELL_VARIABLE_STAND_IN = "JOD"


def _invoked_argv_from_run_scheduled_ops_sh() -> list[list[str]]:
    """Every run_job call's FULL flask argument list (path + options), shell-
    tokenized the way bash would split it, with "$var" words replaced by a
    stand-in value."""
    text = _RUN_SCHEDULED_OPS_SH.read_text(encoding="utf-8")
    joined = re.sub(r"\\\n\s*", " ", text)  # collapse `\`-continued lines, same as above
    calls = re.findall(r'run_job\s+"[^"]*"\s+([^\n]+)', joined)
    assert calls, "expected at least one run_job(...) invocation in run_scheduled_ops.sh -- none were parsed at all"
    return [
        [_SHELL_VARIABLE_STAND_IN if "$" in word else word for word in shlex.split(call, posix=True)]
        for call in calls
    ]


def _resolve_command(app, argv: list[str]) -> tuple[click.Command, list[str], list[str]]:
    """Walks app.cli by the leading words of argv; returns (command, path, remaining args)."""
    command: click.Command = app.cli
    path: list[str] = []
    rest = list(argv)
    while isinstance(command, click.Group) and rest and rest[0] in command.commands:
        path.append(rest[0])
        command = command.commands[rest.pop(0)]
    return command, path, rest


class TestScheduledOpsArgumentsParse:
    def test_every_invocation_parses_against_the_real_click_command(self, app):
        problems = []
        for argv in _invoked_argv_from_run_scheduled_ops_sh():
            command, path, rest = _resolve_command(app, argv)
            if isinstance(command, click.Group):
                problems.append(f"{argv}: does not resolve to a leaf command (stopped at {path})")
                continue
            try:
                # Parse only -- make_context never invokes the callback.
                command.make_context(path[-1], rest)
            except click.ClickException as exc:
                problems.append(f"{argv}: {exc.format_message()}")
        assert not problems, "run_scheduled_ops.sh passes arguments the real CLI rejects:\n" + "\n".join(problems)

    def test_every_report_period_type_is_scheduled(self, app):
        """generate_snapshot() is idempotent per period (operational_reports/
        scheduler.py returns the existing PUBLISHED row), so running weekly and
        monthly every night is safe -- and without them WEEKLY_/MONTHLY_
        OPERATIONAL_SUMMARY are never generated at all. The expected set is
        read from the real --period-type Choice, not typed here."""
        command, _path, _rest = _resolve_command(app, ["reports", "generate-scheduled"])
        period_option = next(p for p in command.params if p.name == "period_type")
        expected = set(period_option.type.choices)

        scheduled = set()
        for argv in _invoked_argv_from_run_scheduled_ops_sh():
            if argv[:2] == ["reports", "generate-scheduled"] and "--period-type" in argv:
                scheduled.add(argv[argv.index("--period-type") + 1])
        assert scheduled == expected, f"scheduled period types {sorted(scheduled)} != CLI choices {sorted(expected)}"


class TestCleanupJobsAreScheduled:
    """2026-10-10: crash-report pruning, quote expiry and the nonce /
    idempotency / rate-limit purges all existed with no caller. Each must be an
    --apply run_job line (a dry-run line would schedule nothing)."""

    def test_crash_report_prune_and_housekeeping_run_with_apply(self):
        invoked = _invoked_argv_from_run_scheduled_ops_sh()
        assert ["crash-reports", "prune", "--apply"] in invoked
        assert ["commercial", "housekeeping", "--apply"] in invoked
