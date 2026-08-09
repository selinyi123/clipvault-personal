package com.clipvault.app.sync

import com.clipvault.app.otp.OTP_RELAY_MAX_BODY_BYTES
import com.clipvault.app.otp.OtpOnlineTransportResult
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpRelayHttpTransportTest {
    private class EndpointPort(
        private val current: Boolean = true,
        private val baseUrl: String = "http://127.0.0.1:8787/api",
        private val rejectFailure: Boolean = false,
    ) : OtpPairedEndpointPort {
        var acquired = 0
        var rejected = 0

        override fun acquire(): OtpPairedEndpointAcquireResult {
            acquired += 1
            return OtpPairedEndpointAcquireResult.Acquired(
                OtpPairedEndpointLease(
                    baseUrl = baseUrl,
                    bearerToken = "paired-bearer-only",
                    currentCheck = { current },
                    rejectAuth = {
                        rejected += 1
                        if (rejectFailure) throw java.io.IOException("clear failed")
                        true
                    },
                ),
            )
        }
    }

    private class Call(
        private val status: Int,
        private val failWrite: Boolean = false,
        private val errorBody: String = "",
    ) : OtpHttpCall {
        var bearer: String? = null
        var length = -1
        var body: ByteArray? = null
        var closed = 0
        var errorReads = 0

        override fun configure(bearerToken: String, bodyLength: Int) {
            bearer = bearerToken
            length = bodyLength
        }

        override fun write(body: ByteArray) {
            if (failWrite) throw java.io.IOException("offline")
            this.body = body.copyOf()
        }

        override fun responseCode(): Int = status

        override fun readErrorBody(maxBytes: Int): String {
            errorReads += 1
            if (errorBody.toByteArray(Charsets.UTF_8).size > maxBytes) {
                throw java.io.IOException("response body too large")
            }
            return errorBody
        }

        override fun close() {
            closed += 1
        }
    }

    private class Factory(
        private val call: Call? = null,
        private val failOpen: Boolean = false,
    ) : OtpHttpCallFactory {
        var opens = 0
        var url: String? = null

        override fun open(url: String): OtpHttpCall {
            opens += 1
            this.url = url
            if (failOpen) throw java.io.IOException("offline")
            return requireNotNull(call)
        }
    }

    @Test
    fun exactOtpRouteUsesPairedBearerOnceAndAcceptsOnly202() {
        val endpoint = EndpointPort()
        val call = Call(status = 202)
        val factory = Factory(call)
        val transport = OtpHttpOnlineTransport(endpoint, factory)
        val body = "{\"version\":1}".toByteArray()

        assertEquals(OtpOnlineTransportResult.Accepted, transport.post(body))
        assertEquals(1, endpoint.acquired)
        assertEquals(1, factory.opens)
        assertEquals("http://127.0.0.1:8787/api/otp/relay", factory.url)
        assertEquals("paired-bearer-only", call.bearer)
        assertEquals(body.size, call.length)
        assertArrayEquals(body, call.body)
        assertEquals(1, call.closed)
        assertEquals(0, endpoint.rejected)
        assertTrue(body.any { it != 0.toByte() })
        call.body?.fill(0)
        body.fill(0)
    }

    @Test
    fun offlineOpenOrWriteMakesOneAttemptAndNeverQueuesOrRetries() {
        val firstEndpoint = EndpointPort()
        val openFailure = Factory(failOpen = true)
        val first = OtpHttpOnlineTransport(firstEndpoint, openFailure)
        assertEquals(OtpOnlineTransportResult.TransientFailure, first.post(byteArrayOf(1)))
        assertEquals(1, firstEndpoint.acquired)
        assertEquals(1, openFailure.opens)

        val secondEndpoint = EndpointPort()
        val call = Call(status = 202, failWrite = true)
        val writeFailure = Factory(call)
        val second = OtpHttpOnlineTransport(secondEndpoint, writeFailure)
        assertEquals(OtpOnlineTransportResult.TransientFailure, second.post(byteArrayOf(2)))
        assertEquals(1, secondEndpoint.acquired)
        assertEquals(1, writeFailure.opens)
        assertEquals(1, call.closed)
    }

    @Test
    fun missingSyncPairAndTemporarySettingsFailureRemainDistinct() {
        val unpaired = OtpPairedEndpointPort { OtpPairedEndpointAcquireResult.AuthRequired }
        val unavailable = OtpPairedEndpointPort { OtpPairedEndpointAcquireResult.Unavailable }

        assertEquals(
            OtpOnlineTransportResult.AuthRequired,
            OtpHttpOnlineTransport(unpaired, Factory(Call(202))).post(byteArrayOf(1)),
        )
        assertEquals(
            OtpOnlineTransportResult.TransientFailure,
            OtpHttpOnlineTransport(unavailable, Factory(Call(202))).post(byteArrayOf(1)),
        )
    }

    @Test
    fun oversizedEnvelopeIsRejectedBeforePairOrNetworkAccess() {
        val endpoint = EndpointPort()
        val factory = Factory(Call(202))
        val transport = OtpHttpOnlineTransport(endpoint, factory)
        val oversized = ByteArray(OTP_RELAY_MAX_BODY_BYTES + 1)
        try {
            assertEquals(OtpOnlineTransportResult.PolicyRejected, transport.post(oversized))
            assertEquals(0, endpoint.acquired)
            assertEquals(0, factory.opens)
        } finally {
            oversized.fill(0)
        }
    }

    @Test
    fun cleartextLanOrHostnameIsRejectedBeforeOpeningConnection() {
        listOf(
            "http://192.168.1.20:8787/api",
            "http://desktop.tailnet.ts.net:8787/api",
        ).forEach { baseUrl ->
            val endpoint = EndpointPort(baseUrl = baseUrl)
            val factory = Factory(Call(202))
            val transport = OtpHttpOnlineTransport(endpoint, factory)
            assertEquals(OtpOnlineTransportResult.PolicyRejected, transport.post(byteArrayOf(1)))
            assertEquals(1, endpoint.acquired)
            assertEquals(0, factory.opens)
        }
    }

    @Test
    fun authFailureInvalidatesOnlyCapturedPairSnapshotAndDoesNotRetry() {
        val endpoint = EndpointPort()
        val call = Call(status = 401)
        val factory = Factory(call)
        val transport = OtpHttpOnlineTransport(endpoint, factory)
        assertEquals(OtpOnlineTransportResult.AuthRejected, transport.post(byteArrayOf(1)))
        assertEquals(1, endpoint.rejected)
        assertEquals(1, factory.opens)
        assertEquals(1, call.closed)
    }

    @Test
    fun confirmedAuthFailureRemainsTerminalWhenLocalTokenCleanupFails() {
        val endpoint = EndpointPort(rejectFailure = true)
        val call = Call(status = 401)
        val transport = OtpHttpOnlineTransport(endpoint, Factory(call))

        assertEquals(OtpOnlineTransportResult.AuthRejected, transport.post(byteArrayOf(1)))
        assertEquals(1, endpoint.rejected)
        assertEquals(1, call.closed)
    }

    @Test
    fun otpPairOrTransportPolicyFailureDoesNotInvalidateSharedSyncBearer() {
        val endpoint = EndpointPort()
        val call = Call(status = 403)
        val factory = Factory(call)
        val transport = OtpHttpOnlineTransport(endpoint, factory)

        assertEquals(OtpOnlineTransportResult.PolicyRejected, transport.post(byteArrayOf(1)))
        assertEquals(0, endpoint.rejected)
        assertEquals(1, factory.opens)
        assertEquals(1, call.closed)
    }

    @Test
    fun boundedPairAuthorizationErrorsRemainDistinctFromTransportPolicy() {
        val unpairedCall = Call(
            status = 403,
            errorBody = "{\"error\":{\"code\":\"otp_pair_not_authorized\",\"message\":\"rejected\"}}",
        )
        val unpairedEndpoint = EndpointPort()
        assertEquals(
            OtpOnlineTransportResult.Unpaired,
            OtpHttpOnlineTransport(unpairedEndpoint, Factory(unpairedCall)).post(byteArrayOf(1)),
        )

        val mismatchCall = Call(
            status = 403,
            errorBody = "{\"error\":{\"code\":\"otp_target_mismatch\",\"message\":\"rejected\"}}",
        )
        val mismatchEndpoint = EndpointPort()
        assertEquals(
            OtpOnlineTransportResult.Mismatch,
            OtpHttpOnlineTransport(mismatchEndpoint, Factory(mismatchCall)).post(byteArrayOf(1)),
        )
        assertEquals(0, unpairedEndpoint.rejected)
        assertEquals(0, mismatchEndpoint.rejected)
        assertEquals(1, unpairedCall.closed)
        assertEquals(1, mismatchCall.closed)
    }

    @Test
    fun stalePairingOrClosedTransportDropsAcceptedResponse() {
        val stale = EndpointPort(current = false)
        val call = Call(status = 202)
        val factory = Factory(call)
        val transport = OtpHttpOnlineTransport(stale, factory)
        assertEquals(OtpOnlineTransportResult.AuthRejected, transport.post(byteArrayOf(1)))
        transport.close()
        assertEquals(OtpOnlineTransportResult.PolicyRejected, transport.post(byteArrayOf(2)))
        assertEquals(0, factory.opens)
    }

    @Test
    fun boundedStrictRotationResponseProducesTypedRepairSignal() {
        val response = (
            "{\"error\":{\"code\":\"otp_pair_rotation_required\"," +
                "\"message\":\"OTP relay rejected\"}}"
            )
        val call = Call(status = 503, errorBody = response)
        val transport = OtpHttpOnlineTransport(EndpointPort(), Factory(call))

        assertEquals(OtpOnlineTransportResult.RotationRequired, transport.post(byteArrayOf(1)))
        assertEquals(1, call.errorReads)
        assertEquals(1, call.closed)
    }

    @Test
    fun malformedOrOversizedRotationResponseRemainsTransient() {
        val duplicateCode = (
            "{\"error\":{\"code\":\"otp_pair_rotation_required\"," +
                "\"code\":\"other\",\"message\":\"rejected\"}}"
            )
        val malformedCall = Call(status = 503, errorBody = duplicateCode)
        assertEquals(
            OtpOnlineTransportResult.TransientFailure,
            OtpHttpOnlineTransport(EndpointPort(), Factory(malformedCall)).post(byteArrayOf(1)),
        )

        val oversizedCall = Call(status = 503, errorBody = "x".repeat(1_025))
        assertEquals(
            OtpOnlineTransportResult.TransientFailure,
            OtpHttpOnlineTransport(EndpointPort(), Factory(oversizedCall)).post(byteArrayOf(1)),
        )
        assertEquals(1, malformedCall.closed)
        assertEquals(1, oversizedCall.closed)
    }
}
