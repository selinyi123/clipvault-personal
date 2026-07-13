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
            Regex("""\b(?:android\.net|java\.net|javax\.net|okhttp3|retrofit2|io\.ktor)\."""),
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
    fun syncPackageDoesNotReachImeOrInputContext() {
        val syncSourceDir = mainSourceDir.resolve(Path.of("com", "clipvault", "app", "sync"))
        assertTrue("Android sync source directory is missing: $syncSourceDir", Files.isDirectory(syncSourceDir))

        val blocked = listOf(
            Regex("""\bcom\.clipvault\.app\.ime(?:\.|\b)"""),
            Regex("""\bandroid\.inputmethodservice(?:\.|\b)"""),
            Regex("""\bandroid\.view\.inputmethod(?:\.|\b)"""),
            Regex("""\b(InputMethodService|InputConnection|currentInputConnection)\b"""),
            Regex("""\b(getTextBeforeCursor|getTextAfterCursor|getSelectedText|getExtractedText|getSurroundingText|getInitialTextBeforeCursor|getInitialSelectedText|getInitialTextAfterCursor|getCursorCapsMode|requestCursorUpdates|onUpdateSelection|onUpdateCursorAnchorInfo|onUpdateExtractedText)\b"""),
        )
        val violations = mutableListOf<String>()
        val stream = Files.walk(syncSourceDir)
        try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .collect(Collectors.toList())
                .forEach { path ->
                    Files.readAllLines(path).forEachIndexed { index, line ->
                        val trimmed = line.trim()
                        if (trimmed.startsWith("//") || trimmed.startsWith("*")) return@forEachIndexed
                        if (blocked.any { it.containsMatchIn(line) }) {
                            violations += "${syncSourceDir.relativize(path)}:${index + 1}: " +
                                "sync must not depend on IME or typed input context"
                        }
                    }
                }
        } finally {
            stream.close()
        }

        assertTrue(
            "Android sync-to-IME boundary violations:\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }

    @Test
    fun workManagerCodeStaysInsideSyncPackage() {
        val allowedPrefix = Path.of("com", "clipvault", "app", "sync")
        val blocked = listOf(
            Regex("""^\s*import\s+androidx\.work\."""),
            Regex("""\bandroidx\.work\."""),
            Regex("""\b(WorkManager|WorkRequest|OneTimeWorkRequestBuilder|PeriodicWorkRequestBuilder|ExistingWorkPolicy)\b"""),
        )
        val violations = mutableListOf<String>()
        val stream = Files.walk(mainSourceDir)
        try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .collect(Collectors.toList())
                .forEach { path ->
                    val relative = mainSourceDir.relativize(path)
                    if (relative.startsWith(allowedPrefix)) return@forEach
                    Files.readAllLines(path).forEachIndexed { index, line ->
                        val trimmed = line.trim()
                        if (trimmed.startsWith("//") || trimmed.startsWith("*")) return@forEachIndexed
                        if (blocked.any { it.containsMatchIn(line) }) {
                            violations += "$relative:${index + 1}: WorkManager belongs in app/sync"
                        }
                    }
                }
        } finally {
            stream.close()
        }

        assertTrue(
            "Android WorkManager boundary violations:\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }

    @Test
    fun productionJavaSourcesCannotBypassKotlinBoundaryGates() {
        val javaSourceDir = Path.of("src", "main", "java")
        if (!Files.exists(javaSourceDir)) return

        val stream = Files.walk(javaSourceDir)
        val sources = try {
            stream
                .filter {
                    Files.isRegularFile(it) &&
                        (it.fileName.toString().endsWith(".java") ||
                            it.fileName.toString().endsWith(".kt"))
                }
                .collect(Collectors.toList())
        } finally {
            stream.close()
        }
        assertTrue(
            "Production Java sources require equivalent privacy-boundary scanning: " +
                sources.joinToString { javaSourceDir.relativize(it).toString() },
            sources.isEmpty(),
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
