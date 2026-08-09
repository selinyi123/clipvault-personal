package com.clipvault.app.otp

import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

internal const val OTP_RELAY_MAX_BODY_BYTES = 4_096
internal const val OTP_RELAY_MAX_TTL_MS = 180_000L
internal const val OTP_NONCE_BYTES = 12
internal const val OTP_PAIR_VERIFIER_BYTES = 32
internal const val OTP_PAIR_NONCE_HISTORY_CAPACITY = 4_096
private const val OTP_RELAY_VERSION = 1
private const val OTP_RELAY_ALGORITHM = "A256GCM"
private const val OTP_TAG_BYTES = 16
private const val OTP_RETIRED_SCOPE_CAPACITY = 64
private const val OTP_KDF_PREFIX = "ClipVault OTP Relay KDF v1\u0000"
private const val OTP_KEY_PREFIX = "ClipVault OTP Relay key v1\u0000"
private const val OTP_AAD_PREFIX = "ClipVault OTP Relay AEAD v1\u0000"

internal fun ByteArray.wipe() = fill(0)
internal fun CharArray.wipe() = fill('\u0000')

internal fun canonicalUuid4(value: String, name: String): UUID {
    val parsed = try {
        UUID.fromString(value)
    } catch (_: IllegalArgumentException) {
        throw OtpRelayRejected("invalid $name")
    }
    if (parsed.version() != 4 || parsed.toString() != value) {
        throw OtpRelayRejected("invalid $name")
    }
    return parsed
}

internal fun canonicalDevice(value: String, name: String): UUID {
    if (!value.startsWith("device:")) throw OtpRelayRejected("invalid $name")
    return canonicalUuid4(value.removePrefix("device:"), name)
}

private fun UUID.networkBytes(): ByteArray = ByteBuffer.allocate(16)
    .order(ByteOrder.BIG_ENDIAN)
    .putLong(mostSignificantBits)
    .putLong(leastSignificantBits)
    .array()

internal class OtpRelayRejected(message: String) : Exception(message)

enum class OtpCaptureSource {
    SMS_USER_CONSENT,
    NOTIFICATION_LISTENER,
    APPROVED_SMS_PERMISSION,
}

/** Metadata-only, short-lived authorization. It never owns an SMS body or OTP. */
class OtpCaptureAuthorization(
    val grantId: String,
    val source: OtpCaptureSource,
    val sessionEpoch: String,
    val senderDeviceId: String,
    val targetDeviceId: String,
    val expiresAtMonotonicMs: Long,
    val platformGranted: Boolean,
    val automaticCapture: Boolean = false,
    /** Process-local generation used to invalidate copied grants on revoke. */
    internal val grantGeneration: Long = 0L,
) {
    init {
        canonicalUuid4(grantId, "capture grant")
        canonicalUuid4(sessionEpoch, "capture session")
        canonicalDevice(senderDeviceId, "capture sender")
        canonicalDevice(targetDeviceId, "capture target")
        require(senderDeviceId != targetDeviceId) { "capture devices must differ" }
        require(expiresAtMonotonicMs > 0L) { "capture expiry must be positive" }
    }

    override fun toString(): String = "<OtpCaptureAuthorization redacted source=$source>"
}

/** Single-owner normalized OTP characters; close always wipes the owned buffer. */
class IsolatedOtpCandidate(
    val source: OtpCaptureSource,
    val grantId: String,
    val targetDeviceId: String,
    ownedCharacters: CharArray,
) : AutoCloseable {
    private var characters = ownedCharacters
    private var taken = false

    init {
        try {
            canonicalUuid4(grantId, "candidate grant")
            canonicalDevice(targetDeviceId, "candidate target")
        } catch (exc: Exception) {
            characters.wipe()
            characters = CharArray(0)
            taken = true
            throw exc
        }
    }

    @Synchronized
    internal fun take(
        authorization: OtpCaptureAuthorization,
        nowMonotonicMs: Long,
        explicitUserAction: Boolean,
    ): CharArray {
        if (taken) throw OtpRelayRejected("OTP candidate is unavailable")
        taken = true
        val result = characters
        characters = CharArray(0)
        try {
            if (
                authorization.source != source ||
                authorization.grantId != grantId ||
                authorization.targetDeviceId != targetDeviceId
            ) throw OtpRelayRejected("capture authorization mismatch")
            if (!authorization.platformGranted) {
                throw OtpRelayRejected("platform capture is not authorized")
            }
            if (nowMonotonicMs < 0L || nowMonotonicMs >= authorization.expiresAtMonotonicMs) {
                throw OtpRelayRejected("capture authorization expired")
            }
            if (!explicitUserAction && !authorization.automaticCapture) {
                throw OtpRelayRejected("explicit capture action required")
            }
            return result
        } catch (exc: Exception) {
            result.wipe()
            throw exc
        }
    }

    @Synchronized
    override fun close() {
        taken = true
        characters.wipe()
        characters = CharArray(0)
    }

    override fun toString(): String = "<IsolatedOtpCandidate redacted source=$source>"
}

interface OtpCapturePort : AutoCloseable {
    val source: OtpCaptureSource
    fun capture(authorization: OtpCaptureAuthorization): IsolatedOtpCandidate?

    /**
     * Re-check the live platform/user grant before each irreversible stage.
     * The interface default is fail-closed. Synthetic test ports must opt in
     * explicitly because they do not own an OS grant; a production port must
     * bind this to its process-local controller.
     */
    fun isAuthorizationCurrent(authorization: OtpCaptureAuthorization): Boolean = false

    override fun close() = Unit
}

class DisabledOtpCapturePort : OtpCapturePort {
    override val source = OtpCaptureSource.SMS_USER_CONSENT
    override fun capture(authorization: OtpCaptureAuthorization): IsolatedOtpCandidate? = null
}

/** One ownership-transfer view of a persistently reserved sequence and nonce. */
class OtpPairMaterialLease(
    val sessionEpoch: String,
    val senderDeviceId: String,
    val targetDeviceId: String,
    val sequence: Long,
    ownedPairVerifier: ByteArray,
) : AutoCloseable {
    private var pairVerifier = ownedPairVerifier
    private var closed = false

    init {
        try {
            canonicalUuid4(sessionEpoch, "pair session")
            canonicalDevice(senderDeviceId, "pair sender")
            canonicalDevice(targetDeviceId, "pair target")
            require(senderDeviceId != targetDeviceId) { "pair devices must differ" }
            require(sequence > 0L) { "pair sequence must be positive" }
            require(pairVerifier.size == OTP_PAIR_VERIFIER_BYTES) {
                "OTP pair verifier must be 32 bytes"
            }
        } catch (exc: Exception) {
            pairVerifier.wipe()
            pairVerifier = ByteArray(0)
            closed = true
            throw exc
        }
    }

    internal fun verifier(): ByteArray {
        if (closed) throw OtpRelayRejected("pair material is closed")
        return pairVerifier
    }

    @Synchronized
    override fun close() {
        if (closed) return
        closed = true
        pairVerifier.wipe()
        pairVerifier = ByteArray(0)
    }

    override fun toString(): String = "<OtpPairMaterialLease redacted>"
}

sealed class OtpPairMaterialAcquireResult {
    class Acquired(val lease: OtpPairMaterialLease) : OtpPairMaterialAcquireResult()
    object Unpaired : OtpPairMaterialAcquireResult()
    object Mismatch : OtpPairMaterialAcquireResult()
    object RotationRequired : OtpPairMaterialAcquireResult()
    object Unavailable : OtpPairMaterialAcquireResult()
}

interface OtpPairMaterialPort : AutoCloseable {
    /** Must durably reserve both the next sequence and [nonce] before returning. */
    fun acquire(
        authorization: OtpCaptureAuthorization,
        nonce: ByteArray,
    ): OtpPairMaterialAcquireResult

    override fun close() = Unit
}

class DisabledOtpPairMaterialPort : OtpPairMaterialPort {
    override fun acquire(
        authorization: OtpCaptureAuthorization,
        nonce: ByteArray,
    ): OtpPairMaterialAcquireResult = OtpPairMaterialAcquireResult.Unpaired
}

/** Synchronous, online-only POST. Implementations must never retain [wireBody]. */
interface OtpOnlineTransportPort : AutoCloseable {
    fun post(wireBody: ByteArray): OtpOnlineTransportResult
    override fun close() = Unit
}

class DisabledOtpOnlineTransportPort : OtpOnlineTransportPort {
    override fun post(wireBody: ByteArray): OtpOnlineTransportResult =
        OtpOnlineTransportResult.PolicyRejected
}

sealed class OtpOnlineTransportResult {
    object Accepted : OtpOnlineTransportResult()
    object AuthRequired : OtpOnlineTransportResult()
    object Unpaired : OtpOnlineTransportResult()
    object Mismatch : OtpOnlineTransportResult()
    object RotationRequired : OtpOnlineTransportResult()
    object TransientFailure : OtpOnlineTransportResult()
    object AuthRejected : OtpOnlineTransportResult()
    object PolicyRejected : OtpOnlineTransportResult()
}

enum class OtpRelaySendStatus {
    ACCEPTED,
    DISABLED,
    AUTH_REQUIRED,
    UNPAIRED,
    MISMATCH,
    ROTATION_REQUIRED,
    TRANSIENT_FAILURE,
    AUTH_REJECTED,
    POLICY_REJECTED,
    DROPPED,
}

internal fun interface OtpNonceSource { fun nextNonce(): ByteArray }
internal fun interface OtpEventIdSource { fun nextEventId(): String }

private class SecureRandomOtpNonceSource : OtpNonceSource {
    private val random = SecureRandom()
    override fun nextNonce(): ByteArray = ByteArray(OTP_NONCE_BYTES).also(random::nextBytes)
}

internal fun deriveOtpAesKey(
    pairVerifier: ByteArray,
    sessionEpoch: String,
    senderDeviceId: String,
    targetDeviceId: String,
): ByteArray {
    require(pairVerifier.size == OTP_PAIR_VERIFIER_BYTES)
    val session = canonicalUuid4(sessionEpoch, "KDF session").networkBytes()
    val sender = canonicalDevice(senderDeviceId, "KDF sender").networkBytes()
    val target = canonicalDevice(targetDeviceId, "KDF target").networkBytes()
    val saltInput = OTP_KDF_PREFIX.toByteArray(Charsets.UTF_8) + session
    val salt = MessageDigest.getInstance("SHA-256").digest(saltInput)
    var prk: ByteArray? = null
    var info: ByteArray? = null
    var expandInput: ByteArray? = null
    try {
        prk = Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(salt, "HmacSHA256")); doFinal(pairVerifier)
        }
        info = OTP_KEY_PREFIX.toByteArray(Charsets.UTF_8) + sender + target
        expandInput = info + byteArrayOf(1)
        return Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(prk, "HmacSHA256")); doFinal(expandInput)
        }
    } finally {
        session.wipe(); sender.wipe(); target.wipe(); saltInput.wipe(); salt.wipe()
        prk?.wipe(); info?.wipe(); expandInput?.wipe()
    }
}

internal fun canonicalOtpAad(
    sessionEpoch: String,
    eventId: String,
    senderDeviceId: String,
    targetDeviceId: String,
    sequence: Long,
    issuedAtMs: Long,
    expiresAtMs: Long,
): ByteArray {
    require(sequence > 0L && issuedAtMs >= 0L && expiresAtMs > issuedAtMs)
    val session = canonicalUuid4(sessionEpoch, "AAD session").networkBytes()
    val event = canonicalUuid4(eventId, "AAD event").networkBytes()
    val sender = canonicalDevice(senderDeviceId, "AAD sender").networkBytes()
    val target = canonicalDevice(targetDeviceId, "AAD target").networkBytes()
    val prefix = OTP_AAD_PREFIX.toByteArray(Charsets.UTF_8)
    return ByteBuffer.allocate(prefix.size + 1 + (16 * 4) + (Long.SIZE_BYTES * 3))
        .order(ByteOrder.BIG_ENDIAN)
        .put(prefix)
        .put(OTP_RELAY_VERSION.toByte())
        .put(session).put(event).put(sender).put(target)
        .putLong(sequence).putLong(issuedAtMs).putLong(expiresAtMs)
        .array()
        .also {
            session.wipe(); event.wipe(); sender.wipe(); target.wipe(); prefix.wipe()
        }
}

private class OtpWireEnvelope(
    val sessionEpoch: String,
    val eventId: String,
    val senderDeviceId: String,
    val targetDeviceId: String,
    val sequence: Long,
    val issuedAtMs: Long,
    val expiresAtMs: Long,
    private val nonce: ByteArray,
    private val ciphertext: ByteArray,
    private val tag: ByteArray,
) : AutoCloseable {
    fun toJsonBytes(): ByteArray {
        val encoder = Base64.getUrlEncoder().withoutPadding()
        val body = JSONObject()
            .put("version", OTP_RELAY_VERSION)
            .put("algorithm", OTP_RELAY_ALGORITHM)
            .put("session_epoch", sessionEpoch)
            .put("event_id", eventId)
            .put("sender_device_id", senderDeviceId)
            .put("target_device_id", targetDeviceId)
            .put("sequence", sequence)
            .put("issued_at_ms", issuedAtMs)
            .put("expires_at_ms", expiresAtMs)
            .put("nonce", encoder.encodeToString(nonce))
            .put("ciphertext", encoder.encodeToString(ciphertext))
            .put("authentication_tag", encoder.encodeToString(tag))
            .toString().toByteArray(Charsets.UTF_8)
        if (body.isEmpty() || body.size > OTP_RELAY_MAX_BODY_BYTES) {
            body.wipe()
            throw OtpRelayRejected("OTP wire envelope exceeds online limit")
        }
        return body
    }

    override fun close() { nonce.wipe(); ciphertext.wipe(); tag.wipe() }
    override fun toString(): String = "<OtpWireEnvelope redacted>"
}

private fun sealOtp(
    plaintext: ByteArray,
    material: OtpPairMaterialLease,
    eventId: String,
    issuedAtMs: Long,
    expiresAtMs: Long,
    nonce: ByteArray,
): OtpWireEnvelope {
    val key = deriveOtpAesKey(
        material.verifier(), material.sessionEpoch,
        material.senderDeviceId, material.targetDeviceId,
    )
    val aad = canonicalOtpAad(
        material.sessionEpoch, eventId, material.senderDeviceId,
        material.targetDeviceId, material.sequence, issuedAtMs, expiresAtMs,
    )
    var combined: ByteArray? = null
    var delivered = false
    try {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        cipher.updateAAD(aad)
        combined = cipher.doFinal(plaintext)
        if (combined.size != plaintext.size + OTP_TAG_BYTES) {
            throw OtpRelayRejected("invalid JCA GCM output")
        }
        val ciphertext = combined.copyOfRange(0, plaintext.size)
        val tag = combined.copyOfRange(plaintext.size, combined.size)
        delivered = true
        return OtpWireEnvelope(
            material.sessionEpoch, eventId, material.senderDeviceId,
            material.targetDeviceId, material.sequence, issuedAtMs,
            expiresAtMs, nonce, ciphertext, tag,
        )
    } finally {
        key.wipe(); aad.wipe(); combined?.wipe()
        if (!delivered) nonce.wipe()
    }
}

/** Runtime-only producer. Construction grants no capture, pairing, or network authority. */
class OtpJcaRelayProducer internal constructor(
    private val capturePort: OtpCapturePort,
    private val pairMaterialPort: OtpPairMaterialPort,
    private val transport: OtpOnlineTransportPort,
    private val nonceSource: OtpNonceSource,
    private val eventIdSource: OtpEventIdSource,
    private val wallClockMs: () -> Long,
    private val monotonicClockMs: () -> Long,
) : AutoCloseable {
    constructor(
        capturePort: OtpCapturePort = DisabledOtpCapturePort(),
        pairMaterialPort: OtpPairMaterialPort = DisabledOtpPairMaterialPort(),
        transport: OtpOnlineTransportPort = DisabledOtpOnlineTransportPort(),
    ) : this(
        capturePort, pairMaterialPort, transport,
        SecureRandomOtpNonceSource(), OtpEventIdSource { UUID.randomUUID().toString() },
        System::currentTimeMillis, { System.nanoTime() / 1_000_000L },
    )

    private val stateLock = Any()
    private var nonceScope: String? = null
    private var lastSequence = 0L
    private val nonceDigests = LinkedHashSet<String>()
    private val retiredScopes = LinkedHashSet<String>()
    private var closed = false

    private fun reserveInProcess(material: OtpPairMaterialLease, nonce: ByteArray): Boolean =
        synchronized(stateLock) {
            if (closed) return@synchronized false
            val scope = "${material.sessionEpoch}|${material.senderDeviceId}|${material.targetDeviceId}"
            if (scope != nonceScope) {
                if (scope in retiredScopes) return@synchronized false
                nonceScope?.let {
                    if (retiredScopes.size >= OTP_RETIRED_SCOPE_CAPACITY) return@synchronized false
                    retiredScopes.add(it)
                }
                nonceScope = scope
                lastSequence = 0L
                nonceDigests.clear()
            }
            if (
                material.sequence <= lastSequence ||
                nonceDigests.size >= OTP_PAIR_NONCE_HISTORY_CAPACITY
            ) {
                return@synchronized false
            }
            val digestBytes = MessageDigest.getInstance("SHA-256").digest(nonce)
            val digest = try { Base64.getEncoder().encodeToString(digestBytes) }
            finally { digestBytes.wipe() }
            if (!nonceDigests.add(digest)) return@synchronized false
            lastSequence = material.sequence
            true
        }

    fun captureAndRelay(
        authorization: OtpCaptureAuthorization,
        explicitUserAction: Boolean,
        ttlMs: Long = 120_000L,
    ): OtpRelaySendStatus {
        synchronized(stateLock) { if (closed) return OtpRelaySendStatus.DISABLED }
        var isolated: IsolatedOtpCandidate? = null
        var characters: CharArray? = null
        var plaintext: ByteArray? = null
        var material: OtpPairMaterialLease? = null
        var nonce: ByteArray? = null
        var envelope: OtpWireEnvelope? = null
        var wireBody: ByteArray? = null
        return try {
            if (!capturePort.isAuthorizationCurrent(authorization)) {
                return OtpRelaySendStatus.DISABLED
            }
            if (capturePort.source != authorization.source) {
                throw OtpRelayRejected("capture source mismatch")
            }
            isolated = capturePort.capture(authorization) ?: return OtpRelaySendStatus.DISABLED
            if (!capturePort.isAuthorizationCurrent(authorization)) {
                return OtpRelaySendStatus.DISABLED
            }
            characters = isolated.take(authorization, monotonicClockMs(), explicitUserAction)
            if (characters.size !in 4..8) throw OtpRelayRejected("invalid OTP length")
            if (!capturePort.isAuthorizationCurrent(authorization)) {
                return OtpRelaySendStatus.DISABLED
            }
            plaintext = ByteArray(characters.size)
            for (index in characters.indices) {
                val value = characters[index]
                if (value !in '0'..'9') {
                    throw OtpRelayRejected("invalid OTP character")
                }
                plaintext[index] = value.code.toByte()
            }
            if (ttlMs !in 1..OTP_RELAY_MAX_TTL_MS) throw OtpRelayRejected("invalid OTP TTL")
            val issuedAtMs = wallClockMs()
            if (issuedAtMs < 0L || issuedAtMs > Long.MAX_VALUE - ttlMs) {
                throw OtpRelayRejected("invalid OTP issue time")
            }
            val expiresAtMs = issuedAtMs + ttlMs
            val eventId = canonicalUuid4(eventIdSource.nextEventId(), "event id").toString()
            nonce = nonceSource.nextNonce()
            if (nonce.size != OTP_NONCE_BYTES) throw OtpRelayRejected("invalid OTP nonce")
            material = when (val acquired = pairMaterialPort.acquire(authorization, nonce)) {
                is OtpPairMaterialAcquireResult.Acquired -> acquired.lease
                OtpPairMaterialAcquireResult.Unpaired -> return OtpRelaySendStatus.UNPAIRED
                OtpPairMaterialAcquireResult.Mismatch -> return OtpRelaySendStatus.MISMATCH
                OtpPairMaterialAcquireResult.RotationRequired -> {
                    return OtpRelaySendStatus.ROTATION_REQUIRED
                }
                OtpPairMaterialAcquireResult.Unavailable -> {
                    return OtpRelaySendStatus.TRANSIENT_FAILURE
                }
            }
            if (
                material.sessionEpoch != authorization.sessionEpoch ||
                material.senderDeviceId != authorization.senderDeviceId ||
                material.targetDeviceId != authorization.targetDeviceId ||
                !reserveInProcess(material, nonce)
            ) throw OtpRelayRejected("pair material or nonce reservation mismatch")
            if (!capturePort.isAuthorizationCurrent(authorization)) {
                return OtpRelaySendStatus.DISABLED
            }
            envelope = sealOtp(plaintext, material, eventId, issuedAtMs, expiresAtMs, nonce)
            nonce = null
            wireBody = envelope.toJsonBytes()
            // This is the last local pre-send guard. Once the transport call
            // has started, the event is considered in flight and cannot be
            // recalled from the remote broker; revoke prevents every later
            // capture/send attempt. A transport implementation may add a
            // stronger cancellation primitive in a future protocol version.
            if (!capturePort.isAuthorizationCurrent(authorization)) {
                return OtpRelaySendStatus.DISABLED
            }
            when (transport.post(wireBody)) {
                OtpOnlineTransportResult.Accepted -> OtpRelaySendStatus.ACCEPTED
                OtpOnlineTransportResult.AuthRequired -> OtpRelaySendStatus.AUTH_REQUIRED
                OtpOnlineTransportResult.Unpaired -> OtpRelaySendStatus.UNPAIRED
                OtpOnlineTransportResult.Mismatch -> OtpRelaySendStatus.MISMATCH
                OtpOnlineTransportResult.RotationRequired -> OtpRelaySendStatus.ROTATION_REQUIRED
                OtpOnlineTransportResult.TransientFailure -> {
                    // Sequence 4,096 is the reserved rotation probe. If it was
                    // consumed while offline, local state is still exhausted
                    // and must not collapse back to an ordinary retry signal.
                    if (material.sequence >= OTP_PAIR_NONCE_HISTORY_CAPACITY) {
                        OtpRelaySendStatus.ROTATION_REQUIRED
                    } else {
                        OtpRelaySendStatus.TRANSIENT_FAILURE
                    }
                }
                OtpOnlineTransportResult.AuthRejected -> OtpRelaySendStatus.AUTH_REJECTED
                OtpOnlineTransportResult.PolicyRejected -> OtpRelaySendStatus.POLICY_REJECTED
            }
        } catch (_: Exception) {
            if ((material?.sequence ?: 0L) >= OTP_PAIR_NONCE_HISTORY_CAPACITY) {
                OtpRelaySendStatus.ROTATION_REQUIRED
            } else {
                OtpRelaySendStatus.DROPPED
            }
        } finally {
            wireBody?.wipe(); envelope?.close(); nonce?.wipe(); material?.close()
            plaintext?.wipe(); characters?.wipe(); isolated?.close()
        }
    }

    override fun close() {
        synchronized(stateLock) {
            if (closed) return
            closed = true
            nonceScope = null; lastSequence = 0L
            nonceDigests.clear(); retiredScopes.clear()
        }
        try { capturePort.close() }
        finally {
            try { pairMaterialPort.close() }
            finally { transport.close() }
        }
    }

    override fun toString(): String = "<OtpJcaRelayProducer redacted>"
}
