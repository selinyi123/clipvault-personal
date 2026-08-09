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

private class WipeablePairResponseBuffer : ByteArrayOutputStream() {
    fun ownedCopy(): ByteArray = toByteArray()

    override fun close() {
        buf.fill(0)
        reset()
    }
}

private fun isOnline(context: Context): Boolean {
    val manager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        ?: return false
    val network = manager.activeNetwork ?: return false
    val capabilities = manager.getNetworkCapabilities(network) ?: return false
    return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) ||
        capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
}

private fun readBounded(input: java.io.InputStream, maxBytes: Int): ByteArray {
    val output = WipeablePairResponseBuffer()
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
        return output.ownedCopy()
    } finally {
        chunk.wipe()
        output.close()
    }
}

enum class OtpPairingStatus {
    PAIRED,
    ALREADY_PAIRED,
    OFFLINE,
    UNAVAILABLE,
    REPAIR_REQUIRED,
    REJECTED,
}

/** Redeems the Desktop verifier exactly once and ownership-transfers its response to Keystore storage. */
class OtpPairingClient(
    context: Context,
    private val pairStore: AndroidOtpPairStore = AndroidOtpPairStore(context.applicationContext),
    private val runtimeSettings: OtpRuntimeSettings = OtpRuntimeSettings(context.applicationContext),
    private val syncSettings: Settings = Settings(context.applicationContext),
) {
    private val appContext = context.applicationContext

    private fun requireRepair(): OtpPairingStatus = try {
        runtimeSettings.pairRepairRequired = true
        OtpPairingStatus.REPAIR_REQUIRED
    } catch (_: Exception) {
        OtpPairingStatus.UNAVAILABLE
    }

    fun pair(): OtpPairingStatus {
        if (runtimeSettings.pairRepairRequired) {
            return OtpPairingStatus.REPAIR_REQUIRED
        }
        when (pairStore.inspect().state) {
            OtpPairState.READY -> return OtpPairingStatus.ALREADY_PAIRED
            OtpPairState.ROTATION_REQUIRED -> return OtpPairingStatus.REPAIR_REQUIRED
            OtpPairState.UNAVAILABLE -> return OtpPairingStatus.UNAVAILABLE
            OtpPairState.UNPAIRED -> Unit
        }
        if (!isOnline(appContext)) return OtpPairingStatus.OFFLINE
        var requestBody: ByteArray? = null
        var responseBody: ByteArray? = null
        var requestMayBeCommitted = false
        var remoteCommitted = false
        return try {
            val snapshot = syncSettings.requestSnapshot(hostOverride = null, auth = true)
            if (!isOtpTransportBaseUrlAllowed(snapshot.baseUrl)) return OtpPairingStatus.REJECTED
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
                connection.outputStream.use {
                    // Once a connected request stream exists, a write/close or
                    // response-read failure cannot prove the one-time pairing
                    // mutation did not reach Desktop. Preserve the split-brain
                    // repair signal instead of presenting an ordinary retry.
                    requestMayBeCommitted = true
                    it.write(requestBody)
                }
                val responseCode = connection.responseCode
                if (responseCode == HttpURLConnection.HTTP_CONFLICT) {
                    return requireRepair()
                }
                if (responseCode != HttpURLConnection.HTTP_CREATED) {
                    return OtpPairingStatus.REJECTED
                }
                remoteCommitted = true
                if (!connection.getHeaderField("Cache-Control").orEmpty().contains("no-store")) {
                    return requireRepair()
                }
                responseBody = connection.inputStream.use { readBounded(it, OTP_PAIR_RESPONSE_MAX_BYTES) }
                if (!syncSettings.isCurrent(snapshot)) return requireRepair()
                val transferred = responseBody
                responseBody = null
                when (pairStore.importPairResponse(transferred, sender)) {
                    OtpPairImportResult.IMPORTED -> {
                        runtimeSettings.pairRepairRequired = false
                        OtpPairingStatus.PAIRED
                    }
                    OtpPairImportResult.CONFLICT,
                    OtpPairImportResult.UNAVAILABLE,
                    OtpPairImportResult.REJECTED -> requireRepair()
                }
            } finally {
                connection.disconnect()
            }
        } catch (_: Exception) {
            if (requestMayBeCommitted || remoteCommitted) requireRepair()
            else OtpPairingStatus.REJECTED
        } finally {
            requestBody?.wipe(); responseBody?.wipe()
        }
    }

    override fun toString(): String = "<OtpPairingClient redacted>"
}
