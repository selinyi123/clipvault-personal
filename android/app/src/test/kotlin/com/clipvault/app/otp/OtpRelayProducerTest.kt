package com.clipvault.app.otp

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.Base64

class OtpRelayProducerTest {
    private val session = "11111111-1111-4111-8111-111111111111"
    private val event = "22222222-2222-4222-8222-222222222222"
    private val sender = "device:33333333-3333-4333-8333-333333333333"
    private val target = "device:44444444-4444-4444-8444-444444444444"

    private class Capture(private var value: CharArray?) : OtpCapturePort {
        override val source = OtpCaptureSource.SMS_USER_CONSENT
        override fun capture(authorization: OtpCaptureAuthorization): IsolatedOtpCandidate? {
            val owned = value ?: return null
            value = null
            return IsolatedOtpCandidate(source, authorization.grantId, authorization.targetDeviceId, owned)
        }
        override fun close() { value?.wipe(); value = null }
    }

    private class PairPort(private val verifier: ByteArray, private val sequence: Long) : OtpPairMaterialPort {
        var acquiredNonce: ByteArray? = null
        override fun acquire(
            authorization: OtpCaptureAuthorization,
            nonce: ByteArray,
        ): OtpPairMaterialLease {
            acquiredNonce = nonce.copyOf()
            return OtpPairMaterialLease(
                authorization.sessionEpoch, authorization.senderDeviceId,
                authorization.targetDeviceId, sequence, verifier.copyOf(),
            )
        }
        override fun close() { verifier.wipe(); acquiredNonce?.wipe() }
    }

    private class Transport : OtpOnlineTransportPort {
        var body: ByteArray? = null
        override fun post(wireBody: ByteArray): Boolean {
            body = wireBody.copyOf(); return true
        }
        override fun close() = Unit
    }

    private fun authorization() = OtpCaptureAuthorization(
        "55555555-5555-4555-8555-555555555555",
        OtpCaptureSource.SMS_USER_CONSENT,
        session, sender, target,
        expiresAtMonotonicMs = 20_000L,
        platformGranted = true,
    )

    private fun hex(value: String): ByteArray = ByteArray(value.length / 2) { index ->
        value.substring(index * 2, index * 2 + 2).toInt(16).toByte()
    }

    private fun ByteArray.hex(): String = joinToString("") { "%02x".format(it) }

    private fun frozenVector(): JSONObject {
        var cursor: Path? = Path.of("").toAbsolutePath().normalize()
        while (cursor != null) {
            val candidate = cursor.resolve("contracts/vectors/otp_aead_v1.json")
            if (Files.isRegularFile(candidate)) {
                return JSONObject(Files.readAllBytes(candidate).toString(Charsets.UTF_8))
            }
            cursor = cursor.parent
        }
        error("contracts/vectors/otp_aead_v1.json was not found from the Gradle test directory")
    }

    @Test
    fun otpAeadV001MatchesFrozenHkdfAadCiphertextAndTag() {
        val vector = frozenVector()
        val inputs = vector.getJSONObject("inputs")
        val derived = vector.getJSONObject("derived")
        assertEquals("OTP-AEAD-V001", vector.getString("vector_id"))
        assertEquals(session, inputs.getString("session_epoch"))
        assertEquals(event, inputs.getString("event_id"))
        assertEquals(sender, inputs.getString("sender_device"))
        assertEquals(target, inputs.getString("target_device"))
        val verifier = MessageDigest.getInstance("SHA-256")
            .digest(inputs.getString("pair_secret_utf8").toByteArray())
        assertEquals(
            derived.getString("pair_verifier_sha256_hex"),
            verifier.hex(),
        )
        val key = deriveOtpAesKey(verifier, session, sender, target)
        assertEquals(derived.getString("key_hex"), key.hex())
        key.wipe()
        val aad = canonicalOtpAad(
            session, event, sender, target,
            inputs.getLong("sequence"), inputs.getLong("issued_at_unix_ms"),
            inputs.getLong("expires_at_unix_ms"),
        )
        assertEquals(derived.getString("aad_hex"), aad.hex())
        aad.wipe()

        val candidate = inputs.getString("plaintext_ascii").toCharArray()
        val pairPort = PairPort(verifier.copyOf(), inputs.getLong("sequence"))
        val transport = Transport()
        val nonce = hex(inputs.getString("nonce_hex"))
        val producer = OtpJcaRelayProducer(
            Capture(candidate), pairPort, transport,
            OtpNonceSource { nonce }, OtpEventIdSource { event },
            { inputs.getLong("issued_at_unix_ms") }, { 10_000L },
        )
        assertEquals(
            OtpRelaySendStatus.ACCEPTED,
            producer.captureAndRelay(
                authorization(), explicitUserAction = true,
                ttlMs = inputs.getLong("expires_at_unix_ms") - inputs.getLong("issued_at_unix_ms"),
            ),
        )
        val body = JSONObject(requireNotNull(transport.body).toString(Charsets.UTF_8))
        val decoder = Base64.getUrlDecoder()
        assertArrayEquals(hex(derived.getString("ciphertext_hex")), decoder.decode(body.getString("ciphertext")))
        assertArrayEquals(hex(derived.getString("authentication_tag_hex")), decoder.decode(body.getString("authentication_tag")))
        assertEquals(inputs.getLong("sequence"), body.getLong("sequence"))
        assertTrue(candidate.all { it == '\u0000' })
        assertTrue(nonce.all { it == 0.toByte() })
        requireNotNull(transport.body).wipe()
        producer.close()
        verifier.wipe()
    }

    @Test
    fun defaultAuthoritiesFailClosedWithoutCapturePairOrNetwork() {
        OtpJcaRelayProducer().use { producer ->
            assertEquals(
                OtpRelaySendStatus.DISABLED,
                producer.captureAndRelay(authorization(), explicitUserAction = true),
            )
        }
    }

    @Test
    fun alphabeticCandidateIsRejectedByFrozenNumericProfile() {
        val verifier = ByteArray(32) { it.toByte() }
        val transport = Transport()
        val producer = OtpJcaRelayProducer(
            Capture("a7B9".toCharArray()), PairPort(verifier, 1L), transport,
            OtpNonceSource { ByteArray(12) { (it + 1).toByte() } },
            OtpEventIdSource { event }, { 1_000L }, { 1L },
        )
        assertEquals(
            OtpRelaySendStatus.DROPPED,
            producer.captureAndRelay(authorization(), explicitUserAction = true, ttlMs = 1_000L),
        )
        assertEquals(null, transport.body)
        producer.close()
    }
}
