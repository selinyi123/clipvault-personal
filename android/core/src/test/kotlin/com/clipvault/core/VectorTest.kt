package com.clipvault.core

import kotlin.test.Test
import kotlin.test.assertTrue

/** Gradle entry point for VEC-1 (`gradle :core:test`). The vectors dir is
 * supplied via the `clipvault.vectors` system property (see build.gradle.kts).*/
class VectorTest {
    private val dir: String =
        System.getProperty("clipvault.vectors") ?: "../../contracts/vectors"

    @Test
    fun crossPlatformVectorsMatch() {
        val (total, failures) = collectFailures(dir)
        assertTrue(failures.isEmpty(),
            "VEC-1: ${failures.size}/$total mismatches:\n" + failures.joinToString("\n"))
        assertTrue(total >= 100, "expected >=100 vectors, got $total")
    }
}
