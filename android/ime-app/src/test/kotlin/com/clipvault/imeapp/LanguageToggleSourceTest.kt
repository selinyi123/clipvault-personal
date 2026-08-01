package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class LanguageToggleSourceTest {
    @Test
    fun `daily keyboard exposes an explicit Chinese English mode switch`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
        ).readText()

        assertTrue(source.contains("accessibilityLabel = \"中英文切换\""))
        assertTrue(source.contains("if (!commitComposition()) return"))
        assertTrue(source.contains("if (context.fieldKind != EngineFieldKind.TEXT) return"))
        assertTrue(source.contains("context.fieldKind == EngineFieldKind.PASSWORD || forceEnglishMode"))
        assertTrue(source.contains("forceEnglishMode = !forceEnglishMode"))
        assertTrue(source.contains("startEngine(context)"))
    }
}
