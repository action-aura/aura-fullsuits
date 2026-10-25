"""`flask notifications ...` -- the push sender and its key.

  send-pending          one sender pass now (the aura-owner-push systemd
                        timer runs this every minute on the live server;
                        it is the same pass the web process's thread runs)
  generate-vapid-keys   make the server's key pair. The PRIVATE key is
                        written only to a redirected stdout, as the one
                        env-file line to append; on a terminal it refuses,
                        so the key never lands on a screen or in a
                        terminal log / scrollback:
                          docker compose ... exec -T owner flask notifications \\
                              generate-vapid-keys >> .env.oracle
                        The public key (not secret) goes to stderr.
  status                is push on, how many devices, what is queued

Registered from app/__init__.py, like app/mail/cli.py.
"""
from __future__ import annotations

import json
import sys

import click
from flask import Flask, current_app
from sqlalchemy import func, select


def _stdout_is_terminal() -> bool:
    return sys.stdout.isatty()


def register_cli(app: Flask) -> None:
    @app.cli.group("notifications")
    def notifications_group():
        """Manager push notifications (Web Push)."""

    @notifications_group.command("send-pending")
    @click.option("--batch-size", default=50, show_default=True, type=click.IntRange(1, 500))
    @click.option("--quiet-when-idle", is_flag=True,
                  help="Print nothing when the pass had nothing to do (the every-minute timer uses this).")
    def send_pending(batch_size: int, quiet_when_idle: bool):
        """Send every due notification now (one pass)."""
        from app.push.worker import run_once

        result = run_once(batch_size=batch_size)
        idle = result.get("ran") and not any(result["expand"].values()) and not result["send"]
        if not (quiet_when_idle and idle):
            click.echo(json.dumps(result, sort_keys=True))
        if not result.get("ran"):
            # Not an error: push is simply off on this server.
            click.echo("Push notifications are off: OWNER_PUSH_VAPID_PRIVATE_KEY is not set (or not valid).", err=True)

    @notifications_group.command("generate-vapid-keys")
    @click.option("--force-terminal", is_flag=True, hidden=True,
                  help="Print the private key even to a terminal (not recommended).")
    def generate_vapid_keys(force_terminal: bool):
        """Make a new VAPID key pair; prints OWNER_PUSH_VAPID_PRIVATE_KEY=... for an env file."""
        from app.push.vapid import generate_private_key_b64

        if _stdout_is_terminal() and not force_terminal:
            raise click.ClickException(
                "Refusing to print a private key to the screen. Redirect it into the env file instead, e.g.\n"
                "  docker compose -f docker-compose.yml --env-file .env.oracle exec -T owner "
                "flask --app app:create_app notifications generate-vapid-keys >> .env.oracle"
            )
        private_b64, public_b64 = generate_private_key_b64()
        click.echo(f"OWNER_PUSH_VAPID_PRIVATE_KEY={private_b64}")
        click.echo(f"New VAPID key pair made. Public key (not secret): {public_b64}", err=True)
        click.echo("Restart the owner service so it reads the new key. Phones that turned notifications on "
                   "with an OLD key must turn them on again (the page does it by itself on the next visit).",
                   err=True)

    @notifications_group.command("status")
    def status():
        """Is push on; devices; deliveries by status."""
        from app.extensions import db_session
        from app.models.push import PushDelivery, PushOutbox, PushSubscription
        from app.push.vapid import keys_from_config

        keys = keys_from_config(current_app.config)
        devices = db_session.execute(select(func.count()).select_from(PushSubscription)).scalar_one()
        outbox = dict(db_session.execute(select(PushOutbox.status, func.count()).group_by(PushOutbox.status)).all())
        deliveries = dict(db_session.execute(select(PushDelivery.status, func.count()).group_by(PushDelivery.status)).all())
        click.echo(json.dumps({
            "push_enabled": keys is not None,
            "public_key": keys.public_key_b64 if keys is not None else None,
            "devices": devices,
            "outbox": outbox,
            "deliveries": deliveries,
        }, sort_keys=True))
