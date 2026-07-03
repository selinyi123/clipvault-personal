package com.clipvault.app.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException

class SyncClientBoundsTest {
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
}
