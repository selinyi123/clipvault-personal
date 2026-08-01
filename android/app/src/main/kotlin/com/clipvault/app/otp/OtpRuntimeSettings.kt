package com.clipvault.app.otp

import android.content.Context
import java.io.IOException
import java.util.UUID

private const val OTP_RUNTIME_PREFS = "clipvault_otp_runtime_v1"
private const val OTP_SENDER_DEVICE = "sender_device_id"
private const val OTP_CAPTURE_OPT_IN = "capture_opt_in"

/** Non-secret policy metadata. Pair verifier/sequence never enter this store. */
class OtpRuntimeSettings(context: Context) {
    private val preferences = context.applicationContext
        .getSharedPreferences(OTP_RUNTIME_PREFS, Context.MODE_PRIVATE)

    val senderDeviceId: String
        get() {
            preferences.getString(OTP_SENDER_DEVICE, null)?.let { existing ->
                try {
                    canonicalDevice(existing, "OTP sender")
                    return existing
                } catch (_: Exception) { }
            }
            val generated = "device:${UUID.randomUUID()}"
            if (!preferences.edit().putString(OTP_SENDER_DEVICE, generated).commit()) {
                throw IOException("OTP sender identity write failed")
            }
            return generated
        }

    var captureOptIn: Boolean
        get() = preferences.getBoolean(OTP_CAPTURE_OPT_IN, false)
        set(value) {
            if (!preferences.edit().putBoolean(OTP_CAPTURE_OPT_IN, value).commit()) {
                throw IOException("OTP capture setting write failed")
            }
        }
}
