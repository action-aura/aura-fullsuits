"""One exception type for every bootstrap refusal, carrying a stable reason code.

A refusal is a normal, expected outcome here (wrong shop, newer schema, an open
cash drawer), not a crash: callers map `reason_code` to a message a person can
act on and to an HTTP status on the hub. `message` is safe to log.
"""
from __future__ import annotations


class BootstrapError(Exception):
    def __init__(self, reason_code: str, message: str = ""):
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message or reason_code


# Reason codes. Producer side:
UNCLASSIFIED_TABLE = "UNCLASSIFIED_TABLE"        # a table nobody gave a class (refuse, never guess)
PRODUCER_NOT_ON_RELAY = "PRODUCER_NOT_ON_RELAY"  # the copy's own cursor is bound to a different relay
NO_SIGNER = "NO_SIGNER"                          # this computer cannot sign the manifest
PRODUCER_FAILED = "PRODUCER_FAILED"
# Installer side:
BAD_PACKAGE = "BAD_PACKAGE"                      # not a zip / wrong members / unreadable manifest
MANIFEST_NOT_TRUSTED = "MANIFEST_NOT_TRUSTED"    # signature missing, forged, or from another licence
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
COMPANY_MISMATCH = "COMPANY_MISMATCH"            # a copy of a DIFFERENT shop
SCHEMA_NEWER = "SCHEMA_NEWER"                    # copy is newer than this build can read
MIGRATION_FAILED = "MIGRATION_FAILED"
OPEN_CASH_SESSION = "OPEN_CASH_SESSION"          # a drawer is open: swapping would erase uncounted money
NOT_STAGED = "NOT_STAGED"
