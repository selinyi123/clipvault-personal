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

    @Test
    fun bestEffortSyncSchedulingDoesNotThrowAfterLocalCaptureSucceeds() {
        val src = readSource("SyncWorker.kt")

        val method = src.indexOf("fun requestPushBestEffort(context: Context): Boolean")
        val schedulePeriodic = src.indexOf("fun schedulePeriodic(context: Context)", method)
        assertTrue("requestPushBestEffort is missing", method >= 0)
        assertTrue("requestPushBestEffort boundary is missing", schedulePeriodic > method)

        val body = src.substring(method, schedulePeriodic)
        assertTrue(body.contains("return try"))
        assertTrue(body.contains("requestPush(context)"))
        assertTrue(body.contains("true"))
        assertTrue(body.contains("catch (e: Exception)"))
        assertTrue(body.contains("\"sync schedule failed: ${'$'}{e.javaClass.simpleName}\""))
        assertTrue(body.contains("false"))
        assertFalse("scheduler logs must not include raw exception messages", body.contains("e.message"))
    }

    @Test
    fun replacementPairingStoresHostSynchronouslyBeforeFreshToken() {
        val src = readSource("Sync.kt")

        val method = src.indexOf("fun replacePairing(host: String, token: String)")
        val nextMethod = src.indexOf("private fun migrateLegacyToken()", method)
        assertTrue("replacePairing is missing", method >= 0)
        assertTrue("replacePairing boundary is missing", nextMethod > method)

        val body = src.substring(method, nextMethod)
        val clearToken = body.indexOf("tokenStore.set(null)")
        val commitHost = body.indexOf(".commit()")
        val failedWrite = body.indexOf("throw IOException(\"pairing state write failed\")")
        val freshToken = body.lastIndexOf("tokenStore.set(token)")

        assertTrue("replacePairing must clear the old token first", clearToken >= 0)
        assertTrue("host write must use synchronous commit", commitHost > clearToken)
        assertTrue("host write failure must fail closed before the fresh token is stored", failedWrite > commitHost)
        assertTrue("fresh token must be stored only after the host commit succeeds", freshToken > failedWrite)
        assertFalse(
            "replacePairing must not rely on async SharedPreferences.apply() before storing the fresh token",
            body.substring(clearToken, freshToken).contains(".apply()"),
        )
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
