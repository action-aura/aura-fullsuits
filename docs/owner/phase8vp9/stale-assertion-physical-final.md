# Phase 8V-P9 Part K — Physical Stale-Assertion Rejection: Final Proof

## Real finding this session (Part K investigation)

While building this proof, discovered that no stale-assertion/replay guard existed anywhere in the
real client ingestion path (`commercial_runtime/licensing_contracts/checkin_scheduler.py`). A real,
valid, Owner-signed, not-yet-expired OLDER assertion could silently overwrite a newer one's local
state if delivered/replayed later, within its own TTL window. This is a genuine gap, not a
theoretical one -- `verify_assertion()` checks signature, validity window, and identity match, but
never checked whether the assertion being ingested was actually newer than what was already stored,
and `LicenseStateRepository.save()` always overwrites unconditionally.

## Fix

`checkin_scheduler.py`: new `_is_stale_assertion()` static method, comparing the signed `issued_at` of
the incoming assertion against the stored one's `assertion_issued_at`. Wired into
`ingest_checkin_response()` -- the single real function both the Windows (`run_once()`) and Android
(Kotlin HTTP call -> Python re-verification) check-in paths converge on. A tie (`new_dt == stored_dt`)
is NOT treated as stale -- two distinct, genuinely issued assertions minted within the same
second-resolution instant (e.g. activation immediately followed by a check-in) are real and
legitimate, not a replay. `events.py`: new `ASSERTION_STALE_REJECTED` event type registered.

Unit-level proof: `commercial_runtime/licensing_contracts/tests/test_checkin_scheduler.py` --
`test_older_valid_assertion_rejected_as_stale_after_newer_one_accepted`,
`test_first_ever_assertion_is_never_rejected_as_stale`. Full suite 235/235 passing after the fix
(commercial_runtime), 405/405 (Owner, unrelated collateral check).

This is necessary but explicitly NOT sufficient per this phase's own requirement ("not unit tests
only") -- the below is the real, physical proof.

## Physical proof method

Unit tests alone were explicitly disallowed by this phase's requirements, along with mocked
verifiers, forged signatures, manually edited state versions, or direct DB edits. The real product
process was used instead: the actual rc.5 `AuraRetail.exe` (rebuilt this session, embeds the fixed
`commercial_runtime`), running as installation `a81dfc79-9fe9-48e1-a1d5-651deb0bcd73` on Scenario 7's
real test license `68a467ec-901b-4470-832d-534e9fc24a74` (`AURA_APP_DATA=...AuraRetail-P7-C`, a real
Windows identity established in Phase 8V-P7 with its own real Ed25519 device key).

A thin, transparent reverse proxy (`stale_replay_proxy.py`, scratchpad) was placed in front of the
real Owner (`http://127.0.0.1:5551`). Every request is forwarded to the real Owner and its real
response returned verbatim -- proven by the real Owner wire-capture log showing every one of these
calls actually reaching Owner -- with exactly one exception: on `POST .../check-ins`, the real,
freshly Owner-signed `signed_assertion` in the response is swapped for a genuine, real,
previously-Owner-signed OLDER assertion (`assertion_id b697c9d7...`, `issued_at
2026-07-31T10:26:12.140578+00:00`, real signature `+rGNhA7...` -- pulled directly from this same
installation's own local `licensing_state` table before it was superseded, not forged, not
hand-edited). This reproduces exactly the real-world threat: a genuine, validly signed, not-yet-expired
assertion being delivered again after a newer one was already accepted (replay/downgrade).

## Sequence and evidence

1. **Before state**: real check-in via the real Owner (no proxy) accepted a fresh assertion
   `8cb19efc-8ba7-4d16-ace7-9bf81f3d2fd9` (`issued_at 2026-07-31T11:47:55.439033+00:00`). Confirmed via
   `GET /api/licensing/status` on the running instance: `assertion_expires_at
   2026-08-01T11:47:55.439033+00:00`, `current_state ACTIVE_ONLINE`.
2. Instance restarted pointed at the replay proxy (`AURA_OWNER_LICENSING_URL=http://127.0.0.1:15551/...`),
   same real `AURA_APP_DATA`, same real device key (no reactivation, no key change).
3. Real check-in triggered (`POST /api/licensing/check-in` on the product's own local API). Real Owner
   genuinely issued a brand-new assertion `5877dd32-3acb-469a-bcbe-35b4a5efab2f` (`issued_at
   2026-07-31T11:50:55.748388+00:00`) -- confirmed in Owner's own wire-capture log
   (`capture_raw.jsonl`). The proxy substituted the OLD `b697c9d7` assertion in its place before
   returning it to the client (confirmed in the proxy's own log:
   `intercepted check-in response, real assertion_id=5877dd32... -- substituting OLD
   assertion_id=b697c9d7...`).
4. Client response: `current_state: ACTIVE_OFFLINE`, `last_attempt_reached_owner: false`,
   `assertion_expires_at` **unchanged** at `2026-08-01T11:47:55.439033+00:00` -- the stale assertion
   was rejected, not accepted.
5. Local SQLite state (`AuraRetail-P7-C\database\subsystems\licensing.db`, real file, real product
   database) confirms: `assertion_id` remained `8cb19efc-8ba7-4d16-ace7-9bf81f3d2fd9` -- the OLD
   `b697c9d7` never got written. Local event log (`licensing_events`, same real database) shows, in
   order: `CHECK_IN_SUCCEEDED` / `ASSERTION_ACCEPTED` at `11:47:55` (the real newer assertion, accepted
   normally), then `ASSERTION_STALE_REJECTED` at `11:50:55.793672+00:00` followed by
   `OFFLINE_MODE_ENTERED` (real fallback behavior, same as the unit test's observed event sequence) --
   no new `CHECK_IN_SUCCEEDED`/`ASSERTION_ACCEPTED` pair was produced by the replay.

## What this proves

- Rejection was specifically **staleness**, not signature failure -- the substituted assertion's
  signature was real and valid (it was a genuine, previously-issued Owner signature), so a signature
  failure would not explain the rejection; only the monotonicity check does.
- Device identity, activation state, and license/subscription data were untouched -- confirmed via the
  same `installation_id`/`license_public_id` before and after, no reactivation occurred.
- No secret leakage: proxy log and Owner wire-capture log contain only the same fields already
  captured/redacted under the existing wire-capture redaction policy; no pepper, no device private
  key, no raw license key appeared anywhere.
- This proof used the actual rebuilt rc.5 artifact -- not the pre-fix rc.4 binary -- so it validates
  the artifact that will actually ship, not just the source fix in isolation.

## Cleanup

Instance C was restarted afterward pointed back at the real Owner
(`AURA_OWNER_LICENSING_URL=http://127.0.0.1:5551/api/licensing/v1`) with no proxy involved, to resume
real Scenario 7 continuation work. The replay proxy is not part of the shipped product and was run
only from the scratchpad, entirely separate from any product source tree.
