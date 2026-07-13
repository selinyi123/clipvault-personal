package com.clipvault.core

import java.nio.file.Files
import java.nio.file.Path
import java.util.stream.Collectors
import kotlin.test.Test
import kotlin.test.assertTrue

class CoreBoundaryTest {
    private val mainSourceDir = Path.of("src", "main", "kotlin")

    @Test
    fun mainSourcesRemainAndroidFreeAndDoNotDependOnApp() {
        assertTrue(Files.isDirectory(mainSourceDir), "core main source directory is missing")

        val blocked = listOf(
            Regex("""^\s*import\s+(android|androidx)(?:\.|$)"""),
            Regex("""^\s*import\s+com\.clipvault\.app(?:\.|$)"""),
            Regex("""\b(?:android|androidx)\.[A-Za-z_]"""),
            Regex("""\bcom\.clipvault\.app\."""),
        )
        val violations = mutableListOf<String>()
        val stream = Files.walk(mainSourceDir)
        try {
            stream
                .filter { Files.isRegularFile(it) && it.fileName.toString().endsWith(".kt") }
                .collect(Collectors.toList())
                .forEach { path ->
                    Files.readAllLines(path).forEachIndexed { index, line ->
                        val trimmed = line.trim()
                        if (trimmed.startsWith("//") || trimmed.startsWith("*")) return@forEachIndexed
                        if (blocked.any { it.containsMatchIn(line) }) {
                            violations += "${mainSourceDir.relativize(path)}:${index + 1}: " +
                                "core must stay pure Kotlin/JVM and independent of app"
                        }
                    }
                }
        } finally {
            stream.close()
        }

        assertTrue(violations.isEmpty(), "core source boundary violations:\n${violations.joinToString("\n")}")
    }

    @Test
    fun gradleKeepsJvmPluginJava17AndNoAndroidOrAppDependency() {
        val buildFile = Path.of("build.gradle.kts")
        assertTrue(Files.isRegularFile(buildFile), "core build.gradle.kts is missing")
        val text = Files.readString(buildFile)

        assertTrue("id(\"org.jetbrains.kotlin.jvm\")" in text, "core must use the Kotlin JVM plugin")
        assertTrue("jvmToolchain(17)" in text, "core must stay compatible with the app Java 17 boundary")
        val blocked = listOf(
            "id(\"com.android",
            "id(\"org.jetbrains.kotlin.android\")",
            "android {",
            "androidx.",
            "project(\":app\")",
        )
        val violations = blocked.filter { it in text }
        assertTrue(violations.isEmpty(), "core Gradle boundary violations: ${violations.joinToString()}")
    }

    @Test
    fun javaSourcesCannotBypassTheKotlinCoreBoundaryGate() {
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
            sources.isEmpty(),
            "core Java sources require equivalent boundary scanning: " +
                sources.joinToString { javaSourceDir.relativize(it).toString() },
        )
    }
}
