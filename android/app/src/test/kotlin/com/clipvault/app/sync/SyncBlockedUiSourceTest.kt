package com.clipvault.app.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class SyncBlockedUiSourceTest {
    @Test
    fun blockedHeadProbeReadsOnlySequenceMetadata() {
        val path = Path.of(
            "src", "main", "kotlin", "com", "clipvault", "app", "data", "Db.kt",
        )
        assertTrue("database source is missing: $path", Files.isRegularFile(path))
        val source = String(Files.readAllBytes(path), Charsets.UTF_8)

        assertTrue(source.contains("@Query(\"SELECT seq FROM outbox ORDER BY seq LIMIT 1\")"))
        assertTrue(source.contains("fun firstSeq(): Long?"))
        assertTrue(source.contains("length(payload) AS payloadChars"))
        assertTrue(source.contains("length(CAST(payload AS BLOB)) AS payloadBytes"))
        assertTrue(source.contains("fun batchMetadata(limit: Int): List<OutboxMetadata>"))
        assertTrue(source.contains("SELECT substr(payload, :offset, :charCount)"))
        assertTrue(source.contains("fun payloadChunk(seq: Long, offset: Int, charCount: Int): String?"))
        assertFalse(source.contains("SELECT * FROM outbox"))

        val workerPath = Path.of(
            "src", "main", "kotlin", "com", "clipvault", "app", "sync", "SyncWorker.kt",
        )
        val worker = String(Files.readAllBytes(workerPath), Charsets.UTF_8)
        assertTrue(worker.contains("private const val OUTBOX_PAYLOAD_CHUNK_CHARS = 64 * 1024"))
        assertTrue(worker.contains("chunk.codePointCount(0, chunk.length)"))
        assertFalse(worker.contains("db.outbox().batch(SYNC_OUTBOX_BATCH_LIMIT)"))
    }

    @Test
    fun localUiShowsSafeBlockedStatusAndOffersExplicitRecheck() {
        val path = Path.of(
            "src", "main", "kotlin", "com", "clipvault", "app", "ui", "MainActivity.kt",
        )
        assertTrue("MainActivity source is missing: $path", Files.isRegularFile(path))
        val source = String(Files.readAllBytes(path), Charsets.UTF_8)

        val cardStart = source.indexOf("private fun SyncPushBlockedCard(")
        val nextComposable = source.indexOf("private fun SetupCard(", cardStart)
        assertTrue("blocked sync status card is missing", cardStart >= 0)
        assertTrue("blocked sync status card boundary is missing", nextComposable > cardStart)
        val card = source.substring(cardStart, nextComposable)

        assertTrue(card.contains("同步发送已暂停"))
        assertTrue(card.contains("接收桌面内容仍会继续"))
        assertTrue(card.contains("桌面端版本过旧"))
        assertTrue(card.contains("已修复，重新检查"))
        assertTrue(card.contains("state.seq"))
        assertTrue(card.contains("state.reason"))
        assertFalse(card.contains("payload"))
        assertFalse(card.contains("content_hash"))
        assertFalse(card.contains("source_app"))

        assertTrue(source.contains("Settings(ctx).clearSyncPushBlocked()"))
        assertTrue(source.contains("SyncScheduler.requestPushBestEffort(ctx)"))
    }
}
