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
            assertEquals(
                OtpPairImportResult.IMPORTED,
                store.importPairResponse(response, sender),
            )
            assertTrue(response.all { it == 0.toByte() })
            val pairFile = File(context.noBackupFilesDir, "otp_pair_v1.bin")
            val legacyBackup = File(context.noBackupFilesDir, "otp_pair_v1.bin.bak")
            assertTrue(pairFile.renameTo(legacyBackup))
            assertFalse(pairFile.exists())
            assertEquals(OtpPairState.READY, store.inspect().state)
            assertTrue(pairFile.exists())
            assertFalse(legacyBackup.exists())
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
            val first = store.acquire(authorization, firstNonce)
                as OtpPairMaterialAcquireResult.Acquired
            first.lease.use { assertEquals(1L, it.sequence) }

            val reopened = AndroidOtpPairStore(context)
            assertEquals(1L, reopened.summary()!!.highSequence)
            assertEquals(
                OtpPairMaterialAcquireResult.Mismatch,
                reopened.acquire(authorization, firstNonce.copyOf()),
            )
            val secondNonce = ByteArray(12) { (it + 1).toByte() }
            val second = reopened.acquire(authorization, secondNonce)
                as OtpPairMaterialAcquireResult.Acquired
            second.lease.use { assertEquals(2L, it.sequence) }
            assertEquals(2L, AndroidOtpPairStore(context).summary()!!.highSequence)

            val encrypted = pairFile.readBytes()
            assertFalse(encrypted.toString(Charsets.ISO_8859_1).contains(encoded))
            assertFalse(encrypted.containsSubsequence(verifier))

            val duplicateResponse = (
                "{\"version\":1,\"session_epoch\":\"$session\"," +
                    "\"sender_device_id\":\"$sender\",\"target_device_id\":\"$target\"," +
                    "\"verifier\":\"$encoded\"}"
                ).toByteArray()
            assertEquals(
                OtpPairImportResult.CONFLICT,
                reopened.importPairResponse(duplicateResponse, sender),
            )
            assertTrue(duplicateResponse.all { it == 0.toByte() })
        } finally {
            verifier.wipe()
            assertTrue(store.clear())
            assertNull(store.summary())
        }
    }

    @Test
    fun frameCorruptionIsTerminalButOpenFailurePreservesTheContainer() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val store = AndroidOtpPairStore(context)
        val pairFile = File(context.noBackupFilesDir, "otp_pair_v1.bin")
        store.clear()
        try {
            pairFile.writeBytes(byteArrayOf(1, 2, 3, 4))
            assertEquals(OtpPairState.UNPAIRED, store.inspect().state)
            assertFalse(pairFile.exists())

            assertTrue(pairFile.mkdir())
            assertEquals(OtpPairState.UNAVAILABLE, store.inspect().state)
            assertTrue(pairFile.exists())
        } finally {
            if (pairFile.isDirectory) pairFile.delete()
            store.clear()
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
