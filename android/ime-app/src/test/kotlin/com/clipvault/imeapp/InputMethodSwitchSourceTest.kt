package com.clipvault.imeapp

import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Test

class InputMethodSwitchSourceTest {
    @Test
    fun keyboardHasAnExplicitSystemImeSwitchWithPickerFallback() {
        val source = File(requireNotNull(System.getProperty("user.dir")))
            .resolve("src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt")
            .readText()

        assertTrue(source.contains("accessibilityLabel = \"切换输入法\""))
        assertTrue(source.contains("switchToNextInputMethod(false)"))
        assertTrue(source.contains("showInputMethodPicker()"))
    }
}
