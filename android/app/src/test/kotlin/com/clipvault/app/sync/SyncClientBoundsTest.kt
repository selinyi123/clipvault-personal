package com.clipvault.app.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
