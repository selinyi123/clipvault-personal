"""Static release gates for the supported Windows tray build."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
PRODUCTION_LOCK = DESKTOP / "packaging" / "windows-release-requirements.txt"


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


def test_windows_workflows_build_from_locked_wheels_and_gate_frozen_tray():
    for relative in (
        ".github/workflows/release.yml",
        ".github/workflows/release-candidate.yml",
    ):
        workflow = _read(relative)
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
        assert 'foreach ($requiredModule in @("pystray._win32", "PIL.Image"))' in workflow
        assert '$requiredToken = "\'" + $requiredModule + "\'"' in workflow
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
        installer = workflow.index("Build installer")
        kit = workflow.index("Build-LgplRelinkKit.ps1")
        manifest = workflow.index("scripts/release_candidate_manifest.py", kit)
        assert (
            build
            < tray_gate
            < pillow_probe
            < pillow_validation
            < pillow_persist
            < installer
            < kit
            < manifest
        )


def test_relink_kit_is_fail_closed_and_installer_carries_notices():
    kit = _read("desktop/packaging/Build-LgplRelinkKit.ps1")
    exporter = _read("desktop/packaging/Export-WheelNotices.ps1")
    installer = _read("installer/clipvault.iss")

    assert 'expectedOutputName = "ClipVault-v$Version-LGPL-relink-kit.zip"' in kit
    assert 'if ($Version -cne "1.6.0")' in kit
    assert "This relink kit contract supports only v1.6.0" in kit
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
    assert "Relink kit ZIP inventory does not match the staged payload" in kit
    assert "Extracted relink kit inventory does not match the staged payload" in kit
    assert "Expand-Archive -LiteralPath $outputPath" in kit

    assert 'DestDir: "{app}\\licenses"' in installer
    assert "THIRD_PARTY_NOTICES.md" in installer
    assert "RELINKING_V1_6_0.md" in installer

    release = _read(".github/workflows/release.yml")
    assert "pystray 0.19.5" in release
    assert "LGPL-3.0-or-later" in release
    assert "`ClipVault-${RELEASE_TAG}-LGPL-relink-kit.zip`" in release


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
