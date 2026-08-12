package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class CandidateLifecycleSourceTest {
    @Test
    fun `candidate actions are bound to the input generation and session`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
        ).readText()

        val render = source.indexOf("private fun renderCandidates()")
        val runtime = source.indexOf("private fun renderRuntimeCandidates()")
        val candidateGuard = source.indexOf("fun isCurrentSurface(): Boolean", startIndex = render)
        val candidateAction = source.indexOf("isCurrentSurface() &&", startIndex = render)
        val runtimeGuard = source.indexOf("boundGeneration == inputGeneration", startIndex = runtime)

        assertTrue(render >= 0)
        assertTrue(runtime > render)
        assertTrue(source.contains("val boundRevision = state.revision"))
        assertTrue(source.contains("boundGeneration == inputGeneration"))
        assertTrue(source.contains("boundSession == sessionId"))
        assertTrue(source.contains("boundRevision == state.revision"))
        assertTrue(candidateGuard in (render + 1) until runtime)
        assertTrue(candidateAction in (candidateGuard + 1) until runtime)
        assertTrue(source.contains("current.id == candidate.id && current.text == candidate.text"))
        assertTrue(runtimeGuard > runtime)
    }
}
