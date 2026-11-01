"""`flask sellers scan` -- the periodic part of the seller rules.

One command, called by the nightly scheduled-ops job (deploy/oracle-free/run_scheduled_ops.sh, the same
mechanism every other Owner scan uses; no new cron). Each step is idempotent -- a stamped / already-applied
row is never acted on twice -- so it is safe to run as often as wanted and by two hosts at once, which is
why it has no scheduler-owner gate (like `commercial apply-licence-stops`). Dry-run unless --apply is given.
"""
from __future__ import annotations

import json

import click


def register_cli(app) -> None:
    @app.cli.group("sellers")
    def sellers_group():
        """Seller agreement rules: cash hand-in, customer ownership, sanctions, termination."""

    @sellers_group.command("scan")
    @click.option("--apply", "apply_", is_flag=True, default=False,
                  help="Actually announce / change things. Default is dry-run (counts only).")
    def scan_cmd(apply_: bool):
        """Runs every periodic seller rule once and prints what each did."""
        from app.sellers import cash_handin, ownership

        result = {
            "dry_run": not apply_,
            "cash_overdue": cash_handin.scan_overdue(apply=apply_),
            "customer_ownership": ownership.scan_lapsed(apply=apply_),
        }
        click.echo(json.dumps(result, indent=2))
