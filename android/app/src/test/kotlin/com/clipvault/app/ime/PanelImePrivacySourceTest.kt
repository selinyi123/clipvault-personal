package com.clipvault.app.ime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path
import java.util.stream.Collectors

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
    private val imeSourceDir: Path = panelSource.parent

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

    @Test
    fun explicitSaveIsTheOnlyImeClipboardReadOrRuntimeWrite() {
        val stream = Files.walk(imeSourceDir)
        val sources = try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .map { it to String(Files.readAllBytes(it), Charsets.UTF_8) }
                .collect(Collectors.toList())
        } finally {
            stream.close()
        }

        val clipboardFiles = sources
            .filter { (_, text) ->
                listOf("ClipboardManager", "CLIPBOARD_SERVICE", ".primaryClip", ".getPrimaryClip")
                    .any { it in text }
            }
            .map { (path, _) -> path.fileName.toString() }
            .toSet()
        assertEquals(
            "Only the Panel IME explicit-save action may reach the clipboard",
            setOf(panelSource.fileName.toString()),
            clipboardFiles,
        )
        assertEquals(
            "IME package must have exactly one clipboard content read",
            1,
            sources.sumOf { (_, text) ->
                Regex("""(?:\.primaryClip\b|\.getPrimaryClip\s*\()""").findAll(text).count()
            },
        )
        assertEquals(
            "IME package must have exactly one explicit Runtime save",
            1,
            sources.sumOf { (_, text) -> Regex("""\bsaveExplicit\s*\(""").findAll(text).count() },
        )
        assertTrue(
            "IME package must never listen for clipboard changes",
            sources.none { (_, text) ->
                Regex("""\b(addPrimaryClipChangedListener|onPrimaryClipChanged)\b""")
                    .containsMatchIn(text)
            },
        )
        assertTrue(
            "IME package must not use deprecated or synthetic clipboard text reads",
            sources.none { (_, text) ->
                Regex("""\.getText\s*\(|\b(?:cm|clipboard|clipboardManager)\.text\b""")
                    .containsMatchIn(text)
            },
        )
    }
}
