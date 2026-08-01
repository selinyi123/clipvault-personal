package com.clipvault.app.otp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpUserConsentSessionControllerTest {
    private var now = 1_000L
    private val pair = OtpPairSummary(
        "11111111-1111-4111-8111-111111111111",
        "device:33333333-3333-4333-8333-333333333333",
        "device:44444444-4444-4444-8444-444444444444",
        0L,
    )

    @Test
    fun beginsDisabledThenBindsOneExplicitSessionToPairTargetAndDeadline() {
        val controller = OtpUserConsentSessionController { now }
        assertNull(controller.current())

        val session = controller.begin(pair, 120_000L)!!

        assertEquals(pair.sessionEpoch, session.pairSessionEpoch)
        assertEquals(pair.senderDeviceId, session.senderDeviceId)
        assertEquals(pair.targetDeviceId, session.targetDeviceId)
        assertEquals(121_000L, session.expiresAtMonotonicMs)
        assertEquals(session.sessionId, controller.current()?.sessionId)
        assertFalse(session.toString().contains(pair.targetDeviceId))
    }

    @Test
    fun exactSessionCanBeConsumedOnlyOnce() {
        val controller = OtpUserConsentSessionController { now }
        val session = controller.begin(pair, 100L)!!

        assertEquals(
            session.sessionId,
            controller.consume(session.sessionId, pair.targetDeviceId)?.sessionId,
        )
        assertNull(controller.consume(session.sessionId, pair.targetDeviceId))
    }

    @Test
    fun mismatchExpiryAndOversizedTtlFailClosed() {
        val controller = OtpUserConsentSessionController { now }
        val first = controller.begin(pair, 100L)!!
        assertNull(controller.consume(first.sessionId, pair.senderDeviceId))
        assertNull(controller.current())

        val second = controller.begin(pair, 100L)!!
        now = second.expiresAtMonotonicMs
        assertNull(controller.current())
        assertNull(controller.begin(pair, OTP_USER_CONSENT_MAX_TTL_MS + 1L))
        assertTrue(controller.toString().contains("active=false"))
    }
}
