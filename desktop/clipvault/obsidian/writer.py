"""OBS-1: Obsidian Markdown generation and atomic, idempotent writes.

render() is pure (string in, strings out) so the format is golden-file
testable; write() owns all filesystem concerns.
"""

import os
import re
from datetime import datetime, timezone, tzinfo
from pathlib import Path

from clipvault.core.models import Clip

DEFAULT_TYPE_DIRS = {
    "text": "00_Inbox/Clipboard",
    "prompt": "01_Prompt",
    "code": "02_Code",
    "error_log": "03_Error_Log",
    "url": "04_Web_Link",
    "command": "05_Command",
    "path": "00_Inbox/Clipboard",
}

_SLUG_FORBIDDEN = re.compile(r"[\\/:*?\"<>|#^\[\]]")
_BACKTICK_RUN = re.compile(r"`+")
_RECOVERY_COLLISION_LIMIT = 64


class SecretWriteRefused(Exception):
    """Gate B: secret clips must never be rendered into the vault."""


def _slug(content: str) -> str:
    first_line = next((ln for ln in content.split("\n") if ln.strip()), "")
    s = _SLUG_FORBIDDEN.sub("", first_line).strip().replace(" ", "-")
    return s[:24] or "clip"


def _guess_lang(content: str) -> str:
    if re.search(r"^(?:def |import |from \S+ import )", content, re.MULTILINE):
        return "python"
    if "#include" in content:
        return "c"
    if re.search(r"^(?:function |const |let )", content, re.MULTILINE) or "=>" in content:
        return "javascript"
    if re.search(r"^public (?:class|static)", content, re.MULTILINE):
        return "java"
    return ""


def _fence(content: str) -> str:
    longest = max((len(m) for m in _BACKTICK_RUN.findall(content)), default=0)
    return "`" * max(3, longest + 1)


def render(
    clip: Clip,
    type_dirs: dict[str, str] | None = None,
    tz: tzinfo | None = None,
) -> tuple[str, str]:
    """Return (vault-relative path, file content) for a public clip."""
    if clip.is_secret:
        raise SecretWriteRefused(clip.id)
    dirs = type_dirs or DEFAULT_TYPE_DIRS
    type_dir = dirs.get(clip.content_type, dirs["text"])

    created = datetime.strptime(clip.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    local = created.astimezone(tz)  # tz=None -> machine local time
    filename = f"{local:%Y%m%d}-{local:%H%M%S}_{_slug(clip.content)}_{clip.id[-6:]}.md"
    rel_path = f"{type_dir}/{filename}"

    lines = [
        "---",
        f"clipvault_id: {clip.id}",
        f"created: {clip.created_at}",
        f"source_device: {clip.source_device}",
    ]
    if clip.source_app:
        lines.append(f"source_app: {clip.source_app}")
    lines += [
        f"type: {clip.content_type}",
        f"content_hash: sha256:{clip.content_hash}",
        "tags:",
        "  - clipvault",
        f"  - clipvault/{clip.content_type}",
        "---",
        "",
    ]

    if clip.content_type == "code":
        fence = _fence(clip.content)
        body = f"{fence}{_guess_lang(clip.content)}\n{clip.content}\n{fence}"
    else:
        body = clip.content

    return rel_path, "\n".join(lines) + "\n" + body + "\n"


def write(vault_path: str | Path, rel_path: str, content: str) -> Path:
    """Atomic write with collision-suffix; never overwrites."""
    final = Path(vault_path) / rel_path
    final.parent.mkdir(parents=True, exist_ok=True)
    candidate = final
    n = 0
    while candidate.exists():
        n += 1
        candidate = final.with_name(f"{final.stem}-{n}{final.suffix}")
    tmp = candidate.with_name(candidate.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, candidate)
    return candidate


def write_clip(
    clip: Clip,
    vault_path: str | Path,
    type_dirs: dict[str, str] | None = None,
    tz: tzinfo | None = None,
) -> Path:
    """Idempotent entry point: a clip that already has an obsidian_path is
    never written again (user deletion of the note is a curation decision)."""
    if clip.is_secret:
        raise SecretWriteRefused(clip.id)
    if clip.obsidian_path:
        return Path(clip.obsidian_path)
    rel_path, content = render(clip, type_dirs, tz)
    # The Markdown file may have reached disk before SQLite recorded
    # ``obsidian_path`` (process crash, disk-full commit, transient DB error).
    # Recover that deterministic file by its stable clip id instead of creating
    # a collision-suffixed duplicate on retry.
    final = Path(vault_path) / rel_path
    if final.parent.is_dir():
        id_line = f"clipvault_id: {clip.id}"
        # Probe the deterministic path first, then a fixed collision window.
        # Avoid enumerating an entire large Vault directory on every new write.
        candidates = [final]
        candidates.extend(
            final.with_name(f"{final.stem}-{n}{final.suffix}")
            for n in range(1, _RECOVERY_COLLISION_LIMIT + 1)
        )
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                with candidate.open("r", encoding="utf-8") as handle:
                    header = "".join(handle.readline() for _ in range(12))
            except (OSError, UnicodeError):
                continue
            if id_line in header.splitlines():
                return candidate
    return write(vault_path, rel_path, content)
