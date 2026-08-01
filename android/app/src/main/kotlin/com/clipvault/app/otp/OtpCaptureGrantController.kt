package com.clipvault.app.otp

import java.util.UUID

internal const val OTP_CAPTURE_GRANT_MAX_TTL_MS = 8 * 60 * 60 * 1_000L

/** Process-memory only. Restart, lock, revocation and deadline all erase the grant. */
class OtpCaptureGrantController(
    private val monotonicClockMs: () -> Long,
) {
    private val lock = Any()
    private var active: OtpCaptureAuthorization? = null

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
        OtpCaptureAuthorization(
            grantId = UUID.randomUUID().toString(),
            source = source,
            sessionEpoch = pair.sessionEpoch,
            senderDeviceId = pair.senderDeviceId,
            targetDeviceId = pair.targetDeviceId,
            expiresAtMonotonicMs = now + ttlMs,
            platformGranted = true,
            automaticCapture = automaticCapture,
        ).also { active = it }
    }

    fun current(source: OtpCaptureSource? = null): OtpCaptureAuthorization? = synchronized(lock) {
        val grant = active ?: return@synchronized null
        if (monotonicClockMs() >= grant.expiresAtMonotonicMs || (source != null && grant.source != source)) {
            active = null
            return@synchronized null
        }
        grant
    }

    fun revoke() = synchronized(lock) { active = null }
    override fun toString(): String = "<OtpCaptureGrantController redacted active=${current() != null}>"
}
