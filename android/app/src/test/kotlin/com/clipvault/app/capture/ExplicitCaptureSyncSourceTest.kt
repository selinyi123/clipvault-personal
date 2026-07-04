package com.clipvault.app.capture

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class ExplicitCaptureSyncSourceTest {
    @Test
    fun runtimeSaveSchedulesSyncOnlyForPublicOutboxResults() {
        val source = readSource("runtime", "ClipVaultFacade.kt")

        val ingest = source.indexOf("val result = Capture.ingest(")
        val gate = source.indexOf("if (result.shouldRequestSyncPush) SyncScheduler.requestPushBestEffort(ctx)")
        val returned = source.indexOf("result.didStoreLocally")

        assertTrue("Runtime explicit save must inspect Capture.Result", ingest >= 0)
        assertTrue("Runtime sync push must be gated by Capture.Result.shouldRequestSyncPush", gate > ingest)
        assertTrue("Runtime save return value must mirror local store/update status", returned > gate)
    }

    @Test
    fun shareTargetSchedulesSyncOnlyForPublicOutboxResults() {
        val source = readSource("share", "ShareReceiverActivity.kt")

        val ingest = source.indexOf("val r = Capture.ingest(")
        val gate = source.indexOf("if (r.shouldRequestSyncPush) SyncScheduler.requestPushBestEffort(this)")
        val toast = source.indexOf("Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()")

        assertTrue("Share target must inspect Capture.Result", ingest >= 0)
        assertTrue("Share target sync push must be gated by Capture.Result.shouldRequestSyncPush", gate > ingest)
        assertTrue("Share target must not defer an unconditional sync push to the UI toast block", toast > gate)
        assertTrue(
            "Share target should call SyncScheduler only once",
            source.split("SyncScheduler.requestPush").size == 2,
        )
    }

    @Test
    fun quickSettingsTileSchedulesSyncOnlyForPublicOutboxResults() {
        val source = readSource("tile", "SaveClipboardTileService.kt")

        val ingest = source.indexOf("val r = Capture.ingest(")
        val gate = source.indexOf("if (r.shouldRequestSyncPush) SyncScheduler.requestPushBestEffort(this)")
        val status = source.indexOf("when (r.status)")

        assertTrue("QS tile must inspect Capture.Result", ingest >= 0)
        assertTrue("QS tile sync push must be gated by Capture.Result.shouldRequestSyncPush", gate > ingest)
        assertTrue("QS tile status handling must happen after the sync side-effect gate", status > gate)
        assertTrue(
            "QS tile should call SyncScheduler only once",
            source.split("SyncScheduler.requestPush").size == 2,
        )
    }

    private fun readSource(packageDir: String, fileName: String): String {
        val path = Path.of(
            "src",
            "main",
            "kotlin",
            "com",
            "clipvault",
            "app",
            packageDir,
            fileName,
        )
        assertTrue("source file is missing: $path", Files.isRegularFile(path))
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
