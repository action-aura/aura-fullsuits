package com.actionaura.retail

import com.google.common.truth.Truth.assertThat
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
