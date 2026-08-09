package com.clipvault.app.sync

import com.clipvault.app.otp.OTP_RELAY_MAX_BODY_BYTES
import com.clipvault.app.otp.OtpOnlineTransportPort
import com.clipvault.app.otp.OtpOnlineTransportResult
import com.clipvault.app.otp.isOtpTransportBaseUrlAllowed
import org.json.JSONObject
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL

private const val OTP_RELAY_PATH = "/otp/relay"
private const val OTP_CONNECT_TIMEOUT_MS = 2_000
private const val OTP_READ_TIMEOUT_MS = 3_000
private const val OTP_ERROR_RESPONSE_MAX_BYTES = 1_024
private const val OTP_ROTATION_REQUIRED_CODE = "otp_pair_rotation_required"
private const val OTP_PAIR_NOT_AUTHORIZED_CODE = "otp_pair_not_authorized"
private val OTP_PAIR_MISMATCH_CODES = setOf("otp_sender_mismatch", "otp_target_mismatch")

private fun JSONObject.exactKeys(): Set<String> = buildSet {
    val iterator = keys()
    while (iterator.hasNext()) add(iterator.next())
}

private fun parseOtpErrorCode(raw: String): String? {
    val body = parseStrictJsonObject(raw) ?: return null
    if (body.exactKeys() != setOf("error")) return null
    val error = body.opt("error") as? JSONObject ?: return null
    if (error.exactKeys() != setOf("code", "message")) return null
    val code = error.opt("code") as? String ?: return null
    if (error.opt("message") !is String) return null
    return code.takeIf { it.length in 1..80 && it.all { ch -> ch == '_' || ch in 'a'..'z' } }
}

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

internal sealed class OtpPairedEndpointAcquireResult {
    class Acquired(val lease: OtpPairedEndpointLease) : OtpPairedEndpointAcquireResult()
    object AuthRequired : OtpPairedEndpointAcquireResult()
    object Unavailable : OtpPairedEndpointAcquireResult()
}

internal fun interface OtpPairedEndpointPort {
    fun acquire(): OtpPairedEndpointAcquireResult
}

internal class SettingsOtpPairedEndpointPort(
    private val settings: Settings,
) : OtpPairedEndpointPort {
    override fun acquire(): OtpPairedEndpointAcquireResult {
        val snapshot = try {
            settings.requestSnapshot(hostOverride = null, auth = true)
        } catch (_: SyncAuthException) {
            return OtpPairedEndpointAcquireResult.AuthRequired
        } catch (_: Exception) {
            return OtpPairedEndpointAcquireResult.Unavailable
        }
        val bearer = snapshot.bearerToken?.takeIf(String::isNotEmpty)
            ?: return OtpPairedEndpointAcquireResult.AuthRequired
        return OtpPairedEndpointAcquireResult.Acquired(
            OtpPairedEndpointLease(
                baseUrl = snapshot.baseUrl,
                bearerToken = bearer,
                currentCheck = { settings.isCurrent(snapshot) },
                rejectAuth = { settings.clearTokenIfCurrent(snapshot) },
            ),
        )
    }
}

internal interface OtpHttpCall : AutoCloseable {
    fun configure(bearerToken: String, bodyLength: Int)
    fun write(body: ByteArray)
    fun responseCode(): Int
    fun readErrorBody(maxBytes: Int): String
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

    override fun readErrorBody(maxBytes: Int): String {
        val input = connection.errorStream ?: return ""
        return input.use { readUtf8BodyBounded(it, maxBytes) }
    }

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

    override fun post(wireBody: ByteArray): OtpOnlineTransportResult {
        if (closed || wireBody.isEmpty() || wireBody.size > OTP_RELAY_MAX_BODY_BYTES) {
            return OtpOnlineTransportResult.PolicyRejected
        }
        val endpoint = when (val acquired = endpointPort.acquire()) {
            is OtpPairedEndpointAcquireResult.Acquired -> acquired.lease
            OtpPairedEndpointAcquireResult.AuthRequired -> {
                return OtpOnlineTransportResult.AuthRequired
            }
            OtpPairedEndpointAcquireResult.Unavailable -> {
                return OtpOnlineTransportResult.TransientFailure
            }
        }
        if (!endpoint.isCurrent()) return OtpOnlineTransportResult.AuthRejected
        if (!isOtpTransportBaseUrlAllowed(endpoint.baseUrl)) {
            return OtpOnlineTransportResult.PolicyRejected
        }
        val call = try {
            callFactory.open(endpoint.baseUrl + OTP_RELAY_PATH)
        } catch (_: Exception) {
            return OtpOnlineTransportResult.TransientFailure
        }
        return try {
            call.configure(endpoint.bearerToken, wireBody.size)
            call.write(wireBody)
            val status = call.responseCode()
            // OTP endpoints use 403 for pair authorization and transport-policy
            // failures even when the shared sync bearer is still valid. Only an
            // explicit 401 proves that this bearer itself was rejected.
            if (status == HttpURLConnection.HTTP_UNAUTHORIZED) {
                try {
                    endpoint.rejectAuthentication()
                } catch (_: Exception) {
                    // The server response already proved this bearer invalid.
                    // Local persistence cleanup is best-effort and must not
                    // downgrade the terminal auth result to a retryable error.
                }
                OtpOnlineTransportResult.AuthRejected
            } else if (!endpoint.isCurrent()) {
                OtpOnlineTransportResult.AuthRejected
            } else if (status == HttpURLConnection.HTTP_ACCEPTED) {
                OtpOnlineTransportResult.Accepted
            } else if (status == HttpURLConnection.HTTP_FORBIDDEN) {
                when (parseOtpErrorCode(call.readErrorBody(OTP_ERROR_RESPONSE_MAX_BYTES)).orEmpty()) {
                    OTP_PAIR_NOT_AUTHORIZED_CODE -> OtpOnlineTransportResult.Unpaired
                    in OTP_PAIR_MISMATCH_CODES -> OtpOnlineTransportResult.Mismatch
                    else -> OtpOnlineTransportResult.PolicyRejected
                }
            } else if (status == HttpURLConnection.HTTP_UNAVAILABLE) {
                if (
                    parseOtpErrorCode(call.readErrorBody(OTP_ERROR_RESPONSE_MAX_BYTES)) ==
                    OTP_ROTATION_REQUIRED_CODE
                ) {
                    OtpOnlineTransportResult.RotationRequired
                } else {
                    OtpOnlineTransportResult.TransientFailure
                }
            } else if (
                status == HttpURLConnection.HTTP_CLIENT_TIMEOUT ||
                status == 429 ||
                status in 500..599
            ) {
                OtpOnlineTransportResult.TransientFailure
            } else {
                OtpOnlineTransportResult.PolicyRejected
            }
        } catch (_: Exception) {
            OtpOnlineTransportResult.TransientFailure
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
