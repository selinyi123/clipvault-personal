package com.clipvault.app.runtime

import android.content.Context
import com.clipvault.app.ClipVaultApp
import com.clipvault.app.capture.Capture
import com.clipvault.app.data.AppDatabase
import com.clipvault.app.data.ClipCandidateMetadata
import com.clipvault.app.data.ClipEntity
import com.clipvault.app.data.MemoryEntity
import com.clipvault.app.data.MemoryPrivacy
import com.clipvault.app.sync.SyncScheduler
import com.clipvault.core.SecretGuard

/**
 * ClipVault Runtime API (ADR-0008, ROADMAP_V2 PR2).
 *
 * The single surface that input frontends call to read/write ClipVault content,
 * instead of touching Room directly. Today the Panel IME uses it; later the Full
 * Keyboard Lab and the CandidateMixer use the same facade, so the Runtime stays
 * the one source of truth and the frontends stay thin.
 *
 * Methods are blocking and meant to be called off the main thread (the IME
 * already does its data work on a worker thread). They never throw for empty
 * results — callers get empty lists / false.
 */

/** What a panel or keyboard shows and pastes for a clipboard item. */
data class ClipCandidate(val id: String, val text: String, val contentType: String)

/** What a panel or keyboard shows and pastes for a Personal Memory item. */
data class MemoryCandidate(val id: String, val text: String, val kind: String, val label: String?)

/** Unified candidate consumed by every input frontend. */
data class Candidate(
    val id: String,
    val source: String,        // clip | memory
    val kind: String,          // content_type for clips; memory kind for memory
    val text: String,
    val label: String,
    val score: Int,
    val riskFlags: List<String> = emptyList(), // reserved; do not use as a privacy boundary
)

/**
 * Final read-only privacy gate for clip candidates.
 *
 * Room's `isSecret = 0` predicate reflects the rule set that classified a row
 * when it was captured or received.  Rules can become stricter later, so the
 * persisted flag cannot authorize text to leave the Runtime for an IME.  Every
 * clip candidate exit re-scans the content with the current SG-1 rules and
 * fails closed without mutating Room.  An explicit Owner release authorizes
 * only the authority that recorded it; release provenance is deliberately not
 * accepted over the sync wire, so a receiving Android device can quarantine
 * the same secret-shaped text again.
 */
internal object ClipCandidatePrivacy {
    fun allows(clip: ClipEntity): Boolean =
        !clip.deleted && !clip.isSecret && !SecretGuard.scan(clip.content).isSecret
}

/**
 * Opaque batch whose rows have passed the final candidate privacy gate and the
 * IME memory budgets below. The private constructor prevents Runtime callers
 * from treating a raw Room result as candidate-authorized content.
 */
internal class EligibleClipCandidates private constructor(
    internal val rows: List<ClipEntity>,
) {
    fun toRecentCandidates(limit: Int): List<ClipCandidate> = rows
        .take(limit.coerceAtLeast(0))
        .map { ClipCandidate(it.id, it.content, it.contentType) }

    companion object {
        // A valid clip can be 1 MiB. Keep only a small Room payload batch live,
        // reject keyboard-hostile individual payloads before SG-1 allocates,
        // and bound both current-rule scan work and retained candidate text.
        internal const val MATERIALIZE_BATCH_SIZE = 4
        internal const val MAX_ROWS = 128
        internal const val MAX_ITEMS = 100
        internal const val MAX_ITEM_CHARS = 64 * 1024
        internal const val MAX_ITEM_UTF8_BYTES = 64 * 1024
        internal const val MAX_SCANNED_CHARS = 512 * 1024
        internal const val MAX_RETAINED_CHARS = 256 * 1024

        fun empty(): EligibleClipCandidates = EligibleClipCandidates(emptyList())

        fun fromRows(rows: List<ClipEntity>, desiredCount: Int): EligibleClipCandidates {
            val collector = Collector(desiredCount)
            for (row in rows) {
                if (!collector.offer(row)) break
            }
            return collector.finish()
        }

        fun loadWindow(
            desiredCount: Int,
            fetchMetadata: (limit: Int) -> List<ClipCandidateMetadata>,
            fetchRows: (ids: List<String>) -> List<ClipEntity>,
        ): EligibleClipCandidates {
            val collector = Collector(desiredCount)
            if (!collector.needsMore) return collector.finish()

            // The LIKE/order work happens once. This projection contains no
            // clip text and is therefore safe to hold for the fixed raw window.
            val metadata = fetchMetadata(MAX_ROWS)
            require(metadata.size <= MAX_ROWS) { "candidate metadata window exceeded raw-row budget" }
            val materializableIds = LinkedHashSet<String>(metadata.size)
            for (item in metadata) {
                if (item.contentBytes in 1..MAX_ITEM_UTF8_BYTES.toLong()) {
                    materializableIds.add(item.id)
                }
            }

            // Full rows are fetched in tiny batches. Rebuild metadata order
            // explicitly because SQL IN does not promise result ordering.
            for (ids in materializableIds.toList().chunked(MATERIALIZE_BATCH_SIZE)) {
                if (!collector.needsMore) break
                val rows = fetchRows(ids)
                require(rows.size <= ids.size) { "candidate row fetch exceeded requested batch" }
                val requestedIds = ids.toHashSet()
                val rowsById = LinkedHashMap<String, ClipEntity>(rows.size)
                for (row in rows) {
                    require(row.id in requestedIds) { "candidate row fetch returned an unexpected id" }
                    require(rowsById.put(row.id, row) == null) { "candidate row fetch returned a duplicate id" }
                }
                for (id in ids) {
                    val row = rowsById[id] ?: continue
                    if (!collector.offer(row)) break
                }
            }
            return collector.finish()
        }

        private class Collector(desiredCount: Int) {
            private val desired = desiredCount.coerceIn(0, MAX_ITEMS)
            private val accepted = LinkedHashMap<String, ClipEntity>(desired)
            private var scannedRows = 0
            private var scannedChars = 0
            private var retainedChars = 0
            private var exhausted = desired == 0

            val needsMore: Boolean
                get() = !exhausted && scannedRows < MAX_ROWS && accepted.size < desired

            fun offer(row: ClipEntity): Boolean {
                if (!needsMore) return false
                scannedRows += 1

                // Cheap fail-closed checks run before current SG-1. Oversized
                // text remains available in the main app but is not suitable
                // for an IME candidate or its latency/memory budget.
                if (
                    row.content.isEmpty() ||
                    row.deleted ||
                    row.isSecret ||
                    row.content.length > MAX_ITEM_CHARS ||
                    row.content.toByteArray(Charsets.UTF_8).size > MAX_ITEM_UTF8_BYTES
                ) {
                    return needsMore
                }
                if (scannedChars > MAX_SCANNED_CHARS - row.content.length) {
                    exhausted = true
                    return false
                }
                scannedChars += row.content.length
                if (!ClipCandidatePrivacy.allows(row)) return needsMore
                if (accepted.containsKey(row.id)) return needsMore
                if (retainedChars > MAX_RETAINED_CHARS - row.content.length) {
                    return needsMore
                }

                accepted[row.id] = row
                retainedChars += row.content.length
                if (retainedChars >= MAX_RETAINED_CHARS) exhausted = true
                return needsMore
            }

            fun finish(): EligibleClipCandidates = EligibleClipCandidates(accepted.values.toList())
        }
    }
}

internal object CandidateMixer {
    private val memoryKindWeight = mapOf(
        "phrase" to 42,
        "prompt" to 40,
        "command" to 38,
        "term" to 34,
        "key_info" to 30,
        "path" to 28,
    )

    fun mix(clips: List<ClipEntity>, memories: List<MemoryEntity>, query: String, limit: Int): List<Candidate> {
        // Ranking fields such as favorite/timesSeen and deterministic id
        // tie-breaks must see the complete bounded source window. Applying the
        // caller's output limit here would make Room order decide the winner.
        return mix(EligibleClipCandidates.fromRows(clips, EligibleClipCandidates.MAX_ITEMS), memories, query, limit)
    }

    fun mix(clips: EligibleClipCandidates, memories: List<MemoryEntity>, query: String, limit: Int): List<Candidate> {
        val q = query.trim()
        val ranked = (clips.rows.map { fromClip(it, q) } + memories
            .filterNot { MemoryPrivacy.containsSecret(it.text, it.label) }
            .map { fromMemory(it, q) })
            .filter { q.isEmpty() || it.text.contains(q, ignoreCase = true) || it.label.contains(q, ignoreCase = true) }
            .sortedWith(compareByDescending<Candidate> { it.score }
                .thenBy { it.source }
                .thenBy { it.kind }
                .thenBy { it.label }
                .thenBy { it.id })
        return capSources(ranked, limit)
    }

    /** Source caps: when candidates overflow [limit] and both sources are present,
     * guarantee each source at least `max(1, limit / 4)` slots so a flood of one
     * source cannot fully starve the other. The ranked priority order is otherwise
     * preserved — reserved minority items take the lowest slots rather than
     * displacing higher-priority ones. Mirrors the desktop suggest ranker (SUG-1.2). */
    internal fun capSources(ranked: List<Candidate>, limit: Int): List<Candidate> {
        if (ranked.size <= limit) return ranked
        val bySource = LinkedHashMap<String, MutableList<Int>>()
        ranked.forEachIndexed { i, c -> bySource.getOrPut(c.source) { mutableListOf() }.add(i) }
        if (bySource.size < 2) return ranked.take(limit)
        val reserve = maxOf(1, limit / 4)
        val chosen = LinkedHashSet<Int>()
        for (idxs in bySource.values) chosen.addAll(idxs.take(minOf(reserve, idxs.size)))
        var i = 0
        while (chosen.size < limit && i < ranked.size) { chosen.add(i); i++ }
        return chosen.sorted().take(limit).map { ranked[it] }
    }

    private fun fromClip(c: ClipEntity, q: String): Candidate {
        val score = 1000 +
            (if (c.pinned) 500 else 0) +
            (if (q.isNotEmpty() && c.content.startsWith(q, ignoreCase = true)) 120 else 0) +
            (if (c.favorite) 80 else 0) +
            c.timesSeen.coerceAtMost(50)
        return Candidate(
            id = c.id,
            source = "clip",
            kind = c.contentType,
            text = c.content,
            label = "[clip:${c.contentType}]",
            score = score,
        )
    }

    private fun fromMemory(m: MemoryEntity, q: String): Candidate {
        val score = 1200 +
            (if (m.pinned) 500 else 0) +
            (if (q.isNotEmpty() && m.text.startsWith(q, ignoreCase = true)) 120 else 0) +
            (memoryKindWeight[m.kind] ?: 20) +
            m.useCount.coerceAtMost(100)
        return Candidate(
            id = "${m.kind}:${m.text}",
            source = "memory",
            kind = m.kind,
            text = m.text,
            label = "[memory:${m.kind}]",
            score = score,
        )
    }
}

interface ClipVaultFacade {
    /** Unified deterministic candidates for all IME frontends.
     *
     * `source`/`kind` are optional narrow filters. Panel tabs pass these filters
     * so a low-frequency memory kind cannot be starved by the global memory top
     * 100 before the tab filter is applied.
     */
    fun listCandidates(query: String = "", limit: Int = 40, source: String? = null, kind: String? = null): List<Candidate>

    /** Recent public clips, newest/pinned first. Secrets are never returned. */
    fun listRecentClips(limit: Int = 40): List<ClipCandidate>

    /** Personal Memory items of one kind (term|phrase|prompt|command|key_info|path). */
    fun listMemory(kind: String, limit: Int = 100): List<MemoryCandidate>

    /** Explicitly save text into the Runtime (e.g. the IME "保存剪贴板" action).
     * Goes through the full capture pipeline (normalize/hash/classify/Secret Guard)
     * and schedules a sync push only when a new public outbox event exists.
     * Returns true if something was stored or locally updated. */
    fun saveExplicit(text: String, sourceDevice: String): Boolean
}

/** Default Room-backed implementation.
 *
 * Runtime contract: these methods NEVER throw. A DB error returns an empty
 * list / false instead of crashing the input frontend — for an input method a
 * crash is far worse than a missing panel item (same lesson as the pairing fix).
 */
class RoomClipVaultFacade(context: Context) : ClipVaultFacade {
    private val ctx = context.applicationContext

    override fun listCandidates(query: String, limit: Int, source: String?, kind: String?): List<Candidate> = safe(emptyList()) {
        if (limit <= 0) return@safe emptyList()
        val db = ClipVaultApp.db(ctx)
        val clips = if (source == null || source == "clip") {
            // Authorize the full bounded clip source before final ranking and
            // output limiting. Otherwise limit=1 would always choose the first
            // Room row even when a later candidate has a higher rank.
            loadClipCandidateWindow(db, query, EligibleClipCandidates.MAX_ITEMS)
        } else {
            EligibleClipCandidates.empty()
        }
        val memories = if (source == null || source == "memory") db.memory().list(kind ?: "") else emptyList()
        CandidateMixer.mix(clips, memories, query, limit)
    }

    override fun listRecentClips(limit: Int): List<ClipCandidate> = safe(emptyList()) {
        if (limit <= 0) return@safe emptyList()
        val db = ClipVaultApp.db(ctx)
        loadClipCandidateWindow(db, "", limit).toRecentCandidates(limit)
    }

    override fun listMemory(kind: String, limit: Int): List<MemoryCandidate> = safe(emptyList()) {
        ClipVaultApp.db(ctx).memory().list(kind)
            .filterNot { MemoryPrivacy.containsSecret(it.text, it.label) }
            .take(limit)
            .map { MemoryCandidate("${it.kind}:${it.text}", it.text, it.kind, it.label) }
    }

    override fun saveExplicit(text: String, sourceDevice: String): Boolean = safe(false) {
        if (text.isBlank()) return@safe false
        val result = Capture.ingest(ClipVaultApp.db(ctx), text, sourceDevice = sourceDevice)
        if (result.shouldRequestSyncPush) SyncScheduler.requestPushBestEffort(ctx)
        result.didStoreLocally
    }

    private fun loadClipCandidateWindow(
        db: AppDatabase,
        query: String,
        desiredCount: Int,
    ): EligibleClipCandidates =
        EligibleClipCandidates.loadWindow(
            desiredCount = desiredCount,
            fetchMetadata = { limit -> db.clips().candidateWindowMetadata(query, 0, limit) },
            fetchRows = { ids ->
                db.clips().candidateRowsById(ids, EligibleClipCandidates.MAX_ITEM_UTF8_BYTES)
            },
        )

    private inline fun <T> safe(fallback: T, block: () -> T): T =
        try { block() } catch (e: Exception) {
            android.util.Log.w("clipvault.runtime", "facade op failed: ${e.javaClass.simpleName}")
            fallback
        }
}

/** Entry point so frontends don't construct the implementation directly. */
object ClipVaultRuntime {
    fun facade(context: Context): ClipVaultFacade = RoomClipVaultFacade(context)
}
