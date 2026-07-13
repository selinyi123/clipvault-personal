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
    fun imePackageStaysThinAndDoesNotBypassRuntimePrivacyBoundary() {
        assertTrue("IME source directory is missing: $imeSourceDir", Files.isDirectory(imeSourceDir))

        val facadeSource = imeSourceDir.parent.resolve(Path.of("runtime", "ClipVaultFacade.kt"))
        assertTrue("Runtime facade source is missing: $facadeSource", Files.isRegularFile(facadeSource))
        val facadeText = Files.readString(facadeSource)
        val interfaceStart = facadeText.indexOf("interface ClipVaultFacade {")
        val interfaceEnd = facadeText.indexOf("\n}", interfaceStart)
        assertTrue("ClipVaultFacade interface boundary is missing", interfaceStart >= 0 && interfaceEnd > interfaceStart)
        val approvedImeMethods = setOf("listCandidates", "saveExplicit")
        val facadeMethods = Regex("""\bfun\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(""")
            .findAll(facadeText.substring(interfaceStart, interfaceEnd))
            .map { it.groupValues[1] }
            .toSet()
        assertTrue("approved IME Runtime methods must exist", approvedImeMethods.all { it in facadeMethods })
        val disallowedFacadePatterns = (facadeMethods - approvedImeMethods).map { method ->
            BlockedPattern(
                Regex("""\b${Regex.escape(method)}\b"""),
                "IME services may not call unapproved Runtime method $method",
            )
        }

        val blockedPatterns = listOf(
            BlockedPattern(
                Regex("""^\s*import\s+com\.clipvault\.app\.ClipVaultApp\b"""),
                "IME services must not reach the application database singleton directly",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+com\.clipvault\.app\.(capture|data)(\.|$)"""),
                "capture/data persistence belongs behind the Runtime facade",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+com\.clipvault\.core(\.|$)"""),
                "IME services must not bypass Runtime/Capture privacy gates with direct core scanning",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+com\.clipvault\.app\.sync(\.|$)"""),
                "project sync imports belong outside IME services",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+androidx\.room(\.|$)"""),
                "Room access belongs behind the Runtime facade, not in IME services",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+androidx\.work\."""),
                "WorkManager scheduling belongs outside IME services",
            ),
            BlockedPattern(
                Regex("""\bandroidx\.work\."""),
                "fully-qualified WorkManager calls belong outside IME services",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+android\.content\.SharedPreferences\b"""),
                "IME services must not add direct preference persistence paths",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+(android\.net|java\.net|javax\.net|okhttp3|retrofit2|io\.ktor)\."""),
                "network imports belong outside IME services",
            ),
            BlockedPattern(
                Regex("""^\s*import\s+(java\.io|java\.nio\.file)\."""),
                "file persistence belongs outside IME services",
            ),
            BlockedPattern(
                Regex("""\b(ClipVaultApp|AppDatabase|ClipDao|OutboxDao|MemoryDao|ClipEntity|OutboxEntity|MemoryEntity)\b"""),
                "IME services must not touch database types directly",
            ),
            BlockedPattern(
                Regex("""\b(Capture\.ingest|SecretGuard\.scan|Classifier\.classify|Normalize\.)\b"""),
                "IME services must not bypass Runtime/Capture privacy gates",
            ),
            BlockedPattern(
                Regex("""\b(getSharedPreferences|openFileInput|openFileOutput|getDatabasePath|getExternalFilesDir)\s*\("""),
                "IME services must not add direct local persistence calls",
            ),
            BlockedPattern(
                Regex("""\b(?:android\.net|java\.net|javax\.net|okhttp3|retrofit2|io\.ktor)\."""),
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
            BlockedPattern(
                Regex("""\b(getTextBeforeCursor|getTextAfterCursor|getSelectedText|getExtractedText|getSurroundingText|getInitialTextBeforeCursor|getInitialSelectedText|getInitialTextAfterCursor|getCursorCapsMode|requestCursorUpdates|onUpdateSelection|onUpdateCursorAnchorInfo|onUpdateExtractedText)\b"""),
                "IME services must not observe ordinary typed text or cursor context",
            ),
            BlockedPattern(
                Regex("""\.getText\s*\(|\b(?:cm|clipboard|clipboardManager)\.text\b"""),
                "IME services must not use deprecated or synthetic clipboard text reads",
            ),
            BlockedPattern(
                Regex("""\b(addPrimaryClipChangedListener|removePrimaryClipChangedListener|onPrimaryClipChanged)\b"""),
                "IME clipboard access must remain an explicit button action, never a listener",
            ),
            BlockedPattern(
                Regex("""\b(?:kotlin\.io\.)?(?:print|println)\s*\(|\bSystem\.(?:out|err)\b"""),
                "IME services must not write typed-text-adjacent stdout or stderr",
            ),
            BlockedPattern(
                Regex("""\bruntime\.(?!(?:listCandidates|saveExplicit)\s*\()[A-Za-z_][A-Za-z0-9_]*\s*\("""),
                "new IME Runtime capabilities require explicit privacy-boundary review",
            ),
            BlockedPattern(
                Regex("""::(?:listCandidates|saveExplicit)\b"""),
                "IME Runtime callable references obscure the reviewed call boundary",
            ),
        ) + disallowedFacadePatterns

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
