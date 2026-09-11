# Encryption at Rest — Design Note (Retail)

**This is a design note, not an implementation. No code was changed to produce
it.** Every factual claim below was checked against a file actually opened
during this pass (cited as `path:line`); anywhere that wasn't possible, this
note says so rather than guessing. Written 2026-09-11, against retail schema
`RETAIL_SCHEMA_VERSION = 29` (`products/retail/backend/database/schema.py:714`).

## 0. The question

An external review flagged that `retail.db` stores customer names, phone
numbers, addresses, and outstanding credit balances in plain SQLite. On a
stolen or resold shop laptop, that's all readable with a text editor's hex
view or a five-second `sqlite3 retail.db "select * from customers"`. The
threat is ordinary theft against small shops in Jordan, not a nation-state
adversary. The task: decide **how, or whether,** to encrypt — with enough
rigour that someone can act on it, because this is the one change in this
product that can permanently destroy a customer's records if the key
management is wrong.

---

## 1. What is actually exposed today

### 1.1 `retail.db` — measured directly, not assumed

Every `CREATE TABLE` below is quoted from `products/retail/backend/database/schema.py`'s
base DDL (`_init_retail`, schema.py:6443-6772) and its UUID-migration
counterparts, which carry the same columns as first-class fields.

| Table | Sensitive columns | Plaintext? | Citation |
|---|---|---|---|
| `customers` | `name`, `phone`, `email`, `address` | Yes | schema.py:6517-6527 |
| `customers` | `credit_mode`, `credit_limit`, `credit_balance` (added by `_migrate_customers_to_uuid`, folded into base DDL) | Yes | schema.py:1368-1382 |
| `suppliers` | `name`, `phone`, `email`, `address` | Yes | schema.py:6528-6543 |
| `suppliers` | `payment_terms`, `credit_balance` | Yes | schema.py:1457-1469 |
| `supplier_contacts` | `name`, `email`, `phone`, `whatsapp` | Yes | schema.py:6757-6769 |
| `whatsapp_recipients` (owner/manager/accountant contact list) | `display_name`, `phone_e164` | Yes | `commercial_runtime/notifications/schema.py:198-210` |
| `payments` | `party_id` → resolves to a customer/supplier row, `notes` (free text — a cashier can and does type a customer's name or phone into this field) | Yes (indirect + free-text) | schema.py:6649-6660, `retail_api.py:11328-11341` |
| `audit_log` | `details` (free text — see 1.3, this is where a "hashed" customer name reappears in plaintext) | Yes | schema.py:6680-6689 |

**Not exposed in `retail.db`:** there is no `employees` table. Employee login
identity lives in `registry.db` (below), not here.

### 1.2 `registry.db` — shared across every product on the install

`commercial_runtime/identity/registry_db.py:1-32` states plainly this file
"owns the tables that back tenant identity, licensing, and audit for every
product in this suite" — it is not retail-only, and it sits in the same
`<app_data>/database/` directory as `retail.db`
(`commercial_runtime/backup/service.py:74-81`), on the same physical disk.

| Table | Sensitive columns | Hashed or plaintext | Citation |
|---|---|---|---|
| `users` | `email` | Plaintext | registry_db.py:190-206 |
| `users` | `password_hash` | **Hashed** — PBKDF2-HMAC-SHA256, 600,000 iterations, random 16-byte salt per password (`commercial_runtime/security/passwords.py:4,30-57`) | registry_db.py:195 |
| `users` | `pin_hash` (multi-device PIN login) | **Hashed** — same `hash_password()` call (`commercial_runtime/identity/user_accounts.py:701`) | account_schema.py:89-90 |
| `users` | `employee_id` | Plaintext, but it is a shop-assigned login code/username, not a person's real name — there is no `full_name` column anywhere in this table across all 7 migration steps (registry_db.py:42-88 lists every version; none adds one) | registry_db.py:193,205 |
| `audit_logs` | `old_value_json`, `new_value_json` | Plaintext, unbounded — whatever the calling code serialized | registry_db.py:174-187 |
| `company_settings` | country/timezone/currency/business_type | Plaintext, but not personal data | registry_db.py:232-244 |

The password/PIN hashing is real and was verified by reading
`commercial_runtime/security/passwords.py:1-57` directly: PBKDF2-HMAC-SHA256
at the 2023 OWASP-minimum iteration count, self-describing stored format, no
legacy bare-SHA256 path reachable from `hash_password()`. **Credentials are
not the gap.** The gap is the customer/supplier contact and money data next
to them.

### 1.3 A concrete leak that field-level encryption would NOT close by itself

`create_customer()` writes the customer's row *and* separately calls
`_audit(conn, 'CUSTOMER_CREATED', 'customer', nid, data['name'])`
(`retail_api.py:2476`), which lands the customer's plaintext name in
`audit_log.details` (schema.py:6687) — a **second, unencrypted copy of the
same PII in the same database file**, written by a code path that has
nothing to do with the `customers` table's own columns. Any encryption
design that touches only `customers.name` and declares victory leaves this
copy standing. This was found by reading the actual call site, not inferred
from the schema.

### 1.4 `licensing.db` — verified to hold none of this

`commercial_runtime/licensing_contracts/state_repository.py:1-10` states
outright: "Deliberately does NOT store: full license key, license HMAC,
Owner pepper, Owner private keys, the device private key... or any
patient/sales/inventory/customer data." Reading the actual `CREATE TABLE
licensing_state` (state_repository.py:32-61) confirms it: installation IDs,
assertion envelopes, entitlement JSON, state machine fields — no name, phone,
email, or address column anywhere. **`licensing.db` is a non-issue for this
review.**

### 1.5 Blast radius of a stolen laptop, quantified

A thief who takes the till (or a shop that resells it without wiping the
drive, which `docs/hardware/receipt-printer-architecture.md`'s own register
of "what is NOT verified" suggests is exactly the kind of gap nobody
rehearses for) gets, with zero password and zero tooling beyond a copy of
`sqlite3.exe` or DB Browser for SQLite:

- Every customer's full name, phone, address, and running credit balance —
  the exact scenario the external review named — in one query:
  `SELECT id,name,phone,credit_mode,credit_limit,credit_balance FROM customers`
  is literally the query the app's own **Accounts Receivable report** runs
  (`retail_api.py:10844-10851`, `GET /customers/receivables`).
- Every supplier's contact details and what the shop owes them.
- The **same data a second time** in `registry.db`'s backup copy and in any
  `.aurabak.zip` file the shop has ever made (§4.1) — a USB backup stick is
  exactly as exposed as the laptop itself.
- Nothing about payment card numbers or government ID (this product does not
  appear to collect either — not found in any customer/payment schema read
  during this pass).

This is real but bounded: it is small-shop CRM/AR data, not health records,
not card data, not national ID numbers. That matters for how much
engineering risk is proportionate to take on to close it (§5).

---

## 2. The options

### 2a. SQLCipher (whole-database encryption)

**This is a new native dependency**, which this project's engineering
standard forbids adding without being asked (`ENGINEERING.md` §10, "does not
introduce... a dependency that is not already there without being asked").
Flagging that up front because it disqualifies this option from being a
quiet drop-in regardless of anything else below.

**What it would actually touch, measured:**

- Every one of `retail.db`'s connections funnels through
  `_conn(name)` (schema.py:851-862) → `get_retail_conn()` (schema.py:874-875).
  That is one centralized place to add `PRAGMA key = ...` **for retail.db
  specifically**. But `registry.db` has its *own* separate factory
  (`registry_db.py:144-151, get_conn()`), and `licensing.db`,
  `commercial_runtime/backup/service.py`, `commercial_runtime/security/
  migration_safety.py`, and every `einvoicing`/`notifications` module open
  their **own** `sqlite3.connect(...)` calls independently. There is no
  single choke point across the whole install — there are at least half a
  dozen.
- Grep count, this pass: **35 non-test production files** across
  `products/retail/backend`, `products/clinic/backend`, and
  `commercial_runtime/` do `import sqlite3` directly (86 including tests).
  SQLCipher bindings (`pysqlcipher3`, `sqlcipher3-binary`) are a *different*
  Python module, not a drop-in for the stdlib `sqlite3` name — every one of
  those 35 import statements (and whatever tests construct raw
  `sqlite3.connect()` fixtures) would need to change, or every call site
  would need an indirection shim that doesn't exist today. This is a large,
  mostly-mechanical diff, not a one-line dependency swap — and "mostly" is
  doing real work in that sentence, because the exception is the part that
  matters most:

- **`Connection.backup()` is load-bearing infrastructure in this codebase
  beyond the backup feature.** `commercial_runtime/security/
  migration_safety.py:20-26` uses `conn.backup(backup_conn)`
  (migration_safety.py:111) to take the pre-migration, integrity-checked
  snapshot that `ensure_schema_version()` runs before **every schema
  migration on every install** — this is the "paranoid on purpose" mechanism
  root `CLAUDE.md` calls out by name. `commercial_runtime/backup/
  service.py:84-97` (`_snapshot_db`) uses the identical API for the
  shop-facing backup feature. Both rely on `src.backup(dst)` working between
  two `sqlite3.Connection` objects from the **same** underlying C extension.
  Whether a SQLCipher binding's `Connection` object is backup-compatible
  with a stdlib `sqlite3.Connection` (mixed pairs, e.g. an encrypted source
  backed up to a plain destination for `.aurabak.zip`, which stores raw
  `.db` files today per §4.1) was **not verified in this pass** — no spike
  was run, because the task was to write this note, not to prototype
  against production migration safety. What is fact, not guess: if it does
  break, the failure mode isn't "the backup feature is degraded," it's "the
  migration-safety net this whole schema-versioning discipline depends on
  loses its backup step" — the highest-blast-radius thing this option could
  touch. Any actual spike of this option must prove `Connection.backup()`
  survives, on both a same-type and mixed-type pair, before one line of
  migration code changes.
- **Chaquopy/Android precedent, already measured by this codebase's own
  history:** `android/aura-retail/app/build.gradle:270-277` documents that
  `cryptography` (a far more widely-used package than any SQLCipher binding)
  has **no pre-built Chaquopy wheel for pin `43.0.1`** on this Python/ABI
  combination, and Chaquopy had to silently fall back to whatever older
  version (`42.0.8`) it *did* have a wheel for — a source build would have
  needed a Rust cross-compiler unavailable in that build environment. A
  SQLCipher Python binding is a far more niche package with a C (not Rust)
  native extension that also needs OpenSSL linked in; there is no evidence
  in this repo, and this pass could not independently verify, that a
  Chaquopy-compatible pre-built wheel exists for it at all. This is a real
  and likely blocker for Android, not a hypothetical one — it is the same
  failure class that already bit `cryptography`, just with worse odds.
- PyInstaller (Windows): the desktop spec already had to hand-list
  non-statically-discoverable modules and data files explicitly
  (`products/retail/packaging/aura_retail.spec:76-177`, — `trust_anchor.json`,
  `certifi`'s CA bundle, `tzdata`, a long `hiddenimports` list) because
  PyInstaller's static analysis kept missing things that only broke on a
  real packaged run. A native SQLCipher `.pyd` plus its OpenSSL DLL would be
  a new case of exactly that failure class — the spec's own `binaries=[]` is
  currently empty (aura_retail.spec:122), meaning nothing like this has ever
  had to be solved here before.

**Verdict:** technically the "real" encryption-at-rest answer, but the cost
here is not "add a pip package" — it's a driver swap across two products and
a shared runtime, with the single largest identified risk being to the
migration-safety mechanism this codebase is unusually paranoid about for
good, documented reasons, and a live, previously-proven Android packaging
failure mode standing in the way on that platform specifically.

### 2b. Field-level encryption of the sensitive columns only

Crucially, **this codebase already has a working, dependency-free local-secret
pattern**: `commercial_runtime/licensing_contracts/device_identity.py`'s
`WindowsDpapiDeviceIdentityProvider` (device_identity.py:87-226). It wraps
Windows' `CryptProtectData`/`CryptUnprotectData` via `ctypes` against
`crypt32.dll` — stdlib-only, no new dependency, current-user scope, with a
documented, hard-won gotcha already paid for (`os.O_BINARY` on the key-file
write; device_identity.py:118-129) and a retry/verify wrapper
(`_dpapi_protect_verified`, device_identity.py:284-313) proven against a real
intermittent failure. This is the right template to reuse for a **key
wrapper**, not for encrypting every row directly (DPAPI has payload-size and
performance characteristics suited to wrapping a single small key, not to
encrypting thousands of customer rows one call at a time) — encrypt the
columns with a symmetric cipher (e.g. AES-GCM via the `cryptography` package
this codebase already depends on and already ships on both platforms,
per §2a's Chaquopy note) using a locally-generated data key, and use DPAPI
only to protect *that* key at rest, exactly the role DPAPI already plays for
the device's Ed25519 private key.

**What breaks, found by grepping the actual query sites rather than guessing:**

Encrypted columns are opaque to SQL. `customers.name`, `.phone`, `.email`
cannot be filtered, sorted, or matched at the database layer once encrypted
unless a separate searchable-index scheme is built (e.g. a hashed/blind-index
side column) — non-trivial extra work in its own right. The actual query
sites, counted directly in `products/retail/backend/api/retail_api.py`:

1. **`retail_api.py:2437`** — `list_customers()`'s search:
   `WHERE ... AND (c.name LIKE ? OR c.phone LIKE ? OR c.email LIKE ?)`.
2. **`retail_api.py:2438`** — same query, `ORDER BY c.name` — alphabetical
   sort breaks too, not just substring search, since ciphertext doesn't sort
   the way plaintext does.
3. **`retail_api.py:6049`** — sale/receipt lookup:
   `(s.sale_number LIKE ? OR c.name LIKE ?)`.

That's it — **two customer-facing endpoints, three clauses across them.**
(A fourth `LIKE` site, `retail_api.py:1603`, searches `products.name/sku/
barcode`, which is not PII and is out of scope for this review.) This is a
small, enumerable blast radius — genuinely tractable to special-case (e.g.
keep a normalized, blind-indexed `phone_search` / `name_search` hash column
for exact/prefix matching, or simply accept that customer search moves from
`LIKE` to an in-memory scan over a decrypted set for the handful of customers
a small shop actually has — most of this product's installs are unlikely to
carry more than a few hundred customer rows, though that scale assumption
was **not independently verified** in this pass).

**What does NOT need to change, also verified by reading the code:**
`commercial_runtime/sync/sync_service.py`'s conflict resolution keys on
`row_version`/timestamp comparisons (`sync_service.py:1133,1435`, `WHERE
excluded.row_version > ...`), not on field-value diffing. Sync also always
serializes from the **already-decrypted** Python value at the call site —
`create_customer()` queues the sync event with the plaintext fields it just
read back (`retail_api.py:2477-2481`, `_queue_sync_event('customer', ...,
{'name': data['name'], 'phone': ..., ...})`) — so field-level encryption at
rest would not, by itself, change what sync ships over the wire (§4.3 covers
what that actually means for the bigger picture: it's already plaintext in
transit today, encryption-at-rest doesn't touch that).

**What this option does NOT close by itself:** the `audit_log.details`
plaintext-name leak found in §1.3. Any field-level design has to explicitly
decide what happens to that column too, or it ships a customer-PII-encrypted
`customers` table sitting next to a customer-PII-plaintext `audit_log` in the
same file — a review that only checked "is `customers.name` encrypted" would
wrongly call that done.

### 2c. OS-level full-disk encryption (BitLocker)

Zero application code. Genuinely the cheapest option to ship, but has to be
assessed honestly against what it does and doesn't cover:

- **Covers:** the disk read out of a powered-off machine — exactly "stolen
  laptop, thief removes the drive or boots a live USB to read it cold."
- **Does NOT cover:** a *running or logged-in* machine (BitLocker decrypts
  transparently once Windows has booted and the volume is unlocked — which,
  for a till that auto-logs-in or shares one blank/weak password across
  every cashier, a real thief who simply takes the machine while it still
  has power, or waits for the shop to hand over the login the way a "we're
  repossessing this for non-payment" scenario might, gets past trivially); a
  **copied backup file** — the `.aurabak.zip` from §4.1 sitting on a USB
  stick or emailed anywhere is plaintext SQLite the moment it leaves the
  BitLocker-protected volume; and a **shared folder or cloud-synced
  directory** the shop might point its backups at, which was not checked for
  in this pass but is a plausible small-shop habit.
- **Windows edition risk, not verified in this pass and worth stating
  plainly:** BitLocker-proper (the drive-encryption feature with a recovery
  key, group policy, etc.) requires Windows Pro/Enterprise/Education.
  Windows Home ships a narrower "Device Encryption" that auto-enables only
  under specific hardware preconditions (TPM 2.0, Modern Standby capability)
  **and** requires signing in with a Microsoft account so the recovery key
  escrows to that account. This codebase gives no indication of what Windows
  edition or hardware tier its actual Jordan shop customers run — cheap
  till hardware skewing toward Windows Home, with local (non-Microsoft)
  accounts for a shared shop login, is a plausible and common configuration
  in which "just turn on BitLocker" is either unavailable or silently
  doesn't actually protect anything because there's no way to prove the
  recovery key was ever escrowed anywhere retrievable. **This needs to be
  checked against real customer hardware before this is written into any
  install runbook as a solved requirement — it was not checked here.**

Making it a real, documented install requirement, if adopted: an addition to
`docs/release/go-live-runbook.md` (referenced but not opened in this pass)
or an install-time checklist item, plus a way for support staff to verify a
given install actually has it on (not just recommend it and hope).

### 2d. A better-fitted option: encrypt the backup artifact, not the live database, for now

Given §2a's driver-swap cost and §2b's search/sort tax, the option this note
thinks is actually worth building *if and when* field encryption is
justified (§6) is narrower than either: encrypt only the **`.aurabak.zip`**
that `commercial_runtime/backup/service.py` already produces
(`create_backup`, service.py:100-171), using a shop-chosen passphrase run
through a KDF (PBKDF2 or scrypt — this codebase's own `passwords.py` already
has the PBKDF2 primitive in hand) to derive an AES key that wraps the zip's
contents, or a per-file encryption of the two `.db` snapshots before they're
written into the archive. This does **not** touch the live `retail.db` at
all — no query, no `LIKE`, no `sqlite3` import, no PyInstaller/Chaquopy
native-dependency risk, because it operates only on the already-`Connection.
backup()`-produced snapshot files, after the point where they're just bytes
on disk. It closes the single most portable, most likely-to-leave-the-shop
exposure (a backup USB stick, §4.1) for close to the engineering cost of
§2b's key-wrapping half, with none of its query-breakage problems, at the
cost of leaving the *live*, on-premise laptop copy exactly as exposed as it
is today. It is a real, scoped, shippable piece of defense-in-depth — but it
is not a substitute for deciding the live-database question, which is why
it's listed here as a supplement to §5's recommendation, not a replacement
for it.

---

## 3. The key problem — the actual decision

Every option above (except 2c) needs an answer to: **where does the key
live, what happens when the laptop dies and the shop restores onto a new
machine, and what happens when Windows is reinstalled?** This is the part
that determines whether encryption protects the shop or destroys their
records more thoroughly than any thief would.

**A concrete, in-repo cautionary example, found while researching this
note, not invented for it:** `commercial_runtime/security/app_secret.py`
already implements "generate a local secret, store it next to the data it
protects, per-installation." Its own documented failure policy
(`app_secret.py:9-13,59-95`): "If the stored secret is missing, unreadable,
or malformed, a fresh one is generated and persisted — this invalidates any
active sessions... but never falls back to a public default." That policy is
**correct** for a Flask session-signing secret, where the worst case of
losing it is "everyone has to log in again." It would be **catastrophic** if
the same pattern were reused verbatim for a data-encryption key: "corrupted
key file → silently generate a new one" would mean every encrypted customer
row becomes permanently unreadable, and the failure would look like nothing
worse than a routine restart. **Do not reuse `app_secret.py`'s pattern for a
data-encryption key.** Any design here must instead follow
`device_identity.py`'s harder-won contract: a corrupt or unreadable key
routes to an explicit `LocalStateCorruptError`
(device_identity.py:34-37,213-225) that the caller is required to handle
deliberately — never a silent regenerate.

**DPAPI specifically ties decryption to "can this Windows user account, on
this machine, currently decrypt its own master key" — which is a narrower
bar than it sounds.** `WindowsDpapiDeviceIdentityProvider` uses current-user
scope, no `CRYPTPROTECT_LOCAL_MACHINE` flag (device_identity.py:265-267).
That protects a **removed disk** read on a different machine cold — DPAPI's
master key material isn't present there at all. It does **not** meaningfully
protect a **stolen, running-or-logged-in laptop**: if the till auto-logs-in
(common on shared shop hardware) or the thief simply doesn't power it off, or
correctly guesses/keeps a shared blank/weak shop password, Windows itself
transparently unlocks the DPAPI master key on login — the same way it
already does for the device's own Ed25519 private key today. **Say this
plainly, because the review's threat model is "ordinary theft," and ordinary
theft of a running or easily-logged-into till is not stopped by DPAPI.** A
key stored next to the data, DPAPI-wrapped or not, protects against a stolen
**disk**; it does not protect against a stolen **laptop** the thief can boot
into. This is exactly the caveat the task asked this note to state plainly
if true — it is true.

**Backup/restore onto a new machine:** `commercial_runtime/backup/
service.py`'s `restore_backup()` (service.py:200-306) validates checksums,
product identity, schema version, and runs `PRAGMA integrity_check` on the
extracted files (service.py:262-266) before ever touching a live file — this
is a well-built, careful restore path. But it has **zero concept of a key**
today, because there is no encryption to key. If field-level or whole-DB
encryption were added with a DPAPI-wrapped local key: a backup restored onto
a **different physical machine** (the exact "laptop died, shop gets a
replacement" scenario the task named) would restore a `.db` file whose
encrypted columns (or whole file, for SQLCipher) were wrapped by a key tied
to the *old* machine's DPAPI master key — which does not exist on the new
machine and cannot be recreated. **Every encrypted row would become
permanently unreadable the moment a shop does the single most normal thing
this feature exists for them to do: replace a dead machine.** That is a
worse outcome than the status quo, and it is the load-bearing reason this
note does not recommend a DPAPI-wrapped live-database key without also
designing, and shipping in the same change, a **portable recovery path** —
e.g. a shop-set recovery passphrase (independent of any one machine's DPAPI
state) escrowed the way a Windows BitLocker recovery key is escrowed, or the
key itself is escrowed to Owner (the existing Owner Control Center licensing
relationship already has a signed-assertion channel to every activated
device — `commercial_runtime/licensing_contracts/` — that is architecturally
capable of carrying a recovery blob, though this was not designed here and
is a real, separate body of work). Windows being reinstalled is the identical
failure mode as a new machine: DPAPI current-user master keys do not survive
a clean reinstall (a domain-joined or Microsoft-account-backed profile can
in some configurations escrow DPAPI master keys for recovery, but nothing in
this codebase indicates any install here is domain-joined, and this was not
independently verified against real customer hardware).

**Bottom line of this section:** a DPAPI-only key design trades "thief reads
`retail.db` in a text editor" for "a shop that replaces a dead laptop loses
every customer record encryption ever touched" — and the second failure is
strictly worse, because it is not theft, it is certain, and it happens to
every legitimate customer who uses the backup/restore feature exactly as
designed. **No live-database encryption should ship without a recovery
story that survives a new machine**, and none of options 2a/2b above have
one today.

---

## 4. Interaction with what already exists

### 4.1 Backup/restore

Covered in depth in §3. One more fact worth stating here: the backup archive
itself (`commercial_runtime/backup/service.py:154-157`) writes the raw
`.db` snapshots straight into the zip with `zipfile.ZIP_DEFLATED` —
compression, not encryption. **A `.aurabak.zip` sitting anywhere — a USB
stick, an email attachment, a shared network drive a shop points backups at
— is exactly as exposed as the live laptop, today, regardless of whatever
this note recommends for the live database.** This is the gap §2d targets
directly.

### 4.2 CSV exports

`export_sales_csv()` (`retail_api.py:11251-11301`) explicitly selects and
streams `customer_name` (via `COALESCE(c.name,'Walk-in')`,
retail_api.py:11285) into the CSV, one row per sold line item. Any customer
who has ever bought anything has their name in every sales export a manager
pulls. `export_payments_csv()` (retail_api.py:11304-11345) does not include
name directly but does include `party_id`, which resolves back to the same
customer. **These exports already re-expose customer PII in plaintext, by
design, to anyone with `CAP_REPORTS`** — a legitimate business need (an
accountant needs the customer's name on the ledger), but it means encrypting
`customers.name` at rest does nothing to stop the same value from leaving
the machine the moment someone runs a report they're authorized to run. This
is expected and not a defect — reports are supposed to show this data to
authorized staff — but it bounds how much protection at-rest encryption
actually buys: it protects against a thief who does not have valid
credentials, not against misuse by someone who does.

### 4.3 Sync outbox

Already covered concretely in §2b: `_queue_sync_event('customer', ...)`
(`retail_api.py:2477-2481`) serializes the full plaintext row — name, phone,
email, address — into `sync_outbox.payload` (schema.py:6695-6701, a `TEXT`
column holding a JSON blob), which `commercial_runtime/sync/relay_client.py`
then pushes to Owner's relay server over HTTPS
(`relay_client.py:86-121,162-181`, `verify_tls: bool = True` by default,
TLS-in-transit but no additional payload-level encryption on top of it).
**This means customer PII already leaves the originating machine today**,
lands on Owner's relay server, and lands in **every other device's own
unencrypted `retail.db`** on the same license. Encrypting the originating
laptop's local file at rest does nothing about any of that — a thief who
steals a *different* device on the same multi-device license, or who
compromises Owner's relay, gets the identical data regardless of what this
review decides for any one machine's disk. This is the single strongest
argument in this whole note for why database-at-rest encryption is a
**partial** answer to the review's concern, not a complete one: the same
data is designed to be, and already is, present unencrypted on N machines,
not one.

### 4.4 Diagnostics export

This is the one place the codebase already does the hard, honest work this
whole design note is arguing for. `diagnostics_export()`
(`retail_api.py:12666-12729`) explicitly documents "NEVER includes: customer
names/phones/emails/addresses..." (retail_api.py:12674-12676) and backs that
with two real redaction passes over the one genuinely free-text field it
does include (the log tail): a regex sweep for recognizable shapes (email,
phone, this product's own licence-key format —
`_redact_diagnostics_log_line`, retail_api.py:12570-12598) **and** an
exact-match sweep against this company's own current customer records
pulled fresh from the database (`_known_customer_pii_values`,
retail_api.py:12601-12623, `_redact_known_pii_from_line`,
retail_api.py:12626-12642) — because, as its own docstring correctly notes,
"there is no regex shape for an arbitrary human name." This is a real
existing precedent for "PII discipline enforced by design, with the
limitations stated in writing rather than assumed away," and it's the
pattern any future work in this area should match, not reinvent.

---

## 5. Recommendation

**Ship BitLocker (or verified Windows Device Encryption) plus documentation
now, and encrypt the backup archive (§2d) as a small, scoped follow-on. Do
not encrypt the live `retail.db` — neither whole-database nor field-level —
yet.**

Reasons, in order of weight:

1. **The key-recovery story does not exist for either encryption option**,
   and shipping either without one converts "a thief might read this" into
   "a shop that replaces a dead laptop, or reinstalls Windows, permanently
   loses every customer record" — a worse and more certain outcome than the
   status quo, for the exact ordinary-business-continuity event (hardware
   dies, gets replaced) this product's own backup/restore feature exists to
   protect against. §3 is not a hedge; it is the actual reason to wait.
2. **The data already leaves the machine unencrypted through the sync
   outbox and CSV reports** (§4.2, §4.3), to other devices on the same
   license and to Owner's relay. Encrypting one laptop's disk closes one of
   several doors that are all currently open. Doing it first, alone, without
   also deciding what (if anything) changes about sync's own data handling,
   spends real engineering effort for a partial result, and risks the
   product being described internally as "PII is encrypted now" when the
   same PII is still sitting in plaintext on every other device on the
   license and inside a diagnostics-adjacent, unencrypted `audit_log` row
   (§1.3) on the very machine that got the encryption work.
3. **SQLCipher's cost is not proportionate to the threat measured in §1.5.**
   This is small-shop CRM/AR data — real, worth protecting, but not
   payment-card or health-record grade — being weighed against a native
   dependency this project's own rules forbid adding without being asked, a
   35-file `import sqlite3` blast radius, a documented real precedent
   (`cryptography` on Chaquopy) for exactly the kind of native-wheel failure
   this would risk on Android, and — the sharpest risk — an unverified
   interaction with the `Connection.backup()` call that
   `migration_safety.py` uses to protect **every schema migration on every
   install**, not just this one feature.
4. **Field-level encryption is the right shape for the future, but the
   query-site cost (§2b) is real and the recovery-key problem (§3) is
   identical to SQLCipher's** — narrowing the blast radius to two endpoints
   doesn't help if the key itself still can't survive a machine replacement.
5. **BitLocker's coverage gap is real (§2c) and must be stated as a real
   gap, not glossed over**: it does not protect a running/logged-in machine,
   a copied backup file, or a network share. This recommendation is
   explicitly "BitLocker is the right *first* move, not a complete answer" —
   which is why §2d (encrypt the backup artifact) is bundled with it: the
   backup file is the part BitLocker structurally cannot cover, and it is
   cheap to close without touching the live database or its query surface.

### Rejected alternatives, and why

- **SQLCipher now** — rejected for this cycle: driver-swap blast radius (35
  files), unverified `Connection.backup()` interaction against the
  migration-safety mechanism, and a real precedent (Chaquopy/`cryptography`)
  for native-dependency packaging failure on Android, all for a threat model
  this note assesses (§1.5) as real but not severe enough yet to justify
  that risk. Revisit if §6 triggers.
- **Field-level encryption now, DPAPI-keyed, no recovery path** — rejected
  outright: this is the "encryption destroys the records more thoroughly
  than a thief would" failure mode named in the task, made concrete in §3.
  Not acceptable at any point without a recovery design shipped in the same
  change.
- **Do nothing at all** — rejected: the review is correct that plaintext
  customer names, phones, and credit balances in a file a thief can open
  with a hex editor is a real, cheap-to-partially-fix gap, and BitLocker
  plus closing the backup-artifact hole (§2d) is low-risk, low-cost, and
  worth doing regardless of the bigger database decision.

### Rough sizing, if this recommendation is taken

- **BitLocker/Device-Encryption documentation + install-time check:**
  a runbook addition (`docs/release/go-live-runbook.md` or equivalent) plus,
  ideally, a support-facing way to confirm a given install actually has
  drive encryption on (not just a recommendation nobody verifies). Small —
  documentation and process, not application code. Main risk: discovering,
  when actually checked against real customer hardware, that a meaningful
  share of tills run Windows Home without the preconditions Device
  Encryption needs, which would mean the "just turn it on" story doesn't
  hold and a real remediation plan (upgrade, third-party disk encryption) is
  needed instead — **this was not checked in this pass and is the first
  thing to verify before treating §2c as solved.**
- **Encrypt the backup archive (§2d):** touches only
  `commercial_runtime/backup/service.py` (`create_backup`/`restore_backup`)
  and whatever settings UI lets a shop set/enter the backup passphrase —
  does not touch `retail_api.py`, `schema.py`, sync, or any query. Moderate,
  self-contained: a KDF call (this codebase already has PBKDF2 in
  `commercial_runtime/security/passwords.py` to model it on), an AES-GCM
  wrap of the zip contents, and — the part that needs real design attention,
  not just code — a passphrase-recovery UX so a shop that forgets its backup
  passphrase does not lose the backup the same way §3 warns against for a
  live-database key. What could go wrong: the exact same recovery-key
  problem as §3, at smaller scale (one archive, not the live database) —
  still needs a real answer, not an afterthought.

---

## 6. What would change this recommendation

- **A specific customer or regulatory body demands encryption at rest** as a
  condition of doing business — changes the cost/benefit math directly and
  would justify absorbing SQLCipher's or field-level encryption's cost even
  without a fully-resolved recovery story (the deadline would force one).
- **Jordan (or any market this product expands into) enacts or starts
  enforcing data-protection law with a specific at-rest-encryption
  requirement** for the categories of data this product holds (name, phone,
  address, financial balance). This note did not research the current state
  of Jordanian data-protection law — that is a real gap in this note, not a
  claim that no such law exists — and should be checked before this
  recommendation is treated as final for longer than a few months. Related
  prior art in this repo (not opened during this pass, so not relied on for
  any claim above): `docs/privacy/clinic-sensitive-data-boundary.md` and
  `docs/audit/11-clinic-privacy-and-sensitive-data-audit.md` suggest Clinic
  has already had to reason about this for health data, which is a stricter
  category — that work may be directly reusable groundwork if Retail's bar
  rises.
- **A real breach** — a stolen laptop that is confirmed to have actually
  been used to access a customer's data, not merely a theoretical
  possibility. Changes both the urgency and, likely, the acceptable risk
  tolerance for something like SQLCipher's packaging risk.
- **The sync/multi-device population grows** to the point where "PII already
  sits unencrypted on N devices" (§4.3) stops being a reason to deprioritize
  single-device encryption and starts being a reason to design encryption
  for the *sync payload* itself (end-to-end, not just at rest per device) —
  a materially different and larger project than anything in this note.
- **A concrete key-recovery design lands** — e.g. an Owner-escrowed recovery
  blob riding the existing licensing/sync relationship (§3), or a
  shop-managed recovery passphrase with a real UX for setting and recovering
  it. Once §3's objection is actually answered, field-level encryption
  (§2b) becomes the clear next step, ahead of SQLCipher, because its query-
  site cost is small and enumerated (§2b) while SQLCipher's driver-swap and
  Android-packaging risk remain the same size either way.
