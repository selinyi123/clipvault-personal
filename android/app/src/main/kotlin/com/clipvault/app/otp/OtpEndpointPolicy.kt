package com.clipvault.app.otp

import java.net.Inet6Address
import java.net.InetAddress
import java.net.URL

private val TAILSCALE_V6_PREFIX = byteArrayOf(
    0xfd.toByte(), 0x7a, 0x11, 0x5c, 0xa1.toByte(), 0xe0.toByte(),
)

/**
 * OTP may reuse the sync HTTP bearer only when the transport itself is
 * confidential. HTTPS relies on the platform TLS verifier. Cleartext HTTP is
 * limited to literal loopback or Tailscale addresses so a LAN observer cannot
 * steal a bearer that also authorizes ordinary sync APIs.
 */
internal fun isOtpTransportBaseUrlAllowed(raw: String): Boolean {
    val url = try {
        URL(raw)
    } catch (_: Exception) {
        return false
    }
    if (url.userInfo != null || url.query != null || url.ref != null) return false
    return when (url.protocol.lowercase()) {
        "https" -> url.host.isNotBlank()
        "http" -> isLoopbackOrTailscaleLiteral(url.host)
        else -> false
    }
}

private fun isLoopbackOrTailscaleLiteral(rawHost: String): Boolean {
    val host = rawHost.removePrefix("[").removeSuffix("]")
    if (host.equals("localhost", ignoreCase = true)) return true
    if (host.contains('%')) return false

    val v4 = parseIpv4Literal(host)
    if (v4 != null) {
        return v4[0] == 127 || (v4[0] == 100 && v4[1] in 64..127)
    }
    if (!host.contains(':')) return false // Never DNS-resolve a cleartext hostname.

    val address = try {
        InetAddress.getByName(host)
    } catch (_: Exception) {
        return false
    }
    if (address !is Inet6Address) return false
    if (address.isLoopbackAddress) return true
    val bytes = address.address
    return bytes.size == 16 && TAILSCALE_V6_PREFIX.indices.all { index ->
        bytes[index] == TAILSCALE_V6_PREFIX[index]
    }
}

private fun parseIpv4Literal(host: String): IntArray? {
    val pieces = host.split('.')
    if (pieces.size != 4) return null
    val parsed = IntArray(4)
    for (index in pieces.indices) {
        val piece = pieces[index]
        if (piece.isEmpty() || piece.any { !it.isDigit() }) return null
        val value = piece.toIntOrNull() ?: return null
        if (value !in 0..255) return null
        parsed[index] = value
    }
    return parsed
}
