package com.clipvault.app.runtime

import com.clipvault.ime.engine.RuntimeSnapshotContract
import com.clipvault.ime.engine.RuntimeSnapshotItem

/** Final Binder-frame gate; facade authorization alone is never a size grant. */
internal object RuntimeSnapshotBrokerPolicy {
    fun prepare(candidates: List<Candidate>, limit: Int): List<RuntimeSnapshotItem> =
        RuntimeSnapshotContract.sanitizeForBroker(
            candidates.map { candidate ->
                val source = when (candidate.source) {
                    "clip" -> RuntimeSnapshotContract.SOURCE_CLIPBOARD
                    "memory" -> RuntimeSnapshotContract.SOURCE_MEMORY
                    else -> candidate.source
                }
                RuntimeSnapshotItem(
                    candidateId = "$source:${candidate.id}",
                    source = source,
                    label = candidate.label,
                    text = candidate.text,
                )
            },
            limit,
        )
}
