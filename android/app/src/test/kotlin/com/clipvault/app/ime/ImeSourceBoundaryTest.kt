package com.clipvault.app.ime

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path
import java.util.stream.Collectors

class ImeSourceBoundaryTest {
    private val imeSourceDir: Path = Path.of(
        "src",
        "main",
        "kotlin",
        "com",
        "clipvault",
        "app",
        "ime",
    )

    @Test
    fun imePackageDoesNotImportNetworkSyncWorkOrLoggingPaths() {
        assertTrue("IME source directory is missing: $imeSourceDir", Files.isDirectory(imeSourceDir))

        val blockedPatterns = listOf(
            BlockedPattern(
                Regex("""^\s*import\s+com\.clipvault\.app\.sync(\.|$)"""),
                "project sync imports belong outside IME services",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+androidx\.work\."""),
                "WorkManager scheduling belongs outside IME services",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+(android\.net|java\.net|javax\.net|okhttp3|retrofit2|io\.ktor)\."""),
                "network imports belong outside IME services",
            ),
            BlockedPattern(
                Regex("""\b(java|javax)\.net\."""),
                "fully-qualified network calls belong outside IME services",
            ),
            BlockedPattern(
                Regex("""\b(HttpURLConnection|HttpsURLConnection|Socket|DatagramSocket|InetAddress)\b"""),
                "socket/HTTP calls belong outside IME services",
            ),
            BlockedPattern(
                Regex("""\b(android\.util\.Log|Log\.[vdiewtf])\b"""),
                "IME services must not add typed-text-adjacent logging paths",
            ),
        )

        val violations = mutableListOf<String>()
        val stream = Files.walk(imeSourceDir)
        try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .collect(Collectors.toList())
                .forEach { path ->
                    Files.readAllLines(path).forEachIndexed { index, line ->
                        val trimmed = line.trim()
                        if (trimmed.startsWith("//") || trimmed.startsWith("*")) return@forEachIndexed
                        blockedPatterns.forEach { blocked ->
                            if (blocked.regex.containsMatchIn(line)) {
                                violations += "${imeSourceDir.relativize(path)}:${index + 1}: ${blocked.reason}"
                            }
                        }
                    }
                }
        } finally {
            stream.close()
        }

        assertTrue(
            "IME source boundary violations:\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }

    private data class BlockedPattern(
        val regex: Regex,
        val reason: String,
    )
}
