package com.clipvault.app.ime

import com.clipvault.app.runtime.Candidate

internal const val PANEL_CANDIDATE_POOL_LIMIT = 200

internal object PanelCandidateTabs {
    fun filter(candidates: List<Candidate>, source: String, kind: String?, limit: Int): List<Candidate> =
        candidates
            .filter { c -> c.source == source && (kind == null || c.kind == kind) }
            .take(limit)
}
