package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class InlineSuggestionLifecycleSourceTest {
    @Test
    fun `editor transitions clear protected inline suggestion views`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
        ).readText()

        assertTrue(source.contains("private fun clearInlineSurface()"))
        assertTrue(source.contains("inlineGeneration += 1"))
        assertTrue(source.contains("inlineHost?.removeAllViews()"))
        assertTrue(source.contains("inlineHost?.visibility = View.GONE"))

        val startInput = source.indexOf("override fun onStartInput")
        val finishInput = source.indexOf("override fun onFinishInput")
        val destroy = source.indexOf("override fun onDestroy")
        assertTrue(source.indexOf("clearInlineSurface()", startInput) in (startInput + 1) until finishInput)
        assertTrue(source.indexOf("clearInlineSurface()", finishInput) in (finishInput + 1) until destroy)
        assertTrue(source.indexOf("clearInlineSurface()", destroy) > destroy)
    }
}
