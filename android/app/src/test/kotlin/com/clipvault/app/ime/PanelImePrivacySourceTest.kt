package com.clipvault.app.ime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class PanelImePrivacySourceTest {
    private val panelSource: Path = Path.of(
        "src",
        "main",
        "kotlin",
        "com",
        "clipvault",
        "app",
        "ime",
        "ClipVaultPanelImeService.kt",
    )

    @Test
    fun saveClipboardChecksPrivacyBeforeClipboardReadAndBeforeRuntimeWrite() {
        val source = String(Files.readAllBytes(panelSource), Charsets.UTF_8)

        assertEquals(
            "Panel IME should have exactly one explicit-save runtime write path",
            1,
            Regex("""runtime\.saveExplicit\(""").findAll(source).count(),
        )

        val start = source.indexOf("private fun saveClipboard() {")
        assertTrue("saveClipboard() is missing", start >= 0)
        val end = source.indexOf("\n    private fun button", start)
        assertTrue("saveClipboard() boundary is missing", end > start)

        val body = source.substring(start, end)
        val token = body.indexOf("val token = privacySession.token()")
        val firstGuard = body.indexOf("if (!privacySession.allowsPersonalData(token)) return")
        val clipboardService = body.indexOf("getSystemService(Context.CLIPBOARD_SERVICE)")
        val primaryClip = body.indexOf(".primaryClip")
        val worker = body.indexOf("thread {")
        val secondGuard = body.indexOf("if (!privacySession.allowsPersonalData(token)) return@thread")
        val saveExplicit = body.indexOf("runtime.saveExplicit(")

        assertTrue("saveClipboard() must create a session token", token >= 0)
        assertTrue("privacy must be checked before reading clipboard service", firstGuard > token)
        assertTrue("clipboard service must be read only after the privacy guard", clipboardService > firstGuard)
        assertTrue("primaryClip must be read only after the privacy guard", primaryClip > firstGuard)
        assertTrue("runtime write must happen from the explicit-save worker", worker > primaryClip)
        assertTrue("worker must re-check privacy before writing", secondGuard > worker)
        assertTrue("saveExplicit must happen only after the worker privacy guard", saveExplicit > secondGuard)
    }
}
