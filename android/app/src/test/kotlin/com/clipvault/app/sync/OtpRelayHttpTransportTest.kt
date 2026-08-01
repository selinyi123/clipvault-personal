package com.clipvault.app.sync

import com.clipvault.app.otp.OTP_RELAY_MAX_BODY_BYTES
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpRelayHttpTransportTest {
    private class EndpointPort(
        private val current: Boolean = true,
        private val baseUrl: String = "http://127.0.0.1:8787/api",
    ) : OtpPairedEndpointPort {
        var acquired = 0
        var rejected = 0

        override fun acquire(): OtpPairedEndpointLease {
            acquired += 1
            return OtpPairedEndpointLease(
                baseUrl = baseUrl,
                bearerToken = "paired-bearer-only",
                currentCheck = { current },
                rejectAuth = {
                    rejected += 1
                    true
                },
            )
        }
    }

    private class Call(
        private val status: Int,
        private val failWrite: Boolean = false,
    ) : OtpHttpCall {
        var bearer: String? = null
        var length = -1
        var body: ByteArray? = null
        var closed = 0

        override fun configure(bearerToken: String, bodyLength: Int) {
            bearer = bearerToken
            length = bodyLength
        }

        override fun write(body: ByteArray) {
            if (failWrite) throw java.io.IOException("offline")
            this.body = body.copyOf()
        }

        override fun responseCode(): Int = status

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

        assertTrue(transport.post(body))
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
        assertFalse(first.post(byteArrayOf(1)))
        assertEquals(1, firstEndpoint.acquired)
        assertEquals(1, openFailure.opens)

        val secondEndpoint = EndpointPort()
        val call = Call(status = 202, failWrite = true)
        val writeFailure = Factory(call)
        val second = OtpHttpOnlineTransport(secondEndpoint, writeFailure)
        assertFalse(second.post(byteArrayOf(2)))
        assertEquals(1, secondEndpoint.acquired)
        assertEquals(1, writeFailure.opens)
        assertEquals(1, call.closed)
    }

    @Test
    fun oversizedEnvelopeIsRejectedBeforePairOrNetworkAccess() {
        val endpoint = EndpointPort()
        val factory = Factory(Call(202))
        val transport = OtpHttpOnlineTransport(endpoint, factory)
        val oversized = ByteArray(OTP_RELAY_MAX_BODY_BYTES + 1)
        try {
            assertFalse(transport.post(oversized))
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
            assertFalse(transport.post(byteArrayOf(1)))
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
        assertFalse(transport.post(byteArrayOf(1)))
        assertEquals(1, endpoint.rejected)
        assertEquals(1, factory.opens)
        assertEquals(1, call.closed)
    }

    @Test
    fun stalePairingOrClosedTransportDropsAcceptedResponse() {
        val stale = EndpointPort(current = false)
        val call = Call(status = 202)
        val factory = Factory(call)
        val transport = OtpHttpOnlineTransport(stale, factory)
        assertFalse(transport.post(byteArrayOf(1)))
        transport.close()
        assertFalse(transport.post(byteArrayOf(2)))
        assertEquals(0, factory.opens)
    }
}
