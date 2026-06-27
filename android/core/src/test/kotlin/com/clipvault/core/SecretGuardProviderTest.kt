package com.clipvault.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse

/**
 * Provider-key detectors added in v1.7. Inputs are assembled by concatenation so
 * no contiguous secret-shaped literal is committed (GitHub push protection blocks
 * those even in test fixtures), which is also why these cases can't live in the
 * shared JSON vectors. Mirrors the Python test_secret_guard_providers so both
 * platforms stay in lockstep on the new rules.
 */
class SecretGuardProviderTest {
    private fun reasons(content: String): List<String> {
        val v = SecretGuard.scan(content)
        assertEquals(SECRET_LEVEL_HARD, v.level, content)
        return v.reasons
    }

    @Test
    fun detectsProviderKeyPatterns() {
        val a = "A".repeat(20)
        assertEquals(listOf("SG-STRIPE"), reasons("sk_" + "live_" + a))
        assertEquals(listOf("SG-STRIPE"), reasons("rk_" + "test_" + a))
        assertEquals(listOf("SG-GITLAB"), reasons("glpat-" + a))
        assertEquals(listOf("SG-SENDGRID"), reasons("SG." + "A".repeat(22) + "." + "A".repeat(43)))
        assertEquals(listOf("SG-NPM"), reasons("npm_" + "A".repeat(36)))
        assertEquals(listOf("SG-DIGITALOCEAN"), reasons("dop_" + "v1_" + "a".repeat(64)))
        assertEquals(listOf("SG-SLACK-URL"), reasons("https://hooks." + "slack.com/services/" + "A".repeat(24)))
    }

    @Test
    fun ignoresPlainNpmCommand() {
        assertFalse(SecretGuard.scan("npm install --save lodash").isSecret)
    }
}
