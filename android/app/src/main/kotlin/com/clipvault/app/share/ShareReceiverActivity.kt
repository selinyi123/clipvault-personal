package com.clipvault.app.share

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.capture.Capture
import com.clipvault.app.sync.SyncScheduler
import kotlin.concurrent.thread

/** Primary capture path: "Share -> ClipVault" from any app. Translucent, no UI. */
class ShareReceiverActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val text = extractSharedText(intent)
        if (text.isNullOrBlank()) {
            finish(); return
        }
        thread {
            // Guarded: a DB error must not crash the share flow (uncaught thread
            // exceptions kill the process).
            val msg = try {
                val db = ClipVaultApp.db(this)
                val r = Capture.ingest(db, text, sourceDevice = android.os.Build.MODEL ?: "android")
                if (r.shouldRequestSyncPush) SyncScheduler.requestPushBestEffort(this)
                when (r.status) {
                    Capture.Status.NEW -> if (r.clip?.isSecret == true) "已隔离（疑似密钥）" else "已保存到 ClipVault"
                    Capture.Status.DUPLICATE -> "已存在"
                    Capture.Status.REJECTED -> "内容为空/过大，未保存"
                }
            } catch (e: Exception) {
                "保存失败（${e.javaClass.simpleName}）"
            }
            runOnUiThread {
                Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
                finish()
            }
        }
    }

    companion object {
        internal fun extractSharedText(intent: Intent?): String? =
            if (intent?.action == Intent.ACTION_SEND) {
                intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()
            } else {
                null
            }
    }
}
