package com.clipvault.app.otp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpCaptureGrantControllerTest {
    private var now = 10L
    private val pair = OtpPairSummary(
        "11111111-1111-4111-8111-111111111111",
        "device:33333333-3333-4333-8333-333333333333",
        "device:44444444-4444-4444-8444-444444444444",
        0L,
    )

    @Test
    fun grantBindsSourcePairAndMonotonicDeadlineThenExpires() {
        val controller = OtpCaptureGrantController { now }
        val grant = controller.authorize(
            pair, OtpCaptureSource.APPROVED_SMS_PERMISSION,
            platformGranted = true, automaticCapture = true, ttlMs = 100L,
        )!!
        assertEquals(pair.sessionEpoch, grant.sessionEpoch)
        assertEquals(pair.senderDeviceId, grant.senderDeviceId)
        assertEquals(pair.targetDeviceId, grant.targetDeviceId)
        assertEquals(OtpCaptureSource.APPROVED_SMS_PERMISSION, grant.source)
        assertTrue(grant.automaticCapture)
        assertNull(controller.current(OtpCaptureSource.SMS_USER_CONSENT))

        val second = controller.authorize(
            pair, OtpCaptureSource.APPROVED_SMS_PERMISSION,
            platformGranted = true, automaticCapture = true, ttlMs = 100L,
        )!!
        now = second.expiresAtMonotonicMs
        assertNull(controller.current())
    }

    @Test
    fun missingPlatformGrantAndOversizedTtlFailClosed() {
        val controller = OtpCaptureGrantController { now }
        assertNull(controller.authorize(
            pair, OtpCaptureSource.APPROVED_SMS_PERMISSION,
            platformGranted = false, automaticCapture = true, ttlMs = 1L,
        ))
        assertNull(controller.authorize(
            pair, OtpCaptureSource.APPROVED_SMS_PERMISSION,
            platformGranted = true, automaticCapture = true,
            ttlMs = OTP_CAPTURE_GRANT_MAX_TTL_MS + 1,
        ))
        assertFalse(controller.toString().contains(pair.sessionEpoch))
    }

    @Test
    fun revokeAndRotationInvalidateCopiedAuthorizations() {
        val controller = OtpCaptureGrantController { now }
        val first = controller.authorize(
            pair, OtpCaptureSource.APPROVED_SMS_PERMISSION,
            platformGranted = true, automaticCapture = true, ttlMs = 100L,
        )!!
        assertTrue(controller.isCurrent(first))

        controller.revoke()
        assertFalse(controller.isCurrent(first))

        val second = controller.authorize(
            pair, OtpCaptureSource.APPROVED_SMS_PERMISSION,
            platformGranted = true, automaticCapture = true, ttlMs = 100L,
        )!!
        assertFalse(controller.isCurrent(first))
        assertTrue(controller.isCurrent(second))
    }

    @Test
    fun lookupForAnotherCaptureSourceDoesNotRevokeTheActiveGrant() {
        val controller = OtpCaptureGrantController { now }
        val consent = controller.authorize(
            pair, OtpCaptureSource.SMS_USER_CONSENT,
            platformGranted = true, automaticCapture = false, ttlMs = 100L,
        )!!

        assertNull(controller.current(OtpCaptureSource.APPROVED_SMS_PERMISSION))
        assertTrue(controller.isCurrent(consent))
        assertSame(consent, controller.current(OtpCaptureSource.SMS_USER_CONSENT))
    }
}
