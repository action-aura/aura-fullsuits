"""LicenseCheckInScheduler (Part L/M) -- the orchestrator that ties every
other Part C module together into one check-in cycle: call Owner, verify
the response independently, evaluate offline policy, persist state, record
events. This is the one place all of that happens -- routes/UI call
run_once() (or let start() schedule it), never re-implement any piece of
this sequence themselves.
"""
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

# States in which there is no installation_id yet to check in with --
# run_once() is a no-op (by design, not an error) in these states.
_NOT_YET_ACTIVATED = frozenset(
    {LicenseState.NOT_CONFIGURED, LicenseState.ACTIVATION_REQUIRED, LicenseState.ACTIVATING}
)


def _resolve_stored_state(
    record: Optional[LicenseStateRecord], events: LicensingEventRecorder
) -> tuple[LicenseState, bool]:
    """Turn whatever `licensing_state.current_state` actually holds into a
    LicenseState, without ever raising. Mirrors flask_guard.py's
    _resolve_current_state() -- same reasoning, same shape -- because this
    scheduler had the identical unguarded `LicenseState(record.
    current_state)` construction at FOUR call sites (run_once(),
    ingest_checkin_response(), reevaluate_only(), _reevaluate()) that
    flask_guard.py's own AUDIT fix did not touch.

    This matters MORE here than in flask_guard.py, not less: flask_guard
    runs inside a Flask request, so an unhandled ValueError there at least
    produces a visible (if ugly) HTTP 500. This scheduler's tick runs on a
    background threading.Timer thread (see _schedule_next() below) -- an
    exception escaping that thread's target is NOT visible to any caller,
    any route, or any log a shop owner would ever look at. It goes to
    threading.excepthook, which prints a traceback to stderr that nobody is
    watching on an unattended background thread.

    Be precise about what does and does not die, because the difference
    decides where the fix belongs. The TIMER CHAIN SURVIVES: _schedule_next()
    arms the next tick from a `finally`, so it re-arms even when run_once()
    raises, and the scheduler goes on ticking forever. What dies is the WORK
    INSIDE each tick -- the raise happens at the very top of run_once(),
    before _refresh_trust_manifest_best_effort() and before check_in(), so
    every tick aborts having done nothing. (Verified by mutation, not
    inferred: with this guard removed, a corrupt row yields one
    threading.excepthook entry PER TICK while scheduler._timer stays alive
    and armed. An earlier draft of this docstring said the thread "quietly
    stops rescheduling itself"; that is wrong, and believing it would send
    the next reader hunting for a dead thread that is in fact running.)

    The end state is the same and is why this matters: no check-in ever
    completes, the licence drifts toward expiry with nobody told, and the
    first symptom is a lockout that looks unrelated to its actual cause. A
    corrupt current_state must never be allowed to produce that -- and it
    has to be fixed HERE, at the resolution, because a scheduler that is
    still dutifully re-arming a tick that can never do anything will not
    look broken from the outside.

    A MISSING record is a fresh install, not a damaged one -- NOT_CONFIGURED,
    exactly as before this fix. A record that exists but whose current_state
    is None, empty, or not a recognized LicenseState member is genuinely
    damaged, and LOCAL_STATE_CORRUPT already exists in the enum for
    precisely this (state_machine.py: it's in DATA_PRESERVED_FAMILY, so nothing
    downstream that depends on "corrupt still means don't destroy local
    data" breaks).

    The raw stored value is logged (safe: application logs are not subject
    to the forbidden-marker scan) but deliberately never placed in the
    recorded event's details -- raw_state is UNTRUSTED data read off disk,
    and events.record() itself RAISES LicensingEventError when details
    contain a forbidden-marker substring (license_key, patient, sale_total,
    ...). Echoing the raw value into details would risk that exact re-raise
    from inside the handler written to prevent one, which is precisely the
    gap flask_guard.py's own verifier already found and fixed in its twin --
    not to be reintroduced here.

    Returns (state, state_unreadable). The SECOND element is why this is a
    pair and not just a LicenseState, and it is load-bearing:
    LOCAL_STATE_CORRUPT is itself a real, legitimately PERSISTED value of
    this column -- _reevaluate() below writes it whenever a stored assertion
    fails re-verification, and _apply_state_transition() then saves it. So
    "the resolved state is LOCAL_STATE_CORRUPT" and "the stored bytes were
    unreadable" are two DIFFERENT conditions that happen to share one enum
    member, and callers must not conflate them:

      * unreadable (state_unreadable=True) -- we do not know what state this
        install is in, so the tick stops here rather than acting on a
        guess.
      * a stored, readable "LOCAL_STATE_CORRUPT" (state_unreadable=False) --
        we know exactly what state this install is in, and the ONLY way out
        of it short of a destructive local reset (state_repository.py's
        controlled reset flow, which forces full re-activation) is for a
        check-in to succeed and overwrite it. Collapsing this into the
        unreadable case would make run_once() return before
        _refresh_trust_manifest_best_effort() and before check_in() -- which
        would permanently strand exactly the installs that land here for a
        TRANSIENT reason. The most likely such reason is the one this
        package already handles everywhere else: Owner rotated its signing
        key, so the locally stored (still perfectly genuine) assertion no
        longer verifies against a stale trust store -> _reevaluate()'s
        AssertionVerificationError branch -> LOCAL_STATE_CORRUPT persisted.
        The very next tick's manifest refresh is what heals that. A paying
        shop must not be pushed into a manual re-activation because of a key
        rotation it never saw.
    """
    if record is None:
        return LicenseState.NOT_CONFIGURED, False

    raw_state = record.current_state
    try:
        # None and "" both fail this lookup exactly like a bogus string does
        # (LicenseState has no member whose value is None or ""), so one
        # except branch below correctly covers all three cases.
        #
        # A readable value -- INCLUDING a readable "LOCAL_STATE_CORRUPT" --
        # comes back with state_unreadable=False, so every state that
        # parsed before this AUDIT fix keeps behaving exactly as it did.
        # This fix only ever changes what happens to values that used to
        # raise.
        return LicenseState(raw_state), False
    except ValueError:
        logger.warning(
            "checkin_scheduler: stored current_state %r is not a recognized "
            "LicenseState (missing, empty, or unrecognized value -- a "
            "downgrade, restored backup, or partial write can all produce "
            "this). Treating this tick as LOCAL_STATE_CORRUPT instead of "
            "raising -- the scheduler thread must survive this.",
            raw_state,
        )
        events.record(
            "LOCAL_STATE_CORRUPT",
            {"detected_by": "checkin_scheduler._resolve_stored_state"},
            trusted_keys=frozenset({"detected_by"}),
        )
        return LicenseState.LOCAL_STATE_CORRUPT, True


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
        anchor_recovery: Optional[Callable[[], bool]] = None,
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
        # Last-resort trust recovery for a DISCONTINUOUS Owner rotation, the
        # one case _refresh_trust_manifest_best_effort() provably cannot fix:
        # a fresh Owner deploy holds only its new key, so no manifest it can
        # produce is countersigned by anything this install trusts. Without
        # this, an already-ACTIVE device fails every check-in from that moment
        # on and degrades to RESTRICTED with no way back except re-activating.
        # See OwnerTrustStore.admit_bundled_anchor for why it is safe.
        self._anchor_recovery = anchor_recovery
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()

    def _recover_via_bundled_anchor(self, verify: Callable[[], object]):
        """Last resort after a manifest refresh could not resolve an
        UNKNOWN_SIGNING_KEY: re-read the anchor this build shipped with and
        verify once more. Returns the verified assertion, or None.

        The counterpart of activation.py's re-anchor step, and needed for the
        same reason: the manifest bridge cannot cross a DISCONTINUOUS rotation,
        because a fresh Owner deploy holds no key this install ever trusted and
        therefore cannot countersign anything. Without this, activation could
        recover but an already-ACTIVE device could not, and it would grind down
        to RESTRICTED on a licence that is perfectly valid.

        Callers must gate this on the reason code themselves -- see the call
        sites. Recording the event here rather than at each call site keeps a
        re-anchor from ever being silent on this path either.
        """
        if self._anchor_recovery is None:
            return None
        if not self._anchor_recovery():
            return None  # anchor added nothing; re-verifying would repeat itself
        self._events.record("TRUST_ANCHOR_READMITTED", {"trusted_key_ids": self._trust_store.trusted_key_ids()})
        try:
            return verify()
        except AssertionVerificationError:
            return None

    def run_once(self) -> LicenseState:
        """Windows path: this process owns the device key, so it also owns
        the HTTP call. Android never calls this -- see
        ingest_checkin_response()/reevaluate_only() below."""
        record = self._state_repository.load()
        # state_unreadable, NOT "state == LOCAL_STATE_CORRUPT" -- see
        # _resolve_stored_state()'s docstring: a readable, persisted
        # LOCAL_STATE_CORRUPT must still fall through to the manifest
        # refresh + check-in below, because that is the only non-destructive
        # way out of that state.
        current_state, state_unreadable = _resolve_stored_state(record, self._events)
        if state_unreadable or current_state in _NOT_YET_ACTIVATED:
            return current_state

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
        # Same reasoning as run_once(): a readable, persisted
        # LOCAL_STATE_CORRUPT must still be allowed to ingest a fresh,
        # independently verified assertion -- that ingestion is Android's
        # only route out of the state.
        current_state, state_unreadable = _resolve_stored_state(record, self._events)
        if state_unreadable or current_state in _NOT_YET_ACTIVATED:
            return current_state

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

                # Gated on the reason code, NOT merely on "verified is None":
                # this block is reached for every AssertionVerificationError,
                # and an expired or device-mismatched assertion must never
                # trigger a trust change.
                if verified is None and exc.reason_code == "UNKNOWN_SIGNING_KEY":
                    verified = self._recover_via_bundled_anchor(_verify)

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
        current_state, state_unreadable = _resolve_stored_state(record, self._events)
        if state_unreadable or current_state in _NOT_YET_ACTIVATED:
            return current_state
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
            # at all -- nothing to evaluate against. record is guaranteed
            # non-None here (every caller already passed the
            # _resolve_stored_state()/_NOT_YET_ACTIVATED guard above), but
            # current_state itself can still be a corrupt value, so this
            # must go through the same guarded resolution rather than a
            # bare LicenseState(record.current_state). Only the state itself
            # is wanted here: every caller already consumed the
            # state_unreadable flag above, and an unreadable value cannot
            # reach this line at all (it short-circuited there).
            return _resolve_stored_state(record, self._events)[0]

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
