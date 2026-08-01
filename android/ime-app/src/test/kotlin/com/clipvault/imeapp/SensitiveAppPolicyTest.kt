package com.clipvault.imeapp

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SensitiveAppPolicyTest {
    @Test
    fun `configured package is sensitive and ordinary package is allowed`() {
        val policy = SensitiveAppPolicy { setOf("com.example.bank") }

        assertTrue(policy.isSensitive("com.example.bank"))
        assertTrue(policy.isSensitive(" COM.EXAMPLE.BANK "))
        assertFalse(policy.isSensitive("com.example.notes"))
    }

    @Test
    fun `unknown identity and unreadable policy fail closed`() {
        assertTrue(SensitiveAppPolicy { emptySet() }.isSensitive(null))
        assertTrue(SensitiveAppPolicy { null }.isSensitive("com.example.notes"))
        assertTrue(
            SensitiveAppPolicy { throw IllegalStateException("unreadable") }
                .isSensitive("com.example.notes"),
        )
    }

    @Test
    fun `package parser rejects malformed values`() {
        val parsed = ImePreferences.parsePackageList(
            "com.example.bank\nnot-a-package, com.example.auth",
        )

        assertTrue("com.example.bank" in parsed)
        assertTrue("com.example.auth" in parsed)
        assertFalse("not-a-package" in parsed)
    }
}
