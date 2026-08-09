package com.clipvault.app.sync

import com.clipvault.core.Normalize
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import javax.crypto.AEADBadTagException

class SyncClientBoundsTest {

    @Test
    fun secureTokenFailuresDistinguishTerminalCorruptionFromTemporaryProviderErrors() {
        assertEquals(
            SecureTokenFailureDisposition.CORRUPT_BLOB,
            classifySecureTokenFailure(AEADBadTagException("bad tag")),
        )
        assertEquals(
            SecureTokenFailureDisposition.TRANSIENT,
            classifySecureTokenFailure(IOException("provider temporarily unavailable")),
        )
    }
    @Test
    fun syncHostNormalizerAllowsPlainLanAndDnsHosts() {
        assertEquals("192.168.1.5", normalizeSyncHostOrNull(" 192.168.1.5 "))
        assertEquals("desktop.local", normalizeSyncHostOrNull("Desktop.Local"))
        assertEquals("clipvault-pc.tailnet.local", normalizeSyncHostOrNull("clipvault-pc.tailnet.local"))
        assertEquals("[fd7a:115c:a1e0::1]", normalizeSyncHostOrNull("[fd7a:115c:a1e0::1]"))
    }

    @Test
    fun syncHostNormalizerRejectsUrlLikeOrAmbiguousHosts() {
        val rejected = listOf(
            "",
            "http://192.168.1.5",
            "192.168.1.5:8787",
            "desktop.local/api",
            "desktop.local?x=1",
            "user@desktop.local",
            "desktop local",
            "../desktop",
            "[not-ipv6]",
            "desktop.local#fragment",
        )

        rejected.forEach { host ->
            assertNull(host, normalizeSyncHostOrNull(host))
        }
    }

    @Test
    fun boundedReaderAcceptsBodyAtLimit() {
        val body = "ok".toByteArray(Charsets.UTF_8)

        val out = readUtf8BodyBounded(ByteArrayInputStream(body), maxBytes = body.size)

        assertEquals("ok", out)
    }

    @Test
    fun boundedReaderRejectsBodyAboveLimit() {
        val body = "abcd".toByteArray(Charsets.UTF_8)

        try {
            readUtf8BodyBounded(ByteArrayInputStream(body), maxBytes = 3)
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("response body too large", e.message)
        }
    }

    @Test
    fun boundedReaderRejectsMalformedUtf8InsteadOfReplacingIt() {
        val malformed = byteArrayOf(0xC3.toByte(), 0x28)

        try {
            readUtf8BodyBounded(ByteArrayInputStream(malformed))
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("response body is not valid UTF-8", e.message)
        }
    }

    @Test
    fun maxSizedControlCharacterClipFitsProductionPullResponseLimit() {
        val content = "\u0000".repeat(Normalize.DEFAULT_MAX_CLIP_BYTES)
        val body = JSONObject()
            .put(
                "events",
                JSONArray().put(
                    JSONObject()
                        .put("seq", 1)
                        .put("kind", "clip_new")
                        .put(
                            "payload",
                            JSONObject()
                                .put("content", content)
                                .put("content_hash", "a".repeat(64)),
                        )
                        .put("created_at", "2026-07-13T00:00:00Z"),
                ),
            )
            .put("next_seq", 1)
            .put("has_more", false)
            .toString()
        val bytes = body.toByteArray(Charsets.UTF_8)

        assertTrue(bytes.size > 4 * 1024 * 1024)
        assertTrue(bytes.size <= MAX_SYNC_RESPONSE_BYTES)
        assertEquals(body, readUtf8BodyBounded(ByteArrayInputStream(bytes)))
    }

    @Test
    fun productionReaderRejectsBodyAboveHardLimit() {
        val bytes = ByteArray(MAX_SYNC_RESPONSE_BYTES + 1) { 'x'.code.toByte() }

        try {
            readUtf8BodyBounded(ByteArrayInputStream(bytes))
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("response body too large", e.message)
        }
    }

    @Test
    fun syncAuthClassifierOnlyTreatsAuthRejectionsAsPermanent() {
        assertTrue(isPermanentSyncAuthFailure(401))
        assertTrue(isPermanentSyncAuthFailure(403))

        assertFalse(isPermanentSyncAuthFailure(400))
        assertFalse(isPermanentSyncAuthFailure(413))
        assertFalse(isPermanentSyncAuthFailure(429))
        assertFalse(isPermanentSyncAuthFailure(500))
    }

    @Test
    fun authenticatedPermanentAuthFailuresDoNotNeedResponseBodies() {
        assertFalse(shouldReadSyncResponseBody(401, auth = true))
        assertFalse(shouldReadSyncResponseBody(403, auth = true))

        assertTrue(shouldReadSyncResponseBody(401, auth = false))
        assertTrue(shouldReadSyncResponseBody(403, auth = false))
        assertTrue(shouldReadSyncResponseBody(413, auth = true))
        assertTrue(shouldReadSyncResponseBody(429, auth = true))
        assertTrue(shouldReadSyncResponseBody(500, auth = true))
    }

    @Test
    fun pairingBaseEchoAcceptsOnlyPositiveIntegralJsonNumbers() {
        assertEquals(1L, strictPairingBaseSeq(1))
        assertEquals(Long.MAX_VALUE, strictPairingBaseSeq(Long.MAX_VALUE))

        listOf(null, JSONObject.NULL, true, false, 0, 0L, -1, -1L, 1.0, "1").forEach {
            assertEquals(null, strictPairingBaseSeq(it))
        }
    }

    @Test
    fun pairingResponseRequiresExactBaseEchoAndStringToken() {
        val pairingToken = "A".repeat(43)
        val valid = parsePairingResponse(
            JSONObject()
                .put("token", pairingToken)
                .put("server_device", "desktop-main")
                .put("outbox_base_seq", 101)
                .toString(),
            expectedOutboxBaseSeq = 101L,
        )

        assertEquals(pairingToken, valid?.token)
        assertEquals("desktop-main", valid?.serverDevice)

        listOf(
            JSONObject().put("token", "fresh-token"),
            JSONObject().put("token", "fresh-token").put("outbox_base_seq", 100),
            JSONObject().put("token", "fresh-token").put("outbox_base_seq", "101"),
            JSONObject().put("token", "").put("outbox_base_seq", 101),
            JSONObject().put("token", 123).put("outbox_base_seq", 101),
            JSONObject()
                .put("token", "short-token")
                .put("server_device", "desktop-main")
                .put("outbox_base_seq", 101),
            JSONObject()
                .put("token", "fresh-token")
                .put("server_device", 123)
                .put("outbox_base_seq", 101),
            JSONObject()
                .put("token", "fresh-token")
                .put("server_device", "desktop-main")
                .put("outbox_base_seq", 101)
                .put("unexpected", true),
        ).forEach { response ->
            assertNull(parsePairingResponse(response.toString(), expectedOutboxBaseSeq = 101L))
        }
    }

    @Test
    fun pairPushAndPullResponsesRequireStrictRfc8259Objects() {
        val pairingToken = "A".repeat(43)
        val pairing = """{"token":"$pairingToken","server_device":"desktop-main","outbox_base_seq":101}"""
        assertNotNull(parsePairingResponse(pairing, expectedOutboxBaseSeq = 101L))
        listOf(
            "$pairing trailing",
            "{'token':'fresh-token','server_device':'desktop-main','outbox_base_seq':101}",
            "{token:\"fresh-token\",\"server_device\":\"desktop-main\",\"outbox_base_seq\":101}",
            "{\"token\":\"first\",\"\\u0074oken\":\"second\",\"server_device\":\"desktop-main\",\"outbox_base_seq\":101}",
        ).forEach { raw ->
            assertNull(raw, parsePairingResponse(raw, expectedOutboxBaseSeq = 101L))
        }

        assertEquals(7L, parsePushAckResponse("""{"acked_upto":7}"""))
        listOf(
            """{"acked_upto":"7"}""",
            """{"acked_upto":7.0}""",
            """{"acked_upto":true}""",
            """{"acked_upto":-1}""",
            "{'acked_upto':7}",
            "{acked_upto:7}",
            """{"acked_upto":7,"unexpected":false}""",
            "{\"acked_upto\":7,\"\\u0061cked_upto\":8}",
            """{"acked_upto":7} trailing""",
        ).forEach { raw -> assertNull(raw, parsePushAckResponse(raw)) }

        val pull = """{"events":[],"next_seq":9,"has_more":false}"""
        assertNotNull(parsePullResponse(pull))
        listOf(
            """{"events":[],"next_seq":"9","has_more":false}""",
            """{"events":[],"next_seq":9.0,"has_more":false}""",
            """{"events":[],"next_seq":9,"has_more":"false"}""",
            """{"events":{},"next_seq":9,"has_more":false}""",
            """{"events":[],"next_seq":-1,"has_more":false}""",
            "{'events':[],'next_seq':9,'has_more':false}",
            "{events:[],next_seq:9,has_more:false}",
            """{"events":[],"next_seq":9,"has_more":false,"unexpected":0}""",
            "{\"events\":[],\"next_seq\":9,\"\\u0068as_more\":false,\"has_more\":true}",
            """{"events":[],"next_seq":9,"has_more":false} trailing""",
        ).forEach { raw -> assertNull(raw, parsePullResponse(raw)) }
    }

    @Test
    fun pullCursorAllowsEmptyTerminalPageWithoutProgress() {
        val next = nextPullCursorOrThrow(5, eventCount = 0, nextSeq = 5, hasMore = false)

        assertEquals(5, next)
    }

    @Test
    fun pullCursorAllowsForwardProgress() {
        val next = nextPullCursorOrThrow(5, eventCount = 1, nextSeq = 6, hasMore = true)

        assertEquals(6, next)
    }

    @Test
    fun pullCursorRejectsHasMoreWithoutProgress() {
        try {
            nextPullCursorOrThrow(5, eventCount = 0, nextSeq = 5, hasMore = true)
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("sync pull cursor did not advance", e.message)
        }
    }

    @Test
    fun pullCursorRejectsEventsWithoutProgress() {
        try {
            nextPullCursorOrThrow(5, eventCount = 1, nextSeq = 5, hasMore = false)
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("sync pull cursor did not advance", e.message)
        }
    }

    @Test
    fun pullCursorRejectsRegression() {
        try {
            nextPullCursorOrThrow(5, eventCount = 0, nextSeq = 4, hasMore = false)
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("sync pull cursor did not advance", e.message)
        }
    }

    @Test
    fun pullCursorRejectsCoercibleControlFields() {
        val events = JSONArray()
        for (response in listOf(
            JSONObject().put("next_seq", "5").put("has_more", false),
            JSONObject().put("next_seq", 5).put("has_more", "false"),
        )) {
            try {
                nextPullCursorOrThrow(5, events, response)
                fail("expected IOException")
            } catch (_: IOException) {
                // Strict JSON types prevent a forged response from retiring data.
            }
        }
    }

    @Test
    fun pullCursorRequiresOrderedEventSequencesWithinAcknowledgedRange() {
        val response = JSONObject()
            .put("next_seq", 8)
            .put("has_more", false)
        val reversed = JSONArray()
            .put(JSONObject().put("seq", 7))
            .put(JSONObject().put("seq", 6))

        try {
            nextPullCursorOrThrow(5, reversed, response)
            fail("expected IOException")
        } catch (e: IOException) {
            assertEquals("invalid sync pull event sequence", e.message)
        }
    }

    @Test
    fun pullCursorAllowsVisibleGapsForQuarantinedOrHiddenRows() {
        val response = JSONObject()
            .put("events", JSONArray().put(JSONObject().put("seq", 7)))
            .put("next_seq", 8)
            .put("has_more", false)

        assertEquals(
            8L,
            nextPullCursorOrThrow(5, response.getJSONArray("events"), response),
        )
    }
}
