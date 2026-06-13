"""CLS-1: ordered rule classifier (CONTRACTS §3).

First match wins. Secret detection is independent (SG-1) and does not
change the content type.
"""

import re

_URL_LINE = re.compile(r"^https?://\S+$")
_PATH_WIN = re.compile(r"^[A-Za-z]:\\")
_PATH_UNC = re.compile(r"^\\\\")
_PATH_NIX = re.compile(r"^(?:/|~/)[^\s]*$")
_ERR_KEYWORD = re.compile(r"\b(?:ERROR|FATAL|Exception)\b")
_STACK_FRAME = re.compile(r" at .+\(.+:\d+\)")
_CODE_LINE = re.compile(
    r"^(?:def |class |import |from |function |const |let |var |#include|public |private )"
)
_TRACEBACK = "Traceback (most recent call last)"

_COMMAND_WORDS = frozenset(
    {
        "git", "docker", "docker-compose", "kubectl", "npm", "pnpm", "yarn",
        "pip", "pipx", "uv", "python", "node", "cargo", "go", "adb", "gh",
        "ssh", "scp", "curl", "wget", "powershell", "pwsh", "winget", "choco",
    }
)

_PROMPT_PREFIXES = ("你是", "请你", "你现在是", "扮演", "You are", "Act as", "Your task")


def classify(content: str) -> str:
    lines = content.split("\n")
    nonempty = [ln.strip() for ln in lines if ln.strip()]

    # 1. url: every non-empty line is a bare URL, at most 10 lines total
    if nonempty and len(lines) <= 10 and all(_URL_LINE.match(ln) for ln in nonempty):
        return "url"

    single = len(lines) == 1
    line = lines[0].strip() if single else ""

    # 2. path: single line resembling a filesystem path
    if single and (_PATH_WIN.match(line) or _PATH_UNC.match(line) or _PATH_NIX.match(line)):
        return "path"

    # 3. command: single short line starting with a shell prompt or known binary
    if single and len(line) <= 300:
        if line.startswith("$ ") or line.startswith("> "):
            return "command"
        first = line.split(None, 1)[0] if line else ""
        if first in _COMMAND_WORDS:
            return "command"

    # 4. error_log
    if _TRACEBACK in content:
        return "error_log"
    if len(_ERR_KEYWORD.findall(content)) >= 2:
        return "error_log"
    if sum(1 for ln in lines if _STACK_FRAME.search(ln)) >= 2:
        return "error_log"

    # 5. code: needs at least 3 lines plus a structural hint
    if len(lines) >= 3:
        if ("{" in content and "}" in content) or "```" in content:
            return "code"
        if any(_CODE_LINE.match(ln) for ln in lines):
            return "code"

    # 6. prompt
    if content.startswith(_PROMPT_PREFIXES) or "### Instruction" in content or "<system>" in content:
        return "prompt"

    return "text"
