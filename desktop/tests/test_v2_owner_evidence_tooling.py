from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_windows_authenticode_collector_is_read_only_about_signing_identity():
    source = (
        ROOT / "tools" / "Collect-V2WindowsAuthenticodeEvidence.ps1"
    ).read_text(encoding="utf-8")

    assert "Get-AuthenticodeSignature" in source
    assert "Get-FileHash" in source
    assert "ClipVaultImeHost.exe" in source
    assert "ClipVaultOtpBroker.exe" in source
    assert source.count("ClipVaultTextService.dll") == 2
    assert "SignatureStatus]::Valid" in source
    assert "Set-AuthenticodeSignature" not in source
    assert "signtool" not in source.casefold()


def test_windows_authenticode_schema_matches_runtime_member_contract():
    schema = json.loads(
        (
            ROOT / "contracts" / "v2_windows_authenticode_evidence.schema.json"
        ).read_text(encoding="utf-8")
    )
    members = set(
        schema["$defs"]["memberEvidence"]["properties"]["archive_path"]["enum"]
    )

    assert schema["properties"]["schema_version"]["const"] == 1
    assert members == {
        "host-x64/ClipVaultImeHost.exe",
        "otp-broker/ClipVaultOtpBroker.exe",
        "x64/ClipVaultTextService.dll",
        "x86/ClipVaultTextService.dll",
    }


def test_owner_handoff_requires_candidate_and_signature_reports():
    handoff = (ROOT / "docs" / "V2_DAILY_OWNER_HANDOFF.md").read_text(
        encoding="utf-8"
    )

    assert "v2_daily_candidate.py" in handoff
    assert "--candidate-dir" in handoff
    assert "apksigner.jar" in handoff
    assert "Collect-V2WindowsAuthenticodeEvidence.ps1" in handoff
    assert "schema is v3" in handoff
    assert "does not publish" in handoff
    assert "CLIPVAULT_NATIVE_RUNNER_READY" in handoff
    assert "clipvault-android-device" in handoff
    assert "adb devices -l" in handoff
    assert "gh workflow run ci.yml --ref codex/v2-daily-integration" in handoff
