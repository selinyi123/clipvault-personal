#!/usr/bin/env python3
"""Run low-side-effect Windows portable candidate smoke checks for Issue #82.

This helper is intentionally narrow. It checks the downloaded Windows
release-candidate portable executable, renders a JSON report that can be folded
into `tools/field_test_evidence.py`, and keeps remaining manual rows explicit.

The service-mode smoke uses a temporary config with backup disabled, an isolated
data directory, a temporary loopback port, and a very long watcher poll interval
so the short health check does not read or ingest the user's current clipboard.
It does not run the installer, write the clipboard, test sync, post to GitHub,
sign or publish releases, close Issue #82/#36, or claim v1.7 stable.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_VERSION = "1.6.0"
WATCHER_POLL_INTERVAL_MS = 600_000


@dataclass(frozen=True)
class Check:
    status: str
    evidence: str
    next_step: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "evidence": self.evidence,
            "next_step": self.next_step,
        }


def scope_note() -> str:
    return (
        "Windows portable candidate smoke only. This checks the candidate "
        "portable executable with an isolated temporary config and loopback "
        "health probe. It does not run the installer, write or validate "
        "clipboard capture, test LAN/Tailscale sync, validate Android device "
        "behavior, sign or publish releases, close Issue #82/#36, or claim "
        "v1.7 stable."
    )


def expected_portable_name(version: str) -> str:
    version = version.strip()
    if not version:
        raise ValueError("version must be non-empty")
    return f"ClipVault-Desktop-v{version}-portable.exe"


def locate_portable(windows_dir: Path, *, version: str) -> Path:
    portable = windows_dir / expected_portable_name(version)
    if not portable.exists():
        raise FileNotFoundError(f"missing Windows portable candidate: {portable}")
    if not portable.is_file():
        raise ValueError(f"Windows portable candidate is not a file: {portable}")
    return portable


def _path_for_toml(path: Path) -> str:
    return str(path).replace("\\", "/")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_smoke_config(work_dir: Path, *, port: int) -> Path:
    base = work_dir / "ClipVaultSmoke"
    data_dir = base / "data"
    vault_dir = base / "vault"
    log_dir = base / "logs"
    for path in (data_dir, vault_dir, log_dir):
        path.mkdir(parents=True, exist_ok=True)
    config_path = base / "config.toml"
    config_path.write_text(
        "\n".join([
            "[device]",
            'device_id = "SMOKEDEVICE000000000000000001"',
            'device_name = "windows-smoke"',
            "",
            "[storage]",
            f'db_path = "{_path_for_toml(data_dir / "clipvault.db")}"',
            "max_clip_bytes = 1048576",
            "",
            "[watcher]",
            f"poll_fallback_ms = {WATCHER_POLL_INTERVAL_MS}",
            "",
            "[obsidian]",
            f'vault_path = "{_path_for_toml(vault_dir)}"',
            "",
            "[backup]",
            'repo_path = ""',
            "interval_minutes = 15",
            "enabled = false",
            "",
            "[server]",
            'host = "127.0.0.1"',
            f"port = {port}",
            "",
            "[log]",
            f'dir = "{_path_for_toml(log_dir)}"',
            "retention_days = 1",
            "",
        ]),
        encoding="utf-8",
        newline="\n",
    )
    return config_path


def _base_env(work_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(work_dir)
    return env


def _run_help_check(portable: Path, *, work_dir: Path, timeout_s: int) -> Check:
    try:
        completed = subprocess.run(
            [str(portable), "--help"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=_base_env(work_dir),
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return Check(
            "fail",
            f"`{portable.name} --help` timed out after {timeout_s}s.",
            "Inspect the portable executable startup path and rerun the smoke helper.",
        )
    except OSError as exc:
        return Check(
            "fail",
            f"`{portable.name} --help` could not start: {exc}.",
            "Confirm the downloaded Windows candidate is executable on this workstation.",
        )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return Check(
            "fail",
            f"`{portable.name} --help` exited {completed.returncode}: {output.strip()[:500]}",
            "Fix the portable CLI startup error, rebuild candidate artifacts, and rerun smoke.",
        )
    if "%LOCALAPPDATA%/ClipVault/config.toml" not in output:
        return Check(
            "fail",
            f"`{portable.name} --help` exited 0 but did not render the expected config help text.",
            "Inspect argparse help output before treating the portable launch path as covered.",
        )
    return Check(
        "pass",
        f"`{portable.name} --help` exited 0 and rendered the literal `%LOCALAPPDATA%/ClipVault/config.toml` default.",
    )


def _read_health(port: int, *, timeout_s: int) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/health",
        headers={"Host": f"127.0.0.1:{port}"},
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = response.read(64 * 1024)
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("/api/health did not return a JSON object")
    return data


def _service_early_exit_check(portable_name: str, details: str) -> Check:
    details = details.strip()
    if "already running" in details.lower():
        return Check(
            "blocked",
            f"`{portable_name} --headless --no-open` reached the existing ClipVault single-instance lock: {details[:500]}",
            "Close the already-running ClipVault desktop instance or run on a clean Windows test session, then rerun the smoke helper.",
        )
    return Check(
        "fail",
        f"`{portable_name} --headless --no-open` exited before health check: {details[:500]}",
        "Inspect service startup logs, rebuild candidate artifacts if needed, and rerun smoke.",
    )


def _stop_process_tree(process: subprocess.Popen[str], config_path: Path) -> None:
    """Best-effort cleanup for PyInstaller process trees on Windows.

    The one-file bootloader can leave the long-running child process detached
    from the Popen handle. The smoke config path is unique per run, so use it as
    the narrow cleanup selector instead of killing every ClipVault process.
    """
    if platform.system().lower() == "windows":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
        script = f"""
$needle = @'
{config_path}
'@
Get-CimInstance Win32_Process |
  Where-Object {{ $_.CommandLine -like "*$needle*" }} |
  ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
            check=False,
            capture_output=True,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_health_check(
    portable: Path,
    *,
    work_dir: Path,
    version: str,
    timeout_s: int,
) -> Check:
    port = _free_loopback_port()
    config_path = write_smoke_config(work_dir, port=port)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [str(portable), "--config", str(config_path), "--headless", "--no-open"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=_base_env(work_dir),
            creationflags=creationflags,
        )
        deadline = time.monotonic() + timeout_s
        last_error = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                details = (stdout or "") + (stderr or "")
                return _service_early_exit_check(portable.name, details)
            try:
                health = _read_health(port, timeout_s=1)
            except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                time.sleep(0.25)
                continue
            if (
                health.get("status") == "ok"
                and health.get("version") == version
                and health.get("db_ok") is True
            ):
                return Check(
                    "pass",
                    f"`{portable.name}` served `/api/health` on isolated loopback port {port}: "
                    f"status=ok, version={version}, db_ok=true; watcher poll interval "
                    f"was set to {WATCHER_POLL_INTERVAL_MS}ms for this smoke config.",
                )
            return Check(
                "fail",
                f"`{portable.name}` returned unexpected `/api/health`: {json.dumps(health, sort_keys=True)}",
                "Inspect the portable service health response and version metadata.",
            )
        return Check(
            "fail",
            f"`{portable.name}` did not serve `/api/health` within {timeout_s}s. Last error: {last_error}",
            "Inspect service startup logs and confirm no local port/security software conflict.",
        )
    except OSError as exc:
        return Check(
            "fail",
            f"`{portable.name} --headless --no-open` could not start: {exc}.",
            "Confirm the downloaded Windows candidate is executable on this workstation.",
        )
    finally:
        if process is not None:
            _stop_process_tree(process, config_path)


def build_report(
    *,
    windows_dir: Path,
    version: str,
    work_dir: Path,
    timeout_s: int,
) -> dict[str, object]:
    checks: dict[str, Check] = {}
    try:
        portable = locate_portable(windows_dir, version=version)
        checks["portable_file"] = Check(
            "pass",
            f"Found Windows portable candidate `{portable.name}` ({portable.stat().st_size} bytes).",
        )
    except (OSError, ValueError) as exc:
        checks["portable_file"] = Check(
            "fail",
            str(exc),
            "Download and verify the Windows release-candidate artifact directory, then rerun smoke.",
        )
        portable = None

    if portable is None:
        checks["help"] = Check("blocked", "-", "Fix portable candidate discovery first.")
        checks["headless_health"] = Check("blocked", "-", "Fix portable candidate discovery first.")
    elif platform.system().lower() != "windows":
        checks["help"] = Check(
            "blocked",
            f"Current platform is {platform.system()}; Windows portable smoke must run on Windows.",
            "Run this helper on the Owner Windows test device or a Windows runner.",
        )
        checks["headless_health"] = Check(
            "blocked",
            f"Current platform is {platform.system()}; Windows portable smoke must run on Windows.",
            "Run this helper on the Owner Windows test device or a Windows runner.",
        )
    else:
        checks["help"] = _run_help_check(portable, work_dir=work_dir, timeout_s=timeout_s)
        if checks["help"].status == "pass":
            checks["headless_health"] = _run_health_check(
                portable,
                work_dir=work_dir,
                version=version,
                timeout_s=timeout_s,
            )
        else:
            checks["headless_health"] = Check(
                "blocked",
                "-",
                "Fix the portable help/startup check before running service health smoke.",
            )

    statuses = {check.status for check in checks.values()}
    ok = statuses == {"pass"}
    if ok:
        status = "pass"
        next_step = ""
        evidence = (
            checks["portable_file"].evidence
            + " "
            + checks["help"].evidence
            + " "
            + checks["headless_health"].evidence
        )
    elif "fail" in statuses:
        status = "fail"
        failed = [check for check in checks.values() if check.status == "fail"]
        next_step = failed[0].next_step
        evidence = failed[0].evidence
    else:
        status = "blocked"
        blocked = [check for check in checks.values() if check.status == "blocked"]
        next_step = blocked[0].next_step
        evidence = blocked[0].evidence

    return {
        "ok": ok,
        "status": status,
        "evidence": evidence,
        "next_step": next_step,
        "source_version": version,
        "windows_dir": str(windows_dir),
        "work_dir": str(work_dir),
        "windows_environment": platform.platform(),
        "checks": {key: check.as_dict() for key, check in checks.items()},
        "scope_note": scope_note(),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Windows portable release-candidate smoke checks.")
    parser.add_argument("--windows-dir", type=Path, required=True)
    parser.add_argument("--source-version", default=DEFAULT_VERSION)
    parser.add_argument("--work-dir", type=Path, help="isolated working directory; defaults to a temporary directory")
    parser.add_argument("--keep-work-dir", action="store_true", help="do not delete the temporary working directory")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    parser.add_argument("--no-fail", action="store_true", help="return exit code 0 even when smoke is incomplete")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.timeout < 3:
        parser.error("--timeout must be at least 3 seconds")

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir:
        work_dir = args.work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir = tempfile.TemporaryDirectory(
            prefix="clipvault-windows-smoke-",
            ignore_cleanup_errors=True,
        )
        work_dir = Path(temp_dir.name).resolve()

    try:
        report = build_report(
            windows_dir=args.windows_dir.resolve(),
            version=args.source_version,
            work_dir=work_dir,
            timeout_s=args.timeout,
        )
        if not args.keep_work_dir and temp_dir is not None:
            report["work_dir"] = str(work_dir) + " (temporary; cleaned after run)"
        payload = json.dumps(report, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        if args.json or not args.output:
            print(payload)
        return 0 if args.no_fail or report["ok"] else 2
    finally:
        if temp_dir is not None and not args.keep_work_dir:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
