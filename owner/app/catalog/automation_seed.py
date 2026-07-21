"""Future Retail-automation event/notification catalog (Part S).

Deliberately NOT wired to anything: no table stores these (Part D lists no
table for them), no worker consumes them, no product emits them, no channel is
connected. This module exists purely so a future phase has a fixed, agreed-upon
vocabulary to build against, per the spec's explicit "catalog and contract
preparation only" instruction. Importing this module has zero runtime effect.
"""
from __future__ import annotations

FUTURE_EVENT_CODES = (
    "LOW_STOCK_DETECTED",
    "PRODUCT_OUT_OF_STOCK",
    "DAILY_CLOSING_COMPLETED",
    "SHIFT_CLOSED",
    "SALE_COMPLETED",
    "RETURN_COMPLETED",
    "BACKUP_COMPLETED",
    "BACKUP_FAILED",
    "LICENSE_EXPIRING",
    "LICENSE_EXPIRED",
)

FUTURE_NOTIFICATION_CHANNEL_TYPES = (
    "IN_APP",
    "EMAIL",
    "WHATSAPP",
    "SMS",
)

FUTURE_DELIVERY_STATUSES = (
    "PENDING",
    "SENT",
    "DELIVERED",
    "FAILED",
    "RETRYING",
    "CANCELLED",
)
