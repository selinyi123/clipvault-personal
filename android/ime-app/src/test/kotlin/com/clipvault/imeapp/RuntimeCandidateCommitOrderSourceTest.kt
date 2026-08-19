package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class RuntimeCandidateCommitOrderSourceTest {
    @Test
    fun `runtime candidate finalizes Rime composition before insertion`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
        ).readText()
        val guard = source.indexOf("state.preedit.isEmpty() || commitComposition()")
        val insertion = source.indexOf("commitText(boundText, 1)", startIndex = guard)

        assertTrue(guard >= 0)
        assertTrue(insertion > guard)
        assertTrue(source.contains("val boundPublisherEpoch = candidate.publisherEpoch"))
        assertTrue(source.contains("val boundSnapshotGeneration = candidate.snapshotGeneration"))
        assertTrue(source.contains("val boundCandidateId = candidate.candidateId"))
        assertTrue(source.contains("val boundText = candidate.text"))
        assertTrue(source.contains("val committed = currentInputConnection?.commitText(boundText, 1) == true"))
        assertTrue(source.contains("if (committed)"))
        assertTrue(source.indexOf("clearRuntimeSurface()", startIndex = insertion) > insertion)
    }
}
