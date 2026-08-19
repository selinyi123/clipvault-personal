package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class PhysicalKeyboardSourceTest {
    @Test
    fun `physical keys enter the same V2 engine path`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
        ).readText()

        assertTrue(source.contains("override fun onKeyDown"))
        assertTrue(source.contains("KEYCODE_DEL -> backspace()"))
        assertTrue(source.contains("input(String(Character.toChars(unicode)))"))
        assertTrue(source.contains("page(PageDirection.NEXT)"))
        assertTrue(source.contains("cancelComposition()"))
        assertTrue(source.contains("-> enter()"))
        assertTrue(source.contains("private fun enter(): Boolean"))
        assertTrue(source.contains("if (!commitComposition()) return false"))
        assertTrue(source.contains("if (!down) return false"))
        assertTrue(source.contains("return consumed || super.onKeyDown(keyCode, event)"))
    }
}
