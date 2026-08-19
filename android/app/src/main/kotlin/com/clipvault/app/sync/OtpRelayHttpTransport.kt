package com.clipvault.app.sync

import com.clipvault.app.otp.OTP_RELAY_MAX_BODY_BYTES
import com.clipvault.app.otp.OtpOnlineTransportPort
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL

private const val OTP_RELAY_PATH = "/otp/relay"
private const val OTP_CONNECT_TIMEOUT_MS = 2_000
private const val OTP_READ_TIMEOUT_MS = 3_000

/** Immutable bearer/endpoint snapshot whose default representation is redacted. */
internal class OtpPairedEndpointLease(
    val baseUrl: String,
    val bearerToken: String,
    private val currentCheck: () -> Boolean,
    private val rejectAuth: () -> Boolean,
) {
    fun isCurrent(): Boolean = currentCheck()
    fun rejectAuthentication(): Boolean = rejectAuth()
    override fun toString(): String = "<OtpPairedEndpointLease redacted>"
}

internal fun interface OtpPairedEndpointPort {
    fun acquire(): OtpPairedEndpointLease?
}

private class SettingsOtpPairedEndpointPort(
    private val settings: Settings,
) : OtpPairedEndpointPort {
    override fun acquire(): OtpPairedEndpointLease? {
        val snapshot = try {
            settings.requestSnapshot(hostOverride = null, auth = true)
        } catch (_: Exception) {
            return null
        }
        val bearer = snapshot.bearerToken?.takeIf(String::isNotEmpty) ?: return null
        return OtpPairedEndpointLease(
            baseUrl = snapshot.baseUrl,
            bearerToken = bearer,
            currentCheck = { settings.isCurrent(snapshot) },
            rejectAuth = { settings.clearTokenIfCurrent(snapshot) },
        )
    }
}

internal interface OtpHttpCall : AutoCloseable {
    fun configure(bearerToken: String, bodyLength: Int)
    fun write(body: ByteArray)
    fun responseCode(): Int
}

internal fun interface OtpHttpCallFactory {
    fun open(url: String): OtpHttpCall
}

private class UrlConnectionOtpHttpCall(
    private val connection: HttpURLConnection,
) : OtpHttpCall {
    override fun configure(bearerToken: String, bodyLength: Int) {
        connection.instanceFollowRedirects = false
        connection.requestMethod = "POST"
        connection.connectTimeout = OTP_CONNECT_TIMEOUT_MS
        connection.readTimeout = OTP_READ_TIMEOUT_MS
        connection.doOutput = true
        connection.setRequestProperty("Authorization", "Bearer $bearerToken")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setFixedLengthStreamingMode(bodyLength)
    }

    override fun write(body: ByteArray) {
        connection.outputStream.use { output: OutputStream -> output.write(body) }
    }

    override fun responseCode(): Int = connection.responseCode

    override fun close() {
        connection.disconnect()
    }
}

private val DEFAULT_OTP_HTTP_CALL_FACTORY = OtpHttpCallFactory { url ->
    UrlConnectionOtpHttpCall(URL(url).openConnection() as HttpURLConnection)
}

/**
 * Strictly-online OTP transport using the existing paired endpoint and bearer.
 *
 * The bearer authenticates the HTTP request only. OTP encryption keys come
 * from the separately injected OTP pair-material port in the producer.
 */
class OtpHttpOnlineTransport internal constructor(
    private val endpointPort: OtpPairedEndpointPort,
    private val callFactory: OtpHttpCallFactory,
) : OtpOnlineTransportPort {
    constructor(settings: Settings) : this(
        SettingsOtpPairedEndpointPort(settings),
        DEFAULT_OTP_HTTP_CALL_FACTORY,
    )

    @Volatile
    private var closed = false

    override fun post(wireBody: ByteArray): Boolean {
        if (closed || wireBody.isEmpty() || wireBody.size > OTP_RELAY_MAX_BODY_BYTES) {
            return false
        }
        val endpoint = endpointPort.acquire() ?: return false
        if (!endpoint.isCurrent()) return false
        val call = try {
            callFactory.open(endpoint.baseUrl + OTP_RELAY_PATH)
        } catch (_: Exception) {
            return false
        }
        return try {
            call.configure(endpoint.bearerToken, wireBody.size)
            call.write(wireBody)
            val status = call.responseCode()
            if (isPermanentSyncAuthFailure(status)) {
                endpoint.rejectAuthentication()
                false
            } else {
                endpoint.isCurrent() && status == HttpURLConnection.HTTP_ACCEPTED
            }
        } catch (_: Exception) {
            false
        } finally {
            try {
                call.close()
            } catch (_: Exception) {
                // The one-shot envelope is already terminal; never retry it.
            }
        }
    }

    override fun close() {
        closed = true
    }

    override fun toString(): String = "<OtpHttpOnlineTransport redacted>"
}
