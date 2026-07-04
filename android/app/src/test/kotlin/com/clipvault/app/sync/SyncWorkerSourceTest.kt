package com.clipvault.app.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class SyncWorkerSourceTest {
    @Test
    fun workerStopsImmediateRetriesAndClearsTokenOnAuthFailure() {
        val src = readSource("SyncWorker.kt")

        val authCatch = src.indexOf("catch (e: SyncAuthException)")
        val genericCatch = src.indexOf("catch (e: Exception)")

        assertTrue("SyncAuthException catch is missing", authCatch >= 0)
        assertTrue("auth failures must be handled before generic retry catch", authCatch < genericCatch)

        val authBlock = src.substring(authCatch, genericCatch)
        assertTrue(authBlock.contains("s.token = null"))
        assertTrue(authBlock.contains("Result.success()"))
        assertTrue(authBlock.contains("\"sync auth failed\""))
        assertFalse(authBlock.contains("e.message"))
    }

    @Test
    fun clientClassifies401And403BeforeReturningRetrySentinels() {
        val src = readSource("Sync.kt")

        assertTrue(src.contains("internal class SyncAuthException : IOException(\"sync auth rejected\")"))
        assertTrue(src.contains("statusCode == HttpURLConnection.HTTP_UNAUTHORIZED"))
        assertTrue(src.contains("statusCode == HttpURLConnection.HTTP_FORBIDDEN"))
        assertTrue(src.contains("if (isPermanentSyncAuthFailure(code)) throw SyncAuthException()"))
        assertTrue(src.contains("val stream = if (code in 200..299) c.inputStream else c.errorStream"))
        assertTrue(src.contains("?: \"\""))
    }

    private fun readSource(fileName: String): String {
        val path = Path.of(
            "src",
            "main",
            "kotlin",
            "com",
            "clipvault",
            "app",
            "sync",
            fileName,
        )
        assertTrue("source file is missing: $path", Files.isRegularFile(path))
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
