package com.clipvault.app.ime

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InlineSuggestionPolicyTest {
    @Test
    fun `API 30 and newer support inline Autofill`() {
        assertFalse(InlineSuggestionPolicy.isSupported(29))
        assertTrue(InlineSuggestionPolicy.isSupported(30))
        assertTrue(InlineSuggestionPolicy.isSupported(35))
    }
}
