package com.clipvault.app.ime

import com.clipvault.app.runtime.Candidate
import org.junit.Assert.assertEquals
import org.junit.Test

class PanelCandidateTabsTest {
    @Test
    fun filtersBySourceAndKindBeforeApplyingLimit() {
        val candidates = listOf(
            item("a", "source_a", "kind_a"),
            item("b", "source_b", "kind_b"),
            item("c", "source_b", "kind_b"),
            item("d", "source_b", "kind_b"),
        )

        val result = PanelCandidateTabs.filter(
            candidates = candidates,
            source = "source_b",
            kind = "kind_b",
            limit = 2,
        )

        assertEquals(listOf("b", "c"), result.map { it.id })
    }

    @Test
    fun recentTabKeepsClipsAndDropsMemory() {
        // Manual QA (Panel #3): the Recent tab (source=clip) shows clips only.
        val candidates = listOf(
            item("clip1", "clip", "text"),
            item("mem1", "memory", "term"),
        )
        val result = PanelCandidateTabs.filter(candidates, source = "clip", kind = null, limit = 40)
        assertEquals(listOf("clip1"), result.map { it.id })
    }

    @Test
    fun memoryTabsFilterToTheirOwnKind() {
        // Manual QA (Panel #4): each memory tab shows only its own kind.
        val candidates = listOf(
            item("clip1", "clip", "text"),
            item("term1", "memory", "term"),
            item("phrase1", "memory", "phrase"),
            item("prompt1", "memory", "prompt"),
            item("command1", "memory", "command"),
        )
        for (kind in listOf("term", "phrase", "prompt", "command")) {
            val result = PanelCandidateTabs.filter(candidates, source = "memory", kind = kind, limit = 40)
            assertEquals(listOf("${kind}1"), result.map { it.id })
        }
    }

    private fun item(id: String, source: String, kind: String): Candidate = Candidate(
        id = id,
        source = source,
        kind = kind,
        text = id,
        label = id,
        score = 0,
    )
}
