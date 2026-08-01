package com.clipvault.app.otp

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import com.google.android.gms.auth.api.phone.SmsRetriever
import com.google.android.gms.common.api.CommonStatusCodes
import com.google.android.gms.common.api.Status

/** Foreground-only, one-session bridge to the system-owned SMS consent dialog. */
@Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
class OtpUserConsentActivity : ComponentActivity() {
    private var receiverRegistered = false
    private var consentDialogLaunched = false
    private var completed = false
    private var sessionId = ""
    private var targetDeviceId = ""
    private var expiresAtElapsedMs = 0L
    private val expiryRunnable = Runnable(::finishFailClosed)

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQUEST_SMS_CONSENT) return
        if (resultCode != Activity.RESULT_OK) {
            finishFailClosed()
            return
        }
        val message = data?.getStringExtra(SmsRetriever.EXTRA_SMS_MESSAGE)
        if (message.isNullOrEmpty()) {
            finishFailClosed()
            return
        }
        val ownedMessage = message.toCharArray()
        val status = OtpRelayRuntime.relayUserConsentedMessage(
            context = this,
            sessionId = sessionId,
            targetDeviceId = targetDeviceId,
            ownedMessageBody = ownedMessage,
        )
        completed = true
        setResult(if (status == OtpRelaySendStatus.ACCEPTED) Activity.RESULT_OK else Activity.RESULT_CANCELED)
        finish()
    }

    private val smsReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (
                intent.action != SmsRetriever.SMS_RETRIEVED_ACTION ||
                consentDialogLaunched ||
                SystemClock.elapsedRealtime() >= expiresAtElapsedMs ||
                !OtpRelayRuntime.isUserConsentSessionCurrent(
                    context,
                    sessionId,
                    targetDeviceId,
                )
            ) {
                finishFailClosed()
                return
            }
            val status = intent.extras?.parcelable<Status>(SmsRetriever.EXTRA_STATUS)
            when (status?.statusCode) {
                CommonStatusCodes.SUCCESS -> {
                    val consentIntent = intent.extras
                        ?.parcelable<Intent>(SmsRetriever.EXTRA_CONSENT_INTENT)
                    if (consentIntent == null) {
                        finishFailClosed()
                    } else {
                        consentDialogLaunched = true
                        startActivityForResult(consentIntent, REQUEST_SMS_CONSENT)
                    }
                }
                CommonStatusCodes.TIMEOUT -> finishFailClosed()
                else -> finishFailClosed()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        sessionId = intent.getStringExtra(EXTRA_SESSION_ID).orEmpty()
        targetDeviceId = intent.getStringExtra(EXTRA_TARGET_DEVICE_ID).orEmpty()
        expiresAtElapsedMs = intent.getLongExtra(EXTRA_EXPIRES_AT_ELAPSED_MS, 0L)
        if (
            savedInstanceState != null ||
            SystemClock.elapsedRealtime() >= expiresAtElapsedMs ||
            !OtpRelayRuntime.isUserConsentSessionCurrent(this, sessionId, targetDeviceId)
        ) {
            finishFailClosed()
            return
        }

        setContentView(TextView(this).apply {
            text = "正在等待下一条验证码短信；收到后仍需在系统对话框中逐条确认。"
            setPadding(32, 32, 32, 32)
        })
        ContextCompat.registerReceiver(
            this,
            smsReceiver,
            IntentFilter(SmsRetriever.SMS_RETRIEVED_ACTION),
            SmsRetriever.SEND_PERMISSION,
            null,
            ContextCompat.RECEIVER_EXPORTED,
        )
        receiverRegistered = true
        window.decorView.postDelayed(
            expiryRunnable,
            (expiresAtElapsedMs - SystemClock.elapsedRealtime()).coerceAtLeast(1L),
        )
        SmsRetriever.getClient(this).startSmsUserConsent(null)
            .addOnFailureListener { finishFailClosed() }
    }

    override fun onDestroy() {
        window.decorView.removeCallbacks(expiryRunnable)
        if (receiverRegistered) {
            runCatching { unregisterReceiver(smsReceiver) }
            receiverRegistered = false
        }
        if (!completed) OtpRelayRuntime.cancelUserConsentSession(this, sessionId)
        super.onDestroy()
    }

    private fun finishFailClosed() {
        if (isFinishing || isDestroyed) return
        OtpRelayRuntime.cancelUserConsentSession(this, sessionId)
        setResult(Activity.RESULT_CANCELED)
        finish()
    }

    @Suppress("DEPRECATION")
    private inline fun <reified T : android.os.Parcelable> Bundle.parcelable(key: String): T? =
        if (Build.VERSION.SDK_INT >= 33) getParcelable(key, T::class.java)
        else getParcelable(key)

    companion object {
        private const val REQUEST_SMS_CONSENT = 2814
        private const val EXTRA_SESSION_ID = "com.clipvault.app.otp.USER_CONSENT_SESSION_ID"
        private const val EXTRA_TARGET_DEVICE_ID = "com.clipvault.app.otp.USER_CONSENT_TARGET_DEVICE_ID"
        private const val EXTRA_EXPIRES_AT_ELAPSED_MS = "com.clipvault.app.otp.USER_CONSENT_EXPIRES_AT"

        fun intent(context: Context, session: OtpUserConsentSession): Intent =
            Intent(context, OtpUserConsentActivity::class.java)
                .putExtra(EXTRA_SESSION_ID, session.sessionId)
                .putExtra(EXTRA_TARGET_DEVICE_ID, session.targetDeviceId)
                .putExtra(EXTRA_EXPIRES_AT_ELAPSED_MS, session.expiresAtMonotonicMs)
    }
}
