"""`flask email send-test --to <address>` -- one real message, synchronously.

Used for the real-world outbound proof (does the mailbox actually deliver, and
do SPF / DKIM / DMARC pass at the receiving end?). It bypasses the queue on
purpose: the point is to see the SMTP server's own answer line on the terminal.
Prints the server's reply to DATA; on failure prints the exception CLASS only
(an SMTP error text can echo the login) and exits non-zero. The password is
never printed.

Registered from app/__init__.py, like app/crash_reports/cli.py, so a new
command group needs no edit to the shared CLI module.
"""
from __future__ import annotations

import ssl
from typing import Optional

import click
from flask import Flask, current_app

# Tests point this at a context that trusts their self-signed fake server.
# Production leaves it None, which means the default (verifying) context.
SSL_CONTEXT: Optional[ssl.SSLContext] = None


def register_cli(app: Flask) -> None:
    @app.cli.group("email")
    def email_group():
        """Owner email tools."""

    @email_group.command("send-test")
    @click.option("--to", "to_address", required=True, help="Address to send the test message to.")
    def send_test(to_address: str):
        """Sends one test message through OWNER_SMTP_* and prints the SMTP result line."""
        from app.mail import smtp, templates
        from app.mail.worker import REPLY_TO

        settings = smtp.settings_from_config(current_app.config)
        if settings is None:
            raise click.ClickException("OWNER_SMTP_HOST is not set: email is not set up on this server.")
        if any(ch in to_address for ch in "\r\n\x00") or "@" not in to_address:
            raise click.ClickException("That does not look like an email address.")
        subject, body_text, body_html = templates.render("test")
        try:
            code, resp = smtp.send_message(settings, to=to_address, subject=subject, body_text=body_text,
                                           body_html=body_html, reply_to=REPLY_TO, ssl_context=SSL_CONTEXT)
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(f"Send failed: {type(exc).__name__}") from None
        click.echo(f"SMTP result: {code} {resp}")
        click.echo(f"From: {settings.from_name} <{settings.from_address}>  To: {to_address}")
