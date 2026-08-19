package com.clipvault.app.otp

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

/**
 * Review-gated SMS capture entrypoint. It performs no persistence and submits
 * at most one bounded online task. Saturation/offline/invalid input is dropped.
 */
class ApprovedSmsOtpReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val messages = try { Telephony.Sms.Intents.getMessagesFromIntent(intent) }
        catch (_: Exception) { return }
        if (messages.isEmpty()) return
        val sender = messages.first().originatingAddress
        if (messages.any { it.originatingAddress != sender }) return
        var total = 0
        for (message in messages) {
            val length = message.messageBody?.length ?: 0
            if (length > 4_096 - total) return
            total += length
        }
        if (total == 0) return
        val body = CharArray(total)
        var cursor = 0
        for (message in messages) {
            val part = message.messageBody ?: continue
            for (character in part) body[cursor++] = character
        }
        val timestamp = messages.maxOf { it.timestampMillis }
        val pending = goAsync()
        try {
            EXECUTOR.execute {
                try {
                    OtpRelayRuntime.relayApprovedSms(context, body, sender, timestamp)
                } finally {
                    body.wipe()
                    pending.finish()
                }
            }
        } catch (_: RejectedExecutionException) {
            body.wipe()
            pending.finish()
        }
    }

    companion object {
        private val EXECUTOR = ThreadPoolExecutor(
            0, 1, 5L, TimeUnit.SECONDS,
            SynchronousQueue(),
            { runnable -> Thread(runnable, "clipvault-otp-online").apply { isDaemon = true } },
            ThreadPoolExecutor.AbortPolicy(),
        )
    }
}
