package com.actionaura.retail.sync

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

/**
 * The real [Transport] -- a genuine `DatagramSocket` for the
 * beacon listen, and genuine [SpkiPinning]-pinned OkHttp calls for
 * `/identity` and `/join`. `HubDiscovery.kt`'s own [Transport]
 * doc comment is the contract this class fills in; nothing here re-decides
 * any of the trust logic that class already owns (parseBeacon/shouldJoin) --
 * this is wiring, not policy.
 *
 * *** WHY EVERY PINNED CLIENT BELOW SETS `hostnameVerifier { _, _ -> true }`
 * -- READ [SpkiPinning]'S CLASS DOC BEFORE CHANGING THIS ***
 * The hub's certificate is self-signed and carries no LAN IP in its SAN
 * (its address is DHCP-assigned and unstable), so ordinary hostname
 * verification would reject a correctly-pinned hub outright. The
 * [SpkiPinning.trustManager] backing `sslSocketFactory` below has already
 * refused every peer except the one exact pinned key, unconditionally,
 * before this verifier ever runs -- the pin IS the identity, not the name.
 * Mirrors [SyncCoordinator]'s own `relayClient()` (`verifyHostname = false`
 * plus this same "accept every hostname" verifier) and
 * `commercial_runtime/sync/site_relay/pinned_transport.py`'s
 * `SpkiPinnedAdapter` -- the identical reasoning applied a third time, never
 * restated differently.
 */
class HubTransport(private val appContext: Context? = null) : Transport {

    /**
     * WITHOUT THIS LOCK THE PHONE HEARS NOTHING, however correct the socket
     * below is. Android's wifi stack filters out packets that are not
     * explicitly addressed to this device -- broadcast included -- so the
     * hub's beacon never reaches the app at all.
     *
     * Measured on real hardware rather than inferred: with the hub
     * confirmed broadcasting to 255.255.255.255 every 5 seconds (a desktop
     * listener on the same wifi heard it within 4s and printed the hub's
     * url and pin), the phone logged "No hub beacon heard on this sweep" on
     * every tick. Same network, same port, same datagram.
     *
     * Held for the FEW SECONDS OF ONE LISTEN and always released, never for
     * the life of the process: the lock disables a wifi power-saving
     * filter, and Android's own documentation calls out the battery cost of
     * leaving it held. `setReferenceCounted(false)` so a release always
     * really releases, even if a future caller acquires twice.
     *
     * `appContext` is nullable ONLY so tests and any caller without a
     * Context can still construct this class; a null context means no lock,
     * which degrades to exactly the pre-fix behaviour rather than crashing.
     * Every real caller passes one.
     */
    private fun acquireBeaconLock(): WifiManager.MulticastLock? {
        val ctx = appContext ?: return null
        return try {
            val wifi = ctx.applicationContext
                .getSystemService(Context.WIFI_SERVICE) as? WifiManager ?: return null
            wifi.createMulticastLock("aura-hub-beacon").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (exc: Exception) {
            // Never fatal: a device that refuses the lock still tries the
            // listen (it may work on that hardware), and a failed discovery
            // sweep is routine -- the same posture as every other failure
            // on this path.
            Log.i(TAG, "Could not acquire the wifi multicast lock; listening anyway.")
            null
        }
    }

    private fun releaseBeaconLock(lock: WifiManager.MulticastLock?) {
        try {
            if (lock != null && lock.isHeld) lock.release()
        } catch (exc: Exception) {
            // Releasing a lock the system already tore down must never turn
            // a successful discovery into a failure.
        }
    }



    /**
     * A real `DatagramSocket` bound to [BEACON_PORT], `SO_REUSEADDR` set
     * (so a restart of this device's own listener does not collide with a
     * socket the OS has not yet fully released), timed out at
     * [timeoutMillis]. Always closed in a `finally`, whether a datagram
     * arrived, the deadline elapsed, or anything else went wrong.
     *
     * *** THE 1024-BYTE CAP IS ENFORCED BY THE RECEIVE BUFFER ITSELF, BEFORE
     * A SINGLE BYTE IS TURNED INTO A STRING *** -- [beaconBuffer]'s size IS
     * the cap: `DatagramSocket.receive` never writes more bytes into the
     * array than the array is long, so an oversized datagram is truncated
     * by the OS/JVM at the socket layer, before [parseBeacon] or anything
     * else in this app ever sees it. This is unauthenticated input from
     * anyone on the shop wifi (see `HubDiscovery.kt`'s own "A BEACON IS A
     * POINTER, NOT A CREDENTIAL" section) -- a JSON parser must never run
     * over an attacker-chosen payload of unbounded size, the identical
     * reasoning `beacon.py::BEACON_MAX_DATAGRAM_BYTES` and
     * `HubDiscovery.kt`'s own private `BEACON_MAX_DATAGRAM_BYTES` constant
     * both give.
     */
    override fun listenForBeacon(timeoutMillis: Long): String? {
        // Acquired BEFORE the socket is bound and released in the same
        // finally -- see acquireBeaconLock for why the listen is dead
        // without it.
        val multicastLock = acquireBeaconLock()
        val socket = DatagramSocket(null)
        return try {
            socket.reuseAddress = true
            socket.bind(InetSocketAddress(BEACON_PORT))
            socket.soTimeout = timeoutMillis.toInt()
            val beaconBuffer = ByteArray(BEACON_MAX_DATAGRAM_BYTES)
            val packet = DatagramPacket(beaconBuffer, beaconBuffer.size)
            socket.receive(packet)
            String(packet.data, packet.offset, packet.length, Charsets.UTF_8)
        } catch (timeout: SocketTimeoutException) {
            // No beacon before the deadline -- not an error, see
            // HubDiscoveryResult.NoHubFound's own doc comment.
            null
        } catch (ioError: Exception) {
            // Any other socket failure (bind refused, interface down, ...)
            // is likewise "no beacon this attempt" to the caller -- never an
            // uncaught exception a background tick would have to guard
            // against separately (mirrors HubAutoJoinService's own "never
            // throws out of the tick" rule one layer up).
            null
        } finally {
            socket.close()
            releaseBeaconLock(multicastLock)
        }
    }

    /**
     * `GET /api/sync/v1/identity` over a connection pinned to [spkiPin] --
     * see this class's doc comment for why hostname verification is
     * deliberately disabled once that pin is wired in. Any non-2xx status
     * still returns a body (or throws only on a genuine I/O failure);
     * `HubDiscovery.discoverAndJoin` is the layer that decides what a
     * malformed/unexpected body means, not this transport.
     */
    override fun getIdentity(baseUrl: String, spkiPin: String): String {
        val request = Request.Builder()
            .url(baseUrl.trimEnd('/') + IDENTITY_PATH)
            .get()
            .build()
        pinnedClient(spkiPin).newCall(request).execute().use { response ->
            return response.body?.string()
                ?: throw IOException("GET $IDENTITY_PATH returned no response body.")
        }
    }

    /**
     * `POST /api/sync/v1/join` with [body] as the raw JSON request payload,
     * over a connection pinned to [spkiPin]. Returns the HTTP status code
     * verbatim -- `HubDiscovery.discoverAndJoin` is what decides a non-200
     * means [HubDiscoveryResult.Failed], not this transport.
     */
    override fun postJoin(baseUrl: String, spkiPin: String, body: String): Int {
        val request = Request.Builder()
            .url(baseUrl.trimEnd('/') + JOIN_PATH)
            .post(body.toRequestBody(JSON_MEDIA_TYPE))
            .build()
        pinnedClient(spkiPin).newCall(request).execute().use { response ->
            return response.code
        }
    }

    /**
     * Builds a fresh OkHttp client pinned to [spkiPin] via
     * [SpkiPinning.pinnedPair] -- wiring [SpkiPinning] into this transport
     * is what makes it safe to call at all: an UNPINNED discovery transport
     * would hand this device's own installation id, device public key and
     * Owner-signed assertion envelope, in [HubDiscovery.discoverAndJoin]'s
     * `/join` request body, to WHATEVER TLS peer answered on the
     * beacon-advertised address -- an attacker on the shop wifi who raced to
     * grab that address, not necessarily the real hub. Pinning is what turns
     * "answered on the right IP" into "is actually the hub" (see
     * `HubPairingWiringContractTest`'s identical reasoning for the manual
     * pairing call this feature replaces).
     *
     * A new client per call, not a cached one: [spkiPin] varies beacon to
     * beacon (a different hub, or the same hub with a reissued
     * certificate), and OkHttp's `sslSocketFactory`/`hostnameVerifier` are
     * fixed at `Builder.build()` time -- there is no cheaper way to change
     * the pin than building a new client. Short-lived, infrequent calls
     * (once per successful beacon, at most once a minute -- see
     * [HubAutoJoinService]) make the extra allocation immaterial.
     *
     * Short timeouts -- [CONNECT_TIMEOUT_SECONDS]/[READ_TIMEOUT_SECONDS] --
     * because this runs on [HubAutoJoinService]'s background tick and must
     * never wedge it: a hub that stops answering mid-handshake must fail
     * this attempt quickly so the next scheduled tick gets a fair try,
     * rather than blocking the tick indefinitely.
     */
    private fun pinnedClient(spkiPin: String): OkHttpClient {
        val (factory, trustManager) = SpkiPinning.pinnedPair(spkiPin)
        return OkHttpClient.Builder()
            .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .readTimeout(READ_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .sslSocketFactory(factory, trustManager)
            .hostnameVerifier { _, _ -> true }
            .build()
    }

    private companion object {
        // Same tag HubAutoJoinService logs under, deliberately: this class is
        // that loop's transport, and splitting one flow across two logcat
        // tags is how a diagnosis gets missed.
        const val TAG = "HubAutoJoin"
        const val IDENTITY_PATH = "/api/sync/v1/identity"
        const val JOIN_PATH = "/api/sync/v1/join"
        const val CONNECT_TIMEOUT_SECONDS = 5L
        const val READ_TIMEOUT_SECONDS = 10L

        /**
         * Textually identical to `beacon.py::BEACON_MAX_DATAGRAM_BYTES` and
         * `HubDiscovery.kt`'s own private `BEACON_MAX_DATAGRAM_BYTES` -- this
         * transport enforces the SAME wire-format cap those two modules
         * already agree on, rather than inventing a fourth number that could
         * silently drift from either.
         */
        const val BEACON_MAX_DATAGRAM_BYTES = 1024

        val JSON_MEDIA_TYPE = "application/json".toMediaType()
    }
}
