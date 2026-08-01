package com.clipvault.ime.engine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeSnapshotContractTest {
    @Test
    fun `SNAP V001 broker accepts a bounded response`() {
        val items = listOf(item("id-1", "完整候选"))

        assertEquals(items, RuntimeSnapshotContract.sanitizeForBroker(items, 8))
        val decoded = decode(items)
        assertEquals(items, requireNotNull(decoded).items)
        assertTrue(RuntimeSnapshotContract.isFreshExpiry(NOW, NOW + 30_000L))
    }

    @Test
    fun `SNAP V003 stale input session generation is rejected`() {
        assertTrue(RuntimeSnapshotContract.responseMatchesInputSession(7, 7, 7))
        assertTrue(!RuntimeSnapshotContract.responseMatchesInputSession(8, 7, 7))
        assertTrue(!RuntimeSnapshotContract.responseMatchesInputSession(8, 8, 7))
        assertTrue(!RuntimeSnapshotContract.responseMatchesInputSession(0, 0, 0))
    }

    @Test
    fun `SNAP V004 publisher identity requires canonical UUIDv4`() {
        assertTrue(RuntimeSnapshotContract.isCanonicalPublisherEpoch(EPOCH))
        assertTrue(!RuntimeSnapshotContract.isCanonicalPublisherEpoch("00000000-0000-1000-8000-000000000000"))
        assertNull(decode(listOf(item("id-1", "text")), epoch = "not-an-epoch"))
        assertTrue(RuntimeSnapshotContract.acceptsSnapshotIdentity(null, 0, EPOCH, 1))
        assertTrue(!RuntimeSnapshotContract.acceptsSnapshotIdentity(EPOCH, 2, OTHER_EPOCH, 1))
    }

    @Test
    fun `SNAP V005 broker filters oversized items without truncation`() {
        val oversized = "x".repeat(RuntimeSnapshotContract.MAX_TEXT_UTF8_BYTES + 1)
        val valid = "完整候选"

        val accepted = RuntimeSnapshotContract.sanitizeForBroker(
            listOf(
                item("oversized", oversized),
                item("valid", valid),
            ),
            RuntimeSnapshotContract.MAX_ITEMS,
        )

        assertEquals(listOf(item("valid", valid)), accepted)
    }

    @Test
    fun `SNAP V005 broker keeps complete frame inside 64 KiB`() {
        val maxAsciiText = "x".repeat(RuntimeSnapshotContract.MAX_TEXT_UTF8_BYTES)
        val candidates = List(RuntimeSnapshotContract.MAX_ITEMS) { index ->
            item("item-$index", maxAsciiText)
        }

        val accepted = RuntimeSnapshotContract.sanitizeForBroker(
            candidates,
            RuntimeSnapshotContract.MAX_ITEMS,
        )

        assertTrue(accepted.isNotEmpty())
        assertTrue(accepted.size < candidates.size)
        assertTrue(
            requireNotNull(RuntimeSnapshotContract.estimatedFrameBytes(accepted)) <=
                RuntimeSnapshotContract.MAX_FRAME_BYTES,
        )
        assertTrue(accepted.all { it.text === maxAsciiText })
    }

    @Test
    fun `SNAP V006 decoder rejects mismatches duplicates and invalid bounds`() {
        assertNull(
            RuntimeSnapshotContract.decodeFrame(
                publisherEpoch = EPOCH,
                snapshotGeneration = 1,
                expiresAtElapsedMs = NOW + 30_000L,
                candidateIds = listOf("one"),
                sources = emptyList(),
                labels = listOf("label"),
                texts = listOf("text"),
            ),
        )
        assertNull(decode(listOf(item("duplicate", "one"), item("duplicate", "two"))))
        assertNull(decode(listOf(item("x".repeat(129), "text"))))
        assertNull(decode(listOf(item("id", "text", label = "x".repeat(65)))))
        assertNull(decode(listOf(item("id", "x".repeat(16_385)))))
        assertNull(decode(listOf(item("id", "text", source = "unknown"))))
    }

    @Test
    fun `SNAP V006 generation and expiry must be positive and current`() {
        assertNull(decode(listOf(item("id", "text")), generation = 0))
        assertTrue(RuntimeSnapshotContract.acceptsSnapshotIdentity(EPOCH, 1, EPOCH, 2))
        assertTrue(!RuntimeSnapshotContract.acceptsSnapshotIdentity(EPOCH, 2, EPOCH, 2))
        assertTrue(!RuntimeSnapshotContract.acceptsSnapshotIdentity(EPOCH, 2, EPOCH, 1))
        assertTrue(!RuntimeSnapshotContract.isFreshExpiry(NOW, NOW))
        assertTrue(!RuntimeSnapshotContract.isFreshExpiry(NOW, NOW + 30_001L))
    }

    private fun decode(
        items: List<RuntimeSnapshotItem>,
        epoch: String = EPOCH,
        generation: Long = 1,
    ): RuntimeSnapshotFrame? = RuntimeSnapshotContract.decodeFrame(
        publisherEpoch = epoch,
        snapshotGeneration = generation,
        expiresAtElapsedMs = NOW + 30_000L,
        candidateIds = items.map { it.candidateId },
        sources = items.map { it.source },
        labels = items.map { it.label },
        texts = items.map { it.text },
    )

    private fun item(
        id: String,
        text: String,
        label: String = "phrase",
        source: String = RuntimeSnapshotContract.SOURCE_MEMORY,
    ) = RuntimeSnapshotItem(id, source, label, text)

    private companion object {
        const val EPOCH = "123e4567-e89b-42d3-a456-426614174000"
        const val OTHER_EPOCH = "123e4567-e89b-42d3-b456-426614174000"
        const val NOW = 1_000_000L
    }
}
