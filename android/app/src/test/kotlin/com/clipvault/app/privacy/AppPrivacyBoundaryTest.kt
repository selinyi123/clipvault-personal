package com.clipvault.app.privacy

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path
import java.util.stream.Collectors

class AppPrivacyBoundaryTest {
    private val mainSourceDir: Path = Path.of("src", "main", "kotlin")

    @Test
    fun networkCodeStaysOutsideImeAndInsideSyncPackage() {
        assertTrue("Android main source directory is missing: $mainSourceDir", Files.isDirectory(mainSourceDir))

        val networkPatterns = listOf(
            Regex("""^\s*import\s+(android\.net|java\.net|javax\.net|okhttp3|retrofit2|io\.ktor)\."""),
            Regex("""\b(java|javax)\.net\."""),
            Regex("""\b(HttpURLConnection|HttpsURLConnection|Socket|DatagramSocket|InetAddress)\b"""),
        )
        val allowedPrefix = Path.of("com", "clipvault", "app", "sync")
        val violations = mutableListOf<String>()

        val stream = Files.walk(mainSourceDir)
        try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .collect(Collectors.toList())
                .forEach { path ->
                    val relative = mainSourceDir.relativize(path)
                    val allowed = relative.startsWith(allowedPrefix)
                    Files.readAllLines(path).forEachIndexed { index, line ->
                        val trimmed = line.trim()
                        if (trimmed.startsWith("//") || trimmed.startsWith("*")) return@forEachIndexed
                        if (!allowed && networkPatterns.any { it.containsMatchIn(line) }) {
                            violations += "$relative:${index + 1}: network code must stay in app/sync"
                        }
                    }
                }
        } finally {
            stream.close()
        }

        assertTrue(
            "Android network boundary violations:\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }

    @Test
    fun trackingSdkDependenciesAreNotDeclared() {
        val buildFiles = listOf(
            Path.of("build.gradle.kts"),
            Path.of("..", "build.gradle.kts"),
            Path.of("..", "core", "build.gradle.kts"),
        )
        val blockedTokens = listOf(
            "firebase-analytics",
            "firebase-crashlytics",
            "play-services-analytics",
            "sentry-android",
            "amplitude",
            "mixpanel",
            "segment-analytics",
            "appcenter-analytics",
            "bugsnag",
            "datadog",
            "newrelic",
        )

        val violations = mutableListOf<String>()
        buildFiles.forEach { path ->
            assertTrue("Android build file is missing: $path", Files.isRegularFile(path))
            val text = Files.readAllLines(path).joinToString("\n").lowercase()
            blockedTokens.forEach { token ->
                if (token in text) {
                    violations += "$path: blocked tracking dependency token '$token'"
                }
            }
        }

        assertTrue(
            "Tracking SDK dependency violations:\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }
}
