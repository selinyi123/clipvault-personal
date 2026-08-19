package com.clipvault.app.otp

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.clipvault.app.sync.Settings
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

private const val OTP_PAIR_RESPONSE_MAX_BYTES = 1_024
private const val OTP_HTTP_CONNECT_TIMEOUT_MS = 2_500
private const val OTP_HTTP_READ_TIMEOUT_MS = 2_500

private fun isOnline(context: Context): Boolean {
    val manager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        ?: return false
    val network = manager.activeNetwork ?: return false
    val capabilities = manager.getNetworkCapabilities(network) ?: return false
    return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) ||
        capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
}

private fun readBounded(input: java.io.InputStream, maxBytes: Int): ByteArray {
    val output = ByteArrayOutputStream()
    val chunk = ByteArray(1_024)
    var total = 0
    try {
        while (true) {
            val read = input.read(chunk)
            if (read < 0) break
            total += read
            if (total > maxBytes) throw java.io.IOException("OTP response too large")
            output.write(chunk, 0, read)
        }
        return output.toByteArray()
    } finally {
        chunk.wipe()
    }
}

/** Direct synchronous transport. There is deliberately no retry or durable queue. */
class OtpHttpOnlineTransport(
    context: Context,
    private val settings: Settings = Settings(context.applicationContext),
) : OtpOnlineTransportPort {
    private val appContext = context.applicationContext

    override fun post(wireBody: ByteArray): Boolean {
        if (!isOnline(appContext) || wireBody.isEmpty() || wireBody.size > OTP_RELAY_MAX_BODY_BYTES) {
            return false
        }
        return try {
            val snapshot = settings.requestSnapshot(hostOverride = null, auth = true)
            if (!settings.isCurrent(snapshot)) return false
            val connection = (URL(snapshot.baseUrl + "/otp/relay").openConnection() as HttpURLConnection)
            try {
                connection.instanceFollowRedirects = false
                connection.requestMethod = "POST"
                connection.connectTimeout = OTP_HTTP_CONNECT_TIMEOUT_MS
                connection.readTimeout = OTP_HTTP_READ_TIMEOUT_MS
                connection.doOutput = true
                connection.setRequestProperty("Authorization", "Bearer ${snapshot.bearerToken}")
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setFixedLengthStreamingMode(wireBody.size)
                connection.outputStream.use { it.write(wireBody) }
                val accepted = connection.responseCode == HttpURLConnection.HTTP_ACCEPTED
                accepted && settings.isCurrent(snapshot)
            } finally {
                connection.disconnect()
            }
        } catch (_: Exception) {
            false
        }
    }

    override fun toString(): String = "<OtpHttpOnlineTransport redacted>"
}

enum class OtpPairingStatus { PAIRED, ALREADY_PAIRED, OFFLINE, REJECTED }

/** Redeems the Desktop verifier exactly once and ownership-transfers its response to Keystore storage. */
class OtpPairingClient(
    context: Context,
    private val pairStore: AndroidOtpPairStore = AndroidOtpPairStore(context.applicationContext),
    private val runtimeSettings: OtpRuntimeSettings = OtpRuntimeSettings(context.applicationContext),
    private val syncSettings: Settings = Settings(context.applicationContext),
) {
    private val appContext = context.applicationContext

    fun pair(): OtpPairingStatus {
        if (pairStore.summary() != null) return OtpPairingStatus.ALREADY_PAIRED
        if (!isOnline(appContext)) return OtpPairingStatus.OFFLINE
        var requestBody: ByteArray? = null
        var responseBody: ByteArray? = null
        return try {
            val snapshot = syncSettings.requestSnapshot(hostOverride = null, auth = true)
            val sender = runtimeSettings.senderDeviceId
            requestBody = JSONObject().put("sender_device_id", sender)
                .toString().toByteArray(Charsets.UTF_8)
            val connection = (URL(snapshot.baseUrl + "/otp/pair").openConnection() as HttpURLConnection)
            try {
                connection.instanceFollowRedirects = false
                connection.requestMethod = "POST"
                connection.connectTimeout = OTP_HTTP_CONNECT_TIMEOUT_MS
                connection.readTimeout = OTP_HTTP_READ_TIMEOUT_MS
                connection.doOutput = true
                connection.setRequestProperty("Authorization", "Bearer ${snapshot.bearerToken}")
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setFixedLengthStreamingMode(requestBody.size)
                connection.outputStream.use { it.write(requestBody) }
                if (connection.responseCode != HttpURLConnection.HTTP_CREATED) {
                    return OtpPairingStatus.REJECTED
                }
                if (!connection.getHeaderField("Cache-Control").orEmpty().contains("no-store")) {
                    return OtpPairingStatus.REJECTED
                }
                responseBody = connection.inputStream.use { readBounded(it, OTP_PAIR_RESPONSE_MAX_BYTES) }
                if (!syncSettings.isCurrent(snapshot)) return OtpPairingStatus.REJECTED
                val transferred = responseBody
                responseBody = null
                if (pairStore.importPairResponse(transferred, sender)) {
                    OtpPairingStatus.PAIRED
                } else {
                    OtpPairingStatus.REJECTED
                }
            } finally {
                connection.disconnect()
            }
        } catch (_: Exception) {
            OtpPairingStatus.REJECTED
        } finally {
            requestBody?.wipe(); responseBody?.wipe()
        }
    }

    override fun toString(): String = "<OtpPairingClient redacted>"
}
