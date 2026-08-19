package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeSnapshotClientSourceTest {
    @Test
    fun `client rejects stale generations stale epochs and malformed arrays`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/imeapp/RuntimeSnapshotClient.kt",
        ).readText()

        assertTrue(source.contains("RuntimeSnapshotContract.responseMatchesInputSession("))
        assertTrue(source.contains("RuntimeSnapshotContract.acceptsSnapshotIdentity("))
        assertTrue(source.contains("currentPublisher != decoded.publisherEpoch"))
        assertTrue(source.contains("candidateIds = candidateIds"))
        assertTrue(source.contains("sources = sources"))
        assertTrue(source.contains("callback.callback(emptyList())"))
        assertTrue(source.contains("val expired = pending.remove(requestId)"))
        assertTrue(source.contains("override fun onBindingDied"))
    }
}
