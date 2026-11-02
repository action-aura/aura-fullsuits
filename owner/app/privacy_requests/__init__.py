"""Personal-data requests (Alembic DR1): a customer asks for a copy of what we hold about them, or
for it to be deleted. Staff-only (permission privacy.requests).

  service.py   logging a request (30-day due date), closing it, the counts for the dashboard
  export.py    the ZIP: a JSON of everything held + the ID / registration photos decrypted
  erase.py     the erasure: anonymise the person, delete the photos, keep invoices and payments
  panel.py     the card on the customer page
  routes.py    the screens
"""
