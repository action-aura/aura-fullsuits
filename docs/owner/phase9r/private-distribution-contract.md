# Phase 9R — M11: Private Authorized Release Distribution (Real New Code)

## Confirmed genuine gap, built on M10 + existing device-auth infrastructure

No client-facing release/download capability existed at all before this
milestone — only the internal staff-only `/releases/versions` read view.
Built using the exact same device-signature authentication primitives as
check-in (`app/licensing_service/device_identity.py`, `replay.py`,
`canonical.py`) — no new auth mechanism invented — and gated on M10's
`publication_state == "PUBLISHED"`.

## Flow implemented

1. An already-activated installation POSTs a device-signed request to
   `/api/licensing/v1/releases/authorize-download` naming a specific
   `product_version_id`.
2. `authorize_download()` (`app/releases/distribution.py`) verifies, in
   order: request shape, replay protection (nonce + timestamp, same as
   check-in), device signature, installation status, license status
   (deny-list matching `process_checkin()`'s exact pattern — see "Real bug
   found" below), subscription status, release existence, publication
   state, product match, platform match, release-channel match.
3. On success: a `ReleaseDownloadAuthorization` row is created — token
   hashed (SHA-256, same pattern as invitation tokens,
   `app/security/tokens.py`), 5-minute default expiry
   (`OWNER_RELEASE_DOWNLOAD_TOKEN_TTL_SECONDS`), single-use
   (`max_uses=1`). The raw token is returned to the caller exactly once and
   never persisted.
4. `GET /api/licensing/v1/releases/download/<token>` — token-authenticated
   only (no device signature required here: the token itself is
   unguessable, short-lived, and was already issued to a verified device).
   `fetch_download()` re-validates expiry/use-count **and re-checks
   publication state at the moment of fetch**, not just at authorization
   time — a release withdrawn between authorization and fetch (e.g. a
   just-discovered vulnerability) stops being servable immediately, even
   for an already-issued token.
5. Artifact bytes are read from private local/test storage
   (`app/releases/storage.py`) and streamed directly — never a redirect to
   a raw path or a storage credential exposed to the client.
6. Every authorization and every fetch is audited
   (`RELEASE_DOWNLOAD_AUTHORIZED`, `RELEASE_DOWNLOAD_FETCHED`).

## Real bug found and fixed during testing (not just designed correctly on paper)

The happy-path test initially failed with `ACTIVATION_REJECTED` (400).
Root cause: `License.status` after issuance is `"ISSUED"`, not `"ACTIVE"` —
confirmed by direct query against real test data
(`make_license()` → `license.status == "ISSUED"`, `subscription.status ==
"ACTIVE"` — `"ACTIVE"` is a **Subscription** status, not a **License**
one). My first implementation wrongly required `license.status ==
"ACTIVE"`. Fixed to match `process_checkin()`'s own established pattern
exactly: a deny-list (reject only `SUSPENDED`/`REVOKED`/`EXPIRED`) rather
than an allow-list — the same real prior engineering decision, now reused
consistently instead of reinvented incorrectly.

## Anti-enumeration, extended correctly

Reused the existing `PUBLIC_REASON_CODES`/`INTERNAL_ONLY_REASON_CODES`
normalization architecture (`app/licensing_service/reason_codes.py`) rather
than inventing a parallel error vocabulary:

- `RELEASE_NOT_FOUND` and `RELEASE_NOT_PUBLISHED` both normalize to the
  same public `RELEASE_NOT_AVAILABLE` code — a caller must not be able to
  distinguish "no such release" from "a real, unpublished draft exists
  with this ID" (which would leak upcoming/unannounced version
  information).
- **Second bug found and fixed by the test itself:** the two cases
  initially shared the same public reason code but *different HTTP status
  codes* (404 vs 400) — itself a distinguishable side channel, easier to
  observe than the response body. Fixed: both now return 400.
- Token-related codes (`TOKEN_NOT_FOUND`/`TOKEN_EXPIRED`/
  `TOKEN_ALREADY_USED`/`TOKEN_REVOKED`) are kept distinct and public
  deliberately — a download token is a single-use 256-bit random value,
  never guessable, so there is no meaningful enumeration risk in
  distinguishing these for legitimate client debuggability.
- `ARTIFACT_UNAVAILABLE` is deliberately the same public code for both a
  genuinely missing artifact file and a checksum mismatch (possible
  tampering) — the specific reason is audited, not handed to the client as
  a distinguishing signal.

## No path traversal surface, proven not just asserted

The client never supplies a raw path or object key — only an opaque
token. Proven: a `/`-containing "token" never even reaches
`fetch_download()` (Werkzeug's default URL converter excludes `/`,
routing itself 404s with Flask's generic page, confirmed by asserting the
response has *no* JSON body — proof the traversal attempt never reached
application code at all). `app/releases/storage.py`'s realpath-containment
check is documented as defense in depth against a malformed
server-stored `artifact_path` (e.g. a future import bug), not a response
to any client input, since client input never reaches that function.

## Deliberate scoping decisions

- The caller must already know which `product_version_id` it wants — a
  "what's the latest for my product/platform/channel" discovery endpoint
  is real, valuable, distinct future work, not built here, to keep this
  milestone's surface reviewable.
- Local/test storage adapter only (`app/releases/storage.py`), per the
  governing instruction's own explicit allowance. Real external
  object-storage delivery remains **NOT VERIFIED** — blocked on
  infrastructure (`infrastructure-availability-audit.md` #6), not on this
  code.
- Rate limits added (`release_download_authorize`: 20/60s,
  `release_download_fetch`: 10/60s — tighter, since legitimately only one
  fetch is needed per authorization).

## Real test evidence

`owner/tests/test_phase9r_private_distribution.py`, 11/11 passing, all
through real HTTP routes with real device signatures: unpublished release
denied, nonexistent release denied with the *same* code and status as
unpublished (anti-enumeration, both dimensions), wrong product denied,
wrong platform denied, forged signature denied, successful authorization,
successful fetch with byte-for-byte correct content, single-use enforced
(second fetch → 410), unknown token rejected, withdrawal blocks an
already-issued token, and the traversal-shaped path never reaching
application code.
