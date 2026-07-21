package com.clipvault.app.tile

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.TileService
import com.clipvault.app.capture.ClipboardCaptureActivity
import com.clipvault.app.capture.ClipboardCaptureActions

/**
 * Quick Settings tile: save the current clipboard to ClipVault with one tap.
 * Android 10+ does not grant clipboard access to this TileService, so the
 * explicit tap opens a minimal foreground activity which reads after focus.
 */
class SaveClipboardTileService : TileService() {
    override fun onClick() {
        super.onClick()

        if (isLocked) {
            // The callback runs only after a successful unlock. Cancelling the
            // keyguard flow must never fall through to clipboard capture.
            unlockAndRun { launchCapture() }
        } else {
            launchCapture()
        }
    }

    private fun launchCapture() {
        val captureIntent = Intent(this, ClipboardCaptureActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra(ClipboardCaptureActivity.EXTRA_ACTION_ID, ClipboardCaptureActions.issue())
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            val pendingIntent = PendingIntent.getActivity(
                this,
                0,
                captureIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            startActivityAndCollapse(pendingIntent)
        } else {
            startActivityAndCollapseCompat(captureIntent)
        }
    }

    @SuppressLint("StartActivityAndCollapseDeprecated")
    @Suppress("DEPRECATION")
    private fun startActivityAndCollapseCompat(intent: Intent) = startActivityAndCollapse(intent)
}
