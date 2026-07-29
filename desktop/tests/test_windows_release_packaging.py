"""Static release gates for the supported Windows tray build."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
PRODUCTION_LOCK = DESKTOP / "packaging" / "windows-release-requirements.txt"
CPYTHON_WINDOWS_LICENSE = (
    ROOT / "third_party" / "licenses" / "CPython-3.11.9-Windows-LICENSE.txt"
)
CPYTHON_WINDOWS_LICENSE_SHA256 = (
    "e502c6b880ff58d614901495a9009c136539cd0b1e2a2abb8fc00b934c203419"
)
CPYTHON_WINDOWS_RUNTIME_MEMBERS = (
    "_bz2.pyd",
    "_ctypes.pyd",
    "_decimal.pyd",
    "_elementtree.pyd",
    "_hashlib.pyd",
    "_lzma.pyd",
    "_queue.pyd",
    "_socket.pyd",
    "_sqlite3.pyd",
    "_ssl.pyd",
    "_uuid.pyd",
    "libcrypto-3.dll",
    "libffi-8.dll",
    "libssl-3.dll",
    "pyexpat.pyd",
    "python311.dll",
    "select.pyd",
    "sqlite3.dll",
    r"third_party\licenses\CPython-3.11.9-Windows-LICENSE.txt",
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _locked_packages(path: Path) -> dict[str, tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    rows: dict[str, tuple[str, str]] = {}
    pattern = re.compile(
        r"(?mi)^([a-z0-9_-]+)==([^\s\\]+)\s*\\\s*\n"
        r"\s*--hash=sha256:([0-9a-f]{64})$"
    )
    for name, version, digest in pattern.findall(text):
        rows[name.lower().replace("_", "-")] = (version, digest)
    return rows


def test_windows_runtime_and_build_dependencies_are_exactly_approved():
    project = tomllib.loads((DESKTOP / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["dependencies"] == [
        "Pillow==12.3.0",
        "pystray==0.19.5",
    ]
    assert project["dependency-groups"]["dev"] == ["pytest>=8.0"]
    assert project["dependency-groups"]["release"] == [
        "PyInstaller==6.21.0",
        "pyinstaller-hooks-contrib==2026.6",
    ]

    assert _locked_packages(PRODUCTION_LOCK) == {
        "pillow": (
            "12.3.0",
            "8e95e1385e4998ae9694eeaa4730ba5457ff61185b3a55e2e7bea0880aef452a",
        ),
        "pystray": (
            "0.19.5",
            "a0c2229d02cf87207297c22d86ffc57c86c227517b038c0d3c59df79295ac617",
        ),
        "six": (
            "1.17.0",
            "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274",
        ),
        "pyinstaller": (
            "6.21.0",
            "7fae06c494ce0ebfe6bd3055c0e409def884f63af2e3705d06bd431ad9237fc7",
        ),
        "pyinstaller-hooks-contrib": (
            "2026.6",
            "fd13b8ac126b35361175edacd41a0d97080b75dd5f4b594ecefefff969509dd3",
        ),
        "altgraph": (
            "0.17.5",
            "f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597",
        ),
        "packaging": (
            "26.2",
            "5fc45236b9446107ff2415ce77c807cee2862cb6fac22b8a73826d0693b0980e",
        ),
        "pefile": (
            "2024.8.26",
            "76f8b485dcd3b1bb8166f1128d395fa3d87af26360c2358fb75b80019b957c6f",
        ),
        "pywin32-ctypes": (
            "0.2.3",
            "8a1513379d709975552d202d942d9837758905c8d01eb82b8bcc30918929e7b8",
        ),
        "setuptools": (
            "83.0.0",
            "29b23c360f22f414dc7336bb39178cc7bcbf6021ed2733cde173f09dba19abb3",
        ),
    }


def test_exact_cpython_windows_binary_license_bundle_is_tracked():
    payload = CPYTHON_WINDOWS_LICENSE.read_bytes()
    text = payload.decode("utf-8")

    assert len(payload) == 36_874
    assert hashlib.sha256(payload).hexdigest() == CPYTHON_WINDOWS_LICENSE_SHA256
    assert "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2" in text
    assert "Additional Conditions for this Windows binary build" in text
    assert "Microsoft Distributable Code" in text
    assert "bzip2/libbzip2 version 1.0.8" in text
    assert "libffi - Copyright" in text
    assert "Apache License" in text


def test_windows_workflows_build_from_locked_wheels_and_gate_frozen_tray():
    for relative in (
        ".github/workflows/release.yml",
        ".github/workflows/release-candidate.yml",
    ):
        workflow = _read(relative)
        assert 'python-version: "3.11.9"' in workflow
        assert 'python-version: "3.11"\n' not in workflow
        assert 'architecture: "x64"' in workflow
        assert workflow.count("pip download --require-hashes --only-binary=:all:") == 1
        assert "pip install --upgrade" not in workflow
        assert "--no-index --find-links packaging/wheelhouse --require-hashes" in workflow
        assert r'.\.venv-test\Scripts\python.exe -m pip install "pytest>=8.0"' in workflow
        assert r". .\.venv-test\Scripts\Activate.ps1" in workflow
        assert "python -m pytest -q" in workflow
        assert r".\.venv-build\Scripts\python.exe -m PyInstaller" in workflow
        assert "--hide-console hide-early" in workflow
        assert '--icon "$PWD/packaging/clipvault.ico"' in workflow
        assert "--icon packaging/clipvault.ico" not in workflow
        assert "--hidden-import pystray._win32" in workflow
        assert "--self-test-tray" in workflow
        assert '"tray self-test ok"' in workflow
        assert (
            r".\.venv-build\Scripts\python.exe packaging/pillow_feature_probe.py"
            in workflow
        )
        assert "packaging/pillow-feature-report.txt" in workflow
        assert '$pillowFeatureReport[0] -cne "libimagequant=False"' in workflow
        assert '$pillowFeatureReport[1] -cne "raqm=False"' in workflow
        assert "-PillowFeatureReport desktop/packaging/pillow-feature-report.txt" in workflow
        assert "pyi-archive_viewer.exe -r -l dist/clipvault.exe" in workflow
        assert '$inventoryText = ($inventory -join "`n").Replace("\\\\", "\\")' in workflow
        assert 'foreach ($requiredModule in @("pystray._win32", "PIL.Image"))' in workflow
        assert '$requiredToken = "\'" + $requiredModule + "\'"' in workflow
        assert "$requiredWindowsRuntimeMembers = @(" in workflow
        for required_member in CPYTHON_WINDOWS_RUNTIME_MEMBERS:
            assert f'"{required_member}"' in workflow
        assert (
            "Frozen onefile inventory is missing required CPython Windows runtime member"
            in workflow
        )
        assert 'foreach ($disallowedComponent in @("libimagequant", "raqm"))' in workflow
        assert "Frozen onefile inventory is missing required module" in workflow
        assert "Frozen onefile inventory contains disallowed component" in workflow
        assert "Build-LgplRelinkKit.ps1" in workflow

        build = workflow.index("Build portable executable")
        tray_gate = workflow.index(
            "Verify frozen tray and Pillow features, then record onefile inventory"
        )
        pillow_probe = workflow.index(
            r".\.venv-build\Scripts\python.exe packaging/pillow_feature_probe.py"
        )
        pillow_validation = workflow.index(
            '$pillowFeatureReport[1] -cne "raqm=False"'
        )
        pillow_persist = workflow.index(
            "Set-Content -LiteralPath packaging/pillow-feature-report.txt"
        )
        cpython_runtime_gate = workflow.index("$requiredWindowsRuntimeMembers = @(")
        installer = workflow.index("Build installer")
        kit = workflow.index("Build-LgplRelinkKit.ps1")
        manifest = workflow.index("scripts/release_candidate_manifest.py", kit)
        assert (
            build
            < tray_gate
            < pillow_probe
            < pillow_validation
            < pillow_persist
            < cpython_runtime_gate
            < installer
            < kit
            < manifest
        )


def test_powershell_inventory_normalization_matches_real_pyi_path_repr():
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    script = r'''
$inventoryText = "'third_party\\licenses\\CPython-3.11.9-Windows-LICENSE.txt'"
$inventoryText = $inventoryText.Replace("\\", "\")
$requiredToken = "'third_party\licenses\CPython-3.11.9-Windows-LICENSE.txt'"
if (-not $inventoryText.Contains($requiredToken)) {
    throw "Normalized PyInstaller inventory path did not match"
}
'''

    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", "-"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_relink_kit_is_fail_closed_and_installer_carries_notices():
    kit = _read("desktop/packaging/Build-LgplRelinkKit.ps1")
    exporter = _read("desktop/packaging/Export-WheelNotices.ps1")
    installer = _read("installer/clipvault.iss")

    assert 'expectedOutputName = "ClipVault-v$Version-LGPL-relink-kit.zip"' in kit
    assert 'if ($Version -cne "1.6.0")' in kit
    assert "This relink kit contract supports only v1.6.0" in kit
    assert "third_party/licenses/CPython-3.11.9-Windows-LICENSE.txt" in kit
    assert CPYTHON_WINDOWS_LICENSE_SHA256 in kit
    assert '$pythonVersion[0].Trim() -cne "3.11.9"' in kit
    assert '$pythonVersion[1].Trim() -cne "AMD64"' in kit
    assert '$pythonVersion[2].Trim() -cne "cpython"' in kit
    assert (
        kit.index("The CPython 3.11.9 Windows binary license bundle hash")
        < kit.index("Invoke-WebRequest")
    )
    assert "$requiredWindowsRuntimeMembers = @(" in kit
    for required_member in CPYTHON_WINDOWS_RUNTIME_MEMBERS:
        assert f'"{required_member}"' in kit
    assert "Expected exactly 10 production wheels" in exporter
    assert "production_wheel_count = $expectedWheels.Count" in kit
    assert "4751562ba90301e054c87606079c1599301d84e7d1e4074b12af4f54a80a4768" in kit
    assert "bb9fab705983e393a2d1cac77d6972513057ad800215fd861dc15ff5272e98fd" in kit
    assert "bef5002c32f4f50bd55b005da12cff64eca8783e7eaf86a06a62410164bab725" in kit
    for required in (
        "THIRD_PARTY_NOTICES.md",
        "0012-windows-tray-dependencies-and-lgpl-delivery.md",
        "RELINKING_V1_6_0.md",
        "source-acquisition-v1.6.0.json",
        "wheelhouse-SHA256SUMS.txt",
        "source-SHA256SUMS.txt",
        "build-environment.json",
        "relink-kit-inventory.json",
        "clipvault-onefile-inventory.txt",
        "tray-self-test.txt",
        "pillow-feature-report.txt",
        "COPYING.LGPL",
        "CPython-3.11.9-Windows-LICENSE.txt",
        "pystray-COPYING-GPL-3.0.txt",
        "pystray-COPYING-LGPL-3.0.txt",
        "pillow-12.3.0.cdx.json",
        "pillow_feature_probe.py",
        "repack_pystray_wheel.py",
    ):
        assert required in kit
    assert "importlib.metadata.version('pip')" in kit
    assert "pip_version = $pipVersion[0]" in kit
    assert "-m pip --version" not in kit
    assert "Frozen onefile inventory is empty" in kit
    assert '$inventoryText = ($inventoryLines -join "`n").Replace("\\\\", "\\")' in kit
    assert 'foreach ($requiredModule in @("pystray._win32", "PIL.Image"))' in kit
    assert 'foreach ($disallowedComponent in @("libimagequant", "raqm"))' in kit
    assert '$pillowFeatureReportLines[0] -cne "libimagequant=False"' in kit
    assert '$pillowFeatureReportLines[1] -cne "raqm=False"' in kit
    assert "& $pythonPath $pillowFeatureProbePath 2>&1" in kit
    assert (
        '$observedPillowFeatureLines[0] -cne $pillowFeatureReportLines[0]'
        in kit
    )
    assert (
        '$observedPillowFeatureLines[1] -cne $pillowFeatureReportLines[1]'
        in kit
    )
    assert 'evidence = "inventory/pillow-feature-report.txt"' in kit
    assert (
        'frozen_tray_evidence = "inventory/tray-self-test.txt"'
        in kit
    )
    assert 'path = "licenses/CPython-3.11.9-Windows-LICENSE.txt"' in kit
    assert 'source = "official CPython 3.11.9 Windows NuGet tools/LICENSE.txt"' in kit
    assert "Relink kit ZIP inventory does not match the staged payload" in kit
    assert "Extracted relink kit inventory does not match the staged payload" in kit
    assert "Expand-Archive -LiteralPath $outputPath" in kit

    assert 'DestDir: "{app}\\licenses"' in installer
    assert "THIRD_PARTY_NOTICES.md" in installer
    assert "RELINKING_V1_6_0.md" in installer
    assert (
        'Source: "..\\third_party\\licenses\\CPython-3.11.9-Windows-LICENSE.txt"; '
        'DestDir: "{app}\\licenses"; '
        'DestName: "CPython-3.11.9-Windows-LICENSE.txt"; Flags: ignoreversion'
        in installer
    )

    release = _read(".github/workflows/release.yml")
    assert "pystray 0.19.5" in release
    assert "LGPL-3.0-or-later" in release
    assert "`ClipVault-${RELEASE_TAG}-LGPL-relink-kit.zip`" in release


def test_cpython_runtime_source_and_notice_metadata_are_release_bound():
    notices = _read("THIRD_PARTY_NOTICES.md")
    acquisition = json.loads(_read("third_party/source-acquisition-v1.6.0.json"))
    runtime = acquisition["windows_runtime_interpreter"]

    assert "CPython" in notices
    assert "3.11.9" in notices
    assert "PSF License Version 2" in notices
    assert "CPython-3.11.9-Windows-LICENSE.txt" in notices

    assert runtime["name"] == "CPython"
    assert runtime["version"] == "3.11.9"
    assert runtime["implementation"] == "cpython"
    assert runtime["architecture"] == "win_amd64"
    assert runtime["package_type"] == "official NuGet portable package"
    assert "clean-recipient and relinking interpreter distribution" in (
        runtime["package_role"]
    )
    assert runtime["release_workflow_provisioning"] == (
        "actions/setup-python@v6 with exact Python 3.11.9 and x64 architecture, "
        "plus final onefile runtime inventory gates"
    )
    assert runtime["filename"] == "python.3.11.9.nupkg"
    assert runtime["package_url"] == (
        "https://api.nuget.org/v3-flatcontainer/python/3.11.9/"
        "python.3.11.9.nupkg"
    )
    assert runtime["package_size"] == 17_478_009
    assert runtime["sha256"] == (
        "9283876d58c017e0e846f95b490da3bca0fc0a6ee1134b2870677cfb7eec3c67"
    )
    assert runtime["license_path"] == (
        "third_party/licenses/CPython-3.11.9-Windows-LICENSE.txt"
    )
    assert runtime["license_source_path"] == "tools/LICENSE.txt"
    assert runtime["license_sha256"] == CPYTHON_WINDOWS_LICENSE_SHA256


def test_readme_uses_supported_locked_windows_build_instructions():
    readme = _read("README.md")

    assert "零运行时依赖" not in readme
    assert "packaging/windows-release-requirements.txt" in readme
    assert "--require-hashes --only-binary=:all:" in readme
    assert "--hidden-import pystray._win32" in readme
    assert '--icon "$PWD/packaging/clipvault.ico"' in readme
    assert "--self-test-tray" in readme
    assert "ClipVault-v1.6.0-LGPL-relink-kit.zip" in readme
    assert "pip install pyinstaller" not in readme.lower()


def test_pillow_feature_probe_has_exact_release_scope():
    probe = _read("desktop/packaging/pillow_feature_probe.py")

    assert 'for feature_name in ("libimagequant", "raqm"):' in probe
    assert 'print(f"{feature_name}={features.check_feature(feature_name)}")' in probe
    assert probe.count("print(") == 1


def test_relink_guide_proves_recipient_marker_in_frozen_executable():
    guide = _read("third_party/RELINKING_V1_6_0.md")
    main = _read("desktop/clipvault/main.py")
    launcher = _read("desktop/clipvault/launcher.py")

    assert "--self-test-tray-relink-marker" in guide
    assert "Recipient pystray marker did not enter the frozen executable" in guide
    assert "--self-test-tray-relink-marker" in main
    assert "CLIPVAULT_RELINK_EXERCISE_MARKER" in launcher
    assert "TrayRelinkMarkerError" in launcher


def test_windows_installer_rejects_x86_and_keeps_startup_opt_in():
    installer = _read("installer/clipvault.iss")
    install_guide = _read("docs/INSTALL.md")
    contracts = _read("docs/CONTRACTS.md")
    release_workflow = _read(".github/workflows/release.yml")

    assert installer.count("MinVersion=10.0") == 1
    assert installer.count("ArchitecturesAllowed=x64compatible") == 1
    assert installer.count("ArchitecturesInstallIn64BitMode=x64compatible") == 1
    startup_tasks = [
        line for line in installer.splitlines() if line.startswith('Name: "startup"')
    ]
    assert len(startup_tasks) == 1
    assert "Flags: unchecked" in startup_tasks[0]
    assert (
        'Name: "{userstartup}\\ClipVault Personal"; '
        'Filename: "{app}\\{#AppExe}"; Parameters: "--no-open"; Tasks: startup'
        in installer
    )
    postinstall_runs = [
        line for line in installer.splitlines() if "postinstall" in line
    ]
    assert len(postinstall_runs) == 1
    assert "unchecked" in postinstall_runs[0].split("Flags:", 1)[1].split()
    assert "开始监听剪贴板" in postinstall_runs[0]
    assert "安装器和便携包要求系统可运行 x64 应用" in install_guide
    assert "全新安装不会默认启用登录自启动" in install_guide
    assert "升级安装会保留用户此前的登录自启动选择" in install_guide
    assert '"Pillow==12.3.0" "pystray==0.19.5"' in install_guide
    assert "生成可用默认配置" in contracts
    assert "生成默认并提示填 vault_path" not in contracts
    assert "capable of running x64 applications" in release_workflow
    assert "login autostart and the finish-page launch option unselected" in (
        release_workflow
    )


def test_relink_guide_uses_clean_windows_compatible_extraction():
    guide = _read("third_party/RELINKING_V1_6_0.md")

    assert "Compare-Object -CaseSensitive" in guide
    assert "Unlocked or missing file in $($inventory.directory) inventory" in guide
    assert guide.index("Compare-Object -CaseSensitive") < guide.index("$otherWheels")
    assert "if (Test-Path -LiteralPath $work)" in guide
    assert "relink-work already exists; use a fresh kit extraction" in guide
    assert "Add-Type -AssemblyName System.IO.Compression.FileSystem" in guide
    assert guide.count("[IO.Compression.ZipFile]::ExtractToDirectory(") == 2
    assert "Expand-Archive -LiteralPath $appArchives[0].FullName" not in guide
    assert "Expand-Archive -LiteralPath $pystrayArchives[0].FullName" not in guide


def test_relink_guide_verifies_the_compiler_engine_not_pe_metadata():
    guide = _read("third_party/RELINKING_V1_6_0.md")

    assert "Compiler engine version: Inno Setup 6" in guide
    assert "$isccExitCode -ne 0" in guide
    assert "Inno Setup 6 compiler engine evidence is missing or ambiguous" in guide
    assert ".VersionInfo.ProductVersion" not in guide


def test_relink_guide_handles_powershell_51_native_stderr():
    guide = _read("third_party/RELINKING_V1_6_0.md")

    assert "Windows PowerShell 5.1 promotes native stderr" in guide
    assert "$pyinstallerReport = @(" in guide
    assert "packaging/run_clipvault.py 2>&1 |" in guide
    assert "$pyinstallerExitCode = $LASTEXITCODE" in guide
    assert "$pyinstallerExitCode -ne 0" in guide
    assert guide.count('$ErrorActionPreference = "Continue"') == 2
