package com.clipvault.app.ime

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InlineSuggestionSourceTest {
    @Test
    fun `full IME declares and hosts system owned inline suggestions`() {
        val projectDir = File(System.getProperty("user.dir"))
        val config = projectDir.resolve("src/main/res/xml/ime_full_config.xml").readText()
        val service = projectDir.resolve(
            "src/main/kotlin/com/clipvault/app/ime/ClipVaultFullKeyboardService.kt",
        ).readText()

        assertTrue(config.contains("android:supportsInlineSuggestions=\"true\""))
        assertTrue(service.contains("onCreateInlineSuggestionsRequest"))
        assertTrue(service.contains("onInlineSuggestionsResponse"))
        assertTrue(service.contains("suggestion.inflate("))
        assertFalse(service.contains("suggestion.info"))
        assertFalse(service.contains("getAutofillId"))
        assertFalse(service.contains("findViewById<TextView>"))
    }
}
