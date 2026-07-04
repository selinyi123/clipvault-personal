package com.clipvault.app.capture

import com.clipvault.app.data.ClipEntity
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureResultTest {
    @Test
    fun rejectedCaptureIsNotStoredAndDoesNotRequestSyncPush() {
        val result = Capture.Result(Capture.Status.REJECTED, null)

        assertFalse(result.didStoreLocally)
        assertFalse(result.shouldRequestSyncPush)
    }

    @Test
    fun duplicateCaptureIsLocalOnlyBecauseNoOutboxEventWasCreated() {
        val result = Capture.Result(Capture.Status.DUPLICATE, publicClip())

        assertTrue(result.didStoreLocally)
        assertFalse(result.shouldRequestSyncPush)
    }

    @Test
    fun newSecretCaptureIsStoredButNotSynced() {
        val result = Capture.Result(Capture.Status.NEW, publicClip(isSecret = true))

        assertTrue(result.didStoreLocally)
        assertFalse(result.shouldRequestSyncPush)
    }

    @Test
    fun newPublicCaptureRequestsSyncPush() {
        val result = Capture.Result(Capture.Status.NEW, publicClip())

        assertTrue(result.didStoreLocally)
        assertTrue(result.shouldRequestSyncPush)
    }

    private fun publicClip(isSecret: Boolean = false) = ClipEntity(
        id = "clip-1",
        content = if (isSecret) "AKIAIOSFODNN7EXAMPLE" else "hello",
        contentHash = if (isSecret) "hash-secret" else "hash-public",
        contentType = "text",
        isSecret = isSecret,
        secretLevel = if (isSecret) "provider_key" else null,
        secretReasons = if (isSecret) """["SG-AWS"]""" else "[]",
        sourceDevice = "test",
        sourceApp = null,
        createdAt = "2026-07-04T00:00:00Z",
        lastSeenAt = "2026-07-04T00:00:00Z",
        timesSeen = 1,
    )
}
