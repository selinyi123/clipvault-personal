package com.clipvault.app.runtime

import com.clipvault.app.data.ClipEntity
import com.clipvault.app.data.MemoryEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CandidateMixerTest {
    @Test
    fun memoryBeatsRawClipWhenNoQuery() {
        val clips = listOf(clip(id = "clip-1", text = "hello raw clipboard", timesSeen = 20))
        val memories = listOf(memory(kind = "phrase", text = "hello saved phrase", useCount = 1))

        val out = CandidateMixer.mix(clips, memories, query = "", limit = 10)

        assertEquals("memory", out.first().source)
        assertEquals("[memory:phrase]", out.first().label)
    }

    @Test
    fun pinnedClipCanBeatOrdinaryMemory() {
        val clips = listOf(clip(id = "clip-1", text = "deploy command", pinned = true, favorite = true, timesSeen = 50))
        val memories = listOf(memory(kind = "term", text = "deploy", useCount = 1))

        val out = CandidateMixer.mix(clips, memories, query = "", limit = 10)

        assertEquals("clip", out.first().source)
        assertEquals("[clip:command]", out.first().label)
    }

    @Test
    fun queryFiltersAndBoostsPrefixMatches() {
        val clips = listOf(
            clip(id = "clip-1", text = "ssh prod", contentType = "command"),
            clip(id = "clip-2", text = "random note", contentType = "text"),
        )
        val memories = listOf(memory(kind = "phrase", text = "ssh staging"))

        val out = CandidateMixer.mix(clips, memories, query = "ssh", limit = 10)

        assertTrue(out.all { it.text.contains("ssh", ignoreCase = true) || it.label.contains("ssh", ignoreCase = true) })
        assertEquals(listOf("memory", "clip"), out.map { it.source })
    }

    @Test
    fun orderingIsStableForEqualScores() {
        val clips = listOf(
            clip(id = "clip-b", text = "same", contentType = "text"),
            clip(id = "clip-a", text = "same", contentType = "text"),
        )

        val out = CandidateMixer.mix(clips, emptyList(), query = "", limit = 10)

        assertEquals(listOf("clip-a", "clip-b"), out.map { it.id })
    }

    @Test
    fun sourceCapKeepsMinoritySourceWhenOneFloods() {
        // v1.6 source caps: a flood of high-score pinned clips must not fully
        // starve memories (or vice versa).
        val clips = (0 until 12).map { clip(id = "clip-$it", text = "clip $it", pinned = true, timesSeen = 50) }
        val memories = (0 until 4).map { memory(kind = "term", text = "mem $it") }

        val out = CandidateMixer.mix(clips, memories, query = "", limit = 8)

        assertEquals(8, out.size)
        assertTrue(out.count { it.source == "memory" } >= 2)  // reserve = max(1, 8 / 4) = 2
        assertTrue(out.count { it.source == "clip" } >= 2)
    }

    @Test
    fun noSourceCapWhenEverythingFits() {
        val clips = listOf(clip(id = "clip-1", text = "deploy", pinned = true, timesSeen = 50))
        val memories = (0 until 3).map { memory(kind = "term", text = "m$it") }

        val out = CandidateMixer.mix(clips, memories, query = "", limit = 10)

        assertEquals(4, out.size)
        assertEquals("clip", out.first().source)  // pinned clip leads; no cap reshuffle
    }

    @Test
    fun legacySecretMemoryNeverBecomesCandidate() {
        val memories = listOf(
            memory(kind = "term", text = "safe term"),
            memory(kind = "term", text = "AKIAIOSFODNN7EXAMPLE"),
        )

        val out = CandidateMixer.mix(emptyList(), memories, query = "", limit = 10)

        assertEquals(listOf("safe term"), out.map { it.text })
    }

    private fun clip(
        id: String,
        text: String,
        contentType: String = "command",
        pinned: Boolean = false,
        favorite: Boolean = false,
        timesSeen: Int = 1,
    ) = ClipEntity(
        id = id,
        content = text,
        contentHash = "hash-$id",
        contentType = contentType,
        isSecret = false,
        secretLevel = null,
        secretReasons = "[]",
        sourceDevice = "test",
        sourceApp = null,
        createdAt = "2026-06-21T00:00:00Z",
        lastSeenAt = "2026-06-21T00:00:00Z",
        timesSeen = timesSeen,
        pinned = pinned,
        favorite = favorite,
        deleted = false,
    )

    private fun memory(
        kind: String,
        text: String,
        pinned: Boolean = false,
        useCount: Int = 1,
    ) = MemoryEntity(
        kind = kind,
        text = text,
        label = null,
        pinned = pinned,
        useCount = useCount,
        source = "manual",
        deleted = false,
    )
}
