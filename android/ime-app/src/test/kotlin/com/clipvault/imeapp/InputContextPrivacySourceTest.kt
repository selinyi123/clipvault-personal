package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class InputContextPrivacySourceTest {
    @Test
    fun `no suggestions fields disable personal and ClipVault candidates`() {
        val source = File(
            System.getProperty("user.dir"),
            "src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
        ).readText()

        val noSuggestions = source.indexOf(
            "val noSuggestions = (inputType and InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS) != 0",
        )
        val incognito = source.indexOf(
            "val incognito = sensitiveApp || noSuggestions ||",
            startIndex = noSuggestions,
        )
        val personal = source.indexOf(
            "val personal = !password && !incognito",
            startIndex = incognito,
        )

        assertTrue(noSuggestions >= 0)
        assertTrue(incognito > noSuggestions)
        assertTrue(personal > incognito)
        assertTrue(source.contains("return EngineInputContext(kind, incognito, personal, personal)"))
    }
}
