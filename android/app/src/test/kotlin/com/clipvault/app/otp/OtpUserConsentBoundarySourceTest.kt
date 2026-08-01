package com.clipvault.app.otp

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpUserConsentBoundarySourceTest {
    private fun read(path: String) = File(path).readText(Charsets.UTF_8)

    @Test
    fun defaultRuntimeUsesOfficialOneMessageConsentWithoutSmsPermissions() {
        val manifest = read("src/main/AndroidManifest.xml")
        val gradle = read("build.gradle.kts")
        val activity = read("src/main/kotlin/com/clipvault/app/otp/OtpUserConsentActivity.kt")
        val runtime = read("src/main/kotlin/com/clipvault/app/otp/OtpRelayRuntime.kt")
        val lock = read("PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json")

        assertFalse(manifest.contains("android.permission.READ_SMS"))
        assertFalse(manifest.contains("android.permission.RECEIVE_SMS"))
        assertTrue(manifest.contains(".otp.OtpUserConsentActivity"))
        assertTrue(manifest.contains("android:exported=\"false\""))
        assertTrue(gradle.contains("play-services-auth-api-phone:18.2.0"))
        assertTrue(gradle.contains("verifySmsUserConsentDependency"))
        assertTrue(gradle.contains("@aar"))
        assertTrue(gradle.contains("@pom"))
        assertTrue(lock.contains("15963fa1cf08ad2778fd54f17ef72cb7597af15f40415885833b1240369230f3"))
        assertTrue(lock.contains("1014bbbd9f385e57e1fb3f99d536e60e494b2ef7f4e3959088f748413a89a4b0"))
        assertTrue(lock.contains("Android Software Development Kit License"))

        assertTrue(activity.contains("startSmsUserConsent(null)"))
        assertTrue(activity.contains("SmsRetriever.SEND_PERMISSION"))
        assertTrue(activity.contains("startActivityForResult(consentIntent"))
        assertTrue(activity.contains("override fun onActivityResult"))
        assertTrue(activity.contains("SmsRetriever.EXTRA_SMS_MESSAGE"))
        assertTrue(runtime.contains("beginUserConsentSession"))
        assertTrue(runtime.contains("relayUserConsentedMessage"))
        assertTrue(runtime.contains("explicitUserAction = true"))
        assertFalse(runtime.contains("fun authorizeUserConsent"))
    }

    @Test
    fun explicitSessionIsTargetBoundTtlBoundAndNeverPersistedAsOptIn() {
        val controller = read(
            "src/main/kotlin/com/clipvault/app/otp/OtpUserConsentSessionController.kt",
        )
        val runtime = read("src/main/kotlin/com/clipvault/app/otp/OtpRelayRuntime.kt")
        val activity = read("src/main/kotlin/com/clipvault/app/otp/OtpUserConsentActivity.kt")

        assertTrue(controller.contains("OTP_USER_CONSENT_DEFAULT_TTL_MS = 120_000L"))
        assertTrue(controller.contains("targetDeviceId"))
        assertTrue(controller.contains("expiresAtMonotonicMs"))
        assertTrue(controller.contains("active = null"))
        assertTrue(runtime.contains("pair.targetDeviceId != session.targetDeviceId"))
        assertTrue(activity.contains("SystemClock.elapsedRealtime() >= expiresAtElapsedMs"))
        assertFalse(
            runtime.substringAfter("fun beginUserConsentSession")
                .substringBefore("fun relayApprovedSms")
                .contains("settings.captureOptIn = true"),
        )
    }
}
