package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TouchBackspaceSourceTest {
    @Test
    fun deleteKeyUsesPressHoldReleaseAndCancelsAtEveryImeLifecycleBoundary() {
        val source = source()

        assertTrue(source.contains("MotionEvent.ACTION_DOWN"))
        assertTrue(source.contains("MotionEvent.ACTION_UP"))
        assertTrue(source.contains("MotionEvent.ACTION_CANCEL"))
        assertTrue(source.contains("backspaceRepeater.press()"))
        assertTrue(source.contains("backspaceRepeater.release()"))
        assertTrue(source.contains("override fun onFinishInputView"))
        assertFalse(source.contains("repeat(8) { backspace() }"))
        assertFalse(source.contains("setOnLongClickListener"))
    }

    private fun source() = File(requireNotNull(System.getProperty("user.dir")))
        .resolve("src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt")
        .readText()
}
