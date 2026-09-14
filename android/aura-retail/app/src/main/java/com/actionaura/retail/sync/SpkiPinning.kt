package com.actionaura.retail.sync

import java.io.IOException
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.Base64
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * Raised when a TLS peer's certificate does not carry the pinned
 * SubjectPublicKeyInfo (SPKI) -- see [SpkiPinning]'s module doc for the full
 * design. Deliberately an [IOException], not a [CertificateException]: JSSE
 * declares `X509TrustManager.checkServerTrusted` as `throws
 * CertificateException`, but that "throws" clause is enforced by javac only,
 * never by the JVM -- Kotlin has no checked exceptions at all, so this type
 * still compiles and still propagates correctly. Choosing IOException
 * specifically means this exception flows out through the exact channel
 * [SyncRelayClient]'s `pull()` path already expects a TLS failure to arrive
 * through: `SSLSocket.startHandshake()` itself is declared `throws
 * IOException`, so a pin mismatch surfaces there exactly like any other TLS
 * handshake failure, with no wrapping needed -- unlike this pin's Python
 * analogue (`SpkiPinMismatch` in pinned_transport.py), which has to be
 * fished back out of `requests`/`urllib3`'s own exception-wrapping layers
 * precisely because Python's `requests` stack does not offer an equivalent
 * "this IS the handshake's own declared exception type" seam.
 *
 * This must always reach the caller AS ITSELF -- never caught, logged, and
 * silently converted into a retry, and never collapsed into a generic
 * network-error reason code that a caller might treat as "flaky LAN, try
 * again". A pin mismatch is a security decision, not a transport hiccup.
 */
class SpkiPinMismatchException(message: String) : IOException(message)

/**
 * SPKI ("pin the key, not the cert") pinning for the Android half of the LAN
 * site relay -- the client side of the same design
 * `commercial_runtime/sync/site_relay/pinned_transport.py` +
 * `tls_identity.py` implement on desktop. Read both of those modules'
 * docstrings before touching this file; the reasoning below is the Android
 * mirror of exactly the same argument, and the two sides must derive
 * byte-identical pins because the same physical hub serves both.
 *
 * *** WHY THIS IS A SOCKET-FACTORY-AND-TRUST-MANAGER PAIR, NOT AN OKHTTP
 * `CertificatePinner` -- THE SINGLE MOST LIKELY THING FOR A LATER CHANGE TO
 * UNDO ***
 *
 * [SyncRelayClient] has TWO independent TLS paths to the same hub:
 *   - `push()` goes through OkHttp, an ordinary `OkHttpClient.newCall(...)`.
 *   - `pull()` hand-rolls a raw `java.net.Socket`, wrapped in an
 *     `SSLSocketFactory` it is handed directly (defaulting to
 *     `SSLSocketFactory.getDefault()`) -- see that class's own doc comment
 *     for why OkHttp cannot express a GET-with-a-body request at all, which
 *     is the whole reason this second, lower-level path exists.
 *
 * An OkHttp `CertificatePinner` only ever runs inside OkHttp's own TLS
 * verification pipeline. It would pin `push()` perfectly and do *nothing at
 * all* for `pull()`, because `pull()` never goes anywhere near OkHttp -- it
 * builds its own `SSLSocket` straight from whatever `SSLSocketFactory` it
 * was given. And `pull()` is the direction that matters MORE for this
 * threat model: it is what carries the entire event stream back from the
 * hub to this device, unsigned (see pinned_transport.py's module doc for why
 * pull responses being unsigned is the reason TLS has to do this job at
 * all). A `CertificatePinner`-only implementation would look like it pinned
 * the hub, ship, and leave the direction that most needed pinning
 * completely open to LAN injection.
 *
 * So this object hands out a raw [SSLSocketFactory] + [X509TrustManager]
 * pair instead: something usable to build BOTH an `OkHttpClient` (via
 * `OkHttpClient.Builder().sslSocketFactory(factory, trustManager)`) and a raw
 * `SSLSocket` (via `factory.createSocket(...)`, exactly the shape
 * [SyncRelayClient]'s `sslSocketFactory` constructor parameter already
 * expects). Wiring either seam is a follow-up task's job, not this file's --
 * but whichever wires them in MUST use the same pinned pair for both, or the
 * gap described above reopens silently.
 *
 * Android's own `network_security_config.xml` mechanism cannot express this
 * pin either: it trusts the system + user CA stores, and the hub's
 * certificate is self-signed, so the platform's default trust manager
 * rejects it outright regardless of any `<pin-set>` config aimed at a
 * hostname the hub does not have (its address is DHCP-assigned and unstable
 * -- see tls_identity.py). A custom [X509TrustManager] is not an alternative
 * implementation choice here; it is the only mechanism capable of expressing
 * "trust exactly this key, nothing else, regardless of chain or hostname" on
 * this platform.
 */
object SpkiPinning {

    /**
     * `Base64(SHA-256(SubjectPublicKeyInfo DER))` of [certificate]'s public
     * key -- must stay byte-identical to
     * `tls_identity.spki_pin_from_certificate` on desktop, since the same
     * hub certificate is pinned from both sides.
     *
     * `certificate.publicKey.encoded` returns the key already wrapped as a
     * SubjectPublicKeyInfo structure in X.509 DER form (this is what
     * `java.security.PublicKey.getEncoded()` is documented to return for
     * X.509-keyed providers, which is universally what TLS certificates
     * use) -- exactly the same bytes `cert.public_key().public_bytes(...,
     * format=PublicFormat.SubjectPublicKeyInfo)` produces on the Python
     * side. This is deliberately NOT the whole certificate's DER encoding:
     * pinning the certificate itself breaks the moment the hub reissues one
     * (new serial number, new validity window, a SAN list that changes when
     * the hub's DHCP-assigned IP changes) even though the key underneath
     * never moved. Hashing only the SubjectPublicKeyInfo is what lets the
     * pin survive a reissued certificate over the same key -- that survival
     * property is the entire reason this is called SPKI pinning rather than
     * certificate pinning, and it is exactly what
     * [SpkiPinningTest]'s reissue test exists to prove.
     */
    fun pinOf(certificate: X509Certificate): String {
        val subjectPublicKeyInfoDer = certificate.publicKey.encoded
        val digest = MessageDigest.getInstance("SHA-256").digest(subjectPublicKeyInfoDer)
        return Base64.getEncoder().encodeToString(digest)
    }

    /**
     * An [X509TrustManager] that trusts exactly one TLS peer: whichever
     * presents a leaf certificate whose SPKI pin equals [expectedPin].
     *
     * *** CHAIN AND EXPIRY VALIDATION ARE DELIBERATELY NOT PERFORMED HERE --
     * READ THIS BEFORE CHANGING ANYTHING IN THIS METHOD ***
     * The hub's certificate is self-signed (there is no chain to validate --
     * see tls_identity.py's `generate_site_tls_identity`) and deliberately
     * long-lived (~10 years) so that a shop with no internet connection and
     * nobody technical on site is never surprised by an expiry-driven
     * outage. THE PIN IS THE IDENTITY. Nothing else this trust manager could
     * check (chain-of-trust, hostname, expiry) would add any real security
     * here, because none of those properties is what a device actually
     * relies on to know it is talking to the right hub -- only the key is.
     * If the pin check below is ever weakened, made optional, wrapped in a
     * try/catch that swallows a mismatch, or short-circuited by a config
     * flag, this class silently degrades into "trust any TLS server
     * offering any self-signed certificate on the network" -- which is
     * STRICTLY WORSE than plain, unencrypted HTTP, because it still *looks*
     * encrypted and safe to the next person reading a call site that uses
     * it. Do not relax anything below without re-reading this paragraph.
     */
    fun trustManager(expectedPin: String): X509TrustManager = object : X509TrustManager {

        /**
         * This trust manager exists purely to authenticate a SERVER (the
         * hub) to a CLIENT (this device) -- it is never the client side of
         * a mutual-TLS handshake and has no notion of a trusted client
         * identity to check. Throwing unconditionally, rather than e.g.
         * silently approving, means a future accidental use of this trust
         * manager on a server-side listener (which would need to validate
         * CLIENT certificates) fails loudly instead of quietly accepting
         * every client.
         */
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            throw CertificateException(
                "SpkiPinning's trust manager only authenticates a TLS SERVER (the hub) to this " +
                    "device; it never validates a client certificate and must never be used on a " +
                    "server-side listener."
            )
        }

        /**
         * The one security boundary this whole class exists to enforce.
         *
         * A null/empty chain is rejected outright -- a peer that completes
         * a TLS handshake with literally nothing to pin against is refused,
         * never treated as "nothing to check, so allow it".
         *
         * Only `chain[0]` -- the LEAF certificate the peer is actually
         * presenting itself as -- is ever hashed and compared. Never any
         * other element of [chain]: accepting a match anywhere in the chain
         * would let through any certificate issued by (or chained through)
         * a CA that merely happens to also hold the pinned key somewhere in
         * its chain, which is not the property "this exact peer is the
         * pinned hub" that pinning is supposed to guarantee.
         *
         * The comparison is constant-time (`MessageDigest.isEqual` over raw
         * digest bytes, never a `String ==`/`.equals()` short-circuiting
         * comparison) for the same reason `hmac.compare_digest` is used on
         * the desktop side (pinned_transport.py) -- not because the pin
         * itself is secret (it is handed out openly in the pairing QR code)
         * but because there is no reason to prefer a short-circuiting
         * comparison for a security-relevant equality check when a
         * constant-time one is one call away and costs nothing.
         *
         * On any mismatch this throws [SpkiPinMismatchException] -- it
         * never returns quietly, and never merely logs and continues.
         */
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            if (chain.isNullOrEmpty()) {
                throw SpkiPinMismatchException(
                    "TLS peer presented no certificate to pin against -- refusing the connection."
                )
            }

            val leaf = chain[0]
            val actualPinBytes = Base64.getDecoder().decode(pinOf(leaf))
            val expectedPinBytes = Base64.getDecoder().decode(expectedPin)
            if (!MessageDigest.isEqual(actualPinBytes, expectedPinBytes)) {
                throw SpkiPinMismatchException(
                    "TLS peer's certificate SPKI pin does not match the pinned hub identity. " +
                        "Refusing the connection -- this is either the wrong hub or an attacker on " +
                        "the LAN."
                )
            }
        }

        /**
         * No issuer is ever trusted by chain -- there is no chain-of-trust
         * here, only the pin (see [checkServerTrusted]'s doc comment above).
         * An empty array is the correct, honest representation of that.
         */
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    /**
     * The [SSLSocketFactory] half of [pinnedPair], for callers that only
     * need the factory (e.g. handing it straight to
     * [SyncRelayClient]'s `sslSocketFactory` constructor parameter for the
     * raw-socket `pull()` path).
     */
    fun socketFactory(expectedPin: String): SSLSocketFactory = pinnedPair(expectedPin).first

    /**
     * Builds a single [SSLSocketFactory] + [X509TrustManager] pair, both
     * backed by the SAME trust manager instance, so a caller can wire the
     * identical pin into OkHttp (`OkHttpClient.Builder().sslSocketFactory(
     * factory, trustManager)` -- OkHttp requires both explicitly, not just
     * the factory) and into a raw `SSLSocketFactory.createSocket(...)` call,
     * covering both of [SyncRelayClient]'s TLS seams from one pinned
     * identity. See this object's class doc for why both seams must be
     * covered together.
     */
    fun pinnedPair(expectedPin: String): Pair<SSLSocketFactory, X509TrustManager> {
        val trustManager = trustManager(expectedPin)
        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(null, arrayOf<TrustManager>(trustManager), SecureRandom())
        return sslContext.socketFactory to trustManager
    }
}
