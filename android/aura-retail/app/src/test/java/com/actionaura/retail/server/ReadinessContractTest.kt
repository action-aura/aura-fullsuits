package com.actionaura.retail.server

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Phase 4K/4T regression guard: the embedded backend's readiness check must
 * poll GET /api/health, never the bare "/" path (the confirmed Phase 3.7
 * root cause -- see docs/corrections/launcher/root-cause-analysis.md --
 * that was independently found to be present in this Android project's own
 * main.py too, and fixed in this same phase). This is a source-content
 * guard, not a runtime test (no Chaquopy/device available in this test
 * environment) -- it fails loudly if wait_until_ready() ever regresses back
 * to polling "/".
 */
class ReadinessContractTest {

    @Test
    fun main_py_readiness_check_targets_api_health_not_bare_root() {
        // Gradle unit tests run with the module directory as the working
        // directory, so this resolves to app/src/main/python/main.py.
        val mainPy = File("src/main/python/main.py")
        assumeTrue("main.py not found at expected path in this run context", mainPy.exists())
        val text = mainPy.readText()
        assertTrue("main.py must poll /api/health", text.contains("/api/health"))
        assertFalse(
            "main.py must not poll the bare root path (the Phase 3.7/4K regression)",
            text.contains("f'http://{HOST}:{port}/'"),
        )
    }

    @Test
    fun baseUrl_format_is_explicit_loopback_never_a_hostname() {
        // ServerBootstrap.baseUrl() is built from a literal HOST constant
        // ("127.0.0.1") plus the resolved port -- never "localhost" (DNS
        // resolution ambiguity) and never a LAN-reachable address.
        val url = ServerBootstrap.baseUrl()
        assertTrue(url.startsWith("http://127.0.0.1:"))
        assertFalse(url.contains("localhost"))
    }
}
