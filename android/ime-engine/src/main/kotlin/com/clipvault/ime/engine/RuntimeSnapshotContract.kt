package com.clipvault.ime.engine

/** One already-authorized candidate that may cross the Runtime/IME boundary. */
data class RuntimeSnapshotItem(
    val candidateId: String,
    val source: String,
    val label: String,
    val text: String,
)

data class RuntimeSnapshotFrame(
    val publisherEpoch: String,
    val snapshotGeneration: Long,
    val expiresAtElapsedMs: Long,
    val items: List<RuntimeSnapshotItem>,
)

/**
 * Prefix-free Binder Snapshot V1 shared by Runtime and the isolated IME.
 *
 * The protocol never accepts a key, prefix, editor package, or context text.
 * Oversized items are filtered as whole items: payload text is never truncated
 * and then presented as if it were a complete, safe-to-commit candidate.
 */
object RuntimeSnapshotContract {
    const val RUNTIME_PACKAGE = "com.clipvault.app"
    const val RUNTIME_SERVICE = "com.clipvault.app.runtime.ClipVaultSnapshotBrokerService"
    const val SIGNATURE_PERMISSION = "com.clipvault.permission.RUNTIME_SNAPSHOT"
    const val REQUEST_SNAPSHOT = 1
    const val RESPONSE_SNAPSHOT = 2
    const val KEY_LIMIT = "limit"
    const val KEY_REQUEST_ID = "request_id"
    const val KEY_INPUT_SESSION_GENERATION = "input_session_generation"
    const val KEY_PUBLISHER_EPOCH = "publisher_epoch"
    const val KEY_SNAPSHOT_GENERATION = "snapshot_generation"
    const val KEY_EXPIRES_AT_ELAPSED_MS = "expires_at_elapsed_ms"
    const val KEY_CANDIDATE_IDS = "candidate_ids"
    const val KEY_SOURCES = "sources"
    const val KEY_LABELS = "labels"
    const val KEY_TEXTS = "texts"

    const val MAX_ITEMS = 8
    const val MAX_CANDIDATE_ID_UTF8_BYTES = 128
    const val MAX_LABEL_UTF8_BYTES = 64
    const val MAX_TEXT_UTF8_BYTES = 16 * 1024
    const val MAX_FRAME_BYTES = 64 * 1024
    const val REQUEST_TIMEOUT_MS = 250L
    const val MAX_SNAPSHOT_LIFETIME_MS = 30_000L

    const val SOURCE_MEMORY = "memory"
    const val SOURCE_CLIPBOARD = "clipboard"

    private const val FRAME_OVERHEAD_BYTES = 2048
    private const val ITEM_OVERHEAD_BYTES = 96
    private val UUID_V4 = Regex(
        "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )

    /** Broker-side defense in depth over facade-returned candidates. */
    fun sanitizeForBroker(
        candidates: Iterable<RuntimeSnapshotItem>,
        limit: Int,
    ): List<RuntimeSnapshotItem> {
        val desired = limit.coerceIn(0, MAX_ITEMS)
        if (desired == 0) return emptyList()
        val accepted = ArrayList<RuntimeSnapshotItem>(desired)
        val acceptedIds = HashSet<String>(desired)
        var frameBytes = FRAME_OVERHEAD_BYTES
        for (candidate in candidates) {
            if (accepted.size >= desired) break
            if (!acceptedIds.add(candidate.candidateId)) continue
            val itemBytes = candidateWireBytes(candidate)
            if (itemBytes == null || frameBytes > MAX_FRAME_BYTES - itemBytes) {
                acceptedIds.remove(candidate.candidateId)
                continue
            }
            accepted += candidate
            frameBytes += itemBytes
        }
        return accepted
    }

    /** IME-side strict decoder. Any malformed or over-budget frame is rejected. */
    fun decodeFrame(
        publisherEpoch: String,
        snapshotGeneration: Long,
        expiresAtElapsedMs: Long,
        candidateIds: List<String>,
        sources: List<String>,
        labels: List<String>,
        texts: List<String>,
    ): RuntimeSnapshotFrame? {
        if (!isCanonicalPublisherEpoch(publisherEpoch) || snapshotGeneration <= 0L) return null
        val size = candidateIds.size
        if (
            size > MAX_ITEMS ||
            sources.size != size ||
            labels.size != size ||
            texts.size != size
        ) return null
        val items = candidateIds.indices.map { index ->
            RuntimeSnapshotItem(
                candidateId = candidateIds[index],
                source = sources[index],
                label = labels[index],
                text = texts[index],
            )
        }
        if (items.map { it.candidateId }.toSet().size != items.size) return null
        if (sanitizeForBroker(items, items.size) != items) return null
        return RuntimeSnapshotFrame(
            publisherEpoch = publisherEpoch,
            snapshotGeneration = snapshotGeneration,
            expiresAtElapsedMs = expiresAtElapsedMs,
            items = items,
        )
    }

    fun responseMatchesInputSession(
        currentInputSessionGeneration: Long,
        pendingInputSessionGeneration: Long,
        responseInputSessionGeneration: Long,
    ): Boolean = currentInputSessionGeneration > 0L &&
        currentInputSessionGeneration == pendingInputSessionGeneration &&
        currentInputSessionGeneration == responseInputSessionGeneration

    fun isFreshExpiry(nowElapsedMs: Long, expiresAtElapsedMs: Long): Boolean =
        expiresAtElapsedMs > nowElapsedMs &&
            expiresAtElapsedMs - nowElapsedMs <= MAX_SNAPSHOT_LIFETIME_MS

    fun isCanonicalPublisherEpoch(value: String): Boolean = UUID_V4.matches(value)

    fun acceptsSnapshotIdentity(
        currentPublisherEpoch: String?,
        lastSnapshotGeneration: Long,
        incomingPublisherEpoch: String,
        incomingSnapshotGeneration: Long,
    ): Boolean = isCanonicalPublisherEpoch(incomingPublisherEpoch) &&
        incomingSnapshotGeneration > 0L &&
        when (currentPublisherEpoch) {
            null -> true
            incomingPublisherEpoch -> incomingSnapshotGeneration > lastSnapshotGeneration
            else -> false
        }

    /** Conservative upper bound covering UTF-8 content and Binder UTF-16 strings. */
    fun estimatedFrameBytes(items: List<RuntimeSnapshotItem>): Int? {
        if (items.size > MAX_ITEMS || items.map { it.candidateId }.toSet().size != items.size) {
            return null
        }
        var bytes = FRAME_OVERHEAD_BYTES
        for (item in items) {
            val itemBytes = candidateWireBytes(item) ?: return null
            if (bytes > MAX_FRAME_BYTES - itemBytes) return null
            bytes += itemBytes
        }
        return bytes
    }

    private fun candidateWireBytes(item: RuntimeSnapshotItem): Int? {
        if (item.candidateId.isEmpty() || item.candidateId.length > MAX_CANDIDATE_ID_UTF8_BYTES) {
            return null
        }
        if (item.text.isEmpty() || item.text.length > MAX_TEXT_UTF8_BYTES) return null
        if (item.label.length > MAX_LABEL_UTF8_BYTES) return null
        if (item.source != SOURCE_MEMORY && item.source != SOURCE_CLIPBOARD) return null
        val idUtf8 = utf8SizeWithin(item.candidateId, MAX_CANDIDATE_ID_UTF8_BYTES) ?: return null
        val labelUtf8 = utf8SizeWithin(item.label, MAX_LABEL_UTF8_BYTES) ?: return null
        val textUtf8 = utf8SizeWithin(item.text, MAX_TEXT_UTF8_BYTES) ?: return null
        val sourceUtf8 = item.source.length
        val utf8Bytes = idUtf8 + sourceUtf8 + labelUtf8 + textUtf8
        val binderStringBytes = (
            item.candidateId.length + item.source.length + item.label.length + item.text.length + 4
        ) * 2
        return ITEM_OVERHEAD_BYTES + maxOf(utf8Bytes, binderStringBytes)
    }

    private fun utf8SizeWithin(value: String, limit: Int): Int? {
        if (value.length > limit) return null
        val size = value.toByteArray(Charsets.UTF_8).size
        return size.takeIf { it <= limit }
    }
}
