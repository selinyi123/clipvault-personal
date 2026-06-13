package com.clipvault.core

/** Mirrors desktop clipvault.core.models (CONTRACTS §1). */

val CONTENT_TYPES = listOf("text", "url", "path", "command", "code", "error_log", "prompt")

const val SECRET_LEVEL_HARD = "hard"
const val SECRET_LEVEL_SUSPECT = "suspect"

data class SecretVerdict(
    val isSecret: Boolean,
    val level: String?,
    val reasons: List<String> = emptyList(),
)

data class Clip(
    val id: String,
    val content: String,
    val contentHash: String,
    val contentType: String,
    val sourceDevice: String,
    val createdAt: String,
    val lastSeenAt: String,
    val isSecret: Boolean = false,
    val secretLevel: String? = null,
    val secretReasons: List<String> = emptyList(),
    val sourceApp: String? = null,
    val timesSeen: Int = 1,
    val pinned: Boolean = false,
    val favorite: Boolean = false,
    val deleted: Boolean = false,
)
