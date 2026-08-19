package com.clipvault.app.otp

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
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

    @Test
    fun otpAeadV001MatchesFrozenHkdfAadCiphertextAndTag() {
        val verifier = MessageDigest.getInstance("SHA-256")
            .digest("clipvault-test-pair-token-v1".toByteArray())
        assertEquals(
            "1984dac1230b907d0d407910707577a37f0fa1d2676e3dec3903221edffb4a7d",
            verifier.hex(),
        )
        val key = deriveOtpAesKey(verifier, session, sender, target)
        assertEquals(
            "2a162d27a8d904a9f89858586c108f04d8cf93fb1e2af055bbc04549ac5faeae",
            key.hex(),
        )
        key.wipe()
        val aad = canonicalOtpAad(
            session, event, sender, target, 42L, 1_785_566_400_000L, 1_785_566_520_000L,
        )
        assertEquals(
            "436c69705661756c74204f54502052656c61792041454144207631000111111111111141118111111111111111222222222222422282222222222222223333333333334333833333333333333344444444444444448444444444444444000000000000002a0000019fbc0d0e000000019fbc0ee2c0",
            aad.hex(),
        )
        aad.wipe()

        val candidate = "482917".toCharArray()
        val pairPort = PairPort(verifier.copyOf(), 42L)
        val transport = Transport()
        val nonce = hex("000102030405060708090a0b")
        val producer = OtpJcaRelayProducer(
            Capture(candidate), pairPort, transport,
            OtpNonceSource { nonce }, OtpEventIdSource { event },
            { 1_785_566_400_000L }, { 10_000L },
        )
        assertEquals(
            OtpRelaySendStatus.ACCEPTED,
            producer.captureAndRelay(authorization(), explicitUserAction = true, ttlMs = 120_000L),
        )
        val body = JSONObject(requireNotNull(transport.body).toString(Charsets.UTF_8))
        val decoder = Base64.getUrlDecoder()
        assertArrayEquals(hex("89a93a853549"), decoder.decode(body.getString("ciphertext")))
        assertArrayEquals(hex("bd37d5d249eda03302fbe64b0014d882"), decoder.decode(body.getString("authentication_tag")))
        assertEquals(42L, body.getLong("sequence"))
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
