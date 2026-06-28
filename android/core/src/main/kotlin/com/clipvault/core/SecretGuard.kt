package com.clipvault.core

import kotlin.math.ln

/** SG-1 (+SG-1.1) (CONTRACTS §4). Must agree with desktop clipvault.core.secret_guard. */
object SecretGuard {
    private val PATTERNS: List<Pair<String, Regex>> = listOf(
        "SG-PEM" to Regex("""-----BEGIN (RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----"""),
        "SG-PUTTY" to Regex("""PuTTY-User-Key-File"""),
        "SG-AWS-ID" to Regex("""\bAKIA[0-9A-Z]{16}\b"""),
        "SG-AWS-SECRET" to Regex("""(?i)aws.{0,20}(secret|key).{0,20}['"][0-9A-Za-z/+=]{40}['"]"""),
        "SG-GH" to Regex("""\bgh[pousr]_[A-Za-z0-9]{36,}\b|github_pat_[A-Za-z0-9_]{22,}"""),
        "SG-SLACK" to Regex("""\bxox[baprs]-[A-Za-z0-9-]{10,}\b"""),
        "SG-OPENAI" to Regex("""\bsk-(proj-|ant-)?[A-Za-z0-9_-]{20,}\b"""),
        "SG-GOOGLE" to Regex("""\bAIza[0-9A-Za-z_-]{35}\b"""),
        "SG-JWT" to Regex("""\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"""),
        // High-confidence distinctive-prefix provider keys. Must mirror desktop secret_guard.py.
        "SG-STRIPE" to Regex("""\b[sr]k_(live|test)_[0-9A-Za-z]{16,}\b"""),
        "SG-GITLAB" to Regex("""\bglpat-[A-Za-z0-9_-]{20,}\b"""),
        "SG-SENDGRID" to Regex("""\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b"""),
        "SG-NPM" to Regex("""\bnpm_[A-Za-z0-9]{36}\b"""),
        "SG-DIGITALOCEAN" to Regex("""\bdop_v1_[a-f0-9]{64}\b"""),
        "SG-SLACK-URL" to Regex("""https://hooks\.slack\.com/services/[A-Za-z0-9_/+-]{24,}"""),
        "SG-ASSIGN" to Regex(
            """(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key""" +
                """|client[_-]?secret|auth)\b\s*[:=]\s*\S{8,}"""
        ),
        "SG-CONNSTR" to Regex(
            """(?i)\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@]+@"""
        ),
    )

    private val ENV_LINE = Regex("""^[A-Z][A-Z0-9_]{2,}=\S+$""")
    private val ENV_SENSITIVE = Regex("""KEY|TOKEN|SECRET|PASS|PWD""")
    private val TOKEN_CHARS = Regex("""^[A-Za-z0-9+/=_\-]+$""")
    private val HAS_LETTER = Regex("""[A-Za-z]""")
    private val HAS_DIGIT = Regex("""[0-9]""")
    private val WHITESPACE = Regex("""\s+""")
    private val HEX = Regex("""^[0-9a-fA-F]+$""")
    private val UUID = Regex(
        """^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"""
    )
    private val IMAGE_B64_PREFIXES = listOf("iVBORw0KGgo", "/9j/", "R0lGOD")

    private const val ENTROPY_MIN_LEN = 24
    private const val ENTROPY_THRESHOLD = 3.8

    fun shannonEntropy(s: String): Double {
        if (s.isEmpty()) return 0.0
        val counts = HashMap<Char, Int>()
        for (ch in s) counts[ch] = (counts[ch] ?: 0) + 1
        val n = s.length.toDouble()
        var h = 0.0
        for (c in counts.values) {
            val p = c / n
            h -= p * (ln(p) / ln(2.0))
        }
        return h
    }

    private fun hasLetterAndDigit(s: String): Boolean =
        HAS_LETTER.containsMatchIn(s) && HAS_DIGIT.containsMatchIn(s)

    private fun isKnownNonSecret(token: String): Boolean {
        if (UUID.matches(token)) return true
        if (HEX.matches(token) && token.length in intArrayOf(32, 40, 64)) return true
        if (token.startsWith("/") || token.startsWith("~")) return true
        if (IMAGE_B64_PREFIXES.any { token.startsWith(it) }) return true
        return false
    }

    fun scan(content: String): SecretVerdict {
        val reasons = PATTERNS.filter { it.second.containsMatchIn(content) }.map { it.first }.toMutableList()

        val envLines = content.split("\n").filter { ENV_LINE.matches(it) }
        if (envLines.size >= 2 && envLines.any { ENV_SENSITIVE.containsMatchIn(it.substringBefore("=")) }) {
            reasons.add("SG-ENV")
        }

        if (reasons.isNotEmpty()) {
            return SecretVerdict(true, SECRET_LEVEL_HARD, reasons)
        }

        val token = content.trim()
        if (token.length >= ENTROPY_MIN_LEN &&
            TOKEN_CHARS.matches(token) &&
            !isKnownNonSecret(token) &&
            shannonEntropy(token) >= ENTROPY_THRESHOLD
        ) {
            return SecretVerdict(true, SECRET_LEVEL_SUSPECT, listOf("SG-ENTROPY"))
        }

        // SG-1.2: high-entropy credential-shaped token embedded in surrounding
        // text is missed by the whole-content rule above (content with spaces is
        // never a single token). Scan each whitespace token, gated stricter than
        // the whole-content rule (must contain both letters and digits) so
        // ordinary long words in prose are not flagged. Mirrors secret_guard.py.
        for (tok in content.split(WHITESPACE)) {
            if (tok.length >= ENTROPY_MIN_LEN &&
                TOKEN_CHARS.matches(tok) &&
                hasLetterAndDigit(tok) &&
                !isKnownNonSecret(tok) &&
                shannonEntropy(tok) >= ENTROPY_THRESHOLD
            ) {
                return SecretVerdict(true, SECRET_LEVEL_SUSPECT, listOf("SG-ENTROPY"))
            }
        }
        return SecretVerdict(false, null, emptyList())
    }

    fun redactPreview(content: String): String =
        content.take(4) + "••••"
}
