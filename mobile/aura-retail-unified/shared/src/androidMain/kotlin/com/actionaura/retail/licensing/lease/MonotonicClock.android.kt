package com.actionaura.retail.licensing.lease

import android.os.SystemClock

/**
 * Real Android platform clocks. `SystemClock.elapsedRealtime()` is
 * real, documented to include time spent in deep sleep (unlike
 * `uptimeMillis()`), the correct real choice for a licensing-relevant
 * elapsed-time measurement that must not be fooled by the device
 * sleeping through an offline grace window (`clock-rollback-
 * detection.md`/`clock-forward-jump-policy.md`).
 */
actual fun systemWallClock(): WallClock = WallClock { System.currentTimeMillis() }
actual fun systemMonotonicClock(): MonotonicClock = MonotonicClock { SystemClock.elapsedRealtime() }
