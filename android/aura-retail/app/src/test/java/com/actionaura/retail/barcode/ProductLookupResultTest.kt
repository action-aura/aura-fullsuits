package com.actionaura.retail.barcode

import com.actionaura.retail.net.AuraApi
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.io.IOException

/**
 * lookupProductByCode against a REAL Retrofit-built AuraApi talking to a real
 * local HTTP server (MockWebServer) -- same "real local server, only the
 * network location is faked" discipline as OwnerClientTest (licensing/
 * OwnerClientTest.kt), so this exercises the actual retrofit2.HttpException /
 * IOException shapes Retrofit throws for a 404 and for a dead connection,
 * not a hand-constructed stand-in for either.
 *
 * Launch-readiness "the POS scale fix" (Android half): mirrors the web
 * client's fix (dfc0ef0/17efa5b) -- a scan handler must tell "no such
 * product" (404) apart from "the lookup itself failed" (offline, any other
 * error), and must do no case-folding of its own. The server's actual
 * case-insensitive matching is products/retail/tests/retail_product_lookup_test.py's
 * job, not this file's -- what's proven here is the CLIENT's half of that
 * contract: it forwards the scanned code unmodified and trusts whatever the
 * server resolves.
 */
class ProductLookupResultTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun api(): AuraApi = Retrofit.Builder()
        .baseUrl(server.url("/"))
        .addConverterFactory(GsonConverterFactory.create())
        .build()
        .create(AuraApi::class.java)

    @Test
    fun successful_lookup_returns_the_product() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"status":"success","data":{"id":"1","sku":"SKU-100","barcode":"0123456789012","name":"Widget"}}"""
            )
        )

        val result = lookupProductByCode(api(), "0123456789012")

        assertThat(result).isInstanceOf(ProductLookupResult.Found::class.java)
        assertThat((result as ProductLookupResult.Found).product.name).isEqualTo("Widget")
        assertThat(result.product.id).isEqualTo("1")
    }

    @Test
    fun a_404_is_reported_as_not_found_not_as_an_error() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(404).setBody(
                """{"status":"error","message":"Product not found"}"""
            )
        )

        val result = lookupProductByCode(api(), "does-not-exist")

        // Not merely "isn't Found" -- specifically NotFound, not Failed. A
        // test that only checked "no product came back" would still pass if
        // this collapsed 404 into Failed, which is exactly the bug this
        // route exists to avoid (conflating "no such product" with "the
        // lookup failed").
        assertThat(result).isEqualTo(ProductLookupResult.NotFound)
    }

    @Test
    fun a_transport_failure_is_reported_as_an_error_not_as_not_found() = runBlocking {
        // A dead port, not the shared `server` above -- reusing `server` here
        // would leave it shut down for tearDown() to shut down a second time.
        val down = MockWebServer()
        down.start()
        val deadUrl = down.url("/")
        down.shutdown() // now genuinely unreachable: nothing is listening on deadUrl's port

        val deadApi = Retrofit.Builder()
            .baseUrl(deadUrl)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(AuraApi::class.java)

        val result = lookupProductByCode(deadApi, "0123456789012")

        assertThat(result).isInstanceOf(ProductLookupResult.Failed::class.java)
        // A real IOException from the transport layer (connection refused),
        // never an HttpException -- there was no HTTP response to have a code.
        assertThat((result as ProductLookupResult.Failed).cause).isInstanceOf(IOException::class.java)
    }

    @Test
    fun scan_is_case_insensitive_end_to_end_the_client_does_no_case_folding_of_its_own() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"status":"success","data":{"id":"2","sku":"SKU-200","barcode":"AbCdEf","name":"Gadget"}}"""
            )
        )

        val result = lookupProductByCode(api(), "AbCdEf")

        // Proves the client sends the mixed-case code through byte-for-byte:
        // if a future edit added a client-side .lowercase()/.uppercase() "to
        // help" (which the plan this fix follows explicitly forbids), this
        // recorded request would show the folded value instead.
        val recorded = server.takeRequest()
        assertThat(recorded.path).isEqualTo("/api/sub/retail/products/lookup?code=AbCdEf")
        assertThat(result).isInstanceOf(ProductLookupResult.Found::class.java)
    }
}
