"""Owner's outbound email.

Why this package exists: Owner sent no email at all (the staff invitation page
said "send it to them manually"), and the products send through AURA_SMTP_*
variables that no installed till has. Putting Action Aura's mailbox password
into an installer would let anyone who extracts it send mail AS Action Aura, so
vendor mail is sent by THIS server and by nothing else. A product asks Owner to
send a message (app/mail/relay.py); it never holds a mailbox password.

Layout:
  smtp.py        the SMTP transport (STARTTLS + AUTH); the only place a password is used
  outbox.py      the queue: state machine over owner_email_outbox
  worker.py      the background sender (started only when OWNER_SMTP_HOST is set)
  templates.py   the message texts the relay renders (a product never supplies a subject or body)
  relay.py       POST /api/licensing/v1/mail: validation, per-installation caps, enqueue
  invitations.py staff/employee invitation mail
  cli.py         `flask email send-test --to <address>`

Default OFF, like every optional channel in this codebase: with OWNER_SMTP_HOST
unset nothing here starts a thread or opens a socket; mail rows simply stay
QUEUED and the UI says email is not set up on this server.
"""
