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

    private fun item(id: String, source: String, kind: String): Candidate = Candidate(
        id = id,
        source = source,
        kind = kind,
        text = id,
        label = id,
        score = 0,
    )
}
