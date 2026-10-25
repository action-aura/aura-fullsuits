"""Manager push notifications (Step 1 of the Aura Growth Plan, 2026-10-25).

When a seller records a sale with "Sell a licence", every manager who may
confirm it (and is not its seller or recorder) gets a notification on each
phone where they turned notifications on; the first to confirm wins (the
confirm itself is already race-safe, commercial_sales/direct_sale.py).
Managers without push see a count badge in the top bar and the menu.

Layout:
  vapid.py          the server key (OWNER_PUSH_VAPID_PRIVATE_KEY); the kill switch
  webpush.py        RFC 8291 encryption + the POST to the push service; endpoint allow-list
  outbox.py         the outbox row, written in the sale's own transaction
  recipients.py     who may be told (decided at send time)
  subscriptions.py  a staff member's devices: on, off, removed on deactivation
  worker.py         expand -> per-device send, burst rule, retries/backoff
  routes.py         POST /push/subscribe, /push/unsubscribe
  cli.py            flask notifications send-pending | generate-vapid-keys | status

Default OFF, like every optional channel here (email, e-invoicing, licence
enforcement): with no VAPID key nothing starts a thread, no outbox row is
written, the "turn on" card is not shown, and /push/* answers 404.

Deliberately imports nothing at package level: commercial_sales/direct_sale.py
imports app.push.outbox, and app.push.recipients imports direct_sale back.
"""
