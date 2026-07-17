package com.actionaura.clinic.server

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/** Phase 4K/4T regression guard -- see
 * com.actionaura.retail.server.ReadinessContractTest's identical docstring
 * for the full rationale (Phase 3.7's root cause was independently found
 * present in this app's own main.py and fixed in this same phase). */
class ReadinessContractTest {

    @Test
    fun main_py_readiness_check_targets_api_health_not_bare_root() {
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
        val url = ServerBootstrap.baseUrl()
        assertTrue(url.startsWith("http://127.0.0.1:"))
        assertFalse(url.contains("localhost"))
    }
}
