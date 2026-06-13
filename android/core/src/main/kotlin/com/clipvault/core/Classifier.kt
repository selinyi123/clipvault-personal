package com.clipvault.core

/** CLS-1 (CONTRACTS §3). First match wins; mirrors desktop clipvault.core.classifier. */
object Classifier {
    private val URL_LINE = Regex("""^https?://\S+$""")
    private val PATH_WIN = Regex("""^[A-Za-z]:\\""")
    private val PATH_UNC = Regex("""^\\\\""")
    private val PATH_NIX = Regex("""^(/|~/)[^\s]*$""")
    private val ERR_KEYWORD = Regex("""\b(ERROR|FATAL|Exception)\b""")
    private val STACK_FRAME = Regex(""" at .+\(.+:\d+\)""")
    private val CODE_LINE =
        Regex("""^(def |class |import |from |function |const |let |var |#include|public |private )""")
    private const val TRACEBACK = "Traceback (most recent call last)"

    private val COMMAND_WORDS = setOf(
        "git", "docker", "docker-compose", "kubectl", "npm", "pnpm", "yarn",
        "pip", "pipx", "uv", "python", "node", "cargo", "go", "adb", "gh",
        "ssh", "scp", "curl", "wget", "powershell", "pwsh", "winget", "choco",
    )

    private val PROMPT_PREFIXES = listOf(
        "你是", "请你", "你现在是", "扮演", "You are", "Act as", "Your task",
    )

    fun classify(content: String): String {
        val lines = content.split("\n")
        val nonEmpty = lines.map { it.trim() }.filter { it.isNotEmpty() }

        // 1. url
        if (nonEmpty.isNotEmpty() && lines.size <= 10 && nonEmpty.all { URL_LINE.matches(it) }) {
            return "url"
        }

        val single = lines.size == 1
        val line = if (single) lines[0].trim() else ""

        // 2. path
        if (single && (PATH_WIN.containsMatchIn(line) || PATH_UNC.containsMatchIn(line) ||
                    PATH_NIX.matches(line))) {
            return "path"
        }

        // 3. command
        if (single && line.length <= 300) {
            if (line.startsWith("$ ") || line.startsWith("> ")) return "command"
            val first = if (line.isNotEmpty()) line.split(Regex("""\s+""")).first() else ""
            if (first in COMMAND_WORDS) return "command"
        }

        // 4. error_log
        if (content.contains(TRACEBACK)) return "error_log"
        if (ERR_KEYWORD.findAll(content).count() >= 2) return "error_log"
        if (lines.count { STACK_FRAME.containsMatchIn(it) } >= 2) return "error_log"

        // 5. code
        if (lines.size >= 3) {
            if ((content.contains("{") && content.contains("}")) || content.contains("```")) {
                return "code"
            }
            if (lines.any { CODE_LINE.containsMatchIn(it) }) return "code"
        }

        // 6. prompt
        if (PROMPT_PREFIXES.any { content.startsWith(it) } ||
            content.contains("### Instruction") || content.contains("<system>")) {
            return "prompt"
        }

        return "text"
    }
}
