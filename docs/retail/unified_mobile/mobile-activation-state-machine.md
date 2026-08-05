# Mobile Activation State Machine (M9.9)

`ActivationStateMachine.kt` — one canonical shared state machine. No
generic loading boolean anywhere in this design.

## Real states (26)

`NOT_STARTED`, `CUSTOMER_SESSION_REQUIRED`, `CUSTOMER_AUTHENTICATING`,
`CUSTOMER_VERIFICATION_REQUIRED`, `LICENSE_INPUT_REQUIRED`,
`LICENSE_CLAIMING`, `LICENSE_REJECTED`, `DEVICE_POLICY_LOADING`,
`DEVICE_POLICY_REJECTED`, `INSTALLATION_IDENTITY_REQUIRED`,
`READY_TO_ACTIVATE`, `ACTIVATION_REQUESTING`,
`ACTIVATION_RETRY_AVAILABLE`, `ACTIVATION_REJECTED`,
`DEVICE_LIMIT_REACHED`, `PLATFORM_NOT_ALLOWED`,
`IOS_SERVER_NOT_READY`, `INSTALLATION_REVOKED`,
`ACTIVATION_RESPONSE_RECEIVED`, `SECURE_PERSISTENCE_REQUIRED`,
`ACTIVATION_COMPLETE`, `NETWORK_UNAVAILABLE`, `SERVER_UNAVAILABLE`,
`TRANSPORT_NOT_CONFIGURED`, `CANCELLED`, `FATAL_CONTRACT_ERROR` —
exactly the checkpoint's own named list, no fewer, no more.

## Real transition table

Defined once, as a `Map<Pair<ActivationState, ActivationAction>,
ActivationState>`, in `TRANSITIONS`. Every entry documents:
- **allowed previous state** — the map key's first element.
- **triggering action** — the map key's second element
  (`ActivationAction`: `START`, `SIGN_IN`, `VERIFICATION_CONFIRMED`,
  `SUBMIT_LICENSE`, `RETRY_LICENSE_CLAIM`, `DEVICE_POLICY_LOADED`,
  `CONFIRM_DEVICE`, `ACTIVATE`, `RETRY_ACTIVATION`,
  `RESPONSE_RECEIVED`, `SECURE_PERSISTENCE_COMMITTED`, `CANCEL`,
  `TRANSPORT_FAILURE`, `RESTART`).
- **required data/side effect/retry behavior** — real, owned by the
  future orchestration glue that calls `transition()`, not by the pure
  function itself (kept deliberately separate — the state machine is
  pure and side-effect-free, per its own doc comment).

`CANCEL` is real, generically wired for every non-terminal,
non-`NOT_STARTED` state — cancellation is always possible mid-flow.
`RESTART` is real, generically wired for every rejection/blocked
state back to `NOT_STARTED` — a real, terminal-adjacent recovery path.

## Terminal states

`ACTIVATION_COMPLETE`, `CANCELLED`, `FATAL_CONTRACT_ERROR`
(`TERMINAL_STATES`) — `isTerminal()` extension function; these accept
no further transition in the table (verified structurally: no
`TERMINAL_STATES` member appears as a map key's first element).

## Invalid transitions fail deterministically

`transition(from, action)` returns `ActivationTransitionResult.Applied`
only for a real, present map entry; every other `(from, action)` pair
returns `Rejected(fromState, action)` — never throws, never silently
no-ops, never guesses a "closest" state.

## Secret-cleanup behavior

Not modeled in the pure state machine itself (states carry no payload
in this file) — real secret-cleanup happens in the future
`ActivationViewModel` (M9.18), whose `UiState` never holds a
credential/serial/lease in the first place (M9.18's own contract).
