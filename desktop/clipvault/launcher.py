"""Desktop app experience: first-run that just works, auto-open panel, system
tray. Keeps the headless service (main.py) intact — this only adds the
human-facing shell around it so double-clicking the app does something visible.

Design goals for first contact:
- No manual config editing required. First run creates a working config under
  %LOCALAPPDATA%\\ClipVault with absolute paths and a default vault.
- The browser opens to the Web UI so the user immediately sees ClipVault.
- A tray icon shows it is running and lets the user reopen the panel or quit.
"""

import os
import sys
import threading
import webbrowser
from pathlib import Path

from clipvault.core import ulid


def default_base_dir() -> Path:
    """Per-user data dir so the app works regardless of where the .exe sits."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "ClipVault"


def default_vault_dir() -> Path:
    return Path.home() / "Documents" / "ClipVault Vault"


def default_config_path() -> Path:
    return default_base_dir() / "config.toml"


def _config_text(*, device_id: str, vault_path: Path, db_path: Path, log_dir: Path) -> str:
    def p(x: Path) -> str:
        return str(x).replace("\\", "/")
    return f"""# ClipVault Personal — 自动生成的配置（可随时修改后重启生效）
[device]
device_id   = "{device_id}"
device_name = "desktop-main"

[storage]
db_path        = "{p(db_path)}"
max_clip_bytes = 1048576

[watcher]
poll_fallback_ms = 500

[obsidian]
# 想入库到你自己的 Obsidian 仓库？把下面改成你的 Vault 路径即可。
vault_path = "{p(vault_path)}"

[backup]
# 准备好一个【私有】GitHub 仓库后，填 repo_path 并设 enabled = true
repo_path        = ""
interval_minutes = 15
enabled          = false

[server]
host = "127.0.0.1"
port = 8787

[log]
dir = "{p(log_dir)}"
retention_days = 14
"""


def ensure_config(config_path: Path | None = None) -> Path:
    """Return a usable config path, creating a working default on first run."""
    path = Path(config_path) if config_path else default_config_path()
    if path.exists():
        return path
    base = path.parent
    base.mkdir(parents=True, exist_ok=True)
    vault = default_vault_dir()
    vault.mkdir(parents=True, exist_ok=True)
    text = _config_text(
        device_id=ulid.new(),
        vault_path=vault,
        db_path=base / "data" / "clipvault.db",
        log_dir=base / "logs",
    )
    path.write_text(text, encoding="utf-8")
    return path


def open_panel(port: int) -> None:
    try:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    except Exception:
        pass


def third_party_notices_path() -> Path:
    """Return the bundled notice in an executable or the repository-root copy."""
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle_root / "THIRD_PARTY_NOTICES.md"
    return Path(__file__).resolve().parents[2] / "THIRD_PARTY_NOTICES.md"


def read_third_party_notices() -> str:
    return third_party_notices_path().read_text(encoding="utf-8")


def open_third_party_notices() -> None:
    try:
        os.startfile(str(third_party_notices_path()))
    except Exception:
        pass


def make_icon_image(size: int = 64):
    """A simple 'CV' badge so the tray/app has a recognizable mark (Pillow)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=size // 5, fill=(110, 168, 254, 255))
    d.rectangle([size * 0.30, size * 0.24, size * 0.40, size * 0.76], fill=(15, 17, 21, 255))
    d.polygon(
        [(size * 0.52, size * 0.24), (size * 0.62, size * 0.24),
         (size * 0.70, size * 0.76), (size * 0.60, size * 0.76)],
        fill=(15, 17, 21, 255),
    )
    return img


def _make_tray_icon(port: int, base_dir: Path, on_quit):
    """Import the real tray dependencies and construct, but do not run, an icon."""
    import pystray

    def _open_panel(icon, item):
        open_panel(port)

    def _open_config(icon, item):
        try:
            os.startfile(str(base_dir))  # noqa: S606 (open the config folder)
        except Exception:
            pass

    def _quit(icon, item):
        icon.stop()
        on_quit()

    menu = pystray.Menu(
        pystray.MenuItem("打开面板", _open_panel, default=True),
        pystray.MenuItem("打开配置文件夹", _open_config),
        pystray.MenuItem(
            "第三方许可证",
            lambda _icon, _item: open_third_party_notices(),
        ),
        pystray.MenuItem("退出 ClipVault", _quit),
    )
    return pystray.Icon(
        "ClipVault",
        make_icon_image(),
        "ClipVault Personal",
        menu,
    )


class PillowFeaturePolicyError(RuntimeError):
    """Raised when a release-disallowed optional Pillow feature is enabled."""


class TrayRelinkMarkerError(RuntimeError):
    """Raised when the recipient relink exercise marker is absent or changed."""


RELINK_EXERCISE_MARKER = "recipient-modified-pystray"


def _enabled_disallowed_pillow_features() -> tuple[str, ...]:
    from PIL import features

    return tuple(
        name
        for name in ("libimagequant", "raqm")
        if features.check_feature(name)
    )


def self_test_tray(*, require_relink_marker: bool = False) -> None:
    """Validate packaged tray policy, imports, and construction without UI."""
    enabled = _enabled_disallowed_pillow_features()
    if enabled:
        raise PillowFeaturePolicyError(
            "release-disallowed optional Pillow features are enabled"
        )
    if require_relink_marker:
        import pystray

        if (
            getattr(pystray, "CLIPVAULT_RELINK_EXERCISE_MARKER", None)
            != RELINK_EXERCISE_MARKER
        ):
            raise TrayRelinkMarkerError(
                "recipient pystray relink exercise marker is absent"
            )
    _make_tray_icon(0, Path("."), lambda: None)


def run_tray(port: int, base_dir: Path, on_quit, stop_event=None) -> bool:
    """Block on a system tray icon. Calls on_quit() when the user exits.
    An optional runtime stop event also closes the icon after worker failure.
    Returns False if the tray can't start (no pystray, or no GUI session)."""
    if stop_event is not None and stop_event.is_set():
        return True
    try:
        icon = _make_tray_icon(port, base_dir, on_quit)
        if stop_event is not None:
            def _stop_on_runtime_exit():
                stop_event.wait()
                icon.stop()

            threading.Thread(
                target=_stop_on_runtime_exit,
                daemon=True,
                name="tray-runtime-stop",
            ).start()
        icon.run()
    except Exception:
        return False
    return True
