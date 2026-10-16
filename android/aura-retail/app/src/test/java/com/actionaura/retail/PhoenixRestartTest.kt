package com.actionaura.retail

import android.content.Intent
import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * The pure rules behind the phoenix restart (emulator run 2026-10-16: the alarm relaunch was blocked and
 * the user landed on the home screen). The once-guard and the foreground/idle gate are pinned in
 * RestartGateTest; this pins the intent construction and the hand-over rules.
 *
 * MUTATION PROOFS (run for real): K1 - drop NEW_TASK from phoenixFlags turns "the phoenix intent carries
 * NEW_TASK" red; K2 - mayStart returns true regardless turns "never starts from the background" red;
 * K3 - launchNow never times out turns "gives up waiting" red.
 */
class PhoenixRestartTest {

    @Test
    fun `the phoenix intent carries NEW_TASK`() {
        assertThat(PhoenixRestart.phoenixFlags() and Intent.FLAG_ACTIVITY_NEW_TASK)
            .isEqualTo(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    @Test
    fun `the real launch intent gets a fresh task`() {
        val flags = PhoenixRestart.mainLaunchFlags()
        assertThat(flags and Intent.FLAG_ACTIVITY_NEW_TASK).isEqualTo(Intent.FLAG_ACTIVITY_NEW_TASK)
        assertThat(flags and Intent.FLAG_ACTIVITY_CLEAR_TASK).isEqualTo(Intent.FLAG_ACTIVITY_CLEAR_TASK)
    }

    @Test
    fun `a restart is never started from the background`() {
        assertThat(PhoenixRestart.mayStart(isForeground = false)).isFalse()
        assertThat(PhoenixRestart.mayStart(isForeground = true)).isTrue()
    }

    @Test
    fun `it waits while the old process is still listed and goes once it is gone`() {
        assertThat(PhoenixRestart.oldProcessGone(1234, listOf(1234, 99))).isFalse()
        assertThat(PhoenixRestart.oldProcessGone(1234, listOf(99))).isTrue()
        assertThat(PhoenixRestart.launchNow(gone = false, waitedMillis = 100)).isFalse()
        assertThat(PhoenixRestart.launchNow(gone = true, waitedMillis = 0)).isTrue()
    }

    @Test
    fun `it gives up waiting and launches anyway after the timeout`() {
        assertThat(PhoenixRestart.launchNow(gone = false, waitedMillis = PhoenixRestart.WAIT_FOR_EXIT_MILLIS)).isTrue()
    }

    @Test
    fun `the coordinator does not restart from the background and keeps asking`() {
        // The once-guard and gate wiring: a performRestart that refuses (not foreground) leaves the
        // request pending, so a later request tries again - nothing is lost.
        var foreground = false
        var restarts = 0
        val queue = ArrayDeque<Pair<Long, () -> Unit>>()
        val coordinator = RestartCoordinator(
            gate = RestartGate({ true }, { 0 }, { 0 }, { 0L }, 0L),
            post = { delay, block -> queue.addLast(delay to block) },
            performRestart = { if (PhoenixRestart.mayStart(foreground)) { restarts++; true } else false },
            log = { },
        )
        coordinator.request()
        while (queue.isNotEmpty()) queue.removeFirst().second()
        assertThat(restarts).isEqualTo(0)
        foreground = true
        coordinator.request()
        while (queue.isNotEmpty()) queue.removeFirst().second()
        assertThat(restarts).isEqualTo(1)
    }
}
