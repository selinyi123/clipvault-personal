package com.clipvault.app.otp

import java.util.UUID

internal const val OTP_USER_CONSENT_MAX_TTL_MS = 5 * 60_000L
internal const val OTP_USER_CONSENT_DEFAULT_TTL_MS = 120_000L

/** Metadata-only, process-memory session created by one explicit user action. */
class OtpUserConsentSession internal constructor(
    val sessionId: String,
    val pairSessionEpoch: String,
    val senderDeviceId: String,
    val targetDeviceId: String,
    val expiresAtMonotonicMs: Long,
) {
    override fun toString(): String = "<OtpUserConsentSession redacted>"
}

class OtpUserConsentSessionController(
    private val monotonicClockMs: () -> Long,
) {
    private val lock = Any()
    private var active: OtpUserConsentSession? = null

    fun begin(pair: OtpPairSummary, ttlMs: Long): OtpUserConsentSession? = synchronized(lock) {
        if (ttlMs !in 1..OTP_USER_CONSENT_MAX_TTL_MS) return@synchronized null
        val now = monotonicClockMs()
        if (now < 0L || now > Long.MAX_VALUE - ttlMs) return@synchronized null
        OtpUserConsentSession(
            sessionId = UUID.randomUUID().toString(),
            pairSessionEpoch = pair.sessionEpoch,
            senderDeviceId = pair.senderDeviceId,
            targetDeviceId = pair.targetDeviceId,
            expiresAtMonotonicMs = now + ttlMs,
        ).also { active = it }
    }

    fun current(): OtpUserConsentSession? = synchronized(lock) {
        val session = active ?: return@synchronized null
        if (monotonicClockMs() >= session.expiresAtMonotonicMs) {
            active = null
            return@synchronized null
        }
        session
    }

    /** Any identity mismatch consumes the pending session and fails closed. */
    fun consume(sessionId: String, targetDeviceId: String): OtpUserConsentSession? = synchronized(lock) {
        val session = active
        active = null
        if (
            session == null ||
            monotonicClockMs() >= session.expiresAtMonotonicMs ||
            session.sessionId != sessionId ||
            session.targetDeviceId != targetDeviceId
        ) return@synchronized null
        session
    }

    fun cancel(sessionId: String? = null) = synchronized(lock) {
        if (sessionId == null || active?.sessionId == sessionId) active = null
    }

    override fun toString(): String = "<OtpUserConsentSessionController redacted active=${current() != null}>"
}
