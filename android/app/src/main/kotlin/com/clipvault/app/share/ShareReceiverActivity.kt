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
        val text = if (intent?.action == Intent.ACTION_SEND)
            intent.getStringExtra(Intent.EXTRA_TEXT) else null
        if (text.isNullOrBlank()) {
            finish(); return
        }
        thread {
            val db = ClipVaultApp.db(this)
            val r = Capture.ingest(db, text, sourceDevice = android.os.Build.MODEL ?: "android")
            runOnUiThread {
                val msg = when (r.status) {
                    Capture.Status.NEW -> if (r.clip?.isSecret == true) "已隔离（疑似密钥）" else "已保存到 ClipVault"
                    Capture.Status.DUPLICATE -> "已存在"
                    Capture.Status.REJECTED -> "内容为空/过大，未保存"
                }
                Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
                SyncScheduler.requestPush(this)
                finish()
            }
        }
    }
}
