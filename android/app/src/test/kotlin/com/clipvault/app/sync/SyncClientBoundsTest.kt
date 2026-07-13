package com.clipvault.app.sync

import com.clipvault.core.Normalize
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException

class SyncClientBoundsTest {
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
}
