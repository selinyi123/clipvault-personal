package com.clipvault.app.tile

import android.content.ClipboardManager
import android.content.Context
import android.service.quicksettings.TileService
import android.widget.Toast
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.capture.Capture
import com.clipvault.app.sync.SyncScheduler
import kotlin.concurrent.thread

/**
 * Quick Settings tile: save the current clipboard to ClipVault with one tap.
 * Reading the clipboard is allowed here because tapping the tile brings our
 * app momentarily to the foreground (Android 10+ rule). We never poll.
 */
class SaveClipboardTileService : TileService() {
    override fun onClick() {
        super.onClick()
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.takeIf { it.itemCount > 0 }
            ?.getItemAt(0)?.coerceToText(this)?.toString()
        if (text.isNullOrBlank()) {
            showToast("剪贴板为空")
            return
        }
        thread {
            // Guarded: an uncaught exception in this thread would crash the app.
            val msg = try {
                val db = ClipVaultApp.db(this)
                val r = Capture.ingest(db, text, sourceDevice = android.os.Build.MODEL ?: "android")
                SyncScheduler.requestPush(this)
                when (r.status) {
                    Capture.Status.NEW -> if (r.clip?.isSecret == true) "已隔离" else "已保存"
                    Capture.Status.DUPLICATE -> "已存在"
                    Capture.Status.REJECTED -> "未保存"
                }
            } catch (e: Exception) {
                "保存失败"
            }
            showToast(msg)
        }
    }

    private fun showToast(msg: String) =
        ContextCompat_mainThread { Toast.makeText(this, msg, Toast.LENGTH_SHORT).show() }

    private fun ContextCompat_mainThread(block: () -> Unit) {
        android.os.Handler(mainLooper).post(block)
    }
}
