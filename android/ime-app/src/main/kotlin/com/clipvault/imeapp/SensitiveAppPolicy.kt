package com.clipvault.imeapp

/** Local-only policy. A missing package identity or unreadable policy fails closed. */
internal class SensitiveAppPolicy(
    private val packageReader: () -> Set<String>?,
) {
    fun isSensitive(packageName: String?): Boolean {
        val normalized = packageName?.trim()?.lowercase()
        if (normalized.isNullOrEmpty()) return true
        val packages = try {
            packageReader()
        } catch (_: RuntimeException) {
            null
        } ?: return true
        return normalized in packages
    }
}
