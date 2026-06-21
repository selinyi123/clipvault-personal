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
