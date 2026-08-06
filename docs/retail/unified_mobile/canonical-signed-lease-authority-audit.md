# Canonical Signed Lease Authority Audit (M11.1)

Real, direct code audit of the executable canonical licensing authority
in `commercial_runtime/licensing_contracts/` — read before any M11
implementation, per the checkpoint's own explicit instruction not to
infer the signed byte sequence from human-readable JSON or invent a
mobile-only dialect. Files read in full:
`canonical.py`, `assertion_verifier.py`, `trust_store.py`,
`trust_anchor_loader.py`, `trusted_time.py`, `policy_evaluator.py`,
`state_machine.py`, `reason_codes.py`, `android_bridge_identity.py`,
plus the real Android legacy reference
`android/aura-retail/.../licensing/DeviceIdentity.kt` and the real,
checked-in `commercial_runtime/licensing_contracts/trust_anchor.json`.

## Canonicalization (`canonical.py`)

The signed byte sequence is **not** the raw JSON the payload happens to
arrive as. It is re-derived deterministically:

1. Recursively NFC-normalize every string value.
2. Reject non-finite floats (`NaN`/`±Infinity`).
3. Reject nesting deeper than 16 levels.
4. Serialize via `json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
   — sorted keys, no insignificant whitespace, raw UTF-8 (not `\uXXXX`
   escapes), array order preserved.
5. Encode the resulting string as UTF-8 bytes.

This exact string is what gets signed and what verification re-derives.
The Kotlin port must reproduce this byte-for-byte — key sort order,
separator characters, NFC normalization, and `ensure_ascii=False`
(non-ASCII characters emitted literally, not escaped) all matter to
signature verification.

## Envelope (`assertion_verifier.py::verify_assertion`)

```
{
  "payload": { ...canonicalizable dict... },
  "signing_key_id": "owner-ed25519-<UTC-timestamp>-<8-hex>",
  "algorithm": "ed25519",
  "signature": "<base64>"
}
```

Only `payload` is canonicalized and signed; `signing_key_id`,
`algorithm`, and `signature` are unsigned envelope metadata used to
locate and apply the verification key.

## Algorithm

**Ed25519**, confirmed both server-side (Python `cryptography` library,
`Ed25519PublicKey.from_public_bytes(...).verify(signature, canonical_bytes)`)
and Android-side (Google Tink's raw "subtle" primitives,
`com.google.crypto.tink.subtle.Ed25519Sign`/`Ed25519Verify`, RFC
8032-compliant raw 32-byte keys/64-byte signatures — no ASN.1/DER
wrapping). The Android legacy app's own `DeviceIdentity.kt` KDoc
records this was deliberately chosen **instead of** `AndroidKeyStore`'s
native Ed25519 support because that requires API 33+, above this
project's minSdk (26), and was physically cross-verified against
Python's own signatures via a real `Ed25519CrossVerifyTest`. No
algorithm allowlist logic exists beyond a single literal equality
check (`algorithm != "ed25519"` → reject) — there is no multi-algorithm
negotiation in the canonical authority today.

## Payload field allowlist (`ALLOWED_PAYLOAD_FIELDS`, 27 fields)

`assertion_id`, `issuer`, `product_code`, `license_public_id`,
`installation_public_id`, `platform`, `app_version_policy`,
`release_channel`, `issued_at`, `not_before`, `expires_at`,
`license_status`, `installation_status`, `subscription_status`,
`allowed_device_count`, `device_key_fingerprint`, `entitlements`,
`offline_policy`, `contract_version`, `commercial_policy_version`,
`renewal_status`, `plan_code`, `term_start`, `term_end`,
`past_due_since`, `commercial_grace_end`, `pilot_status`,
`emergency_extension_id`. Any field outside this set is a hard
rejection (`ASSERTION_FORBIDDEN_FIELD`) — a real, structural
data-minimization gate, not merely documentation.

## Forbidden-marker guard (defense in depth, independent of the allowlist)

A second, independent scan (`str(payload).lower()` substring check)
rejects any payload containing: `license_key`, `key_secret`, `pepper`,
`password`, `card_number`, `bank_account`, `patient`, `medical_note`,
`clinical_note`, `diagnosis`, `prescription`, `appointment`,
`invoice_total`, `sale_total`, `stock_quantity`, `local_database`. This
exists specifically so a regression in Owner's own signing-side guard
does not silently start leaking business/clinical/financial data into
a signed lease the client would otherwise trust.

## Real verification order (exact, as implemented)

1. Parse envelope fields (`payload`/`signing_key_id`/`algorithm`/`signature`) — malformed envelope fails generically.
2. Algorithm literal check (`== "ed25519"`).
3. Payload field allowlist + forbidden-marker guard (**before** any cryptography — a malformed/oversized/forbidden payload is rejected before spending a signature-verification cycle on it).
4. Trust-store lookup: `is_trusted(signing_key_id)` — unknown key fails closed (`UNKNOWN_SIGNING_KEY`) **before** the public key is even resolved.
5. Decode base64 signature and base64 public key; decode canonical payload bytes.
6. `Ed25519PublicKey.verify(signature, canonical_bytes)` — `InvalidSignature` → generic `ASSERTION_VERIFICATION_FAILED` (the reference deliberately does not expose a more specific "signature invalid" code here).
7. Parse `not_before`/`expires_at` as timezone-aware ISO-8601; naive timestamps are rejected outright.
8. Time-window check with a **60-second clock-skew tolerance on both ends** (`CLOCK_SKEW_TOLERANCE_SECONDS = 60`) — real, physically-motivated (Phase 7V-A: a device clock ~1s behind the signing host rejected a genuinely valid, freshly-issued assertion).
9. Context binding: `product_code`, `platform`, `installation_public_id`, `device_key_fingerprint` — exact equality, no fallback/wildcard.
10. Parse `offline_policy` into a structured, validated object (see below).
11. Parse optional `commercial_grace_end`.
12. Return `VerifiedAssertion(payload, evidence, signing_key_id)` — only now is any claim trusted.

**Claims are never acted on before signature verification succeeds** (step 6 gates everything from step 7 onward) — this is a binding, must-preserve invariant for the Kotlin port.

## Public key ring (`trust_store.py`, `trust_anchor_loader.py`)

- `TrustedKey(key_id, public_key_b64, algorithm, status: "ACTIVE"|"RETIRED", source: "BUNDLED_ANCHOR"|"ROTATION_MANIFEST")`.
- **`REVOKED` keys are removed outright, never stored with that status** — `is_trusted()` is a pure membership check.
- Bootstrap: `bootstrap_from_anchor()` seeds from a bundled `trust_anchor.json` exactly once (refuses to re-bootstrap — never a way to reset trust, closing a TOFU-reopening risk).
- Rotation: `admit_manifest()` only admits a new key-set manifest if that manifest is itself signed by an **already-trusted** key (self-referential trust chain); a manifest from an unknown signer, or with a bad signature, is silently discarded (expected, non-exceptional — not raised).
- `revoke_locally()`: emergency, immediate, out-of-band local revocation.

Real, checked-in production trust anchor
(`commercial_runtime/licensing_contracts/trust_anchor.json`):
```json
{"keys": [{"key_id": "owner-ed25519-20260727T053324Z-c32537d7", "public_key": "uYJu57ljNL0VK8SFP883z5J/ZQg9YqDDNZrSsX6eYcU=", "algorithm": "ed25519"}]}
```
`key_id` format: `owner-ed25519-<compact-UTC-ISO8601>-<8-hex-char-suffix>`. Public key: base64 raw 32-byte Ed25519 public key.

## Trusted time (`trusted_time.py`)

`TrustedTimeAnchor(server_time, monotonic_at_anchor)` — a pairing of the
last Owner-verified wall-clock time with the monotonic-clock reading
taken at that same instant. `trusted_now(anchor) = anchor.server_time +
(monotonic.now() - anchor.monotonic_at_anchor)` — **trusted time is
never a fresh read of local wall clock**; it is monotonic-elapsed-time
added to a trusted anchor. A negative monotonic delta fails closed
(`TrustedTimeError`) rather than silently treating it as zero elapsed.
Rollback detection (`detect_rollback`) is a **separate, explicit**
comparison: local wall clock read UTC-normalized (so timezone/DST
changes never false-positive) against the anchor's `server_time`,
flagged only if the local clock reads earlier by more than a
policy-supplied tolerance. **There is no separate "forward jump"
detector in the canonical authority** — because `trusted_now()` never
re-reads wall clock at all, a wall-clock forward jump cannot corrupt
the trusted-time computation by construction; only the rollback
comparison consults wall clock, and only to detect backward movement.
Anchors are cached per-installation and re-pinned to a fresh monotonic
reading on process restart (`rehydrate_anchor`), carrying the
persisted `server_time` forward unchanged — a real, subtle,
already-discovered-and-fixed defect class (naively re-pinning on every
lazy resolution silently collapsed real elapsed offline duration back
to ~0).

## Offline commercial decision model (`policy_evaluator.py::evaluate`)

Real `LicenseState` enum (`state_machine.py`): `NOT_CONFIGURED`,
`ACTIVATION_REQUIRED`, `ACTIVATING`, `ACTIVE_ONLINE`, `ACTIVE_OFFLINE`,
`WARNING`, `GRACE_PERIOD`, `RESTRICTED`, `SUSPENDED`, `REVOKED`,
`EXPIRED`, `DEVICE_DEACTIVATED`, `DEVICE_REPLACED`,
`CLOCK_REVIEW_REQUIRED`, `LOCAL_STATE_CORRUPT`. `ACTIVE_FAMILY` =
`{ACTIVE_ONLINE, ACTIVE_OFFLINE, WARNING, GRACE_PERIOD}`.
`DATA_PRESERVED_FAMILY` extends that with every pre-activation and
restriction state — **read/backup/export access survives commercial
restriction**, never blocked by time-based enforcement alone.

Real evaluation order, exactly as implemented:

1. **Clock rollback short-circuits every other rule** → `CLOCK_REVIEW_REQUIRED`.
2. `installation_status == SUSPENDED|REVOKED` → `SUSPENDED`/`REVOKED` (explicit signed decisions override every timer).
3. `license_status in (EXPIRED, REVOKED)` → `EXPIRED`; `license_status == SUSPENDED` → `SUSPENDED` (a security-driven suspension is never masked by an emergency extension).
4. Commercial/subscription state (`subscription_status`): `EXPIRED|CANCELLED|SUSPENDED` → `RESTRICTED`; `PAST_DUE` past its own signed `commercial_grace_end` → `RESTRICTED` — **unless** an emergency extension is currently effective (`emergency_extension_allowed && now < emergency_extension_until`).
5. `effective_grace_seconds = min(signed offline_grace_seconds, local_safety_ceiling_seconds)` — a local build **may only shorten**, never silently extend, the signed grace window. An effective emergency extension can raise it back up.
6. `elapsed_offline = now - last_successful_checkin_at`. Recently online (`last_checkin_attempt_ok && elapsed < check_in_interval_seconds`) → `ACTIVE_ONLINE`.
7. Within grace: `WARNING` once within `warning_start_seconds` of the grace boundary, else `ACTIVE_OFFLINE`/`ACTIVE_ONLINE`.
8. Within grace + `retry_interval_seconds` → `GRACE_PERIOD`.
9. Grace fully exhausted: `hard_expiry_behavior == "WARN_ONLY"` → still `GRACE_PERIOD` (commercial mutation never blocked by time alone); otherwise → `RESTRICTED`.

`OfflinePolicy` fields (all **signed**, read only from the verified
assertion's own `offline_policy` object — "a local release may apply a
stricter safety limit, but not silently extend" is the binding rule):
`check_in_interval_seconds`, `retry_interval_seconds`,
`offline_grace_seconds`, `warning_start_seconds`,
`hard_expiry_behavior` (`"WARN_ONLY"` | `"RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA"`,
closed set — an unrecognized value raises rather than guessing a safe
default), `clock_rollback_tolerance_seconds`,
`assertion_refresh_threshold_seconds`, `emergency_extension_allowed`,
`emergency_extension_until`.

## Reason codes (`reason_codes.py`)

Closed vocabulary, split `PUBLIC_REASON_CODES` (Owner-issued, e.g.
`INVALID_SIGNATURE`, `DEVICE_LIMIT_REACHED`, `VERSION_UNSUPPORTED`) vs.
`LOCAL_REASON_CODES` (produced entirely on-device by this package's own
transport/verification/policy layers, e.g. `UNKNOWN_SIGNING_KEY`,
`ASSERTION_EXPIRED`, `ASSERTION_NOT_YET_VALID`,
`CLOCK_ROLLBACK_SUSPECTED`, `ASSERTION_FORBIDDEN_FIELD`,
`LOCAL_STATE_CORRUPT`) — deliberately kept separate so a caller can
always tell "Owner told us this" from "we decided this locally without
ever reaching Owner."

## Field classification (per the checkpoint's own requirement)

| Field | Signed | Required | Security-critical |
|---|---|---|---|
| `payload` (all 27 allowlisted subfields) | Yes (canonicalized+signed as a whole) | Per-field, see below | Yes |
| `signing_key_id` | No (envelope metadata) | Yes | Yes (key resolution) |
| `algorithm` | No (envelope metadata) | Yes | Yes (must equal `"ed25519"`) |
| `signature` | No (it *is* the signature) | Yes | Yes |
| `product_code`, `platform`, `installation_public_id`, `device_key_fingerprint` | Yes | Yes | Yes (context binding) |
| `not_before`, `expires_at` | Yes | Yes | Yes (must be tz-aware ISO-8601) |
| `license_status`, `installation_status`, `subscription_status` | Yes | Yes | Yes (override every timer) |
| `offline_policy` (+ subfields) | Yes | Yes | Yes (sole source of offline grace) |
| `commercial_grace_end` | Yes | Optional | Yes when present |
| `entitlements`, `contract_version`, `app_version_policy`, `release_channel` | Yes | Present in allowlist; not consumed by `assertion_verifier.py`/`policy_evaluator.py` directly — real, disclosed gap: version/entitlement *policy enforcement* (M11.11/M11.21) is a Kotlin-side responsibility this milestone must add, since the canonical Python authority allows these fields through but does not itself gate on them in the files audited here | Yes |
| `assertion_id`, `issuer`, `license_public_id`, `allowed_device_count`, `renewal_status`, `plan_code`, `term_start`, `term_end`, `past_due_since`, `pilot_status`, `emergency_extension_id` | Yes | Optional/presentation | No (presentation/audit-trail only, not consulted by the evaluators read here) |

## Entitlement gating (`capability_guard.py::evaluate_capability`)

The real, canonical entitlement/operation-gating authority — a pure,
deny-by-default decision function, distinct from `policy_evaluator.py`
(which classifies *what state the installation is in*, not *what a
specific operation is allowed to do*):

1. **State gate first**: `current_state in ACTIVE_FAMILY` → full
   operation, subject only to the entitlement gate below.
   `current_state in DATA_PRESERVED_FAMILY` (restricted/suspended/
   pre-activation/etc.) → only capabilities in an explicit
   `restricted_mode_allowlist` are permitted, else
   `LICENSE_INACTIVE`. Any other state → deny (`LICENSE_INACTIVE`,
   the backstop branch).
2. **Entitlement gate**: if the capability requires a named
   entitlement, look it up in the verified `entitlements` map;
   missing or falsy → deny (`CAPABILITY_NOT_ENTITLED`).

This is the direct, real, canonical reference for M11.11 (entitlement
validation) and M11.25 (business-operation gating) — a straight port,
not new logic. `restricted_mode_allowlist`/per-capability
`required_entitlement` wiring (which concrete Retail operations map to
which capability codes) is itself real, new Kotlin-side work this
milestone must define, since it is product-specific and not resolved
by this generic guard function.

## Real, disclosed gap this audit surfaces

`app_version_policy` and `contract_version` are real, signed,
allowlisted fields with **no corresponding enforcement logic** found
in any of the four evaluator files read for this audit
(`assertion_verifier.py`, `policy_evaluator.py`, `state_machine.py`,
`capability_guard.py`). Either enforcement lives elsewhere in the
Python tree (e.g. `checkin_scheduler.py`, not read in full for this
audit, out of the core-verification critical path) or it is genuinely
not yet enforced client-side anywhere in the canonical authority.
M11.21 (app-version policy) is real, new Kotlin-side work this
milestone adds — not a port of missing Python logic — and is
documented as such in its own decision doc rather than silently
presented as a straight port.
