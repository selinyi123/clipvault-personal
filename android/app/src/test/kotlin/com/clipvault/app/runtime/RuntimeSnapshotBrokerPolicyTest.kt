package com.clipvault.app.runtime

import com.clipvault.ime.engine.RuntimeSnapshotContract
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeSnapshotBrokerPolicyTest {
    @Test
    fun `broker filters a facade item above the V1 text ceiling`() {
        val oversized = candidate(
            id = "oversized",
            text = "x".repeat(RuntimeSnapshotContract.MAX_TEXT_UTF8_BYTES + 1),
        )
        val valid = candidate(id = "valid", text = "完整内容")

        val frame = RuntimeSnapshotBrokerPolicy.prepare(
            listOf(oversized, valid),
            RuntimeSnapshotContract.MAX_ITEMS,
        )

        assertEquals(listOf("完整内容"), frame.map { it.text })
        assertEquals(listOf("memory:valid"), frame.map { it.candidateId })
    }

    @Test
    fun `broker enforces item count and total Binder frame budget`() {
        val maximumText = "x".repeat(RuntimeSnapshotContract.MAX_TEXT_UTF8_BYTES)
        val frame = RuntimeSnapshotBrokerPolicy.prepare(
            List(20) { index -> candidate("id-$index", maximumText) },
            20,
        )

        assertTrue(frame.size <= RuntimeSnapshotContract.MAX_ITEMS)
        assertTrue(frame.size < RuntimeSnapshotContract.MAX_ITEMS)
        assertTrue(
            requireNotNull(RuntimeSnapshotContract.estimatedFrameBytes(frame)) <=
                RuntimeSnapshotContract.MAX_FRAME_BYTES,
        )
        assertTrue(frame.all { it.text.length == RuntimeSnapshotContract.MAX_TEXT_UTF8_BYTES })
    }

    @Test
    fun `broker maps sources and rejects duplicate transport IDs`() {
        val memory = candidate(id = "same", text = "memory")
        val duplicateMemory = candidate(id = "same", text = "duplicate")
        val clipboard = candidate(id = "same", text = "clipboard", source = "clip")

        val frame = RuntimeSnapshotBrokerPolicy.prepare(
            listOf(memory, duplicateMemory, clipboard),
            RuntimeSnapshotContract.MAX_ITEMS,
        )

        assertEquals(listOf("memory", "clipboard"), frame.map { it.source })
        assertEquals(listOf("memory:same", "clipboard:same"), frame.map { it.candidateId })
        assertEquals(listOf("memory", "clipboard"), frame.map { it.text })
    }

    private fun candidate(id: String, text: String, source: String = "memory"): Candidate = Candidate(
        id = id,
        source = source,
        kind = "phrase",
        text = text,
        label = "phrase",
        score = 1,
    )
}
