package com.clipvault.app.otp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path

class OtpRelayBoundarySourceTest {
    private val producer = Path.of(
        "src", "main", "kotlin", "com", "clipvault", "app", "otp",
        "OtpRelayProducer.kt",
    )
    private val transport = Path.of(
        "src", "main", "kotlin", "com", "clipvault", "app", "sync",
        "OtpRelayHttpTransport.kt",
    )

    @Test
    fun otpProducerHasNoNetworkPersistenceImeClipboardOrLoggingDependency() {
        val source = Files.readAllLines(producer).joinToString("\n")
        val blocked = listOf(
            "java.net.",
            "android.net.",
            "HttpURLConnection",
            "androidx.work",
            "WorkManager",
            "RoomDatabase",
            "com.clipvault.app.data",
            "InputMethodService",
            "InputConnection",
            "ClipboardManager",
            "android.util.Log",
            "println(",
            "com.clipvault.app.sync",
        )
        blocked.forEach { token ->
            assertFalse("producer contains forbidden token $token", source.contains(token))
        }
        assertTrue(source.contains("class DisabledOtpCapturePort"))
        assertTrue(source.contains("class DisabledOtpPairMaterialPort"))
        assertTrue(source.contains("class DisabledOtpOnlineTransportPort"))
        assertTrue(source.contains("Cipher.getInstance(\"AES/GCM/NoPadding\")"))
    }

    @Test
    fun onlineTransportHasNoDurableSchedulerClipboardOrLoggingPath() {
        val source = Files.readAllLines(transport).joinToString("\n")
        val blocked = listOf(
            "androidx.work",
            "WorkManager",
            "RoomDatabase",
            "com.clipvault.app.data",
            "ClipboardManager",
            "android.util.Log",
            "println(",
        )
        blocked.forEach { token ->
            assertFalse("transport contains forbidden token $token", source.contains(token))
        }
        assertTrue(source.contains("private const val OTP_RELAY_PATH = \"/otp/relay\""))
        assertTrue(source.contains("instanceFollowRedirects = false"))
        assertTrue(source.contains("setFixedLengthStreamingMode(bodyLength)"))
        assertFalse(source.contains("Thread("))
    }

    @Test
    fun manifestAddsNoSmsPermissionOrNewImeServiceCapability() {
        val manifest = Files.readAllLines(
            Path.of("src", "main", "AndroidManifest.xml"),
        ).joinToString("\n")
        assertFalse(manifest.contains("android.permission.READ_SMS"))
        assertFalse(manifest.contains("android.permission.RECEIVE_SMS"))
        assertFalse(manifest.contains("android.service.autofill.AutofillService"))
        assertEquals(
            1,
            Regex("android.permission.INTERNET").findAll(manifest).count(),
        )
        assertEquals(
            2,
            Regex("android.permission.BIND_INPUT_METHOD").findAll(manifest).count(),
        )
    }
}
