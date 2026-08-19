package com.clipvault.app.otp

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class OtpRelayBoundarySourceTest {
    private fun read(path: String) = File(path).readText(Charsets.UTF_8)

    @Test
    fun defaultRuntimeAndStandaloneImeHaveNoSmsOrNotificationListenerAuthority() {
        val runtimeManifest = read("src/main/AndroidManifest.xml")
        val imeManifest = read("../ime-app/src/main/AndroidManifest.xml")
        for (manifest in listOf(runtimeManifest, imeManifest)) {
            assertFalse(manifest.contains("android.permission.RECEIVE_SMS"))
            assertFalse(manifest.contains("android.permission.READ_SMS"))
            assertFalse(manifest.contains("BIND_NOTIFICATION_LISTENER_SERVICE"))
        }
        assertFalse(imeManifest.contains("android.permission.INTERNET"))
    }

    @Test
    fun restrictedPermissionAndReceiverExistOnlyInReviewGatedSourceSet() {
        val manifest = read("src/otpSmsRelay/AndroidManifest.xml")
        assertTrue(manifest.contains("android.permission.RECEIVE_SMS"))
        assertFalse(manifest.contains("android.permission.READ_SMS"))
        assertFalse(manifest.contains("BIND_NOTIFICATION_LISTENER_SERVICE"))
        assertTrue(manifest.contains("ApprovedSmsOtpReceiver"))
        val gradle = read("build.gradle.kts")
        assertTrue(gradle.contains("create(\"otpSmsRelay\")"))
        assertTrue(gradle.contains("buildApprovedOtpSmsRelay"))
        assertTrue(gradle.contains("CLIPVAULT_PLAY_SMS_APPROVAL_REF"))
    }

    @Test
    fun receiverAndRuntimeNeverUseDurableQueueDatabaseOrContentLogging() {
        val receiver = read("src/otpSmsRelay/kotlin/com/clipvault/app/otp/ApprovedSmsOtpReceiver.kt")
        val runtime = read("src/main/kotlin/com/clipvault/app/otp/OtpRelayRuntime.kt")
        val network = read("src/main/kotlin/com/clipvault/app/otp/OtpNetwork.kt")
        val combined = receiver + runtime + network
        assertFalse(combined.contains("WorkManager"))
        assertFalse(combined.contains("Outbox"))
        assertFalse(combined.contains("ClipVaultApp.db"))
        assertFalse(combined.contains("android.util.Log"))
        assertFalse(receiver.contains("startService"))
    }

    @Test
    fun settingsUiExposesPairPermissionGrantRevokeAndForgetFlow() {
        val ui = read("src/main/kotlin/com/clipvault/app/otp/OtpRelaySettingsActivity.kt")
        assertTrue(ui.contains("OtpRelayRuntime.pair"))
        assertTrue(ui.contains("RequestPermission"))
        assertTrue(ui.contains("authorizeApprovedSms"))
        assertTrue(ui.contains("revokeCapture"))
        assertTrue(ui.contains("forgetPair"))
        val runtime = read("src/main/kotlin/com/clipvault/app/otp/OtpRelayRuntime.kt")
        assertTrue(runtime.contains("settings.captureOptIn = false"))
        assertTrue(runtime.contains("Intent.ACTION_SCREEN_OFF"))
        assertTrue(runtime.contains("handler.postDelayed"))
    }
}
