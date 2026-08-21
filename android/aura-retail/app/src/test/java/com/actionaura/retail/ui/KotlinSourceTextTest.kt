package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The parser under every source-reading contract test in this module is itself
 * a guard, and until now nothing guarded it.
 *
 * (Like [codeOnly]'s own docstring, this one never spells the block-comment
 * delimiters literally -- OPENER and TERMINATOR stand for them -- because
 * Kotlin block comments nest and a literal opener in here starts a second one.
 * The tests below assemble them from string parts for the same reason.)
 *
 * [codeOnly] strips comments so an assertion about CODE cannot be satisfied --
 * or defeated -- by prose. Its docstring claimed the naivety was safe because
 * "a false strip can only ever make an assertion HARDER to satisfy, never
 * easier". That claim has now been false twice, in both directions.
 *
 * ── One: a phantom block, opened from inside a line comment ──────────────────
 * Stripping BLOCK comments before LINE comments let an OPENER that only ever
 * appeared inside a `//` comment open a phantom block running to the next
 * TERMINATOR anywhere in the file -- deleting real code, which is the EASIER
 * direction. An assertion of the form "every tr() key on this screen has an
 * Arabic entry" then silently stops inspecting whatever fell inside it.
 *
 * Measured on the file it actually happened to: AppRoot.kt:90 is a `//` comment
 * naming the route prefix `/_internal/sync/` followed by a glob star -- slash
 * and star, adjacent, an OPENER -- and the old ordering ran the block from
 * there to the KDoc terminator on line 198. 5,949 characters and 108 lines of
 * live code deleted, including the `tr("Starting…")` call site on line 188 that
 * EmployeeSalesWiringContractTest's
 * `the_screens_this_change_touches_have_no_untranslated_strings` therefore
 * never once inspected. A second instance of the same shape sits at
 * Models.kt:576 and is harmless only by luck: no TERMINATOR happens to follow.
 *
 * Reversing the two operations removes THAT blind spot and introduces its
 * mirror image: line-stripping first can eat the TERMINATOR that closes a real
 * one-line block comment containing a `//`, after which the block runs on and
 * deletes code again -- same easier direction, other side. So [codeOnly] is one
 * left-to-right pass instead: whichever delimiter appears FIRST owns the
 * region, and the other is inert until it closes. That is what the Kotlin
 * lexer does.
 *
 * ── Two: block comments NEST, and this one did not ───────────────────────────
 * The scan still stopped a block comment at its FIRST terminator. Kotlin's
 * compiler does not: a nested OPENER pushes a level, and it takes one
 * TERMINATOR per level to get back out. So for any file whose prose contains an
 * OPENER, [codeOnly] ended the comment EARLIER than the compiler did and handed
 * back, as live code, source the compiler had never seen.
 *
 * TerminalIdentityContractTest.kt was that file. Its KDoc quoted the route glob
 * `/api/devices` plus a star twice and carried one terminator, so to the
 * compiler the entire class -- eleven assertions -- was one unterminated
 * comment and the module failed to build. To the non-nesting [codeOnly] the
 * same file read as ordinary source: `class TerminalIdentityContractTest`,
 * `@Test`, all of it. A structural guard reading that file would have gone on
 * passing while what it guards was not compiled at all.
 *
 * Across all 70 `.kt` files in this module the nesting and non-nesting readings
 * differ on exactly the 3 files that wave touched -- so matching the compiler
 * costs nothing today, and approximating it has now cost a build.
 *
 * The parser stays deliberately naive about STRING LITERALS (a `//` inside a
 * URL still truncates the line). That naivety keeps its documented character:
 * it can only retain or drop text uniformly for both the code and the prose
 * being compared, never open an unbounded region.
 */
class KotlinSourceTextTest {

    // Assembled from parts so this test file cannot open a phantom block in
    // ITSELF while describing one.
    private val open = "/" + "*"
    private val close = "*" + "/"

    @Test
    fun a_block_opener_inside_a_line_comment_does_not_swallow_the_code_after_it() {
        // The AppRoot.kt regression, minimised: a `//` comment that happens to
        // contain `/*`, followed by real code, followed by a genuine block
        // comment whose terminator the phantom latches onto.
        val src = """
            val port = 8000 // serves $open routes) from /_internal/sync
            val keep = tr("Starting…")
            $open* a real KDoc that must still be stripped $close
            val alsoKeep = 42
        """.trimIndent()

        val code = codeOnly(src)

        assertThat(code).contains("""val keep = tr("Starting…")""")
        assertThat(code).contains("val alsoKeep = 42")
        // ...and both comment forms are still genuinely stripped, so the fix is
        // not simply "stop stripping".
        assertThat(code).doesNotContain("_internal")
        assertThat(code).doesNotContain("a real KDoc")
    }

    @Test
    fun a_tr_call_inside_a_phantom_block_is_still_seen_by_the_key_scanner() {
        // The consequence, not just the mechanism: trKeys() runs on codeOnly()
        // output, so anything a phantom ate is a call site the untranslated-
        // strings guard reports as green without ever having looked at it.
        val src = """
            val a = 1 // matches $open.kt under build/
            val b = tr("Not attributed")
            $open* doc $close
        """.trimIndent()

        assertThat(trKeys(codeOnly(src))).contains("Not attributed")
    }

    @Test
    fun a_line_comment_inside_a_block_comment_does_not_swallow_the_code_after_it() {
        // The MIRROR failure -- the one a bare swap of the two operations would
        // ship. Stripping `//` first eats the `*/` that closes line 1's block
        // comment, so the block runs on to the KDoc terminator further down and
        // takes `val x` and `val y` with it. This test is the reason the fix is
        // a single scan rather than a reordering.
        val src = """
            $open block with a // aside inside it $close
            val x = 1 // trailing
            $open*
             * KDoc
             $close
            val y = 2
        """.trimIndent()

        val code = codeOnly(src)
        assertThat(code).contains("val x = 1")
        assertThat(code).contains("val y = 2")
        assertThat(code).doesNotContain("block with")
        assertThat(code).doesNotContain("aside")
        assertThat(code).doesNotContain("trailing")
        assertThat(code).doesNotContain("KDoc")
    }

    @Test
    fun neither_delimiter_can_reopen_a_comment_the_other_one_already_closed() {
        // Stated as the invariant rather than as two examples: whichever
        // delimiter comes first owns the region, and the other is inert inside
        // it. Both orderings above are the same rule read from opposite ends.
        assertThat(codeOnly("val a = 1 // $open $close $open")).isEqualTo("val a = 1 ")
        assertThat(codeOnly("$open // $close val b = 2")).isEqualTo(" val b = 2")
    }

    @Test
    fun an_unterminated_block_comment_ends_at_end_of_file_rather_than_wrapping() {
        // A file whose last block comment is never closed used to leave the
        // regex unmatched, so the WHOLE tail survived as pseudo-code. Dropping
        // it is the harder direction (fewer keys found, assertions get
        // stricter) and is what the compiler does too.
        assertThat(codeOnly("val a = 1 $open never closed")).isEqualTo("val a = 1 ")
    }

    // ── Nesting: the compiler's rule, not the regex's ────────────────────────

    @Test
    fun a_nested_block_comment_does_not_end_the_outer_one_early() {
        // Kotlin block comments NEST. A KDoc that quotes an opener in prose --
        // a route glob, a code sample -- is at level two, and the terminator
        // that follows brings it back to level ONE, not to zero. Stopping at
        // the first terminator (what this parser used to do) therefore ends the
        // comment early and hands the remainder back as live code.
        val src = """
            $open*
             * A KDoc that quotes an opener in prose: $open
             * and then closes that nested one $close
             * but is STILL inside its own comment: class Ghost
             $close
            class Live
        """.trimIndent()

        val code = codeOnly(src)

        assertThat(code).contains("class Live")
        // The whole point. A non-nesting scan reports `class Ghost` here, which
        // is a declaration the Kotlin compiler never sees.
        assertThat(code).doesNotContain("class Ghost")
        assertThat(code).doesNotContain("STILL inside")
    }

    @Test
    fun a_test_class_swallowed_by_an_unbalanced_nested_comment_is_not_reported_as_code() {
        // TerminalIdentityContractTest.kt, minimised to the shape it shipped
        // in: a KDoc quoting the route glob twice (two openers, level three)
        // and carrying one terminator (level two). The comment never closes, so
        // to the compiler the whole class is comment -- it reported "Unclosed
        // comment" and the module's 261 compile errors meant ZERO Android tests
        // ran, this one included.
        //
        // The non-nesting parser read the same bytes as ordinary source and
        // would have reported the class and its `@Test` methods as live. That
        // is the failure mode worth pinning: a structural guard staying green
        // about a file the compiler threw away.
        val src = """
            package com.actionaura.retail.ui

            $open*
             * Prose naming /api/devices$open and again /api/devices$open
             $close
            class TerminalIdentityLookalike {
                @Test
                fun an_assertion_that_must_not_read_as_live_code() {}
            }
        """.trimIndent()

        val code = codeOnly(src)

        // The package line precedes the comment and survives either way, so its
        // presence proves the parser ran rather than returned empty.
        assertThat(code).contains("package com.actionaura.retail.ui")
        assertThat(code).doesNotContain("class TerminalIdentityLookalike")
        assertThat(code).doesNotContain("@Test")
        assertThat(code).doesNotContain("an_assertion_that_must_not_read_as_live_code")
    }

    @Test
    fun a_nested_comment_that_does_balance_closes_exactly_where_the_compiler_says() {
        // The other direction, so the fix is not "treat every nested opener as
        // unterminated": levels that balance DO close, and the code after the
        // final terminator is live. This is the shape KotlinSourceText.kt's own
        // KDoc had before the delimiters were spelled out of it -- balanced,
        // and closing one terminator later than the non-nesting reading claimed.
        val code = codeOnly("$open a $open b $close c $close val d = 4")
        assertThat(code).isEqualTo(" val d = 4")
    }

    @Test
    fun the_live_appRoot_call_site_the_phantom_hid_is_visible_again() {
        // The real file, not a fixture. AppRoot.kt's loading screen calls
        // tr("Starting…") at line 188, INSIDE the span the phantom block used
        // to swallow. If this key is not visible to the scanner, the Arabic
        // coverage guard is not inspecting the file it claims to.
        val appRoot = File("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
        assumeTrue("AppRoot.kt not reachable from this run context", appRoot.exists())
        assertThat(trKeys(codeOnly(appRoot.readText()))).contains("Starting…")
    }
}
