package com.actionaura.retail

import com.actionaura.retail.sync.BOOTSTRAP_NEEDS_SUPPORT_EN
import com.actionaura.retail.sync.bootstrapNeedsSupportText
import com.actionaura.retail.ui.i18n.arabicFor
import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * SYNC-BOOT Phase 3: restarting the app to install a staged copy must never cut a sale in half, never be
 * attempted from the background (Android 10+ would kill the process and refuse to start the activity), be
 * scheduled at most once, and survive a missing launch intent without losing anything.
 */
class RestartGateTest {
    private var now = 0L
    private var foreground = true
    private var cart = 0
    private var inFlight = 0
    private fun gate() = RestartGate({ foreground }, { cart }, { inFlight }, { now }, idleMillis = 60_000)

    @Test fun safeOnlyAfterAFullIdleMinuteInTheForeground() {
        val g = gate()
        assertThat(g.safeNow()).isFalse()                // first look starts the clock
        now += 59_999
        assertThat(g.safeNow()).isFalse()
        now += 1
        assertThat(g.safeNow()).isTrue()
    }

    @Test fun aNonEmptyCartMeansNeverSafe_andEmptyingItRestartsTheMinute() {
        // MUTATION: ignore cartLineCount -> red.
        val g = gate()
        cart = 2
        now += 10 * 60_000
        assertThat(g.safeNow()).isFalse()
        cart = 0
        assertThat(g.safeNow()).isFalse()                // idle only just began
        now += 60_000
        assertThat(g.safeNow()).isTrue()
    }

    @Test fun anInFlightBackendCallResetsTheIdleMinute() {
        // MUTATION: ignore inFlight -> red.
        val g = gate()
        g.safeNow(); now += 50_000
        inFlight = 1
        assertThat(g.safeNow()).isFalse()
        inFlight = 0
        now += 50_000
        assertThat(g.safeNow()).isFalse()                // the earlier 50 s no longer count: idle begins HERE
        now += 59_999
        assertThat(g.safeNow()).isFalse()
        now += 1
        assertThat(g.safeNow()).isTrue()
    }

    @Test fun neverSafeInTheBackground() {
        // MUTATION: ignore isForeground -> red.
        val g = gate()
        g.safeNow()
        now += 61_000                                    // idle long enough...
        foreground = false                               // ...but the app just went to the background
        assertThat(g.safeNow()).isFalse()
        now += 10 * 60_000
        assertThat(g.safeNow()).isFalse()
        foreground = true                                // back in front: the idle minute starts again
        assertThat(g.safeNow()).isFalse()
        now += 60_000
        assertThat(g.safeNow()).isTrue()
    }

    private class Harness {
        var now = 0L
        var cart = 0
        val queue = ArrayDeque<Pair<Long, () -> Unit>>()
        var restarts = 0
        var canRelaunch = true
        val logs = mutableListOf<String>()
        val coordinator = RestartCoordinator(
            gate = RestartGate({ true }, { cart }, { 0 }, { now }, 60_000),
            post = { delay, block -> queue.addLast(delay to block) },
            performRestart = { if (canRelaunch) { restarts++; true } else false },
            log = { logs += it },
            recheckMillis = 5_000)

        /** Runs the queued work, advancing the fake clock by each task's delay. */
        fun pump(untilMillis: Long) {
            while (queue.isNotEmpty() && now <= untilMillis) {
                val (delay, block) = queue.removeFirst()
                now += delay
                block()
            }
        }
    }

    @Test fun waitsForTheTillToBeIdleThenRestartsExactlyOnce() {
        val h = Harness()
        h.cart = 1
        h.coordinator.request()
        h.pump(120_000)
        assertThat(h.restarts).isEqualTo(0)              // busy: it keeps re-checking, never restarts
        h.cart = 0
        h.pump(300_000)
        assertThat(h.restarts).isEqualTo(1)
    }

    @Test fun askingManyTimesSchedulesOneRestart() {
        // MUTATION: drop the compareAndSet guard -> several restarts -> red.
        val h = Harness()
        repeat(5) { h.coordinator.request() }
        h.pump(10 * 60_000)
        assertThat(h.restarts).isEqualTo(1)
    }

    @Test fun noLaunchIntentIsLoggedNothingIsLostAndALaterRequestTriesAgain() {
        val h = Harness()
        h.canRelaunch = false
        h.coordinator.request()
        h.pump(10 * 60_000)
        assertThat(h.restarts).isEqualTo(0)
        assertThat(h.logs.single()).contains("no launch intent")
        h.canRelaunch = true                             // e.g. the package was fixed / the next sync tick asks again
        h.coordinator.request()
        h.pump(10 * 60_000)
        assertThat(h.restarts).isEqualTo(1)
    }

    @Test fun theNeedsSupportSentenceExistsInBothLanguages() {
        assertThat(bootstrapNeedsSupportText()).isEqualTo(BOOTSTRAP_NEEDS_SUPPORT_EN)      // English by default
        val arabic = arabicFor(BOOTSTRAP_NEEDS_SUPPORT_EN)
        assertThat(arabic).isNotNull()
        assertThat(arabic!!.any { it in '؀'..'ۿ' }).isTrue()                      // really Arabic script
        assertThat(arabic).isNotEqualTo(BOOTSTRAP_NEEDS_SUPPORT_EN)
    }
}
