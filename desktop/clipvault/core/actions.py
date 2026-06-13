"""Context Action Engine — rule-based next-step suggestions (PRODUCT_SPEC §5.6).

Pure logic, no IO, no AI (ADR-0007: AI actions are P2 and user-triggered only).
Maps a clip's content type to recommended action chips; the chips reference
operations that already exist (promote to a memory kind / copy / release).
"""

from dataclasses import dataclass


@dataclass
class Action:
    action: str            # "promote" | "copy" | "release"
    label: str
    kind: str | None = None  # target memory kind for promote


# content_type -> (label, target memory kind)
_PROMOTE = {
    "command": ("保存为常用命令", "command"),
    "prompt": ("归档为 Prompt", "prompt"),
    "url": ("保存链接到词库", "path"),
    "path": ("保存路径到词库", "path"),
    "code": ("加入代码片段", "phrase"),
    "error_log": ("加入词库", "phrase"),
    "text": ("加入词库", "phrase"),
}


def recommend(content_type: str, is_secret: bool) -> list[Action]:
    if is_secret:
        # Quarantined clips offer only release; no copy/promote path (G1/threat model).
        return [Action("release", "释放为非密钥")]
    chips: list[Action] = []
    promo = _PROMOTE.get(content_type)
    if promo:
        chips.append(Action("promote", promo[0], promo[1]))
    chips.append(Action("copy", "复制"))
    return chips
