package com.clipvault.app.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MemoryPrivacyTest {
    @Test
    fun rejectsSecretMemoryText() {
        assertTrue(MemoryPrivacy.containsSecret("AKIAIOSFODNN7EXAMPLE", null))
    }

    @Test
    fun rejectsSecretMemoryLabel() {
        assertTrue(MemoryPrivacy.containsSecret("production credential", "AKIAIOSFODNN7EXAMPLE"))
    }

    @Test
    fun allowsOrdinaryMemory() {
        assertFalse(MemoryPrivacy.containsSecret("git status", "daily command"))
    }
}
