package com.actionaura.retail.sync

import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Why this file exists: the phone had a fully working sync engine
 * ([SyncCoordinator]) and no way for the person holding it to see whether
 * sync was actually working, while the desktop web app had a rich four-tier
 * sync banner (products/retail/frontend/app-shell.js). `SyncCoordinator
 * .health()`'s own doc comment said so plainly: "No UI consumes this yet ...
 * this exists so a failure is inspectable rather than invisible, and is the
 * seam any future UI would read." [SyncStatusPresentation.kt] and the
 * screen built on it (ui/screens/SyncStatusScreen.kt) are that reader.
 *
 * The load-bearing part of this file is [classifySync]'s TIER ORDER, not its
 * individual branches -- a fresh install that cannot reach the relay must be
 * told WHY (FAILING) rather than being left on "waiting for first sync"
 * forever, even though it has, technically, also never synced. Several tests
 * below exist specifically to pin that ordering rather than any single
 * outcome in isolation.
 */
class SyncStatusPresentationTest {

    // ── classifySync: tier precedence ─────────────────────────────────────

    @Test
    fun not_configured_wins_over_everything() {
        // Successes present on BOTH halves, still NOT_CONFIGURED -- an
        // unconfigured build has nothing meaningful to say about failures or
        // staleness, so this check must run before any of the others.
        val health = SyncHealth(
            configured = false,
            running = true,
            push = SyncHalfHealth(lastSuccessAtMillis = 1_000L),
            pull = SyncHalfHealth(lastSuccessAtMillis = 2_000L),
        )
        assertThat(classifySync(health)).isEqualTo(SyncTier.NOT_CONFIGURED)
    }

    @Test
    fun push_failures_at_threshold_are_failing() {
        val health = SyncHealth(
            configured = true,
            running = true,
            push = SyncHalfHealth(healthy = false, consecutiveFailures = SYNC_DEGRADED_THRESHOLD),
            pull = SyncHalfHealth(lastSuccessAtMillis = 5_000L),
        )
        assertThat(classifySync(health)).isEqualTo(SyncTier.FAILING)
    }

    @Test
    fun pull_failures_at_threshold_are_failing() {
        val health = SyncHealth(
            configured = true,
            running = true,
            push = SyncHalfHealth(lastSuccessAtMillis = 5_000L),
            pull = SyncHalfHealth(healthy = false, consecutiveFailures = SYNC_DEGRADED_THRESHOLD),
        )
        assertThat(classifySync(health)).isEqualTo(SyncTier.FAILING)
    }

    @Test
    fun below_threshold_is_not_failing() {
        // The allow-half: prove a single blip (or two, one short of the
        // threshold) does NOT alarm the user. A guard that only ever proves
        // the deny side is half-tested.
        val health = SyncHealth(
            configured = true,
            running = true,
            push = SyncHalfHealth(
                consecutiveFailures = SYNC_DEGRADED_THRESHOLD - 1,
                lastSuccessAtMillis = 1_000L,
            ),
            pull = SyncHalfHealth(),
        )
        assertThat(classifySync(health)).isEqualTo(SyncTier.CALM)
    }

    @Test
    fun a_fresh_install_that_cannot_reach_the_relay_says_why() {
        // The precedence rule under test: configured, no success has EVER
        // landed, and push has failed at/above the threshold. This must
        // classify FAILING, not NEVER_SYNCED -- classifySync() checks
        // failures before never-synced specifically so a device stuck behind
        // a broken relay address says why it hasn't synced instead of
        // reading as "give it a moment" forever.
        val health = SyncHealth(
            configured = true,
            running = true,
            push = SyncHalfHealth(
                healthy = false,
                consecutiveFailures = SYNC_DEGRADED_THRESHOLD,
                lastFailureReason = "CONNECTION_REFUSED",
            ),
            pull = SyncHalfHealth(),
        )
        assertThat(classifySync(health)).isEqualTo(SyncTier.FAILING)
        assertThat(classifySync(health)).isNotEqualTo(SyncTier.NEVER_SYNCED)
    }

    @Test
    fun configured_and_quiet_and_never_synced_is_never_synced() {
        val health = SyncHealth(
            configured = true,
            running = true,
            push = SyncHalfHealth(),
            pull = SyncHalfHealth(),
        )
        assertThat(classifySync(health)).isEqualTo(SyncTier.NEVER_SYNCED)
    }

    @Test
    fun a_success_on_either_half_is_calm() {
        val successOnlyOnPush = SyncHealth(
            configured = true,
            running = true,
            push = SyncHalfHealth(lastSuccessAtMillis = 500L),
            pull = SyncHalfHealth(),
        )
        assertThat(classifySync(successOnlyOnPush)).isEqualTo(SyncTier.CALM)

        val successOnlyOnPull = SyncHealth(
            configured = true,
            running = true,
            push = SyncHalfHealth(),
            pull = SyncHalfHealth(lastSuccessAtMillis = 500L),
        )
        assertThat(classifySync(successOnlyOnPull)).isEqualTo(SyncTier.CALM)
    }

    // ── mostRecentSuccessMillis ────────────────────────────────────────────

    @Test
    fun most_recent_success_picks_the_later_half() {
        val pullLater = SyncHealth(
            configured = true, running = true,
            push = SyncHalfHealth(lastSuccessAtMillis = 1_000L),
            pull = SyncHalfHealth(lastSuccessAtMillis = 2_000L),
        )
        assertThat(mostRecentSuccessMillis(pullLater)).isEqualTo(2_000L)

        val pushLater = SyncHealth(
            configured = true, running = true,
            push = SyncHalfHealth(lastSuccessAtMillis = 5_000L),
            pull = SyncHalfHealth(lastSuccessAtMillis = 2_000L),
        )
        assertThat(mostRecentSuccessMillis(pushLater)).isEqualTo(5_000L)

        val bothNull = SyncHealth(
            configured = true, running = true,
            push = SyncHalfHealth(), pull = SyncHalfHealth(),
        )
        assertThat(mostRecentSuccessMillis(bothNull)).isNull()

        val pushNull = SyncHealth(
            configured = true, running = true,
            push = SyncHalfHealth(),
            pull = SyncHalfHealth(lastSuccessAtMillis = 3_000L),
        )
        assertThat(mostRecentSuccessMillis(pushNull)).isEqualTo(3_000L)

        val pullNull = SyncHealth(
            configured = true, running = true,
            push = SyncHalfHealth(lastSuccessAtMillis = 4_000L),
            pull = SyncHalfHealth(),
        )
        assertThat(mostRecentSuccessMillis(pullNull)).isEqualTo(4_000L)
    }

    // ── formatRelative: matches the desktop's _formatRelativeTime exactly ──

    @Test
    fun relative_time_matches_the_desktop_wording() {
        assertThat(formatRelative(0)).isEqualTo("just now")
        assertThat(formatRelative(4_499)).isEqualTo("just now")
        // 4500ms is the boundary that separates "mirrors the desktop" from
        // "close enough". The desktop rounds to seconds FIRST and compares
        // the rounded value, so 4.5s becomes 5 and prints "5s ago"; a raw
        // `deltaMillis < 5000` check would print "just now" here. Pinned
        // because it is the only input that can tell the two implementations
        // apart, and an unpinned boundary is how parity quietly rots.
        assertThat(formatRelative(4_500)).isEqualTo("5s ago")
        assertThat(formatRelative(5_000)).isEqualTo("5s ago")
        assertThat(formatRelative(59_000)).isEqualTo("59s ago")
        assertThat(formatRelative(60_000)).isEqualTo("1m ago")
        assertThat(formatRelative(3_540_000)).isEqualTo("59m ago") // 59 minutes
        assertThat(formatRelative(3_600_000)).isEqualTo("1h ago")  // 60 minutes
        assertThat(formatRelative(82_800_000)).isEqualTo("23h ago") // 23 hours
        assertThat(formatRelative(86_400_000)).isEqualTo("1d ago")  // 24 hours
    }

    @Test
    fun negative_delta_is_just_now() {
        // A clock that stepped backwards (device clock adjustment, NTP
        // correction) must not print a negative age.
        assertThat(formatRelative(-1)).isEqualTo("just now")
        assertThat(formatRelative(-60_000)).isEqualTo("just now")
    }

    // ── Reachability: a screen that exists but is unreachable is the bug ───

    /**
     * This repo's recurring defect class is a complete backend with no
     * doorway to it (see EmployeesWiringContractTest,
     * EmployeeSalesWiringContractTest, OverflowNavigationContractTest for the
     * same shape applied elsewhere) -- SyncCoordinator.health() itself was
     * exactly that until this task. This test blocks the specific regression
     * of SyncStatusScreen shipping fully written and never navigated to:
     * registered in the nav graph AND reachable from the More screen, both
     * required, checked on CODE ONLY (via [codeOnly]) so a mention inside a
     * comment cannot satisfy either assertion.
     */
    @Test
    fun sync_status_is_registered_and_reachable() {
        val moduleRoot = File(".")
        val appRootFile = File(moduleRoot, "src/main/java/com/actionaura/retail/ui/AppRoot.kt")
        val extraScreensFile = File(moduleRoot, "src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
        assumeTrue("AppRoot.kt not reachable from this run context", appRootFile.exists())
        assumeTrue("RetailExtraScreens.kt not reachable from this run context", extraScreensFile.exists())

        val appRootCode = codeOnly(appRootFile.readText())
        assertThat(appRootCode).contains("""composable("sync_status")""")

        // Bounded to MoreScreen's own body -- mirrors OverflowNavigationContractTest's
        // moreScreenBody -- so this cannot be satisfied by some unrelated
        // onNavigate("sync_status") call elsewhere in this large file.
        val moreScreenBody = codeOnly(extraScreensFile.readText())
            .substringAfter("fun MoreScreen(")
            .substringBefore("private fun MoreItem(")
        assertThat(moreScreenBody).contains("""onNavigate("sync_status")""")
    }

    /**
     * Every navigable route needs a line in AppRoot's title map, or it opens
     * under the app's own name.
     *
     * Found by opening this screen on a real device: the bar said "Action
     * Aura", not "Sync status". The map's fallback is a NAME rather than a
     * blank, which is exactly what makes the omission invisible — the screen
     * looks finished, just anonymous, so nothing about it reads as a bug in a
     * screenshot or a code review.
     *
     * `backup` and `licensing` had been falling through the same way for
     * longer; all three are asserted here so the next route that forgets is
     * caught by the test that caught this one.
     */
    @Test
    fun every_pushed_route_has_its_own_title() {
        val appRootFile = File(File("."), "src/main/java/com/actionaura/retail/ui/AppRoot.kt")
        assumeTrue("AppRoot.kt not reachable from this run context", appRootFile.exists())
        val code = codeOnly(appRootFile.readText())

        for (route in listOf("sync_status", "backup", "licensing")) {
            assertThat(code).contains(""""$route" -> """")
        }
    }
}
