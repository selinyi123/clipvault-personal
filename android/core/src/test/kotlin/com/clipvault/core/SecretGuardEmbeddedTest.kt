package com.clipvault.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * SG-1.2: embedded high-entropy credential detection (v1.7 deepening). The
 * whole-content entropy rule only fires for single-token content; SG-1.2 adds a
 * per-token scan gated stricter (token must contain both letters and digits).
 * Mirrors the Python test_secret_guard_embedded so both platforms stay in lockstep.
 */
class SecretGuardEmbeddedTest {
    // Random alphanumeric (not any provider format); high Shannon entropy.
    private val token = "q7VxT2mKp9LzR4wNb8YcJ3hFs6Dg"

    @Test
    fun catchesEmbeddedHighEntropyToken() {
        val v = SecretGuard.scan("deploy key is $token please rotate it")
        assertTrue(v.isSecret)
        assertEquals(SECRET_LEVEL_SUSPECT, v.level)
        assertEquals(listOf("SG-ENTROPY"), v.reasons)
    }

    @Test
    fun stillCatchesTokenAlone() {
        val v = SecretGuard.scan(token)
        assertTrue(v.isSecret)
        assertEquals(listOf("SG-ENTROPY"), v.reasons)
    }

    @Test
    fun ignoresProseLongWord() {
        assertFalse(SecretGuard.scan("antidisestablishmentarianism is a very long word").isSecret)
    }

    @Test
    fun ignoresEmbeddedHashAndUuid() {
        assertFalse(SecretGuard.scan("commit 3f786850e387550fdab836ed7e6dc881de23001b landed").isSecret)
        assertFalse(SecretGuard.scan("ticket 550e8400-e29b-41d4-a716-446655440000 closed").isSecret)
    }

    @Test
    fun ignoresEmbeddedAllDigits() {
        assertFalse(SecretGuard.scan("order 123456789012345678901234567890 shipped").isSecret)
    }
}
