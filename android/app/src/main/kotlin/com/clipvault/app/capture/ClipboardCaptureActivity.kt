package com.clipvault.app.capture

import android.app.Activity
import android.app.KeyguardManager
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.sync.SyncScheduler
import kotlin.concurrent.thread

/**
 * Focused, no-UI capture entrypoint launched only by the Quick Settings tile.
 *
 * Android 10+ limits clipboard access to the focused app or default IME. The
 * TileService itself is neither, so clipboard access must stay behind the
 * positive window-focus callback. This remains an explicit user action and
 * never polls or observes subsequent clipboard changes.
 */
class ClipboardCaptureActivity : Activity() {
    private var captureStarted = false
    private var freshActionId: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // A recreated Activity is not a fresh tile tap. Failing closed avoids
        // reading whatever happens to be on the clipboard after restoration.
        if (savedInstanceState != null) {
            intent?.getStringExtra(EXTRA_ACTION_ID)?.let(ClipboardCaptureActions::cancelPending)
            finish()
            return
        }
        freshActionId = intent?.getStringExtra(EXTRA_ACTION_ID)?.takeIf { it.isNotBlank() }
        if (freshActionId == null) finish()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        freshActionId?.let(ClipboardCaptureActions::cancelPending)
        super.onSaveInstanceState(outState)
    }

    override fun onStop() {
        // If this instance never reached the guarded focus callback, invalidate
        // its pending action so a later resume cannot read a different value.
        if (!captureStarted) {
            freshActionId?.let(ClipboardCaptureActions::cancelPending)
            freshActionId = null
            captureStarted = true
            finish()
        }
        super.onStop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val replacementActionId = intent.getStringExtra(EXTRA_ACTION_ID)?.takeIf { it.isNotBlank() }

        // Before focus, a genuinely newer Tile-issued action may replace the
        // pending one and will still produce at most one read. The same token
        // is cancelled as a replay. Once capture has started, any replacement
        // is dropped without finishing the Activity that protects the worker.
        if (captureStarted) {
            replacementActionId?.let(ClipboardCaptureActions::cancelPending)
            return
        }

        freshActionId?.let(ClipboardCaptureActions::cancelPending)
        if (replacementActionId == null) {
            captureStarted = true
            freshActionId = null
            finish()
            return
        }

        setIntent(intent)
        freshActionId = replacementActionId
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || captureStarted) return
        captureStarted = true

        val actionId = freshActionId
        if (actionId == null || !ClipboardCaptureActions.tryAcquire(actionId)) {
            finish()
            return
        }
        freshActionId = null
        intent?.removeExtra(EXTRA_ACTION_ID)

        // Recheck immediately before clipboard access: the device can lock
        // again after TileService's unlock callback but before this window wins
        // focus. Missing keyguard state is treated as locked.
        val keyguard = getSystemService(KeyguardManager::class.java)
        if (keyguard == null || keyguard.isKeyguardLocked || keyguard.isDeviceLocked) {
            ClipboardCaptureActions.release(actionId)
            finish()
            return
        }

        val text = try {
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.primaryClip?.takeIf { it.itemCount > 0 }
                ?.getItemAt(0)?.text?.toString()
        } catch (_: Exception) {
            ClipboardCaptureActions.release(actionId)
            showToastAndFinish("无法读取剪贴板")
            return
        }
        if (text.isNullOrBlank()) {
            ClipboardCaptureActions.release(actionId)
            showToastAndFinish("剪贴板没有可保存的文本")
            return
        }

        val appContext = applicationContext
        val started = tryStartCaptureWorker(
            startThread = { worker ->
                thread(start = false, name = "clipvault-clipboard-capture", block = worker).start()
            },
            worker = {
                val message = try {
                    val db = ClipVaultApp.db(appContext)
                    val result = Capture.ingest(
                        db,
                        text,
                        sourceDevice = Build.MODEL ?: "android",
                    )
                    if (result.shouldRequestSyncPush) {
                        SyncScheduler.requestPushBestEffort(appContext)
                    }
                    when (result.status) {
                        Capture.Status.NEW -> if (result.clip?.isSecret == true) "已隔离" else "已保存"
                        Capture.Status.DUPLICATE -> "已存在"
                        Capture.Status.REJECTED -> "未保存"
                    }
                } catch (_: Exception) {
                    "保存失败"
                } finally {
                    ClipboardCaptureActions.release(actionId)
                }
                Handler(Looper.getMainLooper()).post {
                    showToastAndFinish(message, appContext)
                }
            },
            onStartFailure = {
                ClipboardCaptureActions.release(actionId)
                showToastAndFinish("保存失败")
            },
        )
        if (!started) return
    }

    private fun showToastAndFinish(message: String) {
        try {
            Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        } finally {
            finish()
        }
    }

    private fun showToastAndFinish(message: String, context: Context) {
        try {
            Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
        } finally {
            finish()
        }
    }

    companion object {
        internal const val EXTRA_ACTION_ID = "com.clipvault.app.extra.CAPTURE_ACTION_ID"
    }
}
