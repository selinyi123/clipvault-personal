package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Guards the ENG2 at-most-once rule at the Android editor boundary. */
class InputConnectionAmbiguousResultSourceTest {
    @Test
    fun `ambiguous editor result retires the session without raw fallback retry`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
        ).readText()
        val applied = source.indexOf("val applied = applyTransition(previous, transition)")
        val ambiguous = source.indexOf("if (!applied)", startIndex = applied)
        val updateHistory = source.indexOf(
            "updateCompositionHistory(kind, event, transition.state)",
            startIndex = ambiguous,
        )
        val ambiguousBlock = source.substring(ambiguous, updateHistory)

        assertTrue(applied >= 0)
        assertTrue(ambiguous > applied)
        assertTrue(updateHistory > ambiguous)
        assertTrue(ambiguousBlock.contains("retireEngineAfterAmbiguousEditorResult()"))
        assertFalse(ambiguousBlock.contains("hotFallback("))
        assertTrue(source.contains("InputConnection reports a potentially ambiguous editor effect"))
    }
}
