package com.clipvault.app.privacy

import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path
import java.util.stream.Collectors

class AndroidLogPrivacySourceTest {
    private val appSourceDir: Path = Path.of("src", "main", "kotlin", "com", "clipvault", "app")

    @Test
    fun productionLogsUseOnlyConstantMessagesOrExceptionClassNames() {
        assertTrue("Android app source directory is missing: $appSourceDir", Files.isDirectory(appSourceDir))

        val logCall = Regex("""\b(?:android\.util\.)?Log\.[vdiewtf]\s*\(""")
        val allowedExceptionClassInterpolation =
            Regex("""\$\{[A-Za-z_][A-Za-z0-9_]*\.javaClass\.simpleName\}""")
        val violations = mutableListOf<String>()
        val stream = Files.walk(appSourceDir)
        try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .collect(Collectors.toList())
                .forEach { path ->
                    Files.readAllLines(path).forEachIndexed { index, line ->
                        if (!logCall.containsMatchIn(line)) return@forEachIndexed

                        val withoutAllowedInterpolation =
                            allowedExceptionClassInterpolation.replace(line, "")
                        if ('$' in withoutAllowedInterpolation ||
                            "\" +" in line ||
                            "+ \"" in line
                        ) {
                            violations += "${appSourceDir.relativize(path)}:${index + 1}: " +
                                "Android production logs must not interpolate or concatenate " +
                                "clip, memory, sync, credential, host, or payload data"
                        }
                    }
                }
        } finally {
            stream.close()
        }

        assertTrue(
            "Android log privacy violations:\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }

    @Test
    fun productionSourcesDoNotPrintStackTraces() {
        assertTrue("Android app source directory is missing: $appSourceDir", Files.isDirectory(appSourceDir))

        val violations = mutableListOf<String>()
        val stream = Files.walk(appSourceDir)
        try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .collect(Collectors.toList())
                .forEach { path ->
                    Files.readAllLines(path).forEachIndexed { index, line ->
                        if (Regex("""\bprintStackTrace\s*\(""").containsMatchIn(line)) {
                            violations += "${appSourceDir.relativize(path)}:${index + 1}: " +
                                "printStackTrace can include sensitive exception details in logs"
                        }
                    }
                }
        } finally {
            stream.close()
        }

        assertTrue(
            "Android stack-trace logging violations:\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }

    @Test
    fun productionSourcesDoNotWriteStdoutOrStderr() {
        assertTrue("Android app source directory is missing: $appSourceDir", Files.isDirectory(appSourceDir))

        val blocked = Regex("""\b(?:kotlin\.io\.)?(?:print|println)\s*\(|\bSystem\.(?:out|err)\b""")
        val violations = mutableListOf<String>()
        val stream = Files.walk(appSourceDir)
        try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .collect(Collectors.toList())
                .forEach { path ->
                    Files.readAllLines(path).forEachIndexed { index, line ->
                        val trimmed = line.trim()
                        if (trimmed.startsWith("//") || trimmed.startsWith("*")) return@forEachIndexed
                        if (blocked.containsMatchIn(line)) {
                            violations += "${appSourceDir.relativize(path)}:${index + 1}: " +
                                "production code must not write potentially sensitive data to stdout/stderr"
                        }
                    }
                }
        } finally {
            stream.close()
        }

        assertTrue(
            "Android stdout/stderr privacy violations:\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }
}
