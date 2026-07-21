package com.clipvault.app.capture

import org.junit.Assert.assertFalse
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
    fun shareTargetAcceptsCharSequencePayloads() {
        val source = readSource("share", "ShareReceiverActivity.kt")

        assertTrue(
            "Share target must preserve Android's CharSequence EXTRA_TEXT contract",
            source.contains("intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()"),
        )
        assertFalse(
            "String-only extraction drops valid styled or non-String shared text",
            source.contains("getStringExtra(Intent.EXTRA_TEXT)"),
        )
    }

    @Test
    fun quickSettingsTileDelegatesClipboardAccessToForegroundActivity() {
        val source = readSource("tile", "SaveClipboardTileService.kt")

        val locked = source.indexOf("if (isLocked)")
        val unlock = source.indexOf("unlockAndRun { launchCapture() }")
        val launch = source.indexOf("private fun launchCapture()")
        val sdkGate = source.indexOf("Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE")
        val pendingLaunch = source.indexOf("startActivityAndCollapse(pendingIntent)")
        val compatLaunch = source.indexOf("startActivityAndCollapseCompat(captureIntent)")

        assertTrue("Locked QS capture must enter a system unlock flow", locked >= 0)
        assertTrue("Clipboard capture may launch only from the successful unlock callback", unlock > locked)
        assertTrue("QS tile must isolate launch code behind the lock gate", launch > unlock)
        assertTrue(
            "QS tile must launch the focused clipboard capture activity",
            source.contains("Intent(this, ClipboardCaptureActivity::class.java)"),
        )
        assertTrue(
            "Each explicit tile action must receive a coordinator-issued ID",
            source.contains("ClipboardCaptureActions.issue()"),
        )
        assertTrue("Android 14 API selection must be explicit", sdkGate > launch)
        assertTrue(
            "Android 14+ tile launch must use the PendingIntent overload",
            pendingLaunch > sdkGate,
        )
        assertTrue(
            "The capture PendingIntent must be immutable",
            source.contains("PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE"),
        )
        assertTrue(
            "QS tile must retain the pre-Android 14 compatibility launch",
            compatLaunch > pendingLaunch,
        )
        assertFalse("TileService must not read clipboard data", source.contains("ClipboardManager"))
        assertFalse("TileService must not read clipboard data", source.contains(".primaryClip"))
        assertFalse("TileService must not ingest outside the focused activity", source.contains("Capture.ingest("))
    }

    @Test
    fun clipboardCaptureReadsAfterFocusAndSchedulesOnlyPublicResults() {
        val source = readSource("capture", "ClipboardCaptureActivity.kt")

        val focusCallback = source.indexOf("override fun onWindowFocusChanged(hasFocus: Boolean)")
        val focusGate = source.indexOf("if (!hasFocus || captureStarted) return")
        val oneShot = source.indexOf("captureStarted = true", focusGate)
        val acquire = source.indexOf("ClipboardCaptureActions.tryAcquire(actionId)", oneShot)
        val keyguard = source.indexOf("getSystemService(KeyguardManager::class.java)", acquire)
        val lockedGate = source.indexOf("keyguard.isKeyguardLocked || keyguard.isDeviceLocked", keyguard)
        val clipboardRead = source.indexOf("clipboard.primaryClip")
        val ingest = source.indexOf("val result = Capture.ingest(")
        val syncGate = source.indexOf("if (result.shouldRequestSyncPush)")
        val syncRequest = source.indexOf("SyncScheduler.requestPushBestEffort(appContext)")

        assertTrue("Clipboard capture must wait for a window focus callback", focusCallback >= 0)
        assertTrue("Clipboard capture must require positive focus", focusGate > focusCallback)
        assertTrue("Clipboard capture must be one-shot per activity instance", oneShot > focusGate)
        assertTrue("Clipboard capture must acquire the cross-instance action gate", acquire > oneShot)
        assertTrue("Clipboard capture must re-read current keyguard state", keyguard > acquire)
        assertTrue("Clipboard capture must fail closed while keyguard is active", lockedGate > keyguard)
        assertTrue("Clipboard access must happen only after the keyguard gate", clipboardRead > lockedGate)
        assertTrue("Clipboard capture must not coerce arbitrary URI content on the UI thread", !source.contains("coerceToText"))
        assertTrue("Clipboard capture must accept direct CharSequence text", source.contains("getItemAt(0)?.text?.toString()"))
        assertTrue("Focused capture must reuse the canonical Capture.ingest path", ingest > clipboardRead)
        assertTrue("Sync scheduling must remain gated by Capture.Result", syncGate > ingest)
        assertTrue("The gated sync request must follow its condition", syncRequest > syncGate)
        assertTrue(
            "Focused capture should call SyncScheduler only once",
            source.split("SyncScheduler.requestPush").size == 2,
        )
    }

    @Test
    fun clipboardCaptureRejectsRestoredRedeliveredAndStoppedActions() {
        val source = readSource("capture", "ClipboardCaptureActivity.kt")

        val restored = source.indexOf("if (savedInstanceState != null)")
        val cancelRestored = source.indexOf("ClipboardCaptureActions::cancelPending", restored)
        val saveState = source.indexOf("override fun onSaveInstanceState(outState: Bundle)")
        val cancelSaved = source.indexOf("ClipboardCaptureActions::cancelPending", saveState)
        val stop = source.indexOf("override fun onStop()")
        val newIntent = source.indexOf("override fun onNewIntent(intent: Intent)")

        assertTrue("Configuration/process restoration must be recognized", restored >= 0)
        assertTrue("Restored actions must be invalidated before finish", cancelRestored > restored)
        assertTrue("State-save lifecycle must invalidate pending action", saveState > cancelRestored)
        assertTrue("State-save must cancel the pending action", cancelSaved > saveState && cancelSaved < stop)
        assertTrue("An unfocused stopped Activity must invalidate its pending action", stop > saveState)
        assertTrue("Intent redelivery must have an explicit fail-closed handler", newIntent > stop)
        assertTrue(
            "All lifecycle replay paths must invalidate coordinator state",
            source.split("ClipboardCaptureActions::cancelPending").size >= 5,
        )
        assertTrue(
            "The launch token must be removed before the Activity ends",
            source.contains("intent?.removeExtra(EXTRA_ACTION_ID)"),
        )
        val onNewIntentBody = source.substring(
            source.indexOf("override fun onNewIntent(intent: Intent)"),
            source.indexOf("override fun onWindowFocusChanged(hasFocus: Boolean)"),
        )
        val activeBranch = onNewIntentBody.substring(
            onNewIntentBody.indexOf("if (captureStarted)"),
            onNewIntentBody.indexOf("freshActionId?.let(ClipboardCaptureActions::cancelPending)"),
        )
        assertTrue(
            "A replacement action must be cancelled while the worker is active",
            activeBranch.contains("replacementActionId?.let(ClipboardCaptureActions::cancelPending)"),
        )
        assertFalse(
            "Intent redelivery must not finish the Activity protecting an active worker",
            activeBranch.contains("finish()"),
        )
    }

    @Test
    fun clipboardCaptureStaysAliveUntilIngestWorkerCompletes() {
        val source = readSource("capture", "ClipboardCaptureActivity.kt")

        val launcher = source.indexOf("val started = tryStartCaptureWorker(")
        val threadCreation = source.indexOf("thread(start = false, name = \"clipvault-clipboard-capture\"")
        val release = source.indexOf("ClipboardCaptureActions.release(actionId)", source.indexOf("finally", launcher))
        val completionPost = source.indexOf("Handler(Looper.getMainLooper()).post", release)
        val completionFinish = source.indexOf("showToastAndFinish(message, appContext)", completionPost)
        val startFailure = source.indexOf("onStartFailure =", completionFinish)
        val startGuard = source.indexOf("if (!started) return", startFailure)
        val normalTail = source.substring(
            startGuard,
            source.indexOf("private fun showToastAndFinish", startGuard),
        )

        assertTrue("Clipboard ingest must use the guarded worker launcher", launcher >= 0)
        assertTrue("Thread construction and start must be inside the guarded launcher", threadCreation > launcher)
        assertTrue("The action gate must remain held until the worker finishes", release > threadCreation)
        assertTrue("Worker completion must return to the main thread", completionPost > release)
        assertTrue("Toast and Activity finish must follow worker completion", completionFinish > completionPost)
        assertTrue("Thread-start failure must have an explicit cleanup callback", startFailure > completionFinish)
        assertTrue("Failed starts must stop the foreground callback", startGuard > startFailure)
        assertFalse("Normal launch must not immediately finish the protective Activity", normalTail.contains("finish()"))
        assertFalse("Activity destruction must not interrupt the ingest worker", source.contains("interrupt()"))
    }

    @Test
    fun clipboardCaptureActivityIsPrivateAndExcludedFromRecents() {
        val manifest = readManifest()
        val declaration = Regex(
            """<activity\s+[^>]*android:name="\.capture\.ClipboardCaptureActivity"[^>]*/>""",
            RegexOption.DOT_MATCHES_ALL,
        ).find(manifest)?.value

        assertTrue("Clipboard capture activity declaration is missing", declaration != null)
        val activityDeclaration = checkNotNull(declaration)
        assertTrue(
            "Clipboard capture activity must not be externally launchable",
            activityDeclaration.contains("android:exported=\"false\""),
        )
        assertTrue(
            "Clipboard capture activity must stay out of recents",
            activityDeclaration.contains("android:excludeFromRecents=\"true\""),
        )
        assertTrue(
            "Clipboard capture activity must remain a no-UI foreground trampoline",
            activityDeclaration.contains("Theme.Translucent.NoTitleBar"),
        )
        assertTrue(
            "Clipboard capture activity must not bring an existing ClipVault task to front",
            activityDeclaration.contains("android:taskAffinity=\"\""),
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

    private fun readManifest(): String {
        val path = Path.of("src", "main", "AndroidManifest.xml")
        assertTrue("Android manifest is missing: $path", Files.isRegularFile(path))
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
