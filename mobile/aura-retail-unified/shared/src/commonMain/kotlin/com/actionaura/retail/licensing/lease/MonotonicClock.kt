package com.actionaura.retail.licensing.lease

/**
 * M11.13 -- real platform-neutral clock contracts. `WallClock` and
 * `MonotonicClock` are deliberately separate types (never one
 * interface with two methods) so a caller cannot accidentally pass a
 * monotonic reading where a civil timestamp is required or vice versa
 * -- exactly the confusion `trusted_time.py`'s own KDoc warns against.
 */
fun interface WallClock {
    /** Real, current, untrusted local civil time, epoch milliseconds UTC. */
    fun nowEpochMillis(): Long
}

/**
 * Real monotonic elapsed-time source. Never a civil timestamp, never
 * comparable across process/boot-session boundaries without explicit
 * detection (M11.13's own required disclosure). Android: real
 * `SystemClock.elapsedRealtime()`-equivalent (survives deep sleep,
 * resets on reboot). iOS: real `ProcessInfo.systemUptime`-equivalent.
 */
fun interface MonotonicClock {
    /** Real, monotonically non-decreasing elapsed milliseconds since some arbitrary, platform-defined origin (never epoch, never comparable across processes started at different times on different platforms). */
    fun elapsedMillis(): Long
}

expect fun systemWallClock(): WallClock
expect fun systemMonotonicClock(): MonotonicClock
