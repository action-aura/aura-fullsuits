package com.actionaura.retail.printer

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Pure JVM tests (no device, no emulator, no real printer) for
 * [NetworkPrinterAdapter] -- modeled on net/SaleContractTest.kt's style.
 *
 * THIS is the actual point of choosing a plain TCP/network transport over
 * Bluetooth or a vendor SDK for receipt printing (see
 * [NetworkPrinterAdapter]'s own doc comment): a raw socket can be verified
 * end to end against a real `java.net.ServerSocket` on localhost, with no
 * hardware and no mock standing in for the actual transport code.
 */
class NetworkPrinterAdapterTest {

    // ── Test 1: happy path, proven end to end against a real listener ──────

    @Test
    fun print_sends_the_exact_bytes_to_a_listening_server() {
        val server = ServerSocket(0)
        val received = ByteArrayOutputStream()
        val acceptedConnection = AtomicBoolean(false)
        val serverDone = CountDownLatch(1)

        val serverThread = Thread {
            server.accept().use { conn ->
                acceptedConnection.set(true)
                conn.getInputStream().copyTo(received)
            }
            serverDone.countDown()
        }
        serverThread.isDaemon = true
        serverThread.start()

        val bytes = "Aura Retail\nSALE-000001\nTotal 12.340 JOD\n".toByteArray(Charsets.US_ASCII)
        val adapter = NetworkPrinterAdapter("127.0.0.1", server.localPort)

        val result = runBlocking { adapter.print(bytes) }

        assertTrue(
            "server thread never finished reading -- adapter likely hung or never closed its socket",
            serverDone.await(5, TimeUnit.SECONDS),
        )
        server.close()

        assertTrue("print() must succeed against a real listening server: ${result.exceptionOrNull()}", result.isSuccess)
        // ANTI-VACUITY: a broken adapter that never actually opened a
        // connection would leave `received` empty and these two assertions
        // failing, not passing -- this is what distinguishes "genuinely
        // sent the bytes" from "silently did nothing", which is the entire
        // reason this is a real socket test rather than a mock of one.
        assertTrue("server never accepted a connection", acceptedConnection.get())
        assertTrue("server received zero bytes", received.size() > 0)
        assertArrayEquals(bytes, received.toByteArray())
    }

    // ── Test 2: connection refused -- nothing listening on the port ────────

    @Test
    fun print_returns_failure_naming_host_and_port_when_connection_is_refused() {
        // Open a server socket only to learn a free port, then close it
        // immediately -- the port is then guaranteed unused, so the
        // connection that follows is a real "nothing is listening" refusal
        // rather than a guess at an arbitrary port number.
        val port = ServerSocket(0).use { it.localPort }
        val adapter = NetworkPrinterAdapter("127.0.0.1", port)

        val result = runBlocking { adapter.print(byteArrayOf(1, 2, 3)) }

        assertTrue("a refused connection must be reported as failure, not thrown", result.isFailure)
        val error = result.exceptionOrNull()
        assertTrue("must fail with a PrinterError, got $error", error is PrinterError)
        val message = error?.message ?: ""
        assertTrue("error message must name the host ('$message')", message.contains("127.0.0.1"))
        assertTrue("error message must name the port ('$message')", message.contains(port.toString()))
    }

    // ── Test 3: an unreachable host must fail, never hang forever ──────────

    @Test(timeout = 8000)
    fun print_fails_rather_than_hanging_when_the_host_is_unreachable() {
        // 10.255.255.1 is a private, non-routable address commonly used in
        // JVM networking tests to force a genuine connect timeout: this
        // dev machine (and most CI networks) has no route that actually
        // reaches it, so the TCP handshake never completes and
        // NetworkPrinterAdapter's own CONNECT_TIMEOUT_MS (5s) is what ends
        // the attempt -- not an immediate "connection refused" the way a
        // closed local port produces.
        //
        // The determinism guarantee here is the JUnit `timeout = 8000`
        // above, not raw speed: this test always completes within 8s
        // (comfortably more than the adapter's own 5s budget) and fails
        // outright -- loudly, not just slowly -- if the adapter ever hangs
        // past its own timeout. That is the exact defect this test exists
        // to catch, whether the specific failure reported is a timeout or
        // an immediate routing error.
        val adapter = NetworkPrinterAdapter("10.255.255.1", 9100)
        val result = runBlocking { adapter.print(byteArrayOf(1)) }
        assertTrue("an unreachable host must fail, never succeed", result.isFailure)
    }

    // ── Test 4: empty payload -- deliberate, documented behaviour ──────────

    @Test
    fun print_with_an_empty_byte_array_succeeds_and_sends_nothing() {
        // Deliberate choice (see NetworkPrinterAdapter's doc comment): this
        // adapter transports bytes, it does not validate receipt content,
        // so connecting and writing zero bytes is not an error -- it
        // succeeds the same way printing any other payload would.
        val server = ServerSocket(0)
        val receivedCount = AtomicInteger(-1)
        val serverDone = CountDownLatch(1)
        val serverThread = Thread {
            server.accept().use { conn ->
                receivedCount.set(conn.getInputStream().readBytes().size)
            }
            serverDone.countDown()
        }
        serverThread.isDaemon = true
        serverThread.start()

        val adapter = NetworkPrinterAdapter("127.0.0.1", server.localPort)
        val result = runBlocking { adapter.print(ByteArray(0)) }

        assertTrue(serverDone.await(5, TimeUnit.SECONDS))
        server.close()

        assertTrue("empty payload must still succeed: ${result.exceptionOrNull()}", result.isSuccess)
        assertEquals(0, receivedCount.get())
    }
}
