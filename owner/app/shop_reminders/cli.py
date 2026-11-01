"""`flask notices shop-reminders [--apply]` -- the nightly pass that queues the shop reminder mails.

Its own click group (`notices`) so it cannot replace another branch's group of the same name at
merge: it holds only this one command."""
from __future__ import annotations

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.group("notices")
    def notices_group():
        """Mails the product sends on a clock."""

    @notices_group.command("shop-reminders")
    @click.option("--apply", "apply_", is_flag=True, default=False,
                  help="Actually queue the mails. Default is a dry run that only lists what would be sent.")
    @click.option("--as-of", "as_of", type=click.DateTime(formats=["%Y-%m-%d"]), default=None,
                  help="Pretend today is this date (YYYY-MM-DD). For checking a day ahead in a dry run.")
    def shop_reminders_cmd(apply_: bool, as_of):
        """Trial ending / ended, renewal due, grace started, selling paused. One mail per
        subscription + kind + period, so running it again the same night sends nothing new."""
        from app.shop_reminders import service

        result = service.run(apply=apply_, as_of=as_of.date() if as_of else None)
        mode = "APPLY" if apply_ else "DRY RUN (nothing queued; add --apply)"
        click.echo(f"shop reminders -- {mode}; email set up: {result['email_set_up']}")
        for item in result["items"]:
            click.echo(f"  {item['outcome']:<15} {item['kind']:<24} subscription {item['subscription_id']}")
        click.echo(f"due: {result['due']}; queued: {result['queued']}; would queue: {result['would_queue']}; "
                   f"already queued: {result['already_queued']}; not sent: {result['not_sent']}")
