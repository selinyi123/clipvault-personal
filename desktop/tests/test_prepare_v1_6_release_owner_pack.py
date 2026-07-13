"""Tests for the fixed-scope Issue #36 Owner pack generator."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "prepare_v1_6_release_owner_pack.py"
SPEC = importlib.util.spec_from_file_location("prepare_v1_6_release_owner_pack", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
owner_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owner_pack)


def _read_bytes(output_dir: Path) -> dict[str, bytes]:
    return {
        name: (output_dir / name).read_bytes()
        for name in owner_pack.EXPECTED_OUTPUT_FILES
    }


def test_manual_template_reuses_canonical_schema_v2_contract():
    generated = owner_pack.manual_qa_template(owner_pack.VERSION)
    canonical = owner_pack.manual_qa_evidence.build_template(owner_pack.VERSION)

    assert generated == canonical
    assert generated["schema_version"] == 2
    assert len(generated["android_runs"]) == 3
    assert [run["sdk_int"] for run in generated["android_runs"][:2]] == [26, 27]
    assert generated["android_runs"][2]["apk_name"] == (
        "ClipVault-Android-v1.6.0-release-signed.apk"
    )
    expected_items = 1 + sum(
        len(section.items) for section in owner_pack.manual_qa_evidence.REQUIRED_SECTIONS
    )
    assert expected_items == 18


def test_artifact_worksheet_is_scoped_and_never_claims_validation():
    worksheet = owner_pack.artifact_template(owner_pack.VERSION)

    assert worksheet["coordination_only"] is True
    assert worksheet["validator_input"] is False
    assert worksheet["android"]["expected_assets"][0] == (
        "ClipVault-Android-v1.6.0-release-signed.apk"
    )
    assert "BLOCKED_UNTIL_ANDROID_RELEASE_CERT_SHA256" in (
        worksheet["android"]["owner_certificate_identity"]
    )
    assert "does not prove" in worksheet["notes"]


def test_generated_guide_binds_same_draft_bytes_and_fail_closed_manual_qa():
    files = owner_pack.build_pack_files(owner_pack.VERSION, owner_pack.ISSUE_URL)
    guide = files["OWNER_RELEASE_ACTION_PACK.md"]
    draft = files["issue-36-comment-draft.md"]

    assert "create_draft_release=false" in guide
    assert "create_draft_release=true" in guide
    assert "same draft=true run" in guide
    assert "ANDROID_RELEASE_CERT_SHA256" in guide
    assert "environment secrets" in guide
    assert "API 26 and API 27" in guide
    assert "app-debug.apk" in guide
    assert "app-debug-androidTest.apk" in guide
    assert "ClipVault-Android-v1.6.0-release-signed.apk" in guide
    assert "python tools/release_artifact_evidence.py" in guide
    assert "--expected-android-cert-sha256 $env:ANDROID_RELEASE_CERT_SHA256" in guide
    assert "-cnotmatch '^[0-9a-f]{64}$'" in guide
    assert "python tools/manual_qa_evidence.py" in guide
    assert "PASS (OWNER-ATTESTED)" in guide
    assert "Do not manually flip" in guide
    assert "v1.6.0-draft-run-$runId" in guide
    assert "Refusing stale evidence directory" in guide
    assert "Assert-NativeSuccess" in guide
    assert "Release artifacts v1.6.0 from main draft=true" in guide
    assert "gh release download v1.6.0" in guide
    assert "Assert-SameSha" in guide
    assert "draft-release-SHA256SUMS.txt" in guide
    assert "Windows observations are Owner-attested" in guide
    assert "Refusing stale prepublish directory" in guide
    assert "Draft assets changed after QA" in guide
    assert '$release.targetCommitish -ne $targetCommit' in guide
    assert "gh release edit v1.6.0" in guide
    assert "v1.6.0-postpublish-$runId" in guide
    assert "$published.isDraft" in guide
    assert "Published Release metadata mismatch" in guide
    assert "Published assets differ from the approved digest set" in guide
    assert "Closure recommendation: `BLOCKED`" in draft
    assert guide.isascii()
    assert draft.isascii()


def test_new_pack_contains_only_the_fixed_known_file_set(tmp_path):
    output_dir = tmp_path / "pack"

    assert owner_pack.main(["--out-dir", str(output_dir)]) == 0

    assert sorted(path.name for path in output_dir.iterdir()) == sorted(
        owner_pack.EXPECTED_OUTPUT_FILES
    )
    summary = json.loads((output_dir / "pack-summary.json").read_text(encoding="utf-8"))
    manual = json.loads(
        (output_dir / "manual-qa-v1.6.0.template.json").read_text(encoding="utf-8")
    )
    assert summary["schema"] == "clipvault.issue36.owner_pack.summary.v2"
    assert summary["generated_files"] == sorted(owner_pack.EXPECTED_OUTPUT_FILES)
    assert manual == owner_pack.manual_qa_evidence.build_template(owner_pack.VERSION)
    assert not list(tmp_path.glob(".pack.staging-*"))


def test_non_empty_output_is_refused_without_partial_writes(tmp_path):
    output_dir = tmp_path / "pack"
    output_dir.mkdir()
    sentinel = output_dir / "owner-notes.txt"
    sentinel.write_bytes(b"preserve me")

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir)])

    assert sentinel.read_bytes() == b"preserve me"
    assert not any((output_dir / name).exists() for name in owner_pack.EXPECTED_OUTPUT_FILES)


def test_existing_filled_pack_is_not_overwritten_without_force(tmp_path):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    manual_path = output_dir / "manual-qa-v1.6.0.template.json"
    manual_path.write_bytes(b'\n{"owner_filled": true}\n')
    before = _read_bytes(output_dir)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir)])

    assert _read_bytes(output_dir) == before


def test_force_replaces_only_known_regular_files_and_preserves_unknown_files(tmp_path):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    sentinel = output_dir / "owner-notes.txt"
    sentinel.write_bytes(b"do not replace")
    known = output_dir / "OWNER_RELEASE_ACTION_PACK.md"
    known.write_bytes(b"stale")

    assert owner_pack.main(["--out-dir", str(output_dir), "--force"]) == 0

    assert sentinel.read_bytes() == b"do not replace"
    assert known.read_text(encoding="utf-8").startswith("# Owner Release Action Pack")
    assert sorted(path.name for path in output_dir.iterdir()) == sorted(
        (*owner_pack.EXPECTED_OUTPUT_FILES, "owner-notes.txt")
    )


def test_force_rejects_known_directory_before_replacing_other_files(tmp_path):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    first = output_dir / "OWNER_RELEASE_ACTION_PACK.md"
    before = first.read_bytes()
    conflict = output_dir / "agent-cluster.md"
    conflict.unlink()
    conflict.mkdir()

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    assert first.read_bytes() == before
    assert conflict.is_dir()


def test_force_rejects_hard_linked_known_file(tmp_path):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    target = tmp_path / "owner-copy.md"
    known = output_dir / "OWNER_RELEASE_ACTION_PACK.md"
    target.write_bytes(b"shared owner data")
    known.unlink()
    os.link(target, known)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    assert target.read_bytes() == b"shared owner data"
    assert known.read_bytes() == b"shared owner data"


@pytest.mark.parametrize(
    "failed_name",
    [
        owner_pack.EXPECTED_OUTPUT_FILES[0],
        owner_pack.EXPECTED_OUTPUT_FILES[len(owner_pack.EXPECTED_OUTPUT_FILES) // 2],
        owner_pack.EXPECTED_OUTPUT_FILES[-1],
    ],
)
def test_force_rolls_back_every_known_file_when_installation_fails(
    tmp_path,
    monkeypatch,
    failed_name,
):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    sentinel = output_dir / "owner-notes.txt"
    sentinel.write_bytes(b"preserve unknown")
    before = _read_bytes(output_dir)
    real_replace = owner_pack.os.replace

    def fail_one_stage_install(source, target):
        source_path = Path(source)
        target_path = Path(target)
        if (
            source_path.parent.name.startswith(".pack.staging-")
            and target_path.parent == output_dir
            and source_path.name == failed_name
        ):
            raise PermissionError("simulated locked Owner pack target")
        return real_replace(source, target)

    monkeypatch.setattr(owner_pack.os, "replace", fail_one_stage_install)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    assert _read_bytes(output_dir) == before
    assert sentinel.read_bytes() == b"preserve unknown"
    assert not list(tmp_path.glob(".pack.staging-*"))
    assert not list(tmp_path.glob(".pack.backup-*"))


def test_force_preserves_backup_when_rollback_cannot_restore_owner_file(
    tmp_path,
    monkeypatch,
    capsys,
):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    sentinel = output_dir / "owner-notes.txt"
    sentinel.write_bytes(b"preserve unknown")
    before = _read_bytes(output_dir)
    install_failure = owner_pack.EXPECTED_OUTPUT_FILES[2]
    restore_failure = owner_pack.EXPECTED_OUTPUT_FILES[0]
    real_replace = owner_pack.os.replace

    def fail_install_and_one_restore(source, target):
        source_path = Path(source)
        target_path = Path(target)
        if (
            source_path.parent.name.startswith(".pack.staging-")
            and target_path.parent == output_dir
            and source_path.name == install_failure
        ):
            raise PermissionError("simulated stage installation failure")
        if (
            source_path.parent.name.startswith(".pack.backup-")
            and target_path.parent == output_dir
            and source_path.name == restore_failure
        ):
            raise PermissionError("simulated rollback restore failure")
        return real_replace(source, target)

    monkeypatch.setattr(owner_pack.os, "replace", fail_install_and_one_restore)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    backups = list(tmp_path.glob(".pack.backup-*"))
    assert len(backups) == 1
    backup = backups[0]
    assert str(backup.resolve()) in capsys.readouterr().err
    assert (backup / restore_failure).read_bytes() == before[restore_failure]
    assert not (output_dir / restore_failure).exists()
    for name in owner_pack.EXPECTED_OUTPUT_FILES:
        if name != restore_failure:
            assert (output_dir / name).read_bytes() == before[name]
    assert sentinel.read_bytes() == b"preserve unknown"
    assert not list(tmp_path.glob(".pack.staging-*"))


def test_symlink_output_directory_is_rejected(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    output_dir = tmp_path / "pack-link"
    try:
        output_dir.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    assert list(target.iterdir()) == []


def test_symlinked_parent_and_known_file_are_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    parent_link = tmp_path / "linked-parent"
    try:
        parent_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(parent_link / "pack")])
    assert list(outside.iterdir()) == []

    output_dir = tmp_path / "regular-pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    external_file = tmp_path / "owner-data.md"
    external_file.write_bytes(b"preserve external bytes")
    known = output_dir / "OWNER_RELEASE_ACTION_PACK.md"
    known.unlink()
    known.symlink_to(external_file)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])
    assert external_file.read_bytes() == b"preserve external bytes"


def test_windows_junction_output_directory_is_rejected(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction behavior is Windows-only")
    target = tmp_path / "outside"
    target.mkdir()
    output_dir = tmp_path / "pack-junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(output_dir), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {completed.stderr}")

    try:
        with pytest.raises(SystemExit, match="2"):
            owner_pack.main(["--out-dir", str(output_dir), "--force"])
        assert list(target.iterdir()) == []
    finally:
        if output_dir.exists():
            output_dir.rmdir()


def test_staging_write_failure_leaves_no_output_or_partial_pack(tmp_path, monkeypatch):
    output_dir = tmp_path / "pack"
    real_write_text = owner_pack.Path.write_text
    writes = 0

    def fail_second_stage_write(path, *args, **kwargs):
        nonlocal writes
        if path.parent.name.startswith(".pack.staging-"):
            writes += 1
            if writes == 2:
                raise OSError("simulated staging disk failure")
        return real_write_text(path, *args, **kwargs)

    monkeypatch.setattr(owner_pack.Path, "write_text", fail_second_stage_write)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir)])

    assert not output_dir.exists()
    assert not list(tmp_path.glob(".pack.staging-*"))


@pytest.mark.parametrize(
    "argv",
    [
        ["--version", "v1.7.0"],
        ["--issue-url", "https://github.com/selinyi123/clipvault-personal/issues/82"],
    ],
)
def test_cli_rejects_scope_drift(argv):
    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(argv)


def test_default_output_stays_in_the_existing_ignored_workspace():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert owner_pack.DEFAULT_OUTPUT_DIR == Path(
        ".field-test-artifacts/v1.6.0-owner-pack"
    )
    assert ".field-test-artifacts/" in gitignore
