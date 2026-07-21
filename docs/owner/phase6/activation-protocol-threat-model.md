# Phase 6 -- Activation Protocol Threat Model

| # | Threat | Mitigation |
|---|---|---|
| 1 | Captured activation/check-in request replayed later | Nonce (single-use, Postgres-unique-constraint-enforced) + timestamp freshness window; replayed nonce rejected with `NONCE_REUSED` |
| 2 | Stolen installation ID used to impersonate a device on check-in | Check-in requires a signature from the device's registered Ed25519 private key -- installation ID alone authenticates nothing (Principle 2) |
| 3 | License key brute-forced via repeated activation attempts | Distributed (Postgres-backed) rate limiting keyed on IP + a safe derived license identifier (never plaintext), tighter policy on invalid-license attempts than on generic traffic |
| 4 | Two simultaneous activations both consume the last device slot | `SELECT ... FOR UPDATE` row lock on the license during the device-count check + insert, inside one transaction (Part F) |
| 5 | Network retry of an already-accepted activation consumes a second slot | Persistent idempotency record keyed on `(license, idempotency_key)`; identical retry returns the same installation, no new slot consumed |
| 6 | Tampered response accepted by a naive client | Every decision-bearing response is Ed25519-signed by Owner; clients verify against published public keys, not transport trust alone |
| 7 | Full license key exposure via logs/errors/metrics | Redaction applied before any structured log line is emitted; plaintext key lives only inside the request-handling stack frame, never assigned to a variable that outlives the HMAC lookup |
| 8 | Enumeration of valid license-key space via timing or unlimited guesses | Constant-time HMAC comparison (`hmac.compare_digest`, unchanged from Phase 5); rate limiting caps guesses; public reason codes normalize `LICENSE_NOT_FOUND` and `INVALID_SIGNATURE` distinctly from license-status-specific codes only where that distinction doesn't aid key-guessing (Part G) |
| 9 | Forged device key claimed to be already-registered | Activation requires the *submitted* public key to sign the activation request itself (proof of possession at registration time), not just an unverified claim |
| 10 | Compromised device key reused after revocation | `owner_device_public_keys.status` checked on every check-in; a revoked key's signature is never accepted regardless of validity |
| 11 | Server signing-key compromise | Key rotation with an overlap window; old assertions remain verifiable against the historical public key until their own expiry, without ever needing to re-trust a compromised key for *new* issuance |
| 12 | Replay-store (Postgres nonce table) unavailable | External API fails closed in production-like mode (`OWNER_REPLAY_PROTECTION_REQUIRED=true` refuses to start / rejects requests rather than silently accepting unverified ones) |
| 13 | Oversized or malformed payload used for resource exhaustion | `OWNER_MAX_REQUEST_BYTES` enforced before JSON parsing; strict `Content-Type: application/json` required |
| 14 | Assertion tampering (client edits a cached assertion) | Assertion is a signed envelope; any byte change invalidates the signature, detected by `verify_assertion` |
| 15 | Remote destructive action via a licensing endpoint | Structurally impossible -- no route in `api_external/` ever touches customer business/medical data or issues a delete; the entire domain model has no such capability (Principle 12) |

## Out of scope for this threat model
Network-layer threats (TLS termination, DDoS at the infrastructure level) -- this service is not exposed publicly in Phase 6. Physical compromise of the Owner host.
