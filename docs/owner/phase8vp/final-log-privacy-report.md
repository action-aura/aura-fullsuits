# Phase 8V-P — Final Log Privacy Report (Part P)

## Android Logcat: NOT VERIFIED (no device)

## Windows: real product logs reviewed, from the actual real runs this session

`AuraClinic.exe`/`AuraRetail.exe` stdout (captured to `/tmp/clinic_product*.log`,
`/tmp/retail_product*.log` this session) and Owner's own server log
(`/tmp/owner_server*.log`) were both grepped for the full forbidden-category list (full license key,
device private key, internal sync secret, Owner private signing key, signing-keystore password,
database credentials, raw request bodies, patient identifiers, diagnoses, prescriptions,
Clinic invoices/payments, Retail sale details, stock quantities, suppliers, transaction totals,
stack traces containing secrets) — **zero matches** across every real log file produced this
session. The only secret-adjacent content that ever appeared was the literal `500` traceback that
led to this session's `DEVICE_ALREADY_REGISTERED` fix (`sqlalchemy.exc.IntegrityError:
... duplicate key value violates unique constraint "owner_device_public_keys_fingerprint_key"`) --
a SQL constraint name and a fingerprint hash, not a secret.

`api_external/routes.py`'s three exception handlers all log the fixed string `"Internal decision
failure during ... (request body not logged)."` verbatim on any unhandled error — confirmed by this
session's own real 500 in Owner's log, which shows exactly that message and nothing else about the
request contents.

## Result: Windows **PASS** (real logs, this session). Android **NOT VERIFIED** (no device).
