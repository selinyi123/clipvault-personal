package com.clipvault.app.otp

import android.content.Context
import android.os.SystemClock
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.Base64

@RunWith(AndroidJUnit4::class)
class AndroidOtpPairStoreInstrumentedTest {
    private val session = "11111111-1111-4111-8111-111111111111"
    private val sender = "device:33333333-3333-4333-8333-333333333333"
    private val target = "device:44444444-4444-4444-8444-444444444444"

    @Test
    fun pairResponseImportsOnceAndSequenceNonceReservationSurvivesReopen() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val store = AndroidOtpPairStore(context)
        store.clear()
        val verifier = ByteArray(32) { it.toByte() }
        val encoded = Base64.getUrlEncoder().withoutPadding().encodeToString(verifier)
        val response = (
            "{\"version\":1,\"session_epoch\":\"$session\"," +
                "\"sender_device_id\":\"$sender\",\"target_device_id\":\"$target\"," +
                "\"verifier\":\"$encoded\"}"
            ).toByteArray()
        try {
            assertTrue(store.importPairResponse(response, sender))
            assertTrue(response.all { it == 0.toByte() })
            val summary = requireNotNull(store.summary())
            assertEquals(0L, summary.highSequence)
            assertFalse(summary.toString().contains(encoded))

            val authorization = OtpCaptureAuthorization(
                "55555555-5555-4555-8555-555555555555",
                OtpCaptureSource.APPROVED_SMS_PERMISSION,
                session, sender, target,
                SystemClock.elapsedRealtime() + 60_000L,
                platformGranted = true,
                automaticCapture = true,
            )
            val firstNonce = ByteArray(12) { it.toByte() }
            store.acquire(authorization, firstNonce)!!.use { assertEquals(1L, it.sequence) }

            val reopened = AndroidOtpPairStore(context)
            assertEquals(1L, reopened.summary()!!.highSequence)
            assertNull(reopened.acquire(authorization, firstNonce.copyOf()))
            val secondNonce = ByteArray(12) { (it + 1).toByte() }
            reopened.acquire(authorization, secondNonce)!!.use { assertEquals(2L, it.sequence) }
            assertEquals(2L, AndroidOtpPairStore(context).summary()!!.highSequence)

            val encrypted = File(context.noBackupFilesDir, "otp_pair_v1.bin").readBytes()
            assertFalse(encrypted.toString(Charsets.ISO_8859_1).contains(encoded))
            assertFalse(encrypted.containsSubsequence(verifier))

            val duplicateResponse = (
                "{\"version\":1,\"session_epoch\":\"$session\"," +
                    "\"sender_device_id\":\"$sender\",\"target_device_id\":\"$target\"," +
                    "\"verifier\":\"$encoded\"}"
                ).toByteArray()
            assertFalse(reopened.importPairResponse(duplicateResponse, sender))
            assertTrue(duplicateResponse.all { it == 0.toByte() })
        } finally {
            verifier.wipe()
            assertTrue(store.clear())
            assertNull(store.summary())
        }
    }

    private fun ByteArray.containsSubsequence(candidate: ByteArray): Boolean {
        if (candidate.isEmpty() || candidate.size > size) return false
        for (start in 0..size - candidate.size) {
            var equal = true
            for (offset in candidate.indices) {
                if (this[start + offset] != candidate[offset]) { equal = false; break }
            }
            if (equal) return true
        }
        return false
    }
}
