import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "download_field_test_artifacts.py"
_spec = importlib.util.spec_from_file_location("download_field_test_artifacts", _SCRIPT)
download_field_test_artifacts = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = download_field_test_artifacts
_spec.loader.exec_module(download_field_test_artifacts)


def _artifact(name, *, artifact_id=1, expired=False, digest=None):
    return download_field_test_artifacts.Artifact(
        name=name,
        artifact_id=artifact_id,
        archive_download_url=f"https://api.github.com/repos/o/r/actions/artifacts/{artifact_id}/zip",
        digest=digest,
        expired=expired,
        size_in_bytes=123,
        expires_at="2026-10-02T00:00:00Z",
    )


def _write_zip(path, rows):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in rows.items():
            archive.writestr(name, data)


def test_select_required_artifacts_rejects_missing_and_expired():
    with pytest.raises(ValueError, match="missing required"):
        download_field_test_artifacts.select_required_artifacts([
            _artifact(download_field_test_artifacts.WINDOWS_ARTIFACT_NAME),
        ])

    with pytest.raises(ValueError, match="expired"):
        download_field_test_artifacts.select_required_artifacts([
            _artifact(download_field_test_artifacts.WINDOWS_ARTIFACT_NAME, expired=True),
            _artifact(download_field_test_artifacts.ANDROID_ARTIFACT_NAME),
        ])


def test_select_required_artifacts_rejects_duplicate_required_names():
    with pytest.raises(ValueError, match="duplicate required"):
        download_field_test_artifacts.select_required_artifacts([
            _artifact(download_field_test_artifacts.WINDOWS_ARTIFACT_NAME, artifact_id=1),
            _artifact(download_field_test_artifacts.WINDOWS_ARTIFACT_NAME, artifact_id=2),
            _artifact(download_field_test_artifacts.ANDROID_ARTIFACT_NAME),
        ])


def test_expected_digest_accepts_github_sha256_prefix():
    digest = "a" * 64

    assert download_field_test_artifacts._expected_digest_hex(f"sha256:{digest}") == digest
    assert download_field_test_artifacts._expected_digest_hex(digest) == digest


def test_expected_digest_rejects_unknown_digest_shape():
    with pytest.raises(ValueError, match="unsupported artifact digest"):
        download_field_test_artifacts._expected_digest_hex("md5:not-supported")


def test_safe_extract_flat_zip_extracts_plain_files(tmp_path):
    zip_path = tmp_path / "artifact.zip"
    out_dir = tmp_path / "out"
    _write_zip(zip_path, {
        "RELEASE_MANIFEST.json": b"{}",
        "SHA256SUMS.txt": b"",
    })

    extracted = download_field_test_artifacts.safe_extract_flat_zip(zip_path, out_dir)

    assert extracted == ["RELEASE_MANIFEST.json", "SHA256SUMS.txt"]
    assert (out_dir / "RELEASE_MANIFEST.json").read_bytes() == b"{}"


@pytest.mark.parametrize("member", ["../evil.txt", "nested/evil.txt", "/absolute.txt", "C:\\evil.txt"])
def test_safe_extract_flat_zip_rejects_unsafe_members(tmp_path, member):
    zip_path = tmp_path / "artifact.zip"
    _write_zip(zip_path, {member: b"bad"})

    with pytest.raises(ValueError, match="unsafe ZIP member"):
        download_field_test_artifacts.safe_extract_flat_zip(zip_path, tmp_path / "out")


def test_safe_extract_flat_zip_rejects_duplicate_members(tmp_path):
    zip_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("RELEASE_MANIFEST.json", b"first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("RELEASE_MANIFEST.json", b"second")

    with pytest.raises(ValueError, match="duplicate ZIP member"):
        download_field_test_artifacts.safe_extract_flat_zip(zip_path, tmp_path / "out")


def test_prepare_output_dir_rejects_non_empty_without_clean(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="not empty"):
        download_field_test_artifacts._prepare_output_dir(output, clean=False)


def test_download_selected_artifacts_extracts_and_checks_digest(tmp_path, monkeypatch):
    windows_zip = tmp_path / "windows.zip"
    android_zip = tmp_path / "android.zip"
    _write_zip(windows_zip, {"ClipVault-Desktop-v1.6.0-portable.exe": b"win"})
    _write_zip(android_zip, {"ClipVault-Android-v1.6.0-debug.apk": b"apk"})

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    selected = {
        download_field_test_artifacts.WINDOWS_ARTIFACT_NAME: _artifact(
            download_field_test_artifacts.WINDOWS_ARTIFACT_NAME,
            artifact_id=10,
            digest=f"sha256:{digest(windows_zip)}",
        ),
        download_field_test_artifacts.ANDROID_ARTIFACT_NAME: _artifact(
            download_field_test_artifacts.ANDROID_ARTIFACT_NAME,
            artifact_id=11,
            digest=f"sha256:{digest(android_zip)}",
        ),
    }
    urls = {
        selected[download_field_test_artifacts.WINDOWS_ARTIFACT_NAME].archive_download_url: "https://artifact.local/windows.zip",
        selected[download_field_test_artifacts.ANDROID_ARTIFACT_NAME].archive_download_url: "https://artifact.local/android.zip",
    }
    payloads = {
        "https://artifact.local/windows.zip": windows_zip,
        "https://artifact.local/android.zip": android_zip,
    }

    monkeypatch.setattr(
        download_field_test_artifacts,
        "resolve_artifact_download_url",
        lambda url, **kwargs: urls[url],
    )
    monkeypatch.setattr(
        download_field_test_artifacts,
        "_download_url_to_file",
        lambda url, path, **kwargs: path.write_bytes(payloads[url].read_bytes()),
    )

    downloaded = download_field_test_artifacts.download_selected_artifacts(
        selected=selected,
        output_dir=tmp_path / "out",
        token="token",
        timeout=1,
        retries=1,
        clean=True,
        verify_zip_digest=True,
        verify_manifests=False,
        source_version="1.6.0",
        target_commit=None,
    )

    assert [row.name for row in downloaded] == [
        download_field_test_artifacts.ANDROID_ARTIFACT_NAME,
        download_field_test_artifacts.WINDOWS_ARTIFACT_NAME,
    ]
    assert all(row.digest_verified for row in downloaded)
    assert (tmp_path / "out" / "windows" / "ClipVault-Desktop-v1.6.0-portable.exe").read_bytes() == b"win"
    assert (tmp_path / "out" / "android" / "ClipVault-Android-v1.6.0-debug.apk").read_bytes() == b"apk"


def test_verify_manifests_resolves_relative_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured = {}

    class Evidence:
        @staticmethod
        def verify_candidate_artifacts(**kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(download_field_test_artifacts, "_load_field_test_evidence", lambda: Evidence)

    assert download_field_test_artifacts._verify_manifests(
        output_dir=Path("field-test-v1.7"),
        source_version="1.6.0",
        target_commit="a" * 40,
    )

    assert captured["windows_dir"] == (tmp_path / "field-test-v1.7" / "windows").resolve()
    assert captured["android_dir"] == (tmp_path / "field-test-v1.7" / "android").resolve()


def test_download_selected_artifacts_rejects_zip_digest_mismatch(tmp_path, monkeypatch):
    zip_path = tmp_path / "artifact.zip"
    _write_zip(zip_path, {"ClipVault-Android-v1.6.0-debug.apk": b"apk"})
    selected = {
        download_field_test_artifacts.WINDOWS_ARTIFACT_NAME: _artifact(
            download_field_test_artifacts.WINDOWS_ARTIFACT_NAME,
            artifact_id=10,
            digest="sha256:" + "0" * 64,
        ),
        download_field_test_artifacts.ANDROID_ARTIFACT_NAME: _artifact(
            download_field_test_artifacts.ANDROID_ARTIFACT_NAME,
            artifact_id=11,
            digest="sha256:" + "1" * 64,
        ),
    }
    monkeypatch.setattr(
        download_field_test_artifacts,
        "resolve_artifact_download_url",
        lambda url, **kwargs: "https://artifact.local/artifact.zip",
    )
    monkeypatch.setattr(
        download_field_test_artifacts,
        "_download_url_to_file",
        lambda url, path, **kwargs: path.write_bytes(zip_path.read_bytes()),
    )

    with pytest.raises(ValueError, match="ZIP digest mismatch"):
        download_field_test_artifacts.download_selected_artifacts(
            selected=selected,
            output_dir=tmp_path / "out",
            token="token",
            timeout=1,
            retries=1,
            clean=True,
            verify_zip_digest=True,
            verify_manifests=False,
            source_version="1.6.0",
            target_commit=None,
        )


def test_download_url_to_file_rejects_non_https(tmp_path):
    with pytest.raises(ValueError, match="must use https"):
        download_field_test_artifacts._download_url_to_file(
            "file:///tmp/artifact.zip",
            tmp_path / "artifact.zip",
            timeout=1,
        )


def test_redacted_error_strips_bearer_token_and_signed_url_query():
    exc = RuntimeError(
        "failed with Bearer ghs_secret and "
        "https://pipelines.actions.githubusercontent.com/blob.zip?sig=secret&token=secret#frag"
    )

    redacted = download_field_test_artifacts._redacted_error(exc)

    assert "ghs_secret" not in redacted
    assert "sig=secret" not in redacted
    assert "token=secret" not in redacted
    assert "#frag" not in redacted
    assert "Bearer <redacted>" in redacted
    assert "https://pipelines.actions.githubusercontent.com/blob.zip?<redacted>" in redacted


def test_scope_note_mentions_optional_manifest_verification_but_keeps_release_boundary():
    note = download_field_test_artifacts.scope_note()

    assert "--verify-manifests" in note
    assert "signed/final release artifacts" in note
    assert "Owner/manual device QA" in note
