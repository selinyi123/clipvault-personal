package com.clipvault.app.otp

import java.util.UUID

internal const val OTP_CAPTURE_GRANT_MAX_TTL_MS = 8 * 60 * 60 * 1_000L

/** Process-memory only. Restart, lock, revocation and deadline all erase the grant. */
class OtpCaptureGrantController(
    private val monotonicClockMs: () -> Long,
) {
    private val lock = Any()
    private var active: OtpCaptureAuthorization? = null
    private var generation = 0L

    private fun nextGenerationLocked(): Long {
        generation = if (generation == Long.MAX_VALUE) 1L else generation + 1L
        return generation
    }

    fun authorize(
        pair: OtpPairSummary,
        source: OtpCaptureSource,
        platformGranted: Boolean,
        automaticCapture: Boolean,
        ttlMs: Long,
    ): OtpCaptureAuthorization? = synchronized(lock) {
        if (!platformGranted || ttlMs !in 1..OTP_CAPTURE_GRANT_MAX_TTL_MS) return@synchronized null
        val now = monotonicClockMs()
        if (now < 0L || now > Long.MAX_VALUE - ttlMs) return@synchronized null
        val grantGeneration = nextGenerationLocked()
        OtpCaptureAuthorization(
            grantId = UUID.randomUUID().toString(),
            source = source,
            sessionEpoch = pair.sessionEpoch,
            senderDeviceId = pair.senderDeviceId,
            targetDeviceId = pair.targetDeviceId,
            expiresAtMonotonicMs = now + ttlMs,
            platformGranted = true,
            automaticCapture = automaticCapture,
            grantGeneration = grantGeneration,
        ).also { active = it }
    }

    fun current(source: OtpCaptureSource? = null): OtpCaptureAuthorization? = synchronized(lock) {
        val grant = active ?: return@synchronized null
        if (monotonicClockMs() >= grant.expiresAtMonotonicMs) {
            active = null
            nextGenerationLocked()
            return@synchronized null
        }
        // A lookup for another capture source is not a revocation. Approved
        // SMS delivery and an explicit User Consent result can race on
        // different threads; the losing lookup must not invalidate the grant
        // that is already authorizing the other one-shot flow.
        if (source != null && grant.source != source) return@synchronized null
        grant
    }

    /** Invalidate both the live reference and every previously copied grant. */
    fun revoke() = synchronized(lock) {
        active = null
        nextGenerationLocked()
    }

    /**
     * Check the exact grant object and generation while holding the same lock
     * used by authorize/revoke. A copied authorization can therefore never be
     * mistaken for the newly authorized grant after a revoke or rotation.
     */
    fun isCurrent(grant: OtpCaptureAuthorization): Boolean = synchronized(lock) {
        val current = active ?: return@synchronized false
        if (current !== grant || current.grantGeneration != generation) {
            return@synchronized false
        }
        if (monotonicClockMs() >= current.expiresAtMonotonicMs) {
            active = null
            nextGenerationLocked()
            return@synchronized false
        }
        true
    }

    override fun toString(): String = "<OtpCaptureGrantController redacted active=${current() != null}>"
}
