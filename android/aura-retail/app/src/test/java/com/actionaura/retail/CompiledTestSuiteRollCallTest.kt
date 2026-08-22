package com.actionaura.retail

import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import org.junit.Ignore
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TestName
import java.io.File

/**
 * Takes a roll call: every test class this module's SOURCE TREE appears to
 * declare must actually exist on the test classpath, with the `@Test` methods
 * it appears to declare.
 *
 * ── The hole this closes ─────────────────────────────────────────────────────
 * A `.kt` file can stop being code without stopping being a file. Kotlin block
 * comments NEST, so a KDoc that quotes a route glob -- a slash immediately
 * followed by a star, in prose -- opens a SECOND comment level, and the
 * terminator below it closes only that one. TerminalIdentityContractTest.kt
 * shipped with two such openers and one terminator: the entire class, all
 * eleven assertions, was a single unterminated comment.
 *
 * That particular instance was loud, because it took the whole module's compile
 * down with it (261 errors, zero Android tests run). The quiet version is one
 * commented-out file in a module that otherwise builds: the file is present, it
 * is in git, it reads exactly like a test suite, `./gradlew test` says BUILD
 * SUCCESSFUL, and nothing anywhere reports that eleven assertions did not run.
 * The source-reading guards in this module were, at that moment, actively
 * WRONG about it -- their parser was not nest-aware, so it reported `class
 * TerminalIdentityContractTest` and `@Test` as live code the compiler had
 * thrown away. Green from a guard about a file that was never compiled.
 *
 * So the question this file asks is not "is the source well-formed" -- the
 * compiler owns that, and gets it right. It is: does the set of tests the
 * REPOSITORY appears to contain match the set the JVM is actually holding?
 *
 * ── Why the scan here is deliberately naive ──────────────────────────────────
 * It does NOT strip comments, and that is the entire point rather than an
 * oversight. A comment-aware scan of a fully-commented-out file finds nothing
 * to check and passes, which is precisely the state being detected: the guard
 * would go blind at exactly the moment it was needed. This reads the file the
 * way a person skimming the diff reads it -- a line starting in column zero
 * with `class Foo` declares a class; a line whose first non-blank token is
 * `@Test` declares a test -- and then asks the JVM whether it agrees.
 *
 * Raw strings ARE skipped, because a `""" ... """` fixture is data, not a
 * declaration, and several tests in this module legitimately quote Kotlin
 * source inside one. That rule is exact rather than approximate: Kotlin raw
 * strings do not nest and have no escapes.
 *
 * ── What it cannot do ────────────────────────────────────────────────────────
 * It cannot vouch for ITSELF. A wave that commented this file out would remove
 * the roll call along with everything else, and nothing inside one module can
 * close that. What it can do is make any OTHER file's disappearance loud, and
 * make its own disappearance visible as a drop in the reported test count.
 */
class CompiledTestSuiteRollCallTest {

    @get:Rule
    val runningTest = TestName()

    /** One top-level `class` declaration as the source tree presents it. */
    private data class DeclaredClass(
        val file: File,
        val fqcn: String,
        /** `@Test`-annotated function names, in source order. */
        val testMethods: List<String>,
    )

    private val testSourceRoot = File("src/test/java")

    private fun sourceFiles(): List<File> {
        // Deliberately NOT assumeTrue. Every other source-reading guard in this
        // module skips when its file is out of reach, which is right for them:
        // they are checking a detail of a file they can name. This one is
        // checking that the suite is the suite, and "the check did not run"
        // reading as a pass is the exact failure it exists to prevent. Gradle
        // runs unit tests with the module directory as the working directory
        // (see ReadinessContractTest); if this fails, run it that way.
        check(testSourceRoot.isDirectory) {
            "src/test/java not reachable from ${File(".").absolutePath} -- " +
                "run via ./gradlew :app:testDebugUnitTest"
        }
        return testSourceRoot.walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()
    }

    /**
     * [src] with every raw-string region blanked, newlines preserved so the
     * line-anchored patterns below still see the file's real line structure.
     */
    private fun withoutRawStrings(src: String): String {
        val quotes = "\"\"\""
        val out = StringBuilder(src.length)
        var i = 0
        while (i < src.length) {
            if (src.startsWith(quotes, i)) {
                val end = src.indexOf(quotes, i + quotes.length)
                val region = if (end < 0) src.substring(i) else src.substring(i, end + quotes.length)
                repeat(region.count { it == '\n' }) { out.append('\n') }
                i = if (end < 0) src.length else end + quotes.length
            } else {
                out.append(src[i++])
            }
        }
        return out.toString()
    }

    // Column zero only: a top-level declaration is never indented, and an
    // indented `class` is either nested or quoted.
    private val classDeclaration = Regex(
        """^(?:(?:internal|private|public|open|abstract|sealed|data|annotation|enum|value)\s+)*class\s+(\w+)"""
    )
    private val packageDeclaration = Regex("""^package\s+([A-Za-z0-9_.]+)""")
    // First non-blank token on the line, so `// @Test` in prose and ` * @Test`
    // in a KDoc are both excluded without any comment tracking.
    private val testAnnotation = Regex("""^\s*@Test\b""")
    // Backticked names included: SyncRelayClientTest and CanonicalDoubleFormatTest
    // both use them, and the JVM method name is the backticked text verbatim.
    private val functionName = """(?:`([^`]+)`|(\w+))\s*\("""
    private val functionDeclaration = Regex("""^\s*fun\s+$functionName""")
    private val annotatedOnOneLine = Regex("""^\s*@Test\b.*?\bfun\s+$functionName""")

    private fun declarationsIn(file: File): List<DeclaredClass> {
        var pkg = ""
        val order = mutableListOf<String>()
        val methods = mutableMapOf<String, MutableList<String>>()
        // A `@Test` before any top-level class declaration belongs to no class
        // this scan understands. It is recorded under "" so the roll call
        // REPORTS it (nothing named "" ever loads) rather than dropping it.
        var current = ""
        var expectingTestFunction = false

        fun record(match: MatchResult) {
            val name = match.groupValues[1].ifEmpty { match.groupValues[2] }
            methods.getOrPut(current) { mutableListOf<String>().also { order.add(current) } }.add(name)
            expectingTestFunction = false
        }

        for (line in withoutRawStrings(file.readText()).lineSequence()) {
            if (pkg.isEmpty()) packageDeclaration.find(line)?.let { pkg = it.groupValues[1] }

            val declaration = classDeclaration.find(line)
            if (declaration != null) {
                current = listOf(pkg, declaration.groupValues[1]).filter { it.isNotEmpty() }.joinToString(".")
                methods.getOrPut(current) { mutableListOf<String>().also { order.add(current) } }
                expectingTestFunction = false
                continue
            }

            val oneLiner = annotatedOnOneLine.find(line)
            if (oneLiner != null) {
                record(oneLiner)
                continue
            }
            if (testAnnotation.containsMatchIn(line)) {
                expectingTestFunction = true
                continue
            }
            if (expectingTestFunction) functionDeclaration.find(line)?.let { record(it) }
        }
        return order.map { DeclaredClass(file, it, methods.getValue(it)) }
    }

    private fun roster(): List<DeclaredClass> = sourceFiles().flatMap { declarationsIn(it) }

    private fun loadOrNull(fqcn: String): Class<*>? = try {
        // initialize = false: a roll call must not run anybody's static
        // initialisers as a side effect of counting them.
        Class.forName(fqcn, false, javaClass.classLoader)
    } catch (e: ClassNotFoundException) {
        null
    } catch (e: NoClassDefFoundError) {
        null
    }

    private fun junitTestMethods(loaded: Class<*>): Set<String> =
        loaded.declaredMethods.filter { it.isAnnotationPresent(Test::class.java) }.map { it.name }.toSet()

    // ── Where the guards in this module open the files they check ────────────

    /** One place a guard opens a path, as this module's source spells it. */
    private data class PathSite(
        val declaredIn: File,
        /** The construction verbatim, so a failure names the offending line rather than guessing. */
        val spelling: String,
        /** Directory the literal resolves against; null when the scan could not work it out. */
        val anchor: String?,
        val relative: String,
        /**
         * True when the anchor is a directory made while the tests RUN, so the
         * site is not a guard and its target is supposed not to exist yet.
         */
        val runtimeAnchored: Boolean,
    )

    /**
     * Every path a source-reading guard in this module opens, read out of the
     * guards themselves rather than listed here. A hand-maintained list is a
     * second place to update and goes stale pointing at files that still
     * exist, which is the failure mode of a guard that guards nothing.
     *
     * ── Why this is no longer a list of call shapes ──────────────────────────
     * It was, and the list was wrong about the two call sites that mattered
     * most. It knew three spellings; BuildGuardContractTest anchors BOTH of its
     * paths on a fourth, so both were invisible. A verifier staled one of them
     * and re-ran: all four of that class's tests went SKIPPED -- including the
     * one standing between this repository and an APK that enforces no
     * licensing at all -- while the guard below reported PASS over the other
     * thirty-eight literals. "All thirty-eight resolve" was true, and was not
     * the claim anybody needed.
     *
     * An enumeration of spellings cannot be trusted to be complete, because
     * being incomplete looks exactly like working. So the anchors are DERIVED:
     *
     *   - a two-argument construction resolves against whatever its receiver
     *     resolves to, and a receiver that is a plain name is looked up among
     *     the file's own root declarations -- a new root, whatever it is
     *     called, needs no change here;
     *   - a one-argument helper that takes the path as a `String` and hands it
     *     straight to a rooted construction is recognised by that SHAPE, not by
     *     its name, so renaming the helper does not blind the scan.
     *
     * Where an anchor still cannot be worked out the site is kept and reported
     * (see [PathSite.anchor] and the two completeness tests below) instead of
     * being dropped. Silence was the whole defect; a site the scan does not
     * understand is now a red test naming the line.
     *
     * ── Why this prose names the shapes instead of writing them ──────────────
     * Spelling one out here puts the exact text this scan looks FOR into a file
     * this scan reads. The roll call is deliberately comment-naive (see the
     * class docstring: a comment-aware scan of a commented-out file finds
     * nothing and passes), so prose is source to it. Writing the shapes out
     * cost this test its first run: three lines of the previous version of this
     * very comment were collected as call sites and reported as missing files.
     * Same trap as the route glob one file over, from the other end -- there a
     * comment was read as code by the compiler, here by the scanner.
     */
    private fun pathSites(): List<PathSite> = sourceFiles().flatMap { file ->
        // Raw strings blanked for the same reason declarationsIn() does it:
        // several tests quote Kotlin source inside a fixture, and every regex
        // below lives in one. A fixture is data, not a call site.
        val text = withoutRawStrings(file.readText())
        val roots = rootDeclaration.findAll(text).associate { it.groupValues[1] to it.groupValues[2] }
        val initialisers = valInitialiser.findAll(text).associate { it.groupValues[1] to it.groupValues[2].trim() }
        val sites = mutableListOf<PathSite>()

        anchoredHelper.findAll(text).forEach { helper ->
            val anchor = roots[helper.groupValues[3]]
            Regex("""\b${helper.groupValues[1]}\(\s*"([^"]+)"\s*\)""").findAll(text).forEach { call ->
                sites.add(PathSite(file, call.value, anchor, call.groupValues[1], false))
            }
        }

        fileConstructions(text).forEach { (spelling, args) ->
            when (args.size) {
                1 -> literalOrNull(args[0])?.let { relative ->
                    // A root declaration names a directory everything else is
                    // resolved against, not a file anybody opens. Recognised by
                    // the path navigating only upward or nowhere, so a root
                    // spelled some new way is covered without being listed.
                    if (!navigatesOnly(relative)) sites.add(PathSite(file, spelling, ".", relative, false))
                }
                2 -> literalOrNull(args[1])?.let { relative ->
                    val anchor = anchorOf(args[0], roots)
                    val runtime = anchor == null &&
                        initialisers[baseNameOf(args[0])] in runtimeDirectories
                    sites.add(PathSite(file, spelling, anchor, relative, runtime))
                }
            }
        }
        sites
    }

    /** Sites whose anchor the scan worked out, paired with the file they name. */
    private fun checkedPaths(): List<Pair<PathSite, File>> =
        pathSites().mapNotNull { site -> site.anchor?.let { site to File(File(it), site.relative) } }

    private val rootDeclaration =
        Regex("""\bval\s+(\w+)\s*=\s*(?:java\.io\.)?File\(\s*"([^"]+)"\s*\)""")
    private val valInitialiser = Regex("""\bval\s+(\w+)\s*=\s*([^\r\n]+)""")
    private val anchoredHelper =
        Regex("""\bfun\s+(\w+)\s*\(\s*(\w+)\s*:\s*String\s*\)[\s\S]{0,200}?File\(\s*(\w+)\s*,\s*\2\s*\)""")
    private val plainName = Regex("""\w+""")
    private val rootedCall = Regex("""File\(\s*"([^"]+)"\s*\)""")
    private val nestedCall = Regex("""File\(\s*(.+),\s*"([^"]+)"\s*\)""")

    /**
     * Receiver initialisers that produce a directory created while the tests
     * RUN -- a JUnit temporary folder, not a place in this repository. Sites
     * anchored on one open a file that is SUPPOSED not to exist yet, so
     * checking them for existence would fail the build over correct code.
     *
     * Keyed on the initialiser text rather than the variable name so that some
     * future local that happens to share a name does not inherit the exemption,
     * and so that adding an entry is a decision somebody has to write down.
     */
    private val runtimeDirectories = setOf("tempFolder.newFolder()")

    /** [arg] if it is one plain string literal, else null. */
    private fun literalOrNull(arg: String): String? {
        val trimmed = arg.trim()
        if (trimmed.length < 2 || !trimmed.startsWith('"') || !trimmed.endsWith('"')) return null
        val inner = trimmed.substring(1, trimmed.length - 1)
        return if (inner.contains('"')) null else inner
    }

    private fun navigatesOnly(relative: String): Boolean =
        relative.split('/', '\\').all { it == "." || it == ".." }

    /** The directory [receiver] denotes, resolved through roots and nesting; null if unknown. */
    private fun anchorOf(receiver: String, roots: Map<String, String>): String? {
        val expression = receiver.trim().removePrefix("java.io.")
        literalOrNull(expression)?.let { return it }
        rootedCall.matchEntire(expression)?.let { return it.groupValues[1] }
        nestedCall.matchEntire(expression)?.let { nested ->
            val outer = anchorOf(nested.groupValues[1], roots) ?: return null
            return "$outer/${nested.groupValues[2]}"
        }
        return if (plainName.matches(expression)) roots[expression] else null
    }

    /** The name at the bottom of a receiver chain, so an unresolved site can still be explained. */
    private fun baseNameOf(receiver: String): String? {
        val expression = receiver.trim().removePrefix("java.io.")
        nestedCall.matchEntire(expression)?.let { return baseNameOf(it.groupValues[1]) }
        return if (plainName.matches(expression)) expression else null
    }

    /**
     * Every `File` construction in [text] with its argument list split at
     * top-level commas -- shape-agnostic on purpose, so a spelling nobody
     * anticipated is still SEEN here and can be reported as not understood
     * rather than never noticed at all.
     */
    private fun fileConstructions(text: String): List<Pair<String, List<String>>> {
        val out = mutableListOf<Pair<String, List<String>>>()
        val token = "File("
        var at = text.indexOf(token)
        while (at >= 0) {
            val before = if (at == 0) ' ' else text[at - 1]
            // A qualified `java.io.` prefix reads as `.`; anything alphanumeric
            // means this is the tail of some other identifier entirely.
            if (!before.isLetterOrDigit() && before != '_') {
                val args = mutableListOf<String>()
                val arg = StringBuilder()
                var depth = 0
                var inString = false
                var end = -1
                var i = at + token.length - 1
                while (i < text.length) {
                    val c = text[i]
                    if (inString) {
                        arg.append(c)
                        if (c == '\\' && i + 1 < text.length) {
                            arg.append(text[i + 1]); i += 2; continue
                        }
                        if (c == '"') inString = false
                        i++
                        continue
                    }
                    when (c) {
                        '"' -> { inString = true; arg.append(c) }
                        '(' -> { depth++; if (depth > 1) arg.append(c) }
                        ')' -> { depth--; if (depth == 0) end = i else arg.append(c) }
                        ',' -> if (depth == 1) { args.add(arg.toString()); arg.setLength(0) } else arg.append(c)
                        else -> arg.append(c)
                    }
                    if (end >= 0) break
                    i++
                }
                if (end > 0) {
                    args.add(arg.toString())
                    out.add(text.substring(at, end + 1) to args.map { it.trim() })
                }
            }
            at = text.indexOf(token, at + token.length)
        }
        return out
    }

    // ── The roll call ────────────────────────────────────────────────────────

    @Test
    fun every_class_the_test_sources_declare_is_actually_on_the_test_classpath() {
        val missing = roster()
            .filter { loadOrNull(it.fqcn) == null }
            .map { "${it.fqcn}  (declared in ${it.file.path})" }

        // The whole finding in one assertion. A file that has become a comment
        // still declares its class to any reader and to git; it declares
        // nothing to the JVM.
        assertThat(missing).isEmpty()
    }

    @Test
    fun every_test_method_the_test_sources_declare_is_a_real_junit_test_on_that_class() {
        val absent = mutableListOf<String>()
        for (declared in roster()) {
            val loaded = loadOrNull(declared.fqcn) ?: continue // reported by the assertion above
            val compiled = junitTestMethods(loaded)
            declared.testMethods.filterNot { it in compiled }
                .forEach { absent.add("${declared.fqcn}#$it  (declared in ${declared.file.path})") }
        }

        // Catches the finer-grained version of the same thing: not the whole
        // file, just some of its assertions, disappearing into a comment while
        // the class around them keeps reporting green.
        assertThat(absent).isEmpty()
    }

    @Test
    fun a_class_named_like_a_test_that_holds_no_runnable_test_is_reported() {
        val empty = roster()
            .filter { it.fqcn.substringAfterLast('.').endsWith("Test") }
            .filter { declared ->
                val loaded = loadOrNull(declared.fqcn) ?: return@filter false
                junitTestMethods(loaded).isEmpty()
            }
            .map { "${it.fqcn}  (declared in ${it.file.path})" }

        // A `*Test` class the runner loads and then runs nothing from is the
        // same silence by another route -- every method commented out, or the
        // JUnit import lost -- and it is likewise reported by nobody.
        assertThat(empty).isEmpty()
    }

    // ── The other two ways a test stops running without failing ──────────────

    @Test
    fun no_source_reading_guard_in_this_module_is_quietly_skipping_its_file() {
        // A file that became a comment and a file that cannot be found are the
        // same silence by two routes. Nineteen `assumeTrue("… not reachable
        // from this run context", file.exists())` calls across nine files in
        // this module turn "I could not open the thing I check" into a SKIP,
        // and Gradle reports a skipped test as BUILD SUCCESSFUL. That is the
        // right call individually -- those guards read Python and JavaScript
        // from sibling products and must not fail a build run from a context
        // that has no repository around it -- but nothing was asking whether
        // they were all skipping AT ONCE, which is what a moved file, a renamed
        // directory or a changed working directory produces.
        //
        // It is not hypothetical here: those paths point across ownership
        // boundaries (commercial_runtime/, products/retail/, owner/) at files
        // other people are editing on other branches. This test is the one
        // place that says so out loud instead of going quiet.
        val checked = checkedPaths()
        val unreachable = checked
            .filterNot { (_, target) -> target.exists() }
            .map { (site, _) -> "${site.relative}  (read by ${site.declaredIn.name})" }

        assertThat(unreachable).isEmpty()

        // Guards the guard. If the scan stopped matching -- a guard switched to
        // holding its path in a constant, a helper renamed -- the assertion
        // above would inspect an empty list and pass while every one of those
        // assumptions skipped. Forty-one sites resolve today: the thirty-eight
        // the shape list could see, plus the three it could not -- both of
        // BuildGuardContractTest's and one of TerminalIdentityContractTest's.
        assertThat(checked.size).isAtLeast(35)
    }

    @Test
    fun the_path_scan_understands_every_way_this_module_spells_a_file_it_opens() {
        // Guards the guard the guard could not guard. The scan used to be a
        // list of three call shapes, and a list of shapes stays complete right
        // up until somebody writes a fourth -- at which point it reports
        // nothing, which is indistinguishable from having nothing to report.
        // BuildGuardContractTest anchored both of its paths the fourth way, so
        // its four tests could all go SKIPPED while the test above reported
        // PASS over the thirty-eight literals it COULD see.
        //
        // Every construction the scan cannot anchor is listed here verbatim, so
        // the next unfamiliar spelling is a red test naming the line rather
        // than a hole nobody is told about.
        val sites = pathSites()

        // Guards this guard in turn. "Nothing the scan failed to understand" is
        // also what a scan that read nothing at all reports, and that is the
        // shape of every bug on this page. Forty-seven sites are seen today
        // (forty-one anchored, six on temporary folders).
        assertThat(sites.size).isAtLeast(35)

        val unknown = sites
            .filter { it.anchor == null && !it.runtimeAnchored }
            .map { "${it.spelling}  (in ${it.declaredIn.name})" }
            .sorted()

        assertThat(unknown).isEmpty()
    }

    @Test
    fun every_guard_that_can_skip_itself_opens_a_path_this_scan_can_see() {
        // The same completeness question from the other end, and the one that
        // would have caught the miss: a file able to turn itself into a SKIP
        // must expose at least one path the test above is watching. This is
        // what goes red if a guard starts keeping its path somewhere the scan
        // has no idea about -- a constant, a companion object, a helper of a
        // shape not derived above -- instead of that guard simply going quiet.
        //
        // Comment- and fixture-aware, unlike everything else in this file,
        // because here the question really is "does this file CALL it": a file
        // that has become a comment is the roll call's business above, and this
        // one must not be fooled by prose or by a quoted Kotlin fixture.
        //
        // The needle is assembled rather than written for the reason the
        // pathSites() docstring gives: this file is read by its own scan, and a
        // needle spelled out here is a needle that matches this file.
        //
        // What this still cannot see: a file that opens TWO paths, one the scan
        // understands and one it does not, keeps a foot in the watched set and
        // passes. Nothing inside a JVM test can close that last gap, because
        // the honest question is about the RUN and a test cannot read the report
        // of the run it belongs to. The build asks it instead --
        // `assertNoSkippedTestDebugUnitTest` in app/build.gradle parses the
        // JUnit XML afterwards and fails on any skip at all, whatever caused it.
        val needle = "assume" + "True("
        val skippable = sourceFiles()
            .filter { codeOnly(withoutRawStrings(it.readText())).contains(needle) }
        // Nine files today. A needle that stopped matching would empty the list
        // and let the assertion below pass while proving nothing.
        assertThat(skippable.size).isAtLeast(8)

        val watched = checkedPaths().map { (site, _) -> site.declaredIn }.toSet()
        val blind = skippable.filterNot { it in watched }.map { it.path }.sorted()

        assertThat(blind).isEmpty()
    }

    @Test
    fun nothing_in_this_module_is_annotated_out_of_the_run() {
        // The third route to the same silence, and the only one that survives
        // both checks above: `@Ignore` leaves the class compiled, on the
        // classpath, and carrying real `@Test` methods, so the roll call sees
        // everything it expects and the runner executes none of it.
        //
        // Zero instances today. Pinned anyway, for the reason KotlinSourceText
        // records about the mirror-image comment bug it had no instance of
        // either: this module has already spent one compile and eleven
        // never-executed assertions discovering that "no example of that shape
        // exists right now" is luck, not a property. Asked of the LOADED class
        // rather than the source, because the annotation is what the runner
        // obeys.
        val ignored = mutableListOf<String>()
        for (declared in roster()) {
            val loaded = loadOrNull(declared.fqcn) ?: continue
            if (loaded.isAnnotationPresent(Ignore::class.java)) {
                ignored.add("${declared.fqcn}  (whole class, declared in ${declared.file.path})")
            }
            loaded.declaredMethods
                .filter { it.isAnnotationPresent(Ignore::class.java) }
                .forEach { ignored.add("${declared.fqcn}#${it.name}  (declared in ${declared.file.path})") }
        }

        assertThat(ignored).isEmpty()
    }

    @Test
    fun the_roll_call_reads_the_whole_tree_and_can_see_the_method_it_is_running_from() {
        val roster = roster()

        // Guards the guard, three ways. A roster that quietly emptied itself --
        // wrong working directory, a regex that stopped matching, a scan that
        // learned to skip comments and therefore skipped the very file it is
        // here for -- would let all three assertions above pass while
        // inspecting nothing.
        assertThat(roster.map { it.file }.toSet().size).isAtLeast(20)
        assertThat(roster.sumOf { it.testMethods.size }).isAtLeast(200)

        // ...and the roster is describing the code that actually executes, not
        // a parallel universe of text: the method running RIGHT NOW has to
        // appear in it, found by reading this file off disk.
        val self = roster.single { it.fqcn == javaClass.name }
        assertThat(self.testMethods).contains(runningTest.methodName)
    }
}
