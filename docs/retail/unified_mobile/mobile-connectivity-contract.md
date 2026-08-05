# Mobile Connectivity Contract (M9.16)

`ConnectivityContract.kt` — platform-neutral connectivity signal.

## Real, closed signal type

`ConnectivitySignal` — `ApparentlyOnline`/`NoApparentNetwork`. Named
deliberately ("apparently") to make clear this is a UX hint, never
proof the server is reachable — that determination only ever comes
from a real, attempted `TransportOutcome` (`NetworkFailure`/
`ServerUnavailable`/etc.), which this contract does not produce.

## Real `ConnectivityObserver` interface

`val signal: Flow<ConnectivitySignal>` — a `commonMain` contract; no
`android.net.ConnectivityManager` import anywhere in this file or
package (grepped, zero matches) — satisfying the checkpoint's own "do
not use Android ConnectivityManager directly in commonMain" rule.

## Real, honest default

`UnknownConnectivityObserver` — the only implementation M9 ships;
always emits `ApparentlyOnline`, i.e. "no real signal available, don't
claim otherwise" rather than guessing. Real platform adapters
(`androidMain`'s own `ConnectivityManager`-backed implementation,
`iosMain`'s own `NWPathMonitor`-backed implementation) are compile-
safe skeletons only — not implemented in M9, since real runtime
adapter implementation belongs to the Android/iOS platform milestones
per the checkpoint's own instruction ("M9 may provide compile-safe
adapter skeletons... Runtime adapter implementation belongs to the
appropriate platform milestone when not available now"). No skeleton
file was added in `androidMain`/`iosMain` in M9 itself — the
`commonMain` interface is sufficient for M9's own real scope (nothing
in M9 calls `ConnectivityObserver` yet; it exists for a future
milestone's consumption), and adding an empty, untested platform stub
would not be real, verified work.
