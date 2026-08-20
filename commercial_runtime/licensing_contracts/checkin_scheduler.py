"""LicenseCheckInScheduler (Part L/M) -- the orchestrator that ties every
other Part C module together into one check-in cycle: call Owner, verify
the response independently, evaluate offline policy, persist state, record
events. This is the one place all of that happens -- routes/UI call
run_once() (or let start() schedule it), never re-implement any piece of
this sequence themselves.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from .assertion_verifier import AssertionVerificationError, verify_assertion
from .client import LicensingClient, LicensingClientError
from .events import LicensingEventRecorder
from .policy_evaluator import evaluate as evaluate_policy
from .state_machine import LicenseState
from .state_repository import LicenseStateRecord, LicenseStateRepository
from .trust_store import OwnerTrustStore
from .trusted_time import TrustedTimeAnchor, cache_fresh_anchor, get_cached_or_rehydrate_anchor, new_anchor

# States in which there is no installation_id yet to check in with --
# run_once() is a no-op (by design, not an error) in these states.
_NOT_YET_ACTIVATED = frozenset(
    {LicenseState.NOT_CONFIGURED, LicenseState.ACTIVATION_REQUIRED, LicenseState.ACTIVATING}
)

# State-entry events (Part W) fired the first time a check-in cycle lands on
# that state, keyed by the target LicenseState.
_STATE_ENTRY_EVENTS = {
    LicenseState.ACTIVE_OFFLINE: "OFFLINE_MODE_ENTERED",
    LicenseState.WARNING: "WARNING_ENTERED",
    LicenseState.GRACE_PERIOD: "GRACE_ENTERED",
    LicenseState.RESTRICTED: "RESTRICTED_MODE_ENTERED",
    LicenseState.SUSPENDED: "LICENSE_SUSPENDED",
    LicenseState.REVOKED: "LICENSE_REVOKED",
    LicenseState.EXPIRED: "LICENSE_EXPIRED",
    LicenseState.CLOCK_REVIEW_REQUIRED: "CLOCK_REVIEW_REQUIRED",
}


class LicenseCheckInScheduler:
    def __init__(
        self,
        *,
        client: LicensingClient,
        signer,
        trust_store: OwnerTrustStore,
        state_repository: LicenseStateRepository,
        event_recorder: LicensingEventRecorder,
        product_code: str,
        platform: str,
        device_public_key_fingerprint: str,
        local_safety_ceiling_seconds: Optional[int] = None,
    ):
        self._client = client
        self._signer = signer
        self._trust_store = trust_store
        self._state_repository = state_repository
        self._events = event_recorder
        self._product_code = product_code
        self._platform = platform
        self._device_fingerprint = device_public_key_fingerprint
        self._safety_ceiling = local_safety_ceiling_seconds
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()

    def run_once(self) -> LicenseState:
        """Windows path: this process owns the device key, so it also owns
        the HTTP call. Android never calls this -- see
        ingest_checkin_response()/reevaluate_only() below."""
        record = self._state_repository.load()
        if record is None or LicenseState(record.current_state) in _NOT_YET_ACTIVATED:
            return LicenseState(record.current_state) if record else LicenseState.NOT_CONFIGURED

        self._refresh_trust_manifest_best_effort()

        try:
            response = self._client.check_in(installation_id=record.owner_installation_id, signer=self._signer)
        except LicensingClientError:
            self._events.record("CHECK_IN_FAILED")
            return self.reevaluate_only(checkin_ok=False)

        return self.ingest_checkin_response(response)

    def ingest_checkin_response(
        self, response: dict, *, trust_refresher: Optional[Callable[[], None]] = None
    ) -> LicenseState:
        """Android path (Part U): the Kotlin layer already made the signed
        HTTP call to Owner and hands the RAW, UNTRUSTED response here over
        the localhost sync endpoint. Independently re-verified from scratch,
        exactly as run_once() verifies a response it fetched itself --
        nothing about Kotlin's own opinion of the outcome is trusted.

        trust_refresher: the same one-shot recovery
        activation.ingest_activation_response() takes, and for the same
        reason -- see make_trust_manifest_refresher(). Owner may have
        rotated its signing key since this install last refreshed, in which
        case a perfectly genuine assertion fails UNKNOWN_SIGNING_KEY here
        and keeps failing forever. run_once() never needs this because it
        refreshes the manifest before EVERY cycle (above), but Android
        never calls run_once(): its Kotlin layer owns the device key and
        makes the Owner call itself, so this method is the only place a
        post-activation rotation can be noticed at all. Left None by the
        Windows path, which is already covered by run_once()'s refresh.
        """
        record = self._state_repository.load()
        if record is None or LicenseState(record.current_state) in _NOT_YET_ACTIVATED:
            return LicenseState(record.current_state) if record else LicenseState.NOT_CONFIGURED

        checkin_ok = False
        envelope = response.get("signed_assertion") if response else None
        if envelope is None:
            self._events.record("CHECK_IN_FAILED")
        else:

            def _verify():
                return verify_assertion(
                    envelope,
                    trust_store=self._trust_store,
                    expected_product_code=self._product_code,
                    expected_platform=self._platform,
                    expected_installation_id=record.owner_installation_id,
                    expected_device_key_fingerprint=self._device_fingerprint,
                    trusted_now=datetime.now(timezone.utc),
                )

            verified = None
            try:
                verified = _verify()
            except AssertionVerificationError as exc:
                # UNKNOWN_SIGNING_KEY is the one verification failure that can
                # be a stale-trust-store problem rather than a bad assertion.
                # Refresh the key-set manifest once and re-verify -- exactly
                # what activation.ingest_activation_response() does, and the
                # exact half of that fix this twin was originally missed by.
                # Every other reason_code means the assertion itself is
                # wrong, and refreshing trust could not possibly change that.
                if exc.reason_code == "UNKNOWN_SIGNING_KEY" and trust_refresher is not None:
                    trust_refresher()
                    try:
                        # Exactly one retry: the refresh either produced a
                        # trusted signer or it did not, and re-running the
                        # same deterministic check against the same store
                        # would just repeat itself.
                        verified = _verify()
                    except AssertionVerificationError:
                        verified = None

            if verified is None:
                # Retain the previous valid assertion (Part L) -- do not
                # overwrite record with anything from this response.
                self._events.record("ASSERTION_REJECTED")
            else:
                # Phase 8V-P9: real finding -- verify_assertion() (signature +
                # validity-window + identity checks) says nothing about
                # whether THIS assertion is newer than the one already
                # stored. Without this check, a genuinely valid, still-
                # unexpired OLDER assertion (e.g. one issued before a real
                # commercial transition, replayed or delivered out of order
                # any time within its own signed TTL window) would silently
                # overwrite a newer one and revert local state -- exactly
                # the "monotonic assertions" guarantee Non-Negotiable Rule 9
                # requires and this ingestion path never actually enforced.
                # `assertion_issued_at` already exists on the stored record
                # (persisted by _persist_fresh_assertion() below) purely as
                # a timestamp; it was just never compared against on the
                # way in. A missing stored value (first-ever assertion for
                # this record) always allows -- there is nothing to be
                # stale relative to.
                if self._is_stale_assertion(record, verified.payload):
                    self._events.record("ASSERTION_STALE_REJECTED")
                else:
                    checkin_ok = True
                    self._events.record("CHECK_IN_SUCCEEDED")
                    self._events.record("ASSERTION_ACCEPTED")
                    record = self._persist_fresh_assertion(record, envelope, verified)

        new_state = self._reevaluate(record, checkin_ok)
        self._apply_state_transition(record, new_state)
        return new_state

    def reevaluate_only(self, *, checkin_ok: bool = False) -> LicenseState:
        """No network call at all -- re-runs offline-policy evaluation
        against the currently stored assertion and elapsed trusted time.
        Used for: a failed check-in attempt (Windows, above), and Android's
        periodic local re-check (app foreground, no fresh network round
        trip needed to notice a WARNING/GRACE_PERIOD/RESTRICTED boundary
        has been crossed purely by time passing)."""
        record = self._state_repository.load()
        if record is None or LicenseState(record.current_state) in _NOT_YET_ACTIVATED:
            return LicenseState(record.current_state) if record else LicenseState.NOT_CONFIGURED
        new_state = self._reevaluate(record, checkin_ok)
        self._apply_state_transition(record, new_state)
        return new_state

    def start(self, interval_seconds: int) -> None:
        self._stopped.clear()
        self._schedule_next(interval_seconds)

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self, interval_seconds: int) -> None:
        if self._stopped.is_set():
            return

        def _tick():
            try:
                self.run_once()
            finally:
                self._schedule_next(interval_seconds)

        self._timer = threading.Timer(interval_seconds, _tick)
        self._timer.daemon = True
        self._timer.start()

    def _refresh_trust_manifest_best_effort(self) -> None:
        try:
            manifest = self._client.fetch_signing_keys()
        except LicensingClientError:
            return
        try:
            self._trust_store.admit_manifest(manifest)
        except Exception:
            return  # malformed manifest -- never let this break the check-in cycle

    @staticmethod
    def _is_stale_assertion(record: LicenseStateRecord, payload: dict) -> bool:
        """Phase 8V-P9 (Part K): the real monotonicity guard. A newly
        verified assertion is stale when a real prior assertion is already
        stored and this one's `issued_at` is strictly older. Compares real,
        signed `issued_at` timestamps (never client wall-clock time, never a
        value this function invents) -- `issued_at` is itself part of the
        signed payload verify_assertion() already authenticated, so this
        comparison is over trustworthy data, not a new trust source.

        Equal `issued_at` is NOT treated as stale: two distinct, genuinely
        issued assertions minted within the same second-resolution instant
        (e.g. activation immediately followed by a check-in) are a real,
        legitimate occurrence, not a replay -- a replay is always a byte-
        identical re-delivery of something with a strictly older `issued_at`
        than what is already stored. Rejecting ties would create a false
        denial against genuine rapid-succession check-ins."""
        stored_issued_at = record.assertion_issued_at
        if not stored_issued_at:
            return False  # nothing stored yet -- first assertion always allowed
        new_issued_at = payload.get("issued_at")
        if not new_issued_at:
            return True  # a signed assertion missing issued_at is never newer than something real
        try:
            stored_dt = datetime.fromisoformat(stored_issued_at)
            new_dt = datetime.fromisoformat(new_issued_at)
        except ValueError:
            return True  # unparseable -- deny-by-default, never guess
        return new_dt < stored_dt

    def _persist_fresh_assertion(self, record: LicenseStateRecord, envelope: dict, verified) -> LicenseStateRecord:
        import json

        payload = verified.payload
        record.assertion_envelope_json = json.dumps(envelope)
        record.assertion_id = payload.get("assertion_id")
        record.assertion_issued_at = payload.get("issued_at")
        record.assertion_not_before = payload.get("not_before")
        record.assertion_expires_at = payload.get("expires_at")
        record.trusted_time_anchor_server_time = payload.get("issued_at")
        record.last_successful_checkin_at = datetime.now(timezone.utc).isoformat()
        record.last_sync_result = "SUCCESS"
        record.license_status = verified.evidence.license_status
        record.installation_status = verified.evidence.installation_status
        record.subscription_status = verified.evidence.subscription_status
        record.entitlements_json = json.dumps(payload.get("entitlements", {}))
        record.offline_policy_json = json.dumps(payload.get("offline_policy", {}))
        self._state_repository.save(record)

        # Phase 7V-F: pin the trusted-time anchor synchronously, right here,
        # at the one moment trusted_time_anchor_server_time is guaranteed to
        # equal real "now" (a live successful sync just happened). See
        # trusted_time.py's cache_fresh_anchor()/get_cached_or_rehydrate_
        # anchor() docstrings for why this cannot be done lazily on first
        # later access. Must use record.trusted_time_anchor_server_time
        # (Owner's issued_at) as the cached anchor's server_time -- that is
        # exactly the value later lookups key on -- while still capturing
        # monotonic_at_anchor fresh, right now (this is the one moment that
        # pairing is valid).
        cache_fresh_anchor(
            record.owner_installation_id,
            datetime.fromisoformat(record.trusted_time_anchor_server_time),
        )
        return record

    def _reevaluate(self, record: LicenseStateRecord, checkin_ok: bool) -> LicenseState:
        if record.assertion_envelope_json is None:
            # Never successfully activated far enough to have an assertion
            # at all -- nothing to evaluate against.
            return LicenseState(record.current_state)

        import json

        envelope = json.loads(record.assertion_envelope_json)
        try:
            verified = verify_assertion(
                envelope,
                trust_store=self._trust_store,
                expected_product_code=self._product_code,
                expected_platform=self._platform,
                expected_installation_id=record.owner_installation_id,
                expected_device_key_fingerprint=self._device_fingerprint,
                trusted_now=datetime.now(timezone.utc),
            )
        except AssertionVerificationError:
            self._events.record("LOCAL_STATE_CORRUPT")
            return LicenseState.LOCAL_STATE_CORRUPT

        anchor = self._resolve_anchor(record)
        last_checkin = (
            datetime.fromisoformat(record.last_successful_checkin_at)
            if record.last_successful_checkin_at
            else verified.evidence.not_before
        )
        return evaluate_policy(
            evidence=verified.evidence,
            anchor=anchor,
            local_wall_clock_now=datetime.now(timezone.utc),
            last_successful_checkin_at=last_checkin,
            last_checkin_attempt_ok=checkin_ok,
            local_safety_ceiling_seconds=self._safety_ceiling,
        )

    def _resolve_anchor(self, record: LicenseStateRecord) -> TrustedTimeAnchor:
        """Phase 7V-F fix (found via live production-like validation):
        routes.py deliberately builds a fresh LicenseCheckInScheduler on
        every request (so config/key changes take effect without a
        restart), which means an anchor cached only on `self` never
        survives between check-ins. The process-lifetime cache in
        trusted_time.py reuses the same monotonic pin (established
        synchronously at the moment of the last successful sync -- see
        _persist_fresh_assertion / activation.py's identical call) as long
        as the persisted server_time hasn't changed, which is what actually
        lets elapsed monotonic time accumulate across repeated failed
        check-ins. If this process never itself witnessed the sync that
        produced the persisted server_time (e.g. a restart with no
        successful sync yet in this process), this correctly falls back to
        rehydrate_anchor() -- a real, narrower residual gap, documented in
        phase7v-final/final-residual-risk-register.md.
        """
        if not record.trusted_time_anchor_server_time:
            return new_anchor(datetime.now(timezone.utc))
        cache_key = record.owner_installation_id or record.id
        return get_cached_or_rehydrate_anchor(cache_key, record.trusted_time_anchor_server_time)

    def _apply_state_transition(self, record: LicenseStateRecord, new_state: LicenseState) -> None:
        if new_state.value != record.current_state:
            event_type = _STATE_ENTRY_EVENTS.get(new_state)
            if event_type:
                self._events.record(event_type)
            self._state_repository.update_state(new_state.value)
