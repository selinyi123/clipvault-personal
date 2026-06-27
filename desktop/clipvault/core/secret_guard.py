"""SG-1: secret detection (CONTRACTS §4, amended SG-1.1).

Hard rules quarantine with level="hard"; the entropy heuristic quarantines
with level="suspect". SG-1.1 amendment: known non-secret formats (pure hex
digests of length 32/40/64, UUIDs, unix-path-like tokens, base64 image
headers) are excluded from the entropy rule because they are common
clipboard content and provably not credentials by shape alone.
"""

import math
import re

from .models import SECRET_LEVEL_HARD, SECRET_LEVEL_SUSPECT, SecretVerdict

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("SG-PEM", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----")),
    ("SG-PUTTY", re.compile(r"PuTTY-User-Key-File")),
    ("SG-AWS-ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SG-AWS-SECRET", re.compile(r"(?i)aws.{0,20}(?:secret|key).{0,20}['\"][0-9A-Za-z/+=]{40}['\"]")),
    ("SG-GH", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|github_pat_[A-Za-z0-9_]{22,}")),
    ("SG-SLACK", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("SG-OPENAI", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("SG-GOOGLE", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("SG-JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b")),
    # High-confidence distinctive-prefix provider keys (low false-positive shape).
    ("SG-STRIPE", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("SG-GITLAB", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("SG-SENDGRID", re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b")),
    ("SG-NPM", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("SG-DIGITALOCEAN", re.compile(r"\bdop_v1_[a-f0-9]{64}\b")),
    ("SG-SLACK-URL", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9_/+-]{24,}")),
    (
        "SG-ASSIGN",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
            r"|client[_-]?secret|auth)\b\s*[:=]\s*\S{8,}"
        ),
    ),
    (
        "SG-CONNSTR",
        re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@]+@"),
    ),
]

_ENV_LINE = re.compile(r"^[A-Z][A-Z0-9_]{2,}=\S+$")
_ENV_SENSITIVE = re.compile(r"KEY|TOKEN|SECRET|PASS|PWD")

_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9+/=_\-]+$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_IMAGE_B64_PREFIXES = ("iVBORw0KGgo", "/9j/", "R0lGOD")

ENTROPY_MIN_LEN = 24
ENTROPY_THRESHOLD = 3.8  # bits per character


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _is_known_non_secret_format(token: str) -> bool:
    """SG-1.1 exclusions."""
    if _UUID.match(token):
        return True
    if _HEX.match(token) and len(token) in (32, 40, 64):
        return True
    if token.startswith(("/", "~")):
        return True
    if token.startswith(_IMAGE_B64_PREFIXES):
        return True
    return False


def scan(content: str) -> SecretVerdict:
    reasons = [rule_id for rule_id, rx in _PATTERNS if rx.search(content)]

    env_lines = [ln for ln in content.split("\n") if _ENV_LINE.match(ln)]
    if len(env_lines) >= 2 and any(
        _ENV_SENSITIVE.search(ln.split("=", 1)[0]) for ln in env_lines
    ):
        reasons.append("SG-ENV")

    if reasons:
        return SecretVerdict(True, SECRET_LEVEL_HARD, reasons)

    token = content.strip()
    if (
        len(token) >= ENTROPY_MIN_LEN
        and _TOKEN_CHARS.match(token)
        and not _is_known_non_secret_format(token)
        and shannon_entropy(token) >= ENTROPY_THRESHOLD
    ):
        return SecretVerdict(True, SECRET_LEVEL_SUSPECT, ["SG-ENTROPY"])

    return SecretVerdict(False, None, [])


def redact_preview(content: str) -> str:
    """UI preview for quarantined clips: first 4 chars, fixed-length mask."""
    return content[:4] + "••••"
