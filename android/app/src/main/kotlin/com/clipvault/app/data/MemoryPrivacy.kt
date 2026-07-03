package com.clipvault.app.data

import com.clipvault.core.SecretGuard

/** SG-1 gate shared by sync ingestion and every local Memory candidate exit. */
internal object MemoryPrivacy {
    fun containsSecret(text: String, label: String?): Boolean =
        SecretGuard.scan(text).isSecret || (label != null && SecretGuard.scan(label).isSecret)
}
