"""The numbers that bound the mail relay, in one place (config as data).

Per INSTALLATION, never global: one noisy till must not be able to starve the
others, and a second installation must be unaffected by the first one's usage.
"""
from __future__ import annotations

# Rolling 24 hours
TOTAL_PER_DAY = 50
IDENTITY_PER_DAY = 10          # verify_email + password_reset together
# The same (kind, recipient) from the same installation at most once per this many seconds.
SAME_KIND_TO_SECONDS = 120

IDENTITY_KINDS = frozenset({"verify_email", "password_reset"})

NAME_MAX = 120
LINE_MAX = 200
LINK_MAX = 500
SHOP_MAX = 120
EMAIL_MAX = 254
MAX_ITEMS = 50          # low_stock
MAX_SHIFT_LINES = 40    # shift_close
MAX_REPORT_LINES = 60   # report_summary
QTY_TEXT_MAX = 32
