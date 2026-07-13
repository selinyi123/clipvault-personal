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
        assertTrue(src.contains("internal fun shouldReadSyncResponseBody(statusCode: Int, auth: Boolean): Boolean"))
        assertTrue(src.contains("if (isPermanentSyncAuthFailure(code)) throw SyncAuthException()"))
        assertTrue(src.contains("if (code == 413) throw SyncPushRequestTooLargeException()"))
        assertTrue(src.contains("val stream = if (code in 200..299) c.inputStream else c.errorStream"))
        assertTrue(src.contains("if (shouldReadSyncResponseBody(code, auth))"))
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
    fun immediateSyncDoesNotCancelInFlightUniqueWork() {
        val src = readSource("SyncWorker.kt")

        val method = src.indexOf("fun requestPush(context: Context)")
        val bestEffort = src.indexOf("fun requestPushBestEffort(context: Context)", method)
        assertTrue("requestPush is missing", method >= 0)
        assertTrue("requestPush boundary is missing", bestEffort > method)

        val body = src.substring(method, bestEffort)
        assertTrue(body.contains("enqueueUniqueWork(\"sync-now\", ExistingWorkPolicy.APPEND_OR_REPLACE, req)"))
        assertFalse(
            "Immediate sync must not cancel in-flight push/pull work on bursty explicit saves",
            body.contains("ExistingWorkPolicy.REPLACE"),
        )
    }

    @Test
    fun workerBuildsBoundedPushBatchesBeforeCallingClientPush() {
        val src = readSource("SyncWorker.kt")

        assertTrue(src.contains("private const val MAX_JSON_ESCAPED_BYTES_PER_CLIP_BYTE = 6"))
        assertTrue(src.contains("private const val MAX_SYNC_EVENT_ENVELOPE_BYTES = 64 * 1024"))
        assertTrue(src.contains("private const val SYNC_OUTBOX_BATCH_LIMIT = 8"))
        assertTrue(src.contains("Normalize.DEFAULT_MAX_CLIP_BYTES * MAX_JSON_ESCAPED_BYTES_PER_CLIP_BYTE"))
        assertTrue(src.contains("internal fun buildSyncPushBatch("))
        assertTrue(src.contains("maxRequestBytes: Int = MAX_SYNC_PUSH_REQUEST_BYTES"))
        assertTrue(src.contains("SyncPushBlockReason.EVENT_TOO_LARGE"))
        assertTrue(src.contains("internal fun drainSyncOutbox("))
        assertTrue(src.contains("var pushBatch = buildSyncPushBatch(candidateRows, deviceId, maxRequestBytes)"))
        assertTrue(src.contains("acked = push(pushBatch.events)"))
        assertTrue(src.contains("if (pushBatch.sourceCount <= 1)"))
        assertTrue(src.contains("candidateRows.take(maxOf(1, pushBatch.sourceCount / 2))"))
        assertTrue(src.contains("if (acked < 0) return false"))
        assertTrue(src.contains("if (acked < pushBatch.maxSeq) return false"))
        assertTrue(src.contains("if (pushBatch.sourceCount < batch.size) continue"))
        assertTrue(src.contains("metadata = outbox.batchMetadata(SYNC_OUTBOX_BATCH_LIMIT)"))
        assertTrue(src.contains("readChunk = outbox::payloadChunk"))
        assertFalse(src.contains("db.outbox().batch(SYNC_OUTBOX_BATCH_LIMIT)"))
        assertTrue(src.contains("push = { events -> client.push(events) }"))

        val loop = src.substring(src.indexOf("pushPhase = {"), src.indexOf("pullPhase = {"))
        assertFalse(
            "SyncWorker must not return to count-only JSON batching before /sync/push",
            loop.contains("val events = JSONArray()"),
        )
        assertFalse(
            "SyncWorker must not call /sync/push with an unbudgeted count-only batch",
            loop.contains("val acked = client.push(events)"),
        )
    }

    @Test
    fun permanentlyUnrepresentableOutboxRowUsesDurableSafeMarkerAndStillPulls() {
        val worker = readSource("SyncWorker.kt")
        val sync = readSource("Sync.kt")

        assertTrue(worker.contains("internal fun runSyncCycle("))
        assertTrue(worker.contains("val sameBlockedHead = blocked != null && blocked.seq == headSeq"))
        assertTrue(worker.contains("if (!sameBlockedHead)"))
        assertTrue(worker.contains("persistBlocked(SyncPushBlockedState(e.seq, e.reason))"))
        assertTrue(worker.contains("val pullComplete = pullPhase()"))
        assertTrue(worker.contains("firstPendingSeq = { db.outbox().firstSeq() }"))
        assertFalse(worker.contains("firstPendingSeq = { db.outbox().batch(1)"))
        assertTrue(worker.contains("readBlocked = { s.syncPushBlocked }"))
        assertTrue(worker.contains("clearBlocked = { s.clearSyncPushBlocked() }"))
        assertTrue(worker.contains("pullPhase = { pullAll(db, s, client) }"))
        assertFalse(worker.contains("Result.failure()"))

        val markerStart = sync.indexOf("internal fun markSyncPushBlocked")
        val markerEnd = sync.indexOf("internal fun clearSyncPushBlocked", markerStart)
        assertTrue("durable blocked marker method is missing", markerStart >= 0)
        assertTrue("durable blocked marker boundary is missing", markerEnd > markerStart)
        val markerBody = sync.substring(markerStart, markerEnd)
        assertTrue(markerBody.contains("putLong(PUSH_BLOCKED_SEQ, state.seq)"))
        assertTrue(markerBody.contains("putString(PUSH_BLOCKED_REASON, state.reason.code)"))
        assertTrue(markerBody.contains(".commit()"))
        assertFalse(markerBody.contains("payload"))
        assertFalse(markerBody.contains("content"))
    }

    @Test
    fun allPairingEntrypointsClearTokenAndOldBlockedStateBeforeFreshToken() {
        val src = readSource("Sync.kt")

        val replaceTokenStart = src.indexOf("internal fun replaceToken(token: String)")
        val installStart = src.indexOf("private fun installFreshToken(token: String)", replaceTokenStart)
        val replacePairingStart = src.indexOf("fun replacePairing(host: String, token: String)", installStart)
        val migrateStart = src.indexOf("private fun migrateLegacyToken()", replacePairingStart)
        assertTrue(replaceTokenStart >= 0)
        assertTrue(installStart > replaceTokenStart)
        assertTrue(replacePairingStart > installStart)
        assertTrue(migrateStart > replacePairingStart)

        val replaceToken = src.substring(replaceTokenStart, installStart)
        val install = src.substring(installStart, replacePairingStart)
        val replacePairing = src.substring(replacePairingStart, migrateStart)
        assertTrue(replaceToken.indexOf("tokenStore.set(null)") < replaceToken.indexOf("installFreshToken(token)"))
        assertTrue(install.indexOf("clearSyncPushBlocked()") < install.indexOf("tokenStore.set(token)"))
        assertTrue(replacePairing.indexOf("tokenStore.set(null)") < replacePairing.indexOf(".commit()"))
        assertTrue(replacePairing.indexOf(".commit()") < replacePairing.indexOf("installFreshToken(token)"))
        assertTrue(replacePairing.contains("throw IOException(\"pairing state write failed\")"))

        val legacyPairStart = src.indexOf("fun pair(code: String): Boolean")
        val pairWithHostStart = src.indexOf("fun pairWithHost(host: String, code: String): Boolean", legacyPairStart)
        val legacyPair = src.substring(legacyPairStart, pairWithHostStart)
        assertTrue(legacyPair.contains("s.replaceToken(token)"))
        assertFalse(legacyPair.contains("s.token = token"))
    }

    @Test
    fun clearingBearerTokenIsSynchronousAndFailClosed() {
        val src = readSource("Sync.kt")
        val storeStart = src.indexOf("private class SecureTokenStore")
        val setStart = src.indexOf("fun set(value: String?)", storeStart)
        val cipherStart = src.indexOf("val cipher = Cipher.getInstance", setStart)
        assertTrue(storeStart >= 0)
        assertTrue(setStart > storeStart)
        assertTrue(cipherStart > setStart)

        val nullBranch = src.substring(setStart, cipherStart)
        assertTrue(nullBranch.contains("remove(TOKEN_IV).remove(TOKEN_CT).commit()"))
        assertTrue(nullBranch.contains("if (!cleared) throw IOException(\"token clear failed\")"))
        assertFalse(nullBranch.contains(".apply()"))
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
