package com.clipvault.app.ime

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class ImeCandidatePrivacySourceTest {
    @Test
    fun panelImeRechecksPrivacyBeforeRuntimeCandidateRead() {
        assertRuntimeCandidateReadIsWorkerGuarded(
            fileName = "ClipVaultPanelImeService.kt",
            functionSignature = "private fun showCandidates(",
        )
    }

    @Test
    fun fullKeyboardRechecksPrivacyBeforeRuntimeCandidateRead() {
        assertRuntimeCandidateReadIsWorkerGuarded(
            fileName = "ClipVaultFullKeyboardService.kt",
            functionSignature = "private fun showCandidates()",
        )
    }

    private fun assertRuntimeCandidateReadIsWorkerGuarded(fileName: String, functionSignature: String) {
        val source = readImeSource(fileName)
        val start = source.indexOf(functionSignature)
        assertTrue("$functionSignature is missing in $fileName", start >= 0)

        val worker = source.indexOf("thread {", start)
        val runtimeRead = source.indexOf("runtime.listCandidates(", worker)
        val workerGuard = source.indexOf(
            "if (!privacySession.allowsPersonalData(token)) return@thread",
            worker,
        )
        val uiApply = source.indexOf("runOnMain {", runtimeRead)

        assertTrue("candidate worker is missing in $fileName", worker > start)
        assertTrue("runtime candidate read is missing in $fileName", runtimeRead > worker)
        assertTrue("worker privacy guard is missing in $fileName", workerGuard > worker)
        assertTrue(
            "worker must re-check privacy before reading Runtime candidates in $fileName",
            workerGuard < runtimeRead,
        )
        assertTrue("UI apply block should remain after Runtime read in $fileName", uiApply > runtimeRead)
    }

    private fun readImeSource(fileName: String): String {
        val path = Path.of(
            "src",
            "main",
            "kotlin",
            "com",
            "clipvault",
            "app",
            "ime",
            fileName,
        )
        assertTrue("source file is missing: $path", Files.isRegularFile(path))
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
