"""`flask seller-discipline scan` -- run the periodic pass once (see scan.py).

Its own command group on purpose: the cash hand-in / customer-ownership scans live in a `sellers`
group on another branch, and two branches registering the same click group name would silently
replace each other's commands at merge."""
from __future__ import annotations

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.group("seller-discipline")
    def discipline_group():
        """Seller sanctions and terminations."""

    @discipline_group.command("scan")
    def discipline_scan():
        """Ends suspensions whose date has passed, applies 7-day notices that are due, and releases finished trial credits."""
        from app.seller_discipline.scan import run_due

        result = run_due()
        click.echo("suspensions ended: {suspensions_ended}; terminations applied: {terminations_applied}; "
                   "trial credits released: {trial_credits_released}".format(**result))
