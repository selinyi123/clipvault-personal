package com.clipvault.app.ime

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class ImeCandidatePrivacySourceTest {
    @Test
    fun panelImeRechecksPrivacyBeforeRuntimeCandidateRead() {
        val fileName = "ClipVaultPanelImeService.kt"
        val source = readImeSource(fileName)
        val start = source.indexOf("private fun showCandidates(")
        assertTrue("showCandidates() is missing in $fileName", start >= 0)

        assertTrue(
            "candidate worker must have one execution thread",
            Regex("""private val candidateExecutor = ThreadPoolExecutor\(\s*1,\s*1,""")
                .containsMatchIn(source),
        )
        assertTrue("candidate worker queue must retain at most one superseding request", source.contains("LinkedBlockingQueue<Runnable>(1)"))
        assertTrue("new queued request must discard the older queued request", source.contains("ThreadPoolExecutor.DiscardOldestPolicy()"))

        val privacyToken = source.indexOf("val privacyToken = privacySession.token()", start)
        val requestToken = source.indexOf("candidateRequestGate::beginRequest", privacyToken)
        val worker = source.indexOf("candidateExecutor.execute {", start)
        val firstGuard = source.indexOf(
            "if (!isCandidateRequestCurrent(requestToken, privacyToken)) return@execute",
            worker,
        )
        val runtimeFacade = source.indexOf("val requestRuntime = runtime", firstGuard)
        val secondGuard = source.indexOf(
            "if (!isCandidateRequestCurrent(requestToken, privacyToken)) return@execute",
            firstGuard + 1,
        )
        val runtimeRead = source.indexOf("requestRuntime.listCandidates(", secondGuard)
        val postReadGuard = source.indexOf(
            "if (!isCandidateRequestCurrent(requestToken, privacyToken)) return@execute",
            runtimeRead,
        )
        val prePostGuard = source.indexOf(
            "if (!isCandidateRequestCurrent(requestToken, privacyToken)) return@execute",
            postReadGuard + 1,
        )
        val post = source.indexOf("runOnMain {", prePostGuard)
        val renderGuard = source.indexOf(
            "if (!isCandidateRequestCurrent(requestToken, privacyToken)) return@runOnMain",
            post,
        )
        val render = source.indexOf("list.removeAllViews()", renderGuard)

        assertTrue("request token must be created after its privacy token", requestToken > privacyToken)
        assertTrue("single-thread candidate executor is missing", worker > requestToken)
        assertTrue("request gate must run before Runtime facade access", firstGuard > worker && firstGuard < runtimeFacade)
        assertTrue("request gate must run after facade access and before read", secondGuard > runtimeFacade && secondGuard < runtimeRead)
        assertTrue("request gate must run after Runtime read", postReadGuard > runtimeRead)
        assertTrue("request gate must run immediately before the main-thread post", prePostGuard > postReadGuard)
        assertTrue("main-thread post must happen after the pre-post gate", post > prePostGuard)
        assertTrue("posted result must be gated before rendering", renderGuard > post && renderGuard < render)
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
