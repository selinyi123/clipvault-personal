"""Unit tests for release manifest/checksum verification."""

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name):
    script = _ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_candidate_manifest = _load_script("release_candidate_manifest")
verify_release_manifest = _load_script("verify_release_manifest")
OWNER_CERT_SHA256 = "ab" * 32
OTHER_CERT_SHA256 = "cd" * 32
VALID_APKSIGNER_EVIDENCE = (
    f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n".encode("ascii")
)
VALID_V2_APKSIGNER_EVIDENCE = (
    "Verifies\n"
    "Verified using v1 scheme (JAR signing): false\n"
    "Verified using v2 scheme (APK Signature Scheme v2): true\n"
    "Verified using v3 scheme (APK Signature Scheme v3): false\n"
    "Number of signers: 1\n"
    "V2 Signer: certificate DN: CN=ClipVault Owner\n"
    f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256.upper()}\n"
    f"V2 Signer: certificate SHA-1 digest: {'1' * 40}\n"
    f"V2 Signer: certificate MD5 digest: {'2' * 32}\n"
    "V2 Signer: key algorithm: RSA\n"
    "V2 Signer: key size (bits): 4096\n"
    f"V2 Signer: public key SHA-256 digest: {OTHER_CERT_SHA256}\n"
).encode("ascii")


def _build_fixture(tmp_path):
    (tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"portable")
    (tmp_path / "ClipVault-Setup-v1.6.0.exe").write_bytes(b"installer")
    (tmp_path / "ClipVault-v1.6.0-LGPL-relink-kit.zip").write_bytes(b"relink")
    release_candidate_manifest.build_manifest(
        tmp_path,
        platform="windows",
        version="1.6.0",
        commit="abc123",
    )


def _build_signed_android_fixture(
    tmp_path,
    *,
    include_evidence=True,
    evidence_body=VALID_APKSIGNER_EVIDENCE,
):
    (tmp_path / "ClipVault-Android-v1.6.0-release-signed.apk").write_bytes(b"signed apk")
    if include_evidence:
        (tmp_path / "ANDROID_APKSIGNER_VERIFY.txt").write_bytes(evidence_body)
    release_candidate_manifest.build_manifest(
        tmp_path,
        kind="release",
        platform="android",
        version="1.6.0",
        commit="123release",
        signed=True,
    )


def test_verify_accepts_matching_dry_run_manifest(tmp_path):
    _build_fixture(tmp_path)

    manifest = verify_release_manifest.verify_manifest(
        tmp_path,
        platform="windows",
        version="1.6.0",
        commit="abc123",
        expect_dry_run=True,
    )

    assert manifest["kind"] == "release-candidate-dry-run"
    assert manifest["signed"] is False
    assert manifest["published"] is False
    assert [row["name"] for row in manifest["artifacts"]] == [
        "ClipVault-Desktop-v1.6.0-portable.exe",
        "ClipVault-Setup-v1.6.0.exe",
        "ClipVault-v1.6.0-LGPL-relink-kit.zip",
    ]


def test_verify_rejects_changed_artifact_hash(tmp_path):
    _build_fixture(tmp_path)
    (tmp_path / "ClipVault-Setup-v1.6.0.exe").write_bytes(b"tamper!!!")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_checksum_file_drift(tmp_path):
    _build_fixture(tmp_path)
    (tmp_path / "SHA256SUMS.txt").write_text("wrong\n", encoding="ascii")

    with pytest.raises(ValueError, match="SHA256SUMS"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_dry_run_signed_flag_drift(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["signed"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="dry-run manifest must be unsigned"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_dry_run_signed_flag_even_without_dry_run_mode(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["signed"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="dry-run manifest must be unsigned"):
        verify_release_manifest.verify_manifest(tmp_path)


def test_verify_rejects_dry_run_published_flag_even_without_dry_run_mode(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["published"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="dry-run manifest must be unpublished"):
        verify_release_manifest.verify_manifest(tmp_path)


def test_verify_rejects_unknown_manifest_kind(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["kind"] = "nightly"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest kind must be one of"):
        verify_release_manifest.verify_manifest(tmp_path)


def test_verify_rejects_manifest_artifact_path_traversal(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["name"] = "../ClipVault-Desktop-v1.6.0-portable.exe"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="plain file name"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_manifest_hidden_artifact_names(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["name"] = ".ClipVault-Desktop-v1.6.0-portable.exe"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact name must not be hidden"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_actual_hidden_artifacts(tmp_path):
    _build_fixture(tmp_path)
    (tmp_path / ".ClipVault-extra.txt").write_text("hidden artifact\n", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact name must not be hidden"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_extra_artifact_even_when_manifest_and_checksums_include_it(
    tmp_path,
):
    (tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"portable")
    (tmp_path / "ClipVault-Setup-v1.6.0.exe").write_bytes(b"installer")
    (tmp_path / "ClipVault-v1.6.0-LGPL-relink-kit.zip").write_bytes(b"relink")
    (tmp_path / "unexpected.bin").write_bytes(b"not part of the release contract")
    release_candidate_manifest.build_manifest(
        tmp_path,
        platform="windows",
        version="1.6.0",
        commit="abc123",
    )

    with pytest.raises(ValueError, match="unexpected release artifact"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_symlink_artifacts(tmp_path):
    _build_fixture(tmp_path)
    artifact = tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe"
    target = tmp_path / "target.exe"
    target.write_bytes(artifact.read_bytes())
    artifact.unlink()
    try:
        artifact.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable in this environment: {exc}")

    with pytest.raises(ValueError, match="must not be a symlink"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_nested_artifact_directories(tmp_path):
    _build_fixture(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "unexpected.txt").write_text("not covered by manifest\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected subdirectory"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_signed_android_manifest_requires_apksigner_evidence(tmp_path):
    _build_signed_android_fixture(tmp_path)

    manifest = verify_release_manifest.verify_manifest(
        tmp_path,
        platform="android",
        version="1.6.0",
        commit="123release",
        require_signed=True,
        expected_android_cert_sha256=OWNER_CERT_SHA256,
    )

    assert manifest["signed"] is True
    assert [row["name"] for row in manifest["artifacts"]] == [
        "ANDROID_APKSIGNER_VERIFY.txt",
        "ClipVault-Android-v1.6.0-release-signed.apk",
    ]


def test_android_certificate_digest_normalization_is_strict_and_case_insensitive():
    assert verify_release_manifest.normalize_android_cert_sha256(
        OWNER_CERT_SHA256.upper()
    ) == OWNER_CERT_SHA256


def test_parse_realistic_multiline_apksigner_output_with_crlf():
    output = (
        "Verifies\r\n"
        "Verified using v1 scheme (JAR signing): true\r\n"
        "Verified using v2 scheme (APK Signature Scheme v2): true\r\n"
        "Verified using v3 scheme (APK Signature Scheme v3): true\r\n"
        "Number of signers: 1\r\n"
        "Signer #1 certificate DN: CN=ClipVault Owner\r\n"
        f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256.upper()}\r\n"
        f"Signer #1 certificate SHA-1 digest: {'1' * 40}\r\n"
        f"Signer #1 certificate MD5 digest: {'2' * 32}\r\n"
        "Signer #1 key algorithm: RSA\r\n"
        "Signer #1 key size (bits): 4096\r\n"
        f"Signer #1 public key SHA-256 digest: {OTHER_CERT_SHA256}\r\n"
        f"Source Stamp Signer certificate SHA-256 digest: {OTHER_CERT_SHA256}\r\n"
    )

    assert (
        verify_release_manifest.parse_android_signer_cert_sha256(output)
        == OWNER_CERT_SHA256
    )


def test_parse_current_v2_apksigner_output():
    output = VALID_V2_APKSIGNER_EVIDENCE.decode("ascii")

    assert (
        verify_release_manifest.parse_android_signer_cert_sha256(output)
        == OWNER_CERT_SHA256
    )


@pytest.mark.parametrize(
    "output",
    [
        (
            "Number of signers: 2\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 2\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"V2 Signer: certificate SHA-256 digest: {OTHER_CERT_SHA256}\n"
        ),
        f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n",
        (
            "Number of signers: 1\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 1\n"
            f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 1\n"
            f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"V2 Signer: certificate SHA-256 digest: {OTHER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 1\n"
            "Number of signers: 1\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 1\n"
            "Number  of signers: 2\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 1\n"
            f" V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ),
        "Number of signers: 1\nV2 Signer: certificate SHA-256 digest: abc123\n",
        (
            "Number of signers: 1\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"V3.1 Signer: certificate SHA-256 digest: {OTHER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 1\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"V2 Signer : certificate SHA-256 digest: {OTHER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 1\n"
            f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"V2 Signer certificate SHA-256 digest: {OTHER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 01\n"
            f"V2 Signer: certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ),
        (
            "Number of signers: 2\n"
            f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ),
    ],
)
def test_parse_rejects_ambiguous_or_incomplete_v2_signer_evidence(output):
    with pytest.raises(ValueError):
        verify_release_manifest.parse_android_signer_cert_sha256(output)


@pytest.mark.parametrize(
    "value",
    [
        "abc123",
        "a" * 63,
        "a" * 65,
        "aa:" * 31 + "aa",
        f"sha256:{OWNER_CERT_SHA256}",
        f" {OWNER_CERT_SHA256}",
        f"{OWNER_CERT_SHA256}\n",
    ],
)
def test_android_certificate_digest_normalization_rejects_ambiguous_values(value):
    with pytest.raises(ValueError, match="exactly 64 unseparated hex"):
        verify_release_manifest.normalize_android_cert_sha256(value)


def test_verify_signed_android_manifest_matches_owner_certificate(tmp_path):
    _build_signed_android_fixture(tmp_path)

    manifest = verify_release_manifest.verify_manifest(
        tmp_path,
        platform="android",
        version="1.6.0",
        commit="123release",
        require_signed=True,
        expected_android_cert_sha256=OWNER_CERT_SHA256.upper(),
    )

    assert manifest["signed"] is True


def test_verify_signed_android_manifest_accepts_current_v2_evidence(tmp_path):
    _build_signed_android_fixture(
        tmp_path,
        evidence_body=VALID_V2_APKSIGNER_EVIDENCE,
    )

    manifest = verify_release_manifest.verify_manifest(
        tmp_path,
        platform="android",
        version="1.6.0",
        commit="123release",
        require_signed=True,
        expected_android_cert_sha256=OWNER_CERT_SHA256,
    )

    assert manifest["signed"] is True


def test_verify_android_release_requires_owner_certificate_even_without_cli_flag(
    tmp_path,
):
    _build_signed_android_fixture(tmp_path)

    with pytest.raises(ValueError, match="requires expected_android_cert_sha256"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
        )


@pytest.mark.parametrize(
    "evidence_body",
    [VALID_APKSIGNER_EVIDENCE, VALID_V2_APKSIGNER_EVIDENCE],
)
def test_verify_signed_android_manifest_rejects_wrong_owner_certificate(
    tmp_path,
    evidence_body,
):
    _build_signed_android_fixture(tmp_path, evidence_body=evidence_body)

    with pytest.raises(ValueError, match="Owner trust anchor"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
            expected_android_cert_sha256=OTHER_CERT_SHA256,
        )


def test_verify_signed_android_manifest_uses_manifest_platform_for_evidence(tmp_path):
    _build_signed_android_fixture(tmp_path, include_evidence=False)

    with pytest.raises(ValueError, match="ANDROID_APKSIGNER_VERIFY.txt"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            version="1.6.0",
            commit="123release",
            require_signed=True,
            expected_android_cert_sha256=OWNER_CERT_SHA256,
        )


def test_verify_rejects_signed_android_manifest_without_apksigner_evidence(tmp_path):
    _build_signed_android_fixture(tmp_path, include_evidence=False)

    with pytest.raises(ValueError, match="ANDROID_APKSIGNER_VERIFY.txt"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
            expected_android_cert_sha256=OWNER_CERT_SHA256,
        )


def test_verify_rejects_empty_android_apksigner_evidence(tmp_path):
    _build_signed_android_fixture(tmp_path, evidence_body=b"")

    with pytest.raises(ValueError, match="must not be empty"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
            expected_android_cert_sha256=OWNER_CERT_SHA256,
        )


def test_verify_rejects_non_apksigner_signed_evidence_text(tmp_path):
    _build_signed_android_fixture(tmp_path, evidence_body=b"not empty\n")

    with pytest.raises(ValueError, match="exactly one Signer #1"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
            expected_android_cert_sha256=OWNER_CERT_SHA256,
        )


@pytest.mark.parametrize(
    "evidence_body",
    [
        b"Signer #1 certificate SHA-256 digest: abc123\n",
        (
            f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"Signer #2 certificate SHA-256 digest: {OTHER_CERT_SHA256}\n"
        ).encode("ascii"),
        (
            f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
            f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n"
        ).encode("ascii"),
        f" Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n".encode("ascii"),
        f"Signer #1 certificate SHA-1 digest: {'1' * 40}\n".encode("ascii"),
        b"\xff\xfe",
    ],
)
def test_verify_rejects_malformed_or_ambiguous_signer_evidence(
    tmp_path,
    evidence_body,
):
    _build_signed_android_fixture(tmp_path, evidence_body=evidence_body)

    with pytest.raises(ValueError):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
            expected_android_cert_sha256=OWNER_CERT_SHA256,
        )


def test_verify_rejects_oversized_signer_evidence(tmp_path):
    evidence = VALID_APKSIGNER_EVIDENCE + b"x" * (
        verify_release_manifest.MAX_ANDROID_APKSIGNER_EVIDENCE_BYTES
    )
    _build_signed_android_fixture(tmp_path, evidence_body=evidence)

    with pytest.raises(ValueError, match="exceeds"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
            expected_android_cert_sha256=OWNER_CERT_SHA256,
        )


def test_verify_rejects_android_release_with_unexpected_signed_apk_name(tmp_path):
    (tmp_path / "renamed.apk").write_bytes(b"signed apk")
    (tmp_path / "ANDROID_APKSIGNER_VERIFY.txt").write_text(
        f"Signer #1 certificate SHA-256 digest: {OWNER_CERT_SHA256}\n",
        encoding="utf-8",
    )
    release_candidate_manifest.build_manifest(
        tmp_path,
        kind="release",
        platform="android",
        version="1.6.0",
        commit="123release",
        signed=True,
    )

    with pytest.raises(ValueError, match="missing expected artifact"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
            expected_android_cert_sha256=OWNER_CERT_SHA256,
        )


def test_cli_returns_nonzero_for_mismatched_version(tmp_path, capsys):
    _build_fixture(tmp_path)

    rc = verify_release_manifest.main([
        "--artifact-dir",
        str(tmp_path),
        "--platform",
        "windows",
        "--version",
        "9.9.9",
        "--expect-dry-run",
    ])

    captured = capsys.readouterr()
    assert rc == 1
    assert "manifest version mismatch" in captured.err


def test_signed_android_cli_requires_owner_certificate_argument(tmp_path, capsys):
    _build_signed_android_fixture(tmp_path)

    rc = verify_release_manifest.main([
        "--artifact-dir",
        str(tmp_path),
        "--platform",
        "android",
        "--version",
        "1.6.0",
        "--commit",
        "123release",
        "--require-signed",
    ])

    assert rc == 1
    assert "requires --expected-android-cert-sha256" in capsys.readouterr().err


def test_signed_android_cli_accepts_matching_owner_certificate(tmp_path, capsys):
    _build_signed_android_fixture(tmp_path)

    rc = verify_release_manifest.main([
        "--artifact-dir",
        str(tmp_path),
        "--platform",
        "android",
        "--version",
        "1.6.0",
        "--commit",
        "123release",
        "--require-signed",
        "--expected-android-cert-sha256",
        OWNER_CERT_SHA256,
    ])

    assert rc == 0
    assert "verified release manifest" in capsys.readouterr().out
