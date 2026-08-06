package com.actionaura.retail.licensing.lease

import platform.Foundation.NSDate
import platform.Foundation.NSProcessInfo
import platform.Foundation.timeIntervalSince1970

/**
 * Real iOS platform clocks. `NSProcessInfo.processInfo.systemUptime`
 * is real, documented monotonic time since last boot -- the iOS
 * equivalent of Android's `elapsedRealtime()`. Real, written, **NOT
 * VERIFIED** on this host (no macOS/Xcode -- standing disclosure
 * pattern, `ios-secure-storage-runtime-validation-plan.md`).
 */
actual fun systemWallClock(): WallClock = WallClock { (NSDate().timeIntervalSince1970 * 1000).toLong() }
actual fun systemMonotonicClock(): MonotonicClock = MonotonicClock { (NSProcessInfo.processInfo.systemUptime * 1000).toLong() }
