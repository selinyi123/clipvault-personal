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
        val candidateGuard = source.indexOf(
            "boundGeneration == inputGeneration && boundSession != null && boundSession == sessionId",
            startIndex = render,
        )
        val candidateAction = source.indexOf("if (isCurrentSurface()) select(candidate)", startIndex = render)
        val runtimeGuard = source.indexOf("boundGeneration == inputGeneration", startIndex = runtime)

        assertTrue(render >= 0)
        assertTrue(runtime > render)
        assertTrue(candidateGuard in (render + 1) until runtime)
        assertTrue(candidateAction in (candidateGuard + 1) until runtime)
        assertTrue(runtimeGuard > runtime)
    }
}
