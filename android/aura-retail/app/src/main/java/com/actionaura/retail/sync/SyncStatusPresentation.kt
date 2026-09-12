package com.actionaura.retail.sync

import kotlin.math.roundToLong

// Pure presentation logic for the sync status screen -- deliberately free of
// Compose and android.* imports so this file is unit-testable on a plain JVM
// (no Robolectric, no instrumented device, matching how the rest of this
// module's business logic is tested). See SyncStatusPresentationTest.kt for
// why this split exists: `SyncCoordinator.health()` has carried a real,
// tested seam since it was written ("this exists so a failure is inspectable
// rather than invisible"), but nothing ever read it -- this file and the
// screen that consumes it are that reader.

/** Consecutive failures (on either push or pull) before a device is shown as
 *  actively FAILING rather than just quiet. Mirrors the desktop's own
 *  `RetailSystem.SYNC_DEGRADED_THRESHOLD` (products/retail/frontend/app-shell.js)
 *  exactly -- a single blip must never alarm the user, only a run of them. */
const val SYNC_DEGRADED_THRESHOLD = 3

/** The four states the desktop's sync banner distinguishes, ported to native
 *  Android. Order is NOT significant here (see [classifySync] for the
 *  precedence that matters). */
enum class SyncTier { NOT_CONFIGURED, NEVER_SYNCED, FAILING, CALM }

/**
 * Classifies this device's current sync health into one of four tiers, for a
 * screen to render. Precedence is load-bearing and deliberately checked in
 * this exact order:
 *
 *  1. Not configured at all (`!health.configured`) always wins, regardless of
 *     what push/pull otherwise report -- an unconfigured build has nothing
 *     meaningful to say about failures or staleness.
 *  2. FAILING is checked BEFORE never-synced. This is the one precedence
 *     choice worth spelling out: a fresh install that cannot reach the relay
 *     has never synced AND is failing at the same time -- its push/pull
 *     counters are climbing from the very first tick. If never-synced were
 *     checked first, a device stuck behind a broken relay address would say
 *     "waiting for first sync" forever, which reads as "give it a moment"
 *     when the true story is "this cannot succeed until something is fixed".
 *     Checking failures first makes the screen say WHY it hasn't synced
 *     instead of leaving the owner to assume it just hasn't had time yet.
 *  3. NEVER_SYNCED: configured, not failing (yet), but no successful push or
 *     pull has ever landed -- e.g. a brand-new install still on its very
 *     first tick.
 *  4. CALM: everything else -- configured, not failing, and has synced at
 *     least once.
 */
fun classifySync(health: SyncHealth): SyncTier {
    if (!health.configured) return SyncTier.NOT_CONFIGURED
    if (health.push.consecutiveFailures >= SYNC_DEGRADED_THRESHOLD ||
        health.pull.consecutiveFailures >= SYNC_DEGRADED_THRESHOLD
    ) {
        return SyncTier.FAILING
    }
    if (mostRecentSuccessMillis(health) == null) return SyncTier.NEVER_SYNCED
    return SyncTier.CALM
}

/**
 * The later of push/pull's `lastSuccessAtMillis`, handling either or both
 * being null. Either half reaching the relay counts as "this device is in
 * contact with sync" -- mirrors the desktop's `_mostRecentSyncIso`
 * (products/retail/frontend/app-shell.js), which makes the same call for the
 * same reason: a device whose push has succeeded but whose pull keeps
 * failing (or vice versa) is still meaningfully in contact, not silent.
 */
fun mostRecentSuccessMillis(health: SyncHealth): Long? {
    val push = health.push.lastSuccessAtMillis
    val pull = health.pull.lastSuccessAtMillis
    return when {
        push == null -> pull
        pull == null -> push
        else -> maxOf(push, pull)
    }
}

/**
 * Renders an already-computed millisecond delta as the desktop's
 * `_formatRelativeTime` (products/retail/frontend/app-shell.js) renders it --
 * same boundaries, same rounding. Takes a delta rather than two timestamps so
 * tests are deterministic (no `System.currentTimeMillis()` inside the
 * function under test).
 *
 * Rounding matches JS `Math.round` (round-half-up, not banker's rounding),
 * which is why this uses [roundToLong] on a `Double` division rather than
 * integer division -- integer division would truncate instead of round and
 * quietly disagree with the desktop at every boundary.
 *
 * On the translation compromise: the desktop's own `t('s ago')` /
 * `t('m ago')` etc. concatenate a bare number with a unit word, so a
 * translated unit can't be wrapped around the number the way `tr(...)`
 * wraps a whole sentence elsewhere in this app -- the desktop documents this
 * as an accepted compromise (English unit suffixes even in Arabic) rather
 * than inventing a template-substitution scheme for one string. This
 * function makes the same call: it returns the bare English string, and the
 * screen passes it through as-is instead of routing it through `tr(...)`.
 */
fun formatRelative(deltaMillis: Long): String {
    // Seconds are computed BEFORE the "just now" comparison, not after, and
    // the comparison is against the ROUNDED seconds rather than the raw
    // millisecond delta. That ordering is the desktop's, and it is not
    // interchangeable with the obvious `deltaMillis < 5000` shortcut: at
    // 4500ms the desktop rounds to 5 and prints "5s ago", where a raw-millis
    // check prints "just now". A 500ms disagreement is invisible to anyone
    // watching a label that refreshes every 2s -- it is corrected here purely
    // so this function's promise of the desktop's exact boundaries is true,
    // since a comment claiming parity that the code does not keep is worse
    // than no comment at all.
    //
    // maxOf(0, ...) also absorbs a negative delta (a clock that stepped
    // backwards), which lands on 0 and therefore on "just now" -- never a
    // negative age. Same guard, same place, as the desktop's Math.max(0, ...).
    val seconds = maxOf(0L, (deltaMillis / 1000.0).roundToLong())
    if (seconds < 5) return "just now"
    if (seconds < 60) return "${seconds}s ago"

    val minutes = (seconds / 60.0).roundToLong()
    if (minutes < 60) return "${minutes}m ago"

    val hours = (minutes / 60.0).roundToLong()
    if (hours < 24) return "${hours}h ago"

    val days = (hours / 24.0).roundToLong()
    return "${days}d ago"
}
