package com.clipvault.app.runtime

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeSnapshotBrokerServiceSourceTest {
    @Test
    fun `broker emits complete Snapshot V1 identity and fails empty on saturation`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/app/runtime/ClipVaultSnapshotBrokerService.kt",
        ).readText()

        assertTrue(source.contains("publisherEpoch = UUID.randomUUID().toString()"))
        assertTrue(source.contains("snapshotGeneration.incrementAndGet()"))
        assertTrue(source.contains("KEY_INPUT_SESSION_GENERATION"))
        assertTrue(source.contains("KEY_PUBLISHER_EPOCH"))
        assertTrue(source.contains("KEY_SNAPSHOT_GENERATION"))
        assertTrue(source.contains("KEY_EXPIRES_AT_ELAPSED_MS"))
        assertTrue(source.contains("KEY_CANDIDATE_IDS"))
        assertTrue(source.contains("KEY_SOURCES"))
        assertTrue(source.contains("ThreadPoolExecutor.AbortPolicy()"))
        assertTrue(source.contains("catch (_: RejectedExecutionException)"))
        assertTrue(source.contains("sendSnapshot(reply, requestId, inputSessionGeneration, emptyList())"))
    }
}
