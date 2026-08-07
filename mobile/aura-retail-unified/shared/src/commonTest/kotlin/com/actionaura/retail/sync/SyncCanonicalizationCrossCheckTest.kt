package com.actionaura.retail.sync

import com.actionaura.retail.licensing.lease.LeaseCanonicalJson
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.putJsonObject
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * Task 9 (multi-device-sync-foundation) -- real cross-runtime compatibility
 * proof for [SyncTransport]'s own payload shapes specifically, NOT a
 * re-test of [LeaseCanonicalJson] in general. `LeaseCanonicalJsonTest`
 * (Task M11.1/M11.35) already proved [LeaseCanonicalJson] byte-identical
 * to Owner's real Python `canonicalize()` for a flat licensing-assertion
 * shape (a nested object, a boolean, an integer, a float, non-ASCII) --
 * but it never exercised an ARRAY of nested objects (this task's `events`
 * field) or a bare top-level integer field (`since`), which is what
 * `owner/app/sync/routes.py`'s own push/pull bodies actually carry. This
 * is the highest-risk single point of failure in the whole mobile sync
 * path (task-9-brief.md's own framing): if array-of-object canonicalization
 * ever diverged from Python's `json.dumps` for this shape, no push
 * signature would ever verify.
 *
 * Both [expected] strings below are the REAL, captured output of
 * `owner/app/licensing_service/canonical.py::canonicalize()` (the exact
 * function `owner/app/sync/routes.py::_authenticate` calls to verify a
 * signature), executed via this repo's own `.venv` against the identical
 * dict shape built here:
 *
 * ```
 * python -c "
 * import sys; sys.path.insert(0, 'owner')
 * from app.licensing_service.canonical import canonicalize
 * print(canonicalize({...}))
 * "
 * ```
 *
 * A real, decisive comparison, not an assumption the existing
 * [LeaseCanonicalJson.writeArray] path (already implemented, never
 * previously exercised by a real cross-language check) matches -- same
 * discipline `LeaseCanonicalJsonTest`'s own KDoc documents.
 */
class SyncCanonicalizationCrossCheckTest {

    /**
     * A push-body-shaped payload: `installation_id`/`timestamp`/`nonce`
     * (the real signed fields every push/pull shares) plus `events`, an
     * array of nested objects -- including a non-ASCII string, a boolean,
     * an integer, and one event with a genuinely EMPTY nested object
     * (`payload: {}`) to also prove the empty-array/empty-object edge case
     * matches Python's `json.dumps({})` == `"{}"`.
     */
    @Test
    fun pushShapedPayloadWithEventsArrayMatchesRealPythonCanonicalizeOutput() {
        val payload = buildJsonObject {
            put("installation_id", JsonPrimitive("test-installation-123"))
            put("timestamp", JsonPrimitive("2026-08-07T00:00:00+00:00"))
            put("nonce", JsonPrimitive("test-nonce-abc"))
            put(
                "events",
                buildJsonArray {
                    add(
                        buildJsonObject {
                            put("id", JsonPrimitive("event-1"))
                            put("entity_type", JsonPrimitive("category"))
                            put("entity_id", JsonPrimitive("entity-1"))
                            put("event_type", JsonPrimitive("create"))
                            putJsonObject("payload") {
                                put("name", JsonPrimitive("Café"))
                                put("active", JsonPrimitive(true))
                                put("sort_order", JsonPrimitive(3))
                            }
                            put("created_at", JsonPrimitive("2026-08-07T00:00:01+00:00"))
                        },
                    )
                    add(
                        buildJsonObject {
                            put("id", JsonPrimitive("event-2"))
                            put("entity_type", JsonPrimitive("category"))
                            put("entity_id", JsonPrimitive("entity-2"))
                            put("event_type", JsonPrimitive("delete"))
                            putJsonObject("payload") {}
                            put("created_at", JsonPrimitive("2026-08-07T00:00:02+00:00"))
                        },
                    )
                },
            )
        }

        val expected = "{\"events\":[{\"created_at\":\"2026-08-07T00:00:01+00:00\",\"entity_id\":\"entity-1\",\"entity_type\":\"category\",\"event_type\":\"create\",\"id\":\"event-1\",\"payload\":{\"active\":true,\"name\":\"Café\",\"sort_order\":3}},{\"created_at\":\"2026-08-07T00:00:02+00:00\",\"entity_id\":\"entity-2\",\"entity_type\":\"category\",\"event_type\":\"delete\",\"id\":\"event-2\",\"payload\":{}}],\"installation_id\":\"test-installation-123\",\"nonce\":\"test-nonce-abc\",\"timestamp\":\"2026-08-07T00:00:00+00:00\"}"

        assertEquals(expected, LeaseCanonicalJson.canonicalize(payload))
    }

    /** A pull-body-shaped payload: same signed envelope fields plus a bare top-level integer `since` -- proving an integer field that sits directly alongside strings at the top level (not nested inside an object, unlike `LeaseCanonicalJsonTest`'s `allowed_device_count`) canonicalizes correctly. */
    @Test
    fun pullShapedPayloadWithIntegerSinceMatchesRealPythonCanonicalizeOutput() {
        val payload = buildJsonObject {
            put("installation_id", JsonPrimitive("test-installation-123"))
            put("timestamp", JsonPrimitive("2026-08-07T00:00:00+00:00"))
            put("nonce", JsonPrimitive("test-nonce-xyz"))
            put("since", JsonPrimitive(42))
        }

        val expected = "{\"installation_id\":\"test-installation-123\",\"nonce\":\"test-nonce-xyz\",\"since\":42,\"timestamp\":\"2026-08-07T00:00:00+00:00\"}"

        assertEquals(expected, LeaseCanonicalJson.canonicalize(payload))
    }

    /** `since=0` is a real, valid value (a device's very first pull) -- proving it canonicalizes as bare `0`, never omitted/null/stringified. */
    @Test
    fun sinceZeroCanonicalizesAsBareIntegerZero() {
        val payload = buildJsonObject {
            put("installation_id", JsonPrimitive("test-installation-123"))
            put("timestamp", JsonPrimitive("2026-08-07T00:00:00+00:00"))
            put("nonce", JsonPrimitive("test-nonce-zero"))
            put("since", JsonPrimitive(0))
        }
        val result = LeaseCanonicalJson.canonicalize(payload)
        assertEquals(true, result.contains("\"since\":0,") || result.contains("\"since\":0}"))
    }

    /** An empty `events` array (a push call with nothing queued, e.g. a defensive no-op path) canonicalizes as `[]`, matching Python's `json.dumps([])`. */
    @Test
    fun emptyEventsArrayCanonicalizesAsEmptyBrackets() {
        val payload = buildJsonObject {
            put("installation_id", JsonPrimitive("test-installation-123"))
            put("timestamp", JsonPrimitive("2026-08-07T00:00:00+00:00"))
            put("nonce", JsonPrimitive("test-nonce-empty"))
            put("events", buildJsonArray {})
        }
        val result = LeaseCanonicalJson.canonicalize(payload)
        assertEquals(true, result.contains("\"events\":[]"))
    }
}
