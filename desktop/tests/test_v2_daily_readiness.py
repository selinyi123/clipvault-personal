from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "v2_daily_readiness.py"
SPEC = importlib.util.spec_from_file_location("v2_daily_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
v2_daily_readiness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = v2_daily_readiness
SPEC.loader.exec_module(v2_daily_readiness)


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate_tool = _load(
    "v2_daily_readiness_candidate_tool", "tools/v2_daily_candidate.py"
)
manifest_writer = _load(
    "v2_daily_readiness_manifest_writer", "scripts/release_candidate_manifest.py"
)
TEST_COMMIT = "a" * 40
TEST_VERSION = "2.2.0-dev"


FOUNDATION_FILES = (
    "THIRD_PARTY_MANIFEST.yaml",
    "contracts/v2_candidate_version.json",
    "contracts/otp_relay_wire_v1.schema.json",
    "contracts/v2_daily_owner_evidence.schema.json",
    "contracts/v2_windows_authenticode_evidence.schema.json",
    "contracts/vectors/engine_protocol_v2_assertions.tsv",
    "contracts/vectors/input_foundation_v2.json",
    "contracts/vectors/otp_aead_v1.json",
    "contracts/vectors/runtime_snapshot_v1.json",
    "docs/CONTRACTS_INPUT_ENGINE_V2.md",
    "docs/CONTRACTS_OTP_RELAY.md",
    "docs/CONTRACTS_RUNTIME_SNAPSHOT_V1.md",
    "docs/THREAT_MODEL_OTP_RELAY.md",
    "docs/V2_DAILY_USE_ACCEPTANCE.md",
    "docs/V2_DAILY_CANDIDATE_WORKFLOW.md",
    "docs/V2_DAILY_OWNER_HANDOFF.md",
    "docs/V2_LICENSE_RELEASE_GATE.md",
    "docs/V2_DAILY_USE_MANUAL_QA.md",
    "scripts/release_candidate_manifest.py",
    "scripts/verify_release_manifest.py",
    "tools/v2_daily_candidate.py",
    "tools/Collect-V2WindowsAuthenticodeEvidence.ps1",
    ".github/workflows/v2-daily-candidate.yml",
)


def test_report_separates_automated_and_owner_release_evidence():
    report = v2_daily_readiness.build_report(ROOT)
    assert report["status"] == "blocked"
    assert report["owner_status"] == "blocked"
    assert {gate["lane"] for gate in report["gates"]} == {
        "foundation",
        "android",
        "windows",
        "otp",
        "candidate",
        "owner",
    }
    assert report["candidate_status"] == "blocked"
    assert report["automated_status"] == "blocked"
    assert any(
        gate["name"] == "signed artifacts and manual daily-use evidence"
        for gate in report["gates"]
    )
    third_party = next(
        gate
        for gate in report["gates"]
        if gate["name"] == "third-party lock and notice consistency"
    )
    assert third_party["status"] == "pass"
    assert "project-license state agree" in third_party["detail"]
    assert "android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json" in third_party["evidence"]

    windows = next(
        gate
        for gate in report["gates"]
        if gate["name"] == "native TSF and external Host"
    )
    assert windows["status"] == "pass"
    assert "windows/ime/tsf/candidate_layout.cpp" in windows["evidence"]
    assert "windows/ime/tests/candidate_layout_tests.cpp" in windows["evidence"]


def test_windows_otp_broker_gate_tracks_production_layout():
    gate = v2_daily_readiness.check_otp_windows_broker(ROOT)

    assert gate.status == "pass"
    assert "windows/otp-relay/crypto/otp_aead_cng.cpp" in gate.evidence
    assert "windows/otp-relay/broker/broker_server.cpp" in gate.evidence
    assert "windows/ime/tsf/text_service.cpp" in gate.evidence
    assert "windows/otp-relay/tests/otp_aead_vectors.cpp" in gate.evidence


def test_owner_evidence_requires_real_artifacts_current_head_and_approved_license(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "LICENSE").write_text("Approved test license\n", encoding="utf-8")
    (root / "THIRD_PARTY_MANIFEST.yaml").write_text(
        json.dumps(
            {
                "project_license": {
                    "status": "approved",
                    "license_file": "LICENSE",
                    "distribution_allowed": True,
                }
            }
        ),
        encoding="utf-8",
    )
    artifacts = root / "artifacts" / "v2-daily"
    artifacts.mkdir(parents=True)
    artifact_paths = {
        "source_manifest": artifacts / "ci-bundle" / "RELEASE_MANIFEST.json",
        "build_receipt": artifacts / "ci-bundle" / "BUILD_RECEIPT.json",
        "android_ime": artifacts / "clipvault-ime.apk",
        "android_ime_report": artifacts / "clipvault-ime-apksigner.txt",
        "android_runtime": artifacts / "clipvault-runtime.apk",
        "android_runtime_report": artifacts / "clipvault-runtime-apksigner.txt",
        "desktop_executable": artifacts / "clipvault.exe",
        "windows_package": artifacts / "clipvault-windows.zip",
        "windows_installer": artifacts / "clipvault-setup.exe",
        "windows_authenticode_report": artifacts / "windows-authenticode.json",
    }
    artifact_paths["source_manifest"].parent.mkdir()
    for name in (
        "source_manifest",
        "build_receipt",
        "android_ime",
        "android_runtime",
        "desktop_executable",
        "windows_installer",
    ):
        artifact = artifact_paths[name]
        artifact.write_bytes(f"real-{name}-artifact".encode())
    cert = "4" * 64
    signer_report = (
        "Verifies\n"
        "Verified using v2 scheme (APK Signature Scheme v2): true\n"
        f"Signer #1 certificate SHA-256 digest: {cert}\n"
    )
    artifact_paths["android_ime_report"].write_text(signer_report, encoding="utf-8")
    artifact_paths["android_runtime_report"].write_text(signer_report, encoding="utf-8")
    member_bytes = {
        name: f"signed:{name}".encode()
        for name in v2_daily_readiness.WINDOWS_SIGNED_PACKAGE_MEMBERS
    }
    with zipfile.ZipFile(artifact_paths["windows_package"], "w") as archive:
        for name, body in member_bytes.items():
            archive.writestr(name, body)
    evidence_location = artifacts / "manual-qa"
    evidence_location.mkdir()
    commit = "a" * 40
    monkeypatch.setattr(v2_daily_readiness, "_git_head", lambda _: commit)
    monkeypatch.setattr(v2_daily_readiness, "_git_worktree_clean", lambda _: True)
    monkeypatch.setattr(
        v2_daily_readiness,
        "_verify_candidate_bundle",
        lambda *args: {
            "receipt": {
                "workflow": {
                    "url": "https://github.com/owner/clipvault/actions/runs/12345"
                }
            }
        },
    )

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    thumbprint = "5" * 40
    windows_report = {
        "schema_version": 1,
        "signing_thumbprint": thumbprint,
        "top_level": [
            {
                "role": role,
                "path": str(artifact_paths[path_name]),
                "sha256": digest(artifact_paths[path_name]),
                "status": "Valid",
                "signing_thumbprint": thumbprint,
            }
            for role, path_name in (
                ("desktop_executable", "desktop_executable"),
                ("windows_installer", "windows_installer"),
            )
        ],
        "package": {
            "path": str(artifact_paths["windows_package"]),
            "sha256": digest(artifact_paths["windows_package"]),
            "members": [
                {
                    "archive_path": name,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "status": "Valid",
                    "signing_thumbprint": thumbprint,
                }
                for name, body in sorted(member_bytes.items())
            ],
        },
    }
    artifact_paths["windows_authenticode_report"].write_text(
        json.dumps(windows_report), encoding="utf-8"
    )
    evidence = artifacts / "owner.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "candidate_id": "v2-daily-test",
                "candidate_commit": commit,
                "source_candidate_dir": str(artifact_paths["source_manifest"].parent),
                "source_manifest_path": str(artifact_paths["source_manifest"]),
                "source_manifest_sha256": digest(artifact_paths["source_manifest"]),
                "build_receipt_path": str(artifact_paths["build_receipt"]),
                "build_receipt_sha256": digest(artifact_paths["build_receipt"]),
                "workflow_run_url": "https://github.com/owner/clipvault/actions/runs/12345",
                "android_ime_path": str(artifact_paths["android_ime"]),
                "android_ime_sha256": digest(artifact_paths["android_ime"]),
                "android_ime_apksigner_report_path": str(artifact_paths["android_ime_report"]),
                "android_ime_apksigner_report_sha256": digest(artifact_paths["android_ime_report"]),
                "android_runtime_path": str(artifact_paths["android_runtime"]),
                "android_runtime_sha256": digest(artifact_paths["android_runtime"]),
                "android_runtime_apksigner_report_path": str(artifact_paths["android_runtime_report"]),
                "android_runtime_apksigner_report_sha256": digest(artifact_paths["android_runtime_report"]),
                "android_signing_cert_sha256": cert,
                "desktop_executable_path": str(artifact_paths["desktop_executable"]),
                "desktop_executable_sha256": digest(artifact_paths["desktop_executable"]),
                "windows_package_path": str(artifact_paths["windows_package"]),
                "windows_package_sha256": digest(artifact_paths["windows_package"]),
                "windows_installer_path": str(artifact_paths["windows_installer"]),
                "windows_installer_sha256": digest(artifact_paths["windows_installer"]),
                "windows_authenticode_report_path": str(artifact_paths["windows_authenticode_report"]),
                "windows_authenticode_report_sha256": digest(artifact_paths["windows_authenticode_report"]),
                "windows_signing_thumbprint": thumbprint,
                "android_manual_pass": True,
                "windows_manual_pass": True,
                "otp_manual_pass": True,
                "seven_day_daily_use_pass": True,
                "license_and_notices_approved": True,
                "owner_approved": True,
                "owner_name": "test-owner",
                "decision_at_utc": "2026-08-01T00:00:00Z",
                "evidence_location": str(evidence_location),
            }
        ),
        encoding="utf-8",
    )
    gate = v2_daily_readiness.check_owner_evidence(root, evidence)
    assert gate.status == "pass"

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    artifact_paths["android_ime_report"].write_text(
        signer_report.replace(cert, "6" * 64), encoding="utf-8"
    )
    payload["android_ime_apksigner_report_sha256"] = digest(
        artifact_paths["android_ime_report"]
    )
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    gate = v2_daily_readiness.check_owner_evidence(root, evidence)
    assert gate.status == "blocked"
    assert any(item.startswith("android_ime_signature_report:") for item in gate.evidence)

    artifact_paths["android_ime_report"].write_text(signer_report, encoding="utf-8")
    payload["android_ime_apksigner_report_sha256"] = digest(
        artifact_paths["android_ime_report"]
    )
    tampered_windows_report = json.loads(json.dumps(windows_report))
    tampered_windows_report["top_level"][0]["status"] = "UnknownError"
    artifact_paths["windows_authenticode_report"].write_text(
        json.dumps(tampered_windows_report), encoding="utf-8"
    )
    payload["windows_authenticode_report_sha256"] = digest(
        artifact_paths["windows_authenticode_report"]
    )
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    gate = v2_daily_readiness.check_owner_evidence(root, evidence)
    assert gate.status == "blocked"
    assert any(item.startswith("windows_authenticode_report:") for item in gate.evidence)

    artifact_paths["windows_authenticode_report"].write_text(
        json.dumps(windows_report), encoding="utf-8"
    )
    payload["windows_authenticode_report_sha256"] = digest(
        artifact_paths["windows_authenticode_report"]
    )
    payload["android_ime_sha256"] = "1" * 64
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    gate = v2_daily_readiness.check_owner_evidence(root, evidence)
    assert gate.status == "blocked"
    assert "android_ime_sha256_mismatch" in gate.evidence

    payload["android_ime_sha256"] = digest(artifact_paths["android_ime"])
    payload["candidate_commit"] = "b" * 40
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    gate = v2_daily_readiness.check_owner_evidence(root, evidence)
    assert gate.status == "blocked"
    assert "candidate_commit_current_head" in gate.evidence

    payload["candidate_commit"] = commit
    artifact_paths["windows_package"].unlink()
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    gate = v2_daily_readiness.check_owner_evidence(root, evidence)
    assert gate.status == "blocked"
    assert "windows_package_path" in gate.evidence

    artifact_paths["windows_package"].write_bytes(b"restored-windows-package")
    payload["windows_package_sha256"] = digest(artifact_paths["windows_package"])
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(v2_daily_readiness, "_git_worktree_clean", lambda _: False)
    gate = v2_daily_readiness.check_owner_evidence(root, evidence)
    assert gate.status == "blocked"
    assert "candidate_source_tree_clean" in gate.evidence


def test_owner_evidence_schema_matches_runtime_contract():
    schema = json.loads(
        (ROOT / "contracts/v2_daily_owner_evidence.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema["properties"]["schema_version"]["const"] == 3
    assert set(schema["required"]) == v2_daily_readiness.OWNER_EVIDENCE_FIELDS


def test_owner_evidence_accepts_internal_only_decision_but_requires_real_evidence(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "owner.json"
    evidence.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(v2_daily_readiness, "_git_head", lambda _: "a" * 40)
    monkeypatch.setattr(v2_daily_readiness, "_git_worktree_clean", lambda _: True)

    gate = v2_daily_readiness.check_owner_evidence(ROOT, evidence)

    assert gate.status == "blocked"
    assert "project_license_decided" not in gate.evidence
    assert "owner_approved" in gate.evidence


def test_no_fail_cli_reports_without_claiming_release_ready(capsys):
    exit_code = v2_daily_readiness.main(
        ["--root", str(ROOT), "--json", "--no-fail"]
    )
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["status"] == "blocked"
    assert "real-device behavior" in report["scope_note"]


def test_automated_readiness_requires_receipt_bound_candidate(tmp_path, monkeypatch):
    names = {
        "CANDIDATE-NOT-A-RELEASE.txt",
        "ClipVault-Desktop-v2-unsigned.exe",
        "ClipVault-IME-v2-unsigned.apk",
        "ClipVault-Runtime-v2-unsigned.apk",
        f"ClipVault-v2-Daily-Setup-v{TEST_VERSION}.exe",
        "ClipVault-Windows-IME-v2-unsigned.zip",
    }
    for name in names:
        (tmp_path / name).write_bytes(f"readiness:{name}".encode())

    monkeypatch.setattr(candidate_tool, "_git_head", lambda _: TEST_COMMIT)
    monkeypatch.setattr(candidate_tool, "_git_tracked_clean", lambda _: True)
    candidate_tool.write_receipt(
        ROOT,
        tmp_path,
        source_commit=TEST_COMMIT,
        source_ref="refs/heads/codex/v2-daily-integration",
        repository="owner/clipvault",
        run_id="12345",
        run_attempt=1,
    )
    manifest_writer.build_manifest(
        tmp_path,
        platform="v2-daily",
        version=TEST_VERSION,
        commit=TEST_COMMIT,
    )
    monkeypatch.setattr(v2_daily_readiness, "_git_head", lambda _: TEST_COMMIT)

    gate = v2_daily_readiness.check_candidate_evidence(ROOT, tmp_path)

    assert gate.status == "pass"
    assert "actions/runs/12345" in gate.evidence[-1]


def test_source_only_and_automated_only_are_distinct_cli_modes(capsys, monkeypatch):
    report = {
        "status": "blocked",
        "source_status": "ready",
        "candidate_status": "blocked",
        "automated_status": "blocked",
        "owner_status": "blocked",
        "blocked": 2,
        "gates": [],
        "scope_note": "test",
    }
    monkeypatch.setattr(v2_daily_readiness, "build_report", lambda *args: report)

    assert v2_daily_readiness.main(["--source-only"]) == 0
    capsys.readouterr()
    assert v2_daily_readiness.main(["--automated-only"]) == 2


def test_foundation_manifest_cannot_silently_authorize_distribution_without_license(tmp_path):
    for relative in FOUNDATION_FILES:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    manifest_path = tmp_path / "THIRD_PARTY_MANIFEST.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_license"]["distribution_allowed"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    gate = v2_daily_readiness.check_foundation(tmp_path)
    assert gate.status == "blocked"
    assert gate.evidence == ("THIRD_PARTY_MANIFEST.yaml",)
