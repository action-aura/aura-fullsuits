"""`flask crash-reports prune` -- the 90-day retention for owner_crash_reports.

Same convention as `flask sync prune-events` / `flask reports generate-
scheduled` (app/cli.py): meant to be run by an external scheduler, refuses to
run unless OWNER_SCHEDULER_ROLE is explicitly "owner", and is a dry run unless
--apply is passed.

KNOWN GAP, written down on purpose: the free-tier host does not run scheduled
ops yet (deploy/oracle-free/run_scheduled_ops.sh does not list this command),
so until someone adds `crash-reports prune --apply` there, or runs it by hand,
the table is not pruned. It is bounded in practice -- at most one row per
(installation, bug, build), and a till sends only what its 200-row local cap
holds -- but "bounded in practice" is not "pruned", and it is recorded here
rather than discovered later.

This lives in the crash_reports package and is registered from app/__init__.py
rather than added to app/cli.py: a new command group needs no edit to the
shared CLI module.
"""
from __future__ import annotations

import json

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.group("crash-reports")
    def crash_reports_group():
        """Crash-report maintenance jobs (retention)."""

    @crash_reports_group.command("prune")
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually delete. Default is dry-run (report only).")
    @click.option("--days", default=None, type=int, help="Override the retention window in days (default 90).")
    def prune_cmd(apply_: bool, days: int | None):
        """Deletes crash reports whose last_seen_at is older than the
        retention window (90 days)."""
        from app.crash_reports.intake import RETENTION_DAYS, prune_old_reports
        from app.scheduling import SchedulerNotOwnerError, require_scheduler_owner

        try:
            require_scheduler_owner(app.config)
        except SchedulerNotOwnerError as exc:
            raise click.ClickException(str(exc)) from exc

        window = days if days else RETENTION_DAYS
        matched = prune_old_reports(days=window, dry_run=not apply_)
        click.echo(json.dumps({"dry_run": not apply_, "retention_days": window, "matched": matched}, indent=2))
