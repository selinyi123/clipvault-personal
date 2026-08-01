package com.clipvault.app.otp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpSmsParserTest {
    private val now = 1_000_000L

    @Test
    fun extractsRecentKeywordBoundNumericOtp() {
        val numeric = OtpSmsParser.parse(
            "登录验证码：482917，2分钟内有效。".toCharArray(),
            "10690000", now - 1_000L, now,
        )
        numeric!!.use {
            val chars = it.take()
            assertEquals("482917", chars.concatToString())
            chars.wipe()
        }
    }

    @Test
    fun failsClosedWithoutKeywordWithStaleSenderOrAmbiguousCodes() {
        assertNull(OtpSmsParser.parse("Use 482917 to login".toCharArray(), "1069", now, now))
        assertNull(OtpSmsParser.parse("验证码 482917".toCharArray(), "1069", now - 120_001L, now))
        assertNull(OtpSmsParser.parse("验证码 482917".toCharArray(), "bad\nsender", now, now))
        assertNull(OtpSmsParser.parse("验证码 482917 或 593028".toCharArray(), "1069", now, now))
        assertNull(OtpSmsParser.parse("verification code A7B9Q2".toCharArray(), "Example", now, now))
    }

    @Test
    fun candidateRepresentationNeverContainsCode() {
        val parsed = OtpSmsParser.parse("OTP: 482917".toCharArray(), "Bank", now, now)!!
        assertTrue("482917" !in parsed.toString())
        parsed.close()
    }
}
