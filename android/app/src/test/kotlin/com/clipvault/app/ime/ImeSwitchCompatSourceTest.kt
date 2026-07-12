package com.clipvault.app.ime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class ImeSwitchCompatSourceTest {
    private val sourceDir = Path.of("src", "main", "kotlin", "com", "clipvault", "app", "ime")

    @Test
    fun api28OnlyPreviousImeCallIsGuardedAndAndroid8HasPickerFallback() {
        val source = read("ImeSwitchCompat.kt")
        val apiGuard = source.indexOf("Build.VERSION.SDK_INT >= Build.VERSION_CODES.P")
        val previousIme = source.indexOf("switchToPreviousInputMethod()")
        val pickerFallback = source.indexOf("showInputMethodPicker()")

        assertTrue("previous-IME API must be guarded by SDK 28", apiGuard >= 0)
        assertTrue("API 28-only call must remain inside the guarded branch", previousIme > apiGuard)
        assertTrue("Android 8.0/8.1 must fall back to the system IME picker", pickerFallback > previousIme)
        assertEquals(
            "compat helper should contain exactly one direct API 28-only call",
            1,
            Regex("""\bswitchToPreviousInputMethod\(\)""").findAll(source).count(),
        )
    }

    @Test
    fun bothImeServicesUseTheCompatPath() {
        listOf("ClipVaultPanelImeService.kt", "ClipVaultFullKeyboardService.kt").forEach { fileName ->
            val source = read(fileName)
            assertTrue(
                "$fileName must use the API-safe IME switch helper",
                source.contains("switchToPreviousInputMethodCompat()"),
            )
            assertTrue(
                "$fileName must not call the API 28-only method directly",
                !Regex("""\bswitchToPreviousInputMethod\(\)""").containsMatchIn(source),
            )
        }
    }

    private fun read(fileName: String): String {
        val path = sourceDir.resolve(fileName)
        assertTrue("source file is missing: $path", Files.isRegularFile(path))
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
