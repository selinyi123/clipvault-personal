package com.clipvault.app.otp

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest
import java.util.Base64
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

class OtpRelayProducerTest {
    private class Capture(
        override val source: OtpCaptureSource,
        private val candidates: ArrayDeque<CharArray>,
    ) : OtpCapturePort {
        var calls = 0
        var closed = 0

        override fun capture(authorization: OtpCaptureAuthorization): IsolatedOtpCandidate? {
            calls += 1
            val candidate = candidates.removeFirstOrNull() ?: return null
            return IsolatedOtpCandidate(
                source = source,
                grantId = authorization.grantId,
                targetDeviceId = authorization.targetDeviceId,
                ownedDigits = candidate,
            )
        }

        override fun close() {
            closed += 1
            candidates.forEach { it.fill('\u0000') }
            candidates.clear()
        }
    }

    private class PairMaterials(
        private val leases: ArrayDeque<OtpPairMaterialLease>,
    ) : OtpPairMaterialPort {
        var calls = 0
        var closed = 0

        override fun acquire(authorization: OtpCaptureAuthorization): OtpPairMaterialLease? {
            calls += 1
            return leases.removeFirstOrNull()
        }

        override fun close() {
            closed += 1
            leases.forEach(OtpPairMaterialLease::close)
            leases.clear()
        }
    }

    private class RecordingTransport(
        private val accepted: Boolean = true,
    ) : OtpOnlineTransportPort {
        var calls = 0
        var closed = 0
        var copiedBody: ByteArray? = null
        var originalBody: ByteArray? = null

        override fun post(wireBody: ByteArray): Boolean {
            calls += 1
            originalBody = wireBody
            copiedBody = wireBody.copyOf()
            return accepted
        }

        override fun close() {
            closed += 1
        }
    }

    private data class Vector(
        val root: JSONObject,
        val inputs: JSONObject,
        val expected: JSONObject,
    )

    private fun vector(): Vector {
        val root = JSONObject(
            File("../../contracts/vectors/otp_aead_v1.json").readText(Charsets.UTF_8),
        )
        return Vector(root, root.getJSONObject("inputs"), root.getJSONObject("derived"))
    }

    private fun authorization(inputs: JSONObject, automatic: Boolean = false) =
        OtpCaptureAuthorization(
            grantId = "55555555-5555-4555-8555-555555555555",
            source = OtpCaptureSource.SMS_CODE_AUTOFILL,
            sessionEpoch = inputs.getString("session_epoch"),
            senderDeviceId = inputs.getString("sender_device"),
            targetDeviceId = inputs.getString("target_device"),
            expiresAtMonotonicMs = 20_000L,
            platformGranted = true,
            automaticCapture = automatic,
        )

    private fun hex(value: String): ByteArray {
        require(value.length % 2 == 0)
        return ByteArray(value.length / 2) { index ->
            value.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
    }

    private fun ByteArray.hex(): String = joinToString("") { "%02x".format(it) }

    private fun decrypt(
        key: ByteArray,
        nonce: ByteArray,
        aad: ByteArray,
        ciphertext: ByteArray,
        tag: ByteArray,
    ): ByteArray = Cipher.getInstance("AES/GCM/NoPadding").run {
        init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        updateAAD(aad)
        doFinal(ciphertext + tag)
    }

    @Test
    fun jcaHkdfAadCiphertextAndTagMatchCanonicalVectorByteForByte() {
        val vector = vector()
        val inputs = vector.inputs
        val expected = vector.expected
        assertEquals("OTP-3A", vector.root.getString("contract"))
        assertEquals("AES-256-GCM", vector.root.getString("algorithm"))

        val pairVerifier = MessageDigest.getInstance("SHA-256").digest(
            inputs.getString("pair_secret_utf8").toByteArray(Charsets.UTF_8),
        )
        assertEquals(expected.getString("pair_verifier_sha256_hex"), pairVerifier.hex())
        val key = deriveOtpAesKey(
            pairVerifier,
            inputs.getString("session_epoch"),
            inputs.getString("sender_device"),
            inputs.getString("target_device"),
        )
        try {
            assertEquals(expected.getString("key_hex"), key.hex())
        } finally {
            key.fill(0)
        }

        val aad = canonicalOtpAad(
            inputs.getString("session_epoch"),
            inputs.getString("event_id"),
            inputs.getString("sender_device"),
            inputs.getString("target_device"),
            inputs.getLong("sequence"),
            inputs.getLong("issued_at_unix_ms"),
            inputs.getLong("expires_at_unix_ms"),
        )
        try {
            assertEquals(expected.getString("aad_hex"), aad.hex())
            assertEquals(
                expected.getString("aad_sha256_hex"),
                MessageDigest.getInstance("SHA-256").digest(aad).hex(),
            )
        } finally {
            aad.fill(0)
        }

        val candidate = inputs.getString("plaintext_ascii").toCharArray()
        val verifierOwnedByLease = pairVerifier.copyOf()
        val nonceOwnedByEnvelope = hex(inputs.getString("nonce_hex"))
        val capture = Capture(
            OtpCaptureSource.SMS_CODE_AUTOFILL,
            ArrayDeque(listOf(candidate)),
        )
        val pairs = PairMaterials(
            ArrayDeque(
                listOf(
                    OtpPairMaterialLease(
                        inputs.getString("session_epoch"),
                        inputs.getString("sender_device"),
                        inputs.getString("target_device"),
                        inputs.getLong("sequence"),
                        verifierOwnedByLease,
                    ),
                ),
            ),
        )
        val transport = RecordingTransport()
        val producer = OtpJcaRelayProducer(
            capture,
            pairs,
            transport,
            OtpNonceSource { nonceOwnedByEnvelope },
            OtpEventIdSource { inputs.getString("event_id") },
            { inputs.getLong("issued_at_unix_ms") },
            { 10_000L },
        )
        val status = producer.captureAndRelay(
            authorization(inputs),
            explicitUserAction = true,
            ttlMs = inputs.getLong("expires_at_unix_ms") -
                inputs.getLong("issued_at_unix_ms"),
        )
        assertEquals(OtpRelaySendStatus.ACCEPTED, status)

        val copiedBody = requireNotNull(transport.copiedBody)
        try {
            val body = JSONObject(copiedBody.toString(Charsets.UTF_8))
            val fields = mutableSetOf<String>()
            body.keys().forEachRemaining(fields::add)
            assertEquals(
                setOf(
                    "version",
                    "algorithm",
                    "session_epoch",
                    "event_id",
                    "sender_device_id",
                    "target_device_id",
                    "sequence",
                    "issued_at_ms",
                    "expires_at_ms",
                    "nonce",
                    "ciphertext",
                    "authentication_tag",
                ),
                fields,
            )
            assertFalse(body.has("key_id"))
            assertEquals(1, body.getInt("version"))
            assertEquals("A256GCM", body.getString("algorithm"))
            assertEquals(inputs.getLong("sequence"), body.getLong("sequence"))
            val decoder = Base64.getUrlDecoder()
            assertArrayEquals(
                hex(expected.getString("ciphertext_hex")),
                decoder.decode(body.getString("ciphertext")),
            )
            assertArrayEquals(
                hex(expected.getString("authentication_tag_hex")),
                decoder.decode(body.getString("authentication_tag")),
            )
            assertEquals(12, decoder.decode(body.getString("nonce")).size)
            assertTrue(copiedBody.size <= OTP_RELAY_MAX_BODY_BYTES)
        } finally {
            copiedBody.fill(0)
            pairVerifier.fill(0)
        }

        assertTrue(candidate.all { it == '\u0000' })
        assertTrue(verifierOwnedByLease.all { it == 0.toByte() })
        assertTrue(nonceOwnedByEnvelope.all { it == 0.toByte() })
        assertTrue(requireNotNull(transport.originalBody).all { it == 0.toByte() })
    }

    @Test
    fun everyAadFieldMutationChangesCanonicalBytes() {
        val inputs = vector().inputs
        val base = canonicalOtpAad(
            inputs.getString("session_epoch"),
            inputs.getString("event_id"),
            inputs.getString("sender_device"),
            inputs.getString("target_device"),
            inputs.getLong("sequence"),
            inputs.getLong("issued_at_unix_ms"),
            inputs.getLong("expires_at_unix_ms"),
        )
        val mutations = listOf(
            base.copyOf().also { it[OTP_AAD_PREFIX_TEST_BYTES] = (it[OTP_AAD_PREFIX_TEST_BYTES].toInt() xor 1).toByte() },
            canonicalOtpAad(
                "11111111-1111-4111-8111-111111111112",
                inputs.getString("event_id"), inputs.getString("sender_device"),
                inputs.getString("target_device"), inputs.getLong("sequence"),
                inputs.getLong("issued_at_unix_ms"), inputs.getLong("expires_at_unix_ms"),
            ),
            canonicalOtpAad(
                inputs.getString("session_epoch"), "22222222-2222-4222-8222-222222222223",
                inputs.getString("sender_device"), inputs.getString("target_device"),
                inputs.getLong("sequence"), inputs.getLong("issued_at_unix_ms"),
                inputs.getLong("expires_at_unix_ms"),
            ),
            canonicalOtpAad(
                inputs.getString("session_epoch"), inputs.getString("event_id"),
                "device:33333333-3333-4333-8333-333333333332",
                inputs.getString("target_device"), inputs.getLong("sequence"),
                inputs.getLong("issued_at_unix_ms"), inputs.getLong("expires_at_unix_ms"),
            ),
            canonicalOtpAad(
                inputs.getString("session_epoch"), inputs.getString("event_id"),
                inputs.getString("sender_device"),
                "device:44444444-4444-4444-8444-444444444445",
                inputs.getLong("sequence"), inputs.getLong("issued_at_unix_ms"),
                inputs.getLong("expires_at_unix_ms"),
            ),
            canonicalOtpAad(
                inputs.getString("session_epoch"), inputs.getString("event_id"),
                inputs.getString("sender_device"), inputs.getString("target_device"),
                inputs.getLong("sequence") xor 1, inputs.getLong("issued_at_unix_ms"),
                inputs.getLong("expires_at_unix_ms"),
            ),
            canonicalOtpAad(
                inputs.getString("session_epoch"), inputs.getString("event_id"),
                inputs.getString("sender_device"), inputs.getString("target_device"),
                inputs.getLong("sequence"), inputs.getLong("issued_at_unix_ms") xor 1,
                inputs.getLong("expires_at_unix_ms"),
            ),
            canonicalOtpAad(
                inputs.getString("session_epoch"), inputs.getString("event_id"),
                inputs.getString("sender_device"), inputs.getString("target_device"),
                inputs.getLong("sequence"), inputs.getLong("issued_at_unix_ms"),
                inputs.getLong("expires_at_unix_ms") xor 1,
            ),
        )
        try {
            mutations.forEach { assertFalse(MessageDigest.isEqual(base, it)) }
        } finally {
            base.fill(0)
            mutations.forEach { it.fill(0) }
        }
    }

    @Test
    fun oneBitAadNonceCiphertextOrTagTamperFailsJcaAuthentication() {
        val vector = vector()
        val inputs = vector.inputs
        val expected = vector.expected
        val verifier = MessageDigest.getInstance("SHA-256").digest(
            inputs.getString("pair_secret_utf8").toByteArray(),
        )
        val key = deriveOtpAesKey(
            verifier,
            inputs.getString("session_epoch"),
            inputs.getString("sender_device"),
            inputs.getString("target_device"),
        )
        val nonce = hex(inputs.getString("nonce_hex"))
        val aad = hex(expected.getString("aad_hex"))
        val ciphertext = hex(expected.getString("ciphertext_hex"))
        val tag = hex(expected.getString("authentication_tag_hex"))
        val plaintext = decrypt(key, nonce, aad, ciphertext, tag)
        assertEquals(inputs.getString("plaintext_ascii"), plaintext.toString(Charsets.US_ASCII))
        plaintext.fill(0)

        val variants = listOf(
            arrayOf(nonce.copyOf(), aad.copyOf().also { it[0] = (it[0].toInt() xor 1).toByte() }, ciphertext.copyOf(), tag.copyOf()),
            arrayOf(nonce.copyOf().also { it[0] = (it[0].toInt() xor 1).toByte() }, aad.copyOf(), ciphertext.copyOf(), tag.copyOf()),
            arrayOf(nonce.copyOf(), aad.copyOf(), ciphertext.copyOf().also { it[0] = (it[0].toInt() xor 1).toByte() }, tag.copyOf()),
            arrayOf(nonce.copyOf(), aad.copyOf(), ciphertext.copyOf(), tag.copyOf().also { it[0] = (it[0].toInt() xor 1).toByte() }),
        )
        try {
            variants.forEach { changed ->
                assertThrows(AEADBadTagException::class.java) {
                    decrypt(key, changed[0], changed[1], changed[2], changed[3])
                }
            }
        } finally {
            verifier.fill(0)
            key.fill(0)
            nonce.fill(0)
            aad.fill(0)
            ciphertext.fill(0)
            tag.fill(0)
            variants.forEach { variant -> variant.forEach { it.fill(0) } }
        }
    }

    @Test
    fun defaultPortsRemainDisabledAndDoNotInventCaptureOrPairAuthority() {
        val inputs = vector().inputs
        val producer = OtpJcaRelayProducer()
        assertEquals(
            OtpRelaySendStatus.DISABLED,
            producer.captureAndRelay(
                authorization(inputs),
                explicitUserAction = true,
            ),
        )
        producer.close()
    }

    @Test
    fun explicitGrantFailureWipesCandidateBeforePairOrNetworkAccess() {
        val inputs = vector().inputs
        val candidate = "482917".toCharArray()
        val capture = Capture(
            OtpCaptureSource.SMS_CODE_AUTOFILL,
            ArrayDeque(listOf(candidate)),
        )
        val pairs = PairMaterials(ArrayDeque())
        val transport = RecordingTransport()
        val producer = OtpJcaRelayProducer(
            capture,
            pairs,
            transport,
            OtpNonceSource { ByteArray(12) },
            OtpEventIdSource { inputs.getString("event_id") },
            { inputs.getLong("issued_at_unix_ms") },
            { 10_000L },
        )
        assertEquals(
            OtpRelaySendStatus.DROPPED,
            producer.captureAndRelay(
                authorization(inputs),
                explicitUserAction = false,
            ),
        )
        assertTrue(candidate.all { it == '\u0000' })
        assertEquals(0, pairs.calls)
        assertEquals(0, transport.calls)
    }

    @Test
    fun offlineDeliveryIsTerminalAndAllOwnedBuffersAreWiped() {
        val vector = vector()
        val inputs = vector.inputs
        val candidate = "482917".toCharArray()
        val verifier = MessageDigest.getInstance("SHA-256").digest(
            inputs.getString("pair_secret_utf8").toByteArray(),
        )
        val nonce = ByteArray(12) { it.toByte() }
        val capture = Capture(
            OtpCaptureSource.SMS_CODE_AUTOFILL,
            ArrayDeque(listOf(candidate)),
        )
        val pairs = PairMaterials(
            ArrayDeque(
                listOf(
                    OtpPairMaterialLease(
                        inputs.getString("session_epoch"),
                        inputs.getString("sender_device"),
                        inputs.getString("target_device"),
                        1L,
                        verifier,
                    ),
                ),
            ),
        )
        val transport = RecordingTransport(accepted = false)
        val producer = OtpJcaRelayProducer(
            capture,
            pairs,
            transport,
            OtpNonceSource { nonce },
            OtpEventIdSource { inputs.getString("event_id") },
            { inputs.getLong("issued_at_unix_ms") },
            { 10_000L },
        )
        assertEquals(
            OtpRelaySendStatus.DROPPED,
            producer.captureAndRelay(authorization(inputs), explicitUserAction = true),
        )
        assertEquals(1, transport.calls)
        assertTrue(candidate.all { it == '\u0000' })
        assertTrue(verifier.all { it == 0.toByte() })
        assertTrue(nonce.all { it == 0.toByte() })
        assertTrue(requireNotNull(transport.originalBody).all { it == 0.toByte() })
        transport.copiedBody?.fill(0)
    }

    @Test
    fun repeatedNonceIsRejectedBeforeSecondEncryptionOrNetworkCall() {
        val vector = vector()
        val inputs = vector.inputs
        val firstCandidate = "482917".toCharArray()
        val secondCandidate = "482918".toCharArray()
        val firstVerifier = MessageDigest.getInstance("SHA-256").digest(
            inputs.getString("pair_secret_utf8").toByteArray(),
        )
        val secondVerifier = firstVerifier.copyOf()
        val firstNonce = ByteArray(12) { 7 }
        val secondNonce = firstNonce.copyOf()
        val nonces = ArrayDeque(listOf(firstNonce, secondNonce))
        val eventIds = ArrayDeque(
            listOf(
                inputs.getString("event_id"),
                "66666666-6666-4666-8666-666666666666",
            ),
        )
        val capture = Capture(
            OtpCaptureSource.SMS_CODE_AUTOFILL,
            ArrayDeque(listOf(firstCandidate, secondCandidate)),
        )
        val pairs = PairMaterials(
            ArrayDeque(
                listOf(
                    OtpPairMaterialLease(
                        inputs.getString("session_epoch"),
                        inputs.getString("sender_device"),
                        inputs.getString("target_device"),
                        1L,
                        firstVerifier,
                    ),
                    OtpPairMaterialLease(
                        inputs.getString("session_epoch"),
                        inputs.getString("sender_device"),
                        inputs.getString("target_device"),
                        2L,
                        secondVerifier,
                    ),
                ),
            ),
        )
        val transport = RecordingTransport()
        val producer = OtpJcaRelayProducer(
            capture,
            pairs,
            transport,
            OtpNonceSource { nonces.removeFirst() },
            OtpEventIdSource { eventIds.removeFirst() },
            { inputs.getLong("issued_at_unix_ms") },
            { 10_000L },
        )
        assertEquals(
            OtpRelaySendStatus.ACCEPTED,
            producer.captureAndRelay(authorization(inputs), explicitUserAction = true),
        )
        assertEquals(
            OtpRelaySendStatus.DROPPED,
            producer.captureAndRelay(authorization(inputs), explicitUserAction = true),
        )
        assertEquals(1, transport.calls)
        assertTrue(firstCandidate.all { it == '\u0000' })
        assertTrue(secondCandidate.all { it == '\u0000' })
        assertTrue(firstVerifier.all { it == 0.toByte() })
        assertTrue(secondVerifier.all { it == 0.toByte() })
        assertTrue(firstNonce.all { it == 0.toByte() })
        assertTrue(secondNonce.all { it == 0.toByte() })
        transport.copiedBody?.fill(0)
    }

    @Test
    fun invalidPairMaterialConstructionWipesTransferredVerifier() {
        val inputs = vector().inputs
        val invalidVerifier = ByteArray(31) { 9 }
        assertThrows(IllegalArgumentException::class.java) {
            OtpPairMaterialLease(
                inputs.getString("session_epoch"),
                inputs.getString("sender_device"),
                inputs.getString("target_device"),
                1L,
                invalidVerifier,
            )
        }
        assertTrue(invalidVerifier.all { it == 0.toByte() })
    }

    companion object {
        private const val OTP_AAD_PREFIX_TEST_BYTES = 28
    }
}
