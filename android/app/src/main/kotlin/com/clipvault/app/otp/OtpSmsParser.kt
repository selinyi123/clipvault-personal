package com.clipvault.app.otp

private const val OTP_SMS_MAX_MESSAGE_CHARS = 4_096
private const val OTP_SMS_MAX_AGE_MS = 120_000L
private const val OTP_SMS_MAX_FUTURE_SKEW_MS = 30_000L
private const val OTP_SMS_MAX_SENDER_CHARS = 64
private const val OTP_SMS_MIN_CONFIDENCE = 0.80f

private val OTP_KEYWORD = Regex(
    "验证码|校验码|动态码|一次性密码|安全码|\\botp\\b|verification\\s*code|security\\s*code|passcode",
    setOf(RegexOption.IGNORE_CASE),
)
private val OTP_TOKEN = Regex("(?<![0-9])[0-9]{4,8}(?![0-9])")
private val SAFE_SENDER = Regex("^[+0-9A-Za-z._ -]{1,$OTP_SMS_MAX_SENDER_CHARS}$")

class ParsedOtpCandidate internal constructor(
    ownedCharacters: CharArray,
    val confidence: Float,
) : AutoCloseable {
    private var characters = ownedCharacters
    private var taken = false

    @Synchronized
    fun take(): CharArray {
        if (taken) throw IllegalStateException("OTP candidate unavailable")
        taken = true
        return characters.also { characters = CharArray(0) }
    }

    @Synchronized
    override fun close() {
        taken = true
        characters.wipe()
        characters = CharArray(0)
    }

    override fun toString(): String = "<ParsedOtpCandidate redacted confidence=$confidence>"
}

/** Pure, bounded parser. It returns only a normalized code and never sender/body metadata. */
object OtpSmsParser {
    fun parse(
        messageBody: CharArray,
        senderAddress: String?,
        receivedAtWallMs: Long,
        nowWallMs: Long,
    ): ParsedOtpCandidate? {
        if (
            messageBody.isEmpty() ||
            messageBody.size > OTP_SMS_MAX_MESSAGE_CHARS ||
            nowWallMs < 0L ||
            receivedAtWallMs < 0L
        ) return null
        val age = nowWallMs - receivedAtWallMs
        if (age > OTP_SMS_MAX_AGE_MS || age < -OTP_SMS_MAX_FUTURE_SKEW_MS) return null
        val sender = senderAddress?.trim()
        if (sender.isNullOrEmpty() || !SAFE_SENDER.matches(sender)) return null

        val body = messageBody.concatToString()
        val keywords = OTP_KEYWORD.findAll(body).toList()
        if (keywords.isEmpty()) return null
        data class Scored(val normalized: String, val score: Float, val distance: Int)
        val candidates = mutableListOf<Scored>()
        for (match in OTP_TOKEN.findAll(body)) {
            val raw = match.value
            val nearest = keywords.minOf { keyword ->
                when {
                    match.range.first > keyword.range.last -> match.range.first - keyword.range.last - 1
                    keyword.range.first > match.range.last -> keyword.range.first - match.range.last - 1
                    else -> 0
                }
            }
            if (nearest > 48) continue
            var score = 0.40f // an explicit OTP keyword is mandatory
            score += if (nearest <= 16) 0.25f else 0.15f
            score += 0.15f // frozen OTP-3A profile: ASCII digits only
            score += 0.10f // bounded, syntactically plausible source address
            if (age <= 60_000L) score += 0.10f
            candidates += Scored(raw, score, nearest)
        }
        if (candidates.isEmpty()) return null
        val bestScore = candidates.maxOf { it.score }
        val best = candidates.filter { it.score == bestScore }.sortedBy { it.distance }
        val distinctBest = best.map { it.normalized }.distinct()
        if (distinctBest.size != 1 || bestScore < OTP_SMS_MIN_CONFIDENCE) return null
        return ParsedOtpCandidate(distinctBest.single().toCharArray(), bestScore)
    }
}
