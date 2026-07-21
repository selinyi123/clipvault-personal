package com.clipvault.app.ime

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class FullKeyboardEditorActionSourceTest {
    private val sourceDir = Path.of("src", "main", "kotlin", "com", "clipvault", "app", "ime")

    @Test
    fun editorActionIsRefreshedForEveryInputSessionAndRenderedOnTheActionKey() {
        val source = read("ClipVaultFullKeyboardService.kt")
        val startInput = source.substringAfter("override fun onStartInput")
            .substringBefore("override fun onFinishInput")

        assertTrue(startInput.contains("editorAction = ImeEditorActionResolver.resolve("))
        assertTrue(startInput.contains("attribute?.imeOptions"))
        assertTrue(startInput.contains("if (::keys.isInitialized) renderKeys()"))
        assertTrue(source.contains("editorAction.keyLabel"))
        assertTrue(source.contains("editorAction.accessibilityLabel"))
        assertTrue(source.contains("performEditorAction = { actionId -> ic.performEditorAction(actionId) }"))
        assertTrue(source.contains("sendEnter = { sendEnterKeyEvent(ic) }"))
        assertTrue(source.contains("editorAction = ImeEditorAction.NEW_LINE"))
        assertFalse("EditorInfo must not be retained beyond onStartInput", source.contains("var editorInfo"))
        assertFalse("EditorInfo must not be queried outside the callback", source.contains("currentInputEditorInfo"))
        assertFalse(
            "InputConnection or EditorInfo must not be retained in a service field",
            Regex("""private\s+(?:lateinit\s+)?(?:val|var)\s+\w+\s*:\s*(?:InputConnection|EditorInfo)\b""")
                .containsMatchIn(source),
        )
        assertFalse("ordinary input context must not be observed", source.contains("getTextBeforeCursor"))
        assertFalse("ordinary input context must not be observed", source.contains("getTextAfterCursor"))
    }

    @Test
    fun specialKeysExposeTalkBackLabelsAndToggleState() {
        val source = read("ClipVaultFullKeyboardService.kt")

        listOf(
            "打开 ClipVault 候选",
            "切回上一个输入法",
            "大写",
            "删除",
            "符号键盘",
            "空格",
        ).forEach { label ->
            assertTrue("missing accessibility label: $label", source.contains("accessibilityLabel = \"$label\""))
        }
        assertTrue(source.contains("contentDescription = accessibilityLabel"))
        assertTrue(source.contains("active = shifted"))
        assertTrue(source.contains("active = symbols"))
        assertTrue(source.contains("isActivated = isActive"))
        assertTrue(source.contains("isSelected = isActive"))
        assertTrue(source.contains("ViewCompat.setStateDescription"))
        assertTrue(source.contains("if (isActive) \"已开启\" else \"已关闭\""))
    }

    private fun read(fileName: String): String {
        val path = sourceDir.resolve(fileName)
        assertTrue("source file is missing: $path", Files.isRegularFile(path))
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
