package com.clipvault.app.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class SyncWorkerSourceTest {
    @Test
    fun workerStopsImmediateRetriesAfterClientConditionallyClearsRejectedToken() {
        val src = readSource("SyncWorker.kt")
        val client = readSource("Sync.kt")

        val authCatch = src.indexOf("catch (e: SyncAuthException)")
        val genericCatch = src.indexOf("catch (e: Exception)")

        assertTrue("SyncAuthException catch is missing", authCatch >= 0)
        assertTrue("auth failures must be handled before generic retry catch", authCatch < genericCatch)

        val authBlock = src.substring(authCatch, genericCatch)
        assertFalse("a late old-peer rejection must not clear a fresh token", authBlock.contains("s.token = null"))
        assertTrue(authBlock.contains("Result.success()"))
        assertTrue(authBlock.contains("\"sync auth failed\""))
        assertFalse(authBlock.contains("e.message"))
        assertTrue(client.contains("s.clearTokenIfCurrent(snapshot)"))
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
        assertTrue(worker.contains("pullPhase = { pullAll(db, s, client, cycleSnapshot) }"))
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
        val replaceTokenIfCurrentStart = src.indexOf("internal fun replaceTokenIfCurrent", replaceTokenStart)
        val installStart = src.indexOf("private fun installFreshToken(token: String)", replaceTokenIfCurrentStart)
        val replacePairingStart = src.indexOf("fun replacePairing(host: String, token: String)", installStart)
        val migrateStart = src.indexOf("private fun migrateLegacyToken()", replacePairingStart)
        assertTrue(replaceTokenStart >= 0)
        assertTrue(replaceTokenIfCurrentStart > replaceTokenStart)
        assertTrue(installStart > replaceTokenIfCurrentStart)
        assertTrue(replacePairingStart > installStart)
        assertTrue(migrateStart > replacePairingStart)

        val replaceToken = src.substring(replaceTokenStart, replaceTokenIfCurrentStart)
        val replaceTokenIfCurrent = src.substring(replaceTokenIfCurrentStart, installStart)
        val install = src.substring(installStart, replacePairingStart)
        val replacePairing = src.substring(replacePairingStart, migrateStart)
        assertTrue(replaceToken.contains("pairingGate.replace"))
        assertTrue(replaceToken.indexOf("clearStoredToken()") < replaceToken.indexOf("installFreshToken(token)"))
        assertTrue(replaceTokenIfCurrent.contains("pairingGate.replacePairingIfCurrent"))
        assertTrue(install.indexOf("clearSyncPushBlocked()") < install.indexOf("tokenStore.set(token)"))
        assertTrue(replacePairing.contains("pairingGate.replace"))
        assertTrue(replacePairing.indexOf("clearStoredToken()") < replacePairing.indexOf(".commit()"))
        assertTrue(replacePairing.indexOf(".commit()") < replacePairing.indexOf("installFreshToken(token)"))
        assertTrue(replacePairing.contains("throw IOException(\"pairing state write failed\")"))

        val legacyPairStart = src.indexOf("fun pair(code: String): Boolean")
        val pairWithHostStart = src.indexOf("fun pairWithHost(host: String, code: String): Boolean", legacyPairStart)
        val legacyPair = src.substring(legacyPairStart, pairWithHostStart)
        val hostPairEnd = src.indexOf("private fun requestPairToken", pairWithHostStart)
        val hostPair = src.substring(pairWithHostStart, hostPairEnd)
        assertTrue(legacyPair.contains("s.replaceTokenIfCurrent("))
        assertTrue(legacyPair.contains("s.finishPairingIfCurrent(redemption.request)"))
        assertTrue(hostPair.contains("s.replacePairingIfCurrent("))
        assertTrue(hostPair.contains("redemption.serverDevice"))
        assertTrue(hostPair.contains("s.finishPairingIfCurrent(redemption.request)"))
        assertFalse(legacyPair.contains("s.token = token"))
    }

    @Test
    fun authenticatedRequestsUseOneSnapshotAndReleaseGateBeforeNetworkIo() {
        val src = readSource("Sync.kt")
        val reqStart = src.indexOf("private fun req(")
        val pairStart = src.indexOf("fun pair(code: String): Boolean", reqStart)
        assertTrue(reqStart >= 0)
        assertTrue(pairStart > reqStart)
        val req = src.substring(reqStart, pairStart)

        val snapshot = req.indexOf("val snapshot = requestOverride ?: fixedSnapshot ?: s.requestSnapshot(hostOverride, auth)")
        val openConnection = req.indexOf("URL(snapshot.baseUrl + path).openConnection()")
        assertTrue(snapshot >= 0)
        assertTrue(openConnection > snapshot)
        assertTrue(req.contains("snapshot.bearerToken?.let"))
        assertTrue(req.contains("if (!s.clearTokenIfCurrent(snapshot)) throw SyncPairingChangedException()"))
        assertTrue(req.contains("if (auth && snapshot.bearerToken.isNullOrEmpty()) throw SyncAuthException()"))
        assertFalse(req.contains("s.host"))
        assertFalse(req.contains("s.port"))
        assertFalse(req.contains("s.token"))

        val snapshotStart = src.indexOf("internal fun requestSnapshot(hostOverride: String?, auth: Boolean)")
        val clearStart = src.indexOf("internal fun clearTokenIfCurrent", snapshotStart)
        val snapshotBody = src.substring(snapshotStart, clearStart)
        assertTrue(snapshotBody.contains("auth && hostOverride != null && host != storedHost"))
        assertTrue(snapshotBody.contains("authenticated sync host override rejected"))
        assertTrue(snapshotBody.contains("if (auth && bearerToken.isNullOrEmpty()) throw SyncAuthException()"))
        assertTrue(snapshotBody.contains("pairingGate.authenticatedSnapshot(read)"))
        assertTrue(snapshotBody.contains("ClipVaultApp.db(appCtx).outbox().pairingBaseSeq()"))

        val pairTokenStart = src.indexOf("private fun requestPairToken(code: String)")
        val pushStart = src.indexOf("fun push(events: JSONArray)", pairTokenStart)
        assertTrue(pairTokenStart >= 0)
        assertTrue(pushStart > pairTokenStart)
        val pairTokenBody = src.substring(pairTokenStart, pushStart)
        assertTrue(pairTokenBody.contains("val pairingSnapshot = s.beginPairingSnapshot(hostOverride)"))
        assertTrue(pairTokenBody.contains(".put(\"outbox_base_seq\", outboxBaseSeq)"))
        assertTrue(pairTokenBody.contains("parsePairingResponse(response.text, outboxBaseSeq)"))
        assertTrue(pairTokenBody.contains("pairingSnapshot.pairingDeviceId"))

        val deviceStart = src.indexOf("private fun readOrCreateDeviceId()")
        val blockedStart = src.indexOf("internal fun markSyncPushBlocked", deviceStart)
        val deviceBody = src.substring(deviceStart, blockedStart)
        assertTrue(deviceBody.contains("putString(\"device_id\", id).commit()"))
        assertTrue(deviceBody.contains("throw IOException(\"sync device identity write failed\")"))
    }

    @Test
    fun workerPinsOnePairingAndGuardsEveryResponseSideEffect() {
        val worker = readSource("SyncWorker.kt")

        assertTrue(worker.contains("val cycleSnapshot = s.requestSnapshot(hostOverride = null, auth = true)"))
        assertTrue(worker.contains("val client = SyncClient(s, cycleSnapshot)"))
        assertTrue(worker.contains("runIfCurrentOrThrow(s, cycleSnapshot) { s.markSyncPushBlocked(state) }"))
        assertTrue(worker.contains("runIfCurrentOrThrow(s, cycleSnapshot) { s.clearSyncPushBlocked() }"))
        assertTrue(worker.contains("runIfCurrentOrThrow(s, cycleSnapshot) { outbox.clearUpTo(seq) }"))
        assertTrue(worker.contains("pullAll(db, s, client, cycleSnapshot)"))
        assertTrue(worker.contains("runIfCurrentOrThrow(s, cycleSnapshot)"))
        assertTrue(worker.contains("SyncApply.applyEvents(db, events)"))
        assertTrue(worker.contains("s.sinceSeq = nextSince"))
        assertTrue(worker.contains("catch (e: SyncPairingChangedException)"))
    }

    @Test
    fun processPairingGateIsGuardedBySingleProcessAndroidTopology() {
        val manifest = readAppFile("src", "main", "AndroidManifest.xml")
        val build = readAppFile("build.gradle.kts")

        assertFalse(manifest.contains("android:process"))
        assertFalse(manifest.contains("android:isolatedProcess"))
        assertFalse(build.contains("work-multiprocess"))
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

        assertTrue(src.contains("private const val LEGACY_TOKEN_MIGRATED = \"token_migrated_v1\""))
        val clearStoredStart = src.indexOf("private fun clearStoredToken()")
        val migrateStart = src.indexOf("private fun migrateLegacyToken()", clearStoredStart)
        val clearStored = src.substring(clearStoredStart, migrateStart)
        assertTrue(clearStored.indexOf("putBoolean(LEGACY_TOKEN_MIGRATED, true)") < clearStored.indexOf("tokenStore.set(null)"))
        assertTrue(clearStored.contains(".commit()"))
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

    private fun readAppFile(first: String, vararg more: String): String {
        val path = Path.of(first, *more)
        assertTrue("app file is missing: $path", Files.isRegularFile(path))
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
