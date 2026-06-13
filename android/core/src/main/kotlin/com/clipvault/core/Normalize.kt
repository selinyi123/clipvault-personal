package com.clipvault.core

import java.security.MessageDigest
import java.text.Normalizer

/** NORM-1 (CONTRACTS §2). Must match desktop clipvault.core.normalize byte-for-byte. */
object Normalize {
    const val DEFAULT_MAX_CLIP_BYTES = 1_048_576
    const val REJECT_EMPTY = "empty"
    const val REJECT_TOO_LARGE = "too_large"

    fun normalize(text: String): String {
        var s = text.replace("\r\n", "\n").replace("\r", "\n")
        s = Normalizer.normalize(s, Normalizer.Form.NFC)
        return s.trimEnd()  // Kotlin trimEnd() strips Unicode whitespace, like Python rstrip()
    }

    fun rejectReason(normalized: String, maxBytes: Int = DEFAULT_MAX_CLIP_BYTES): String? {
        if (normalized.isEmpty()) return REJECT_EMPTY
        if (normalized.toByteArray(Charsets.UTF_8).size > maxBytes) return REJECT_TOO_LARGE
        return null
    }

    fun contentHash(normalized: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(normalized.toByteArray(Charsets.UTF_8))
        return digest.joinToString("") { "%02x".format(it) }
    }
}
