"""Fail-closed tests for live Issue #36 final-draft artifact evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import py_compile
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OWNER_CERT = "ab" * 32
COMMIT = "c" * 40
RUN_ID = 12345
RUN_URL = f"https://github.com/selinyi123/clipvault-personal/actions/runs/{RUN_ID}"


def _load_script(name: str, relative_path: str):
    script = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


manifest_builder = _load_script(
    "release_candidate_manifest_for_live_evidence_tests",
    "scripts/release_candidate_manifest.py",
)
evidence = _load_script(
    "release_artifact_evidence_live_tests",
    "tools/release_artifact_evidence.py",
)


def test_trusted_source_loader_ignores_unchecked_hash_bytecode_cache(
    tmp_path: Path,
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    trusted_source = scripts / "verify_release_manifest.py"
    trusted_source.write_text("VALUE = 'trusted-source'\n", encoding="utf-8")
    cache_path = Path(importlib.util.cache_from_source(str(trusted_source)))
    cache_path.parent.mkdir()
    attacker_source = tmp_path / "attacker.py"
    attacker_source.write_text("VALUE = 'ignored-bytecode'\n", encoding="utf-8")
    py_compile.compile(
        str(attacker_source),
        cfile=str(cache_path),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )

    module = evidence._load_source_module(
        "verify_release_manifest_no_bytecode_test",
        trusted_source,
    )

    assert module.VALUE == "trusted-source"


def test_trusted_source_loader_restores_preexisting_module_entry(tmp_path, monkeypatch):
    trusted_source = tmp_path / "trusted.py"
    trusted_source.write_text("VALUE = 'trusted-source'\n", encoding="utf-8")
    sentinel = object()
    monkeypatch.setitem(sys.modules, "trusted_restore_test", sentinel)

    module = evidence._load_source_module("trusted_restore_test", trusted_source)

    assert module.VALUE == "trusted-source"
    assert sys.modules["trusted_restore_test"] is sentinel


def test_subprocess_runner_sanitizes_pager_and_java_injection_environment(monkeypatch):
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return Completed()

    for name in (
        "GH_FORCE_TTY",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "CLASSPATH",
    ):
        monkeypatch.setenv(name, "attacker-controlled")
    monkeypatch.setenv("GH_PAGER", "attacker-pager")
    monkeypatch.setenv("PAGER", "attacker-pager")
    monkeypatch.setenv("GH_TOKEN", "preserved-token")
    monkeypatch.setattr(evidence.subprocess, "run", fake_run)

    result = evidence.SubprocessRunner().run(["trusted.exe", "--version"], timeout=3)

    assert result.returncode == 0
    assert captured["argv"] == ["trusted.exe", "--version"]
    assert captured["cwd"] == Path("trusted.exe").resolve().parent
    env = captured["env"]
    assert isinstance(env, dict)
    for name in (
        "GH_FORCE_TTY",
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "CLASSPATH",
    ):
        assert name not in env
    assert env["GH_PAGER"] == ""
    assert env["PAGER"] == ""
    assert env["GH_TOKEN"] == "preserved-token"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    windows = root / "windows"
    android = root / "android"
    release = root / "draft-release"
    windows.mkdir()
    android.mkdir()
    release.mkdir()

    (windows / "ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"portable")
    (windows / "ClipVault-Setup-v1.6.0.exe").write_bytes(b"installer")
    (windows / "ClipVault-v1.6.0-LGPL-relink-kit.zip").write_bytes(b"relink")
    manifest_builder.build_manifest(
        windows,
        kind="release",
        platform="windows",
        version="1.6.0",
        commit=COMMIT,
    )

    apk_name = "ClipVault-Android-v1.6.0-release-signed.apk"
    (android / apk_name).write_bytes(b"signed-apk")
    (android / "ANDROID_APKSIGNER_VERIFY.txt").write_text(
        f"Signer #1 certificate SHA-256 digest: {OWNER_CERT}\n",
        encoding="utf-8",
    )
    manifest_builder.build_manifest(
        android,
        kind="release",
        platform="android",
        version="1.6.0",
        commit=COMMIT,
        signed=True,
    )

    mapping = {
        windows / "ClipVault-Desktop-v1.6.0-portable.exe": release
        / "ClipVault-Desktop-v1.6.0-portable.exe",
        windows / "ClipVault-Setup-v1.6.0.exe": release / "ClipVault-Setup-v1.6.0.exe",
        windows
        / "ClipVault-v1.6.0-LGPL-relink-kit.zip": release
        / "ClipVault-v1.6.0-LGPL-relink-kit.zip",
        windows / "SHA256SUMS.txt": release / "windows-SHA256SUMS.txt",
        windows / "RELEASE_MANIFEST.json": release / "windows-RELEASE_MANIFEST.json",
        android / apk_name: release / apk_name,
        android / "ANDROID_APKSIGNER_VERIFY.txt": release / "ANDROID_APKSIGNER_VERIFY.txt",
        android / "SHA256SUMS.txt": release / "android-SHA256SUMS.txt",
        android / "RELEASE_MANIFEST.json": release / "android-RELEASE_MANIFEST.json",
    }
    for source, target in mapping.items():
        shutil.copyfile(source, target)

    apksigner = root / "apksigner.jar"
    apksigner.write_bytes(b"fake jar")
    (root / "java.exe").write_bytes(b"fake executable")
    (root / "gh.exe").write_text("fake executable\n", encoding="ascii")
    return windows, android, release, apksigner


class FakeRunner:
    _UNCHANGED = object()

    def __init__(self, release_dir: Path, apksigner: Path):
        self.release_dir = release_dir
        self.apksigner = apksigner
        self.java = release_dir.parent / "java.exe"
        self.gh = release_dir.parent / "gh.exe"
        self.calls: list[tuple[list[str], int]] = []
        self.branch_calls = 0
        self.run_calls = 0
        self.release_calls = 0
        self.cert_variable_calls = 0
        self.release_tag_calls = 0
        self.run_overrides: dict[str, object] = {}
        self.final_run_overrides: dict[str, object] = {}
        self.release_overrides: dict[str, object] = {}
        self.final_release_overrides: dict[str, object] = {}
        self.workflow_artifact_overrides: dict[str, object] = {}
        self.attestation_invocation = f"{RUN_URL}/attempts/1"
        self.attestation_digest_override: str | None = None
        self.apksigner_cert = OWNER_CERT
        self.release_environment_cert = OWNER_CERT
        self.final_release_environment_cert = OWNER_CERT
        self.release_digest_override: tuple[str, str] | None = None
        self.release_asset_id_offset = 0
        self.release_is_published = False
        self.final_main_sha = COMMIT
        self.release_tag_object: dict[str, str] | None = None
        self.final_release_tag_object: dict[str, str] | None | object = self._UNCHANGED
        self.annotated_tag_objects: dict[str, dict[str, str]] = {}

    def _result(self, value, returncode=0):
        output = value if isinstance(value, str) else json.dumps(value)
        return evidence.CommandResult(returncode, output, "")

    def _run_record(self):
        record = {
            "id": RUN_ID,
            "html_url": RUN_URL,
            "name": "Release artifacts v1.6.0 from main draft=true",
            "path": ".github/workflows/release.yml",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": COMMIT,
            "head_repository": {"full_name": "selinyi123/clipvault-personal"},
            "display_title": "Release artifacts v1.6.0 from main draft=true",
            "run_attempt": 1,
        }
        record.update(self.run_overrides)
        if self.run_calls > 1:
            record.update(self.final_run_overrides)
        return record

    def _release_record(self):
        assets = []
        for index, path in enumerate(sorted(self.release_dir.iterdir()), start=100):
            digest = _sha256(path)
            if self.release_digest_override and path.name == self.release_digest_override[0]:
                digest = self.release_digest_override[1]
            assets.append({
                "id": index + self.release_asset_id_offset,
                "name": path.name,
                "state": "uploaded",
                "size": path.stat().st_size,
                "digest": f"sha256:{digest}",
            })
        record = {
            "id": 77,
            "html_url": (
                "https://github.com/selinyi123/clipvault-personal/releases/tag/v1.6.0"
                if self.release_is_published
                else "https://github.com/selinyi123/clipvault-personal/releases/tag/untagged-77"
            ),
            "tag_name": "v1.6.0",
            "name": "ClipVault Personal v1.6.0",
            "draft": not self.release_is_published,
            "prerelease": False,
            "target_commitish": COMMIT,
            "assets": assets,
        }
        if self.release_is_published:
            record["published_at"] = "2026-07-13T01:00:00Z"
        record.update(self.release_overrides)
        if self.release_calls > 1:
            record.update(self.final_release_overrides)
        return record

    def run(self, argv, *, timeout):
        args = [str(value) for value in argv]
        self.calls.append((args, timeout))
        if args[:3] == [str(self.java), "-jar", str(self.apksigner)]:
            return self._result(
                f"Signer #1 certificate SHA-256 digest: {self.apksigner_cert}\n"
            )
        if args[0] == str(self.gh) and args[1:4] == ["api", "-X", "GET"]:
            endpoint = args[-1]
            if endpoint.endswith("/branches/main"):
                self.branch_calls += 1
                sha = COMMIT if self.branch_calls == 1 else self.final_main_sha
                return self._result({"commit": {"sha": sha}})
            if endpoint.endswith("/git/matching-refs/tags/v1.6.0"):
                self.release_tag_calls += 1
                tag_object = self.release_tag_object
                if (
                    self.release_tag_calls > 1
                    and self.final_release_tag_object is not self._UNCHANGED
                ):
                    tag_object = self.final_release_tag_object
                if tag_object is None:
                    return self._result([])
                return self._result([
                    {"ref": "refs/tags/v1.6.0", "object": tag_object}
                ])
            if "/git/tags/" in endpoint:
                object_sha = endpoint.rsplit("/", 1)[-1]
                return self._result({"object": self.annotated_tag_objects[object_sha]})
            if endpoint.endswith(
                "/environments/release/variables/ANDROID_RELEASE_CERT_SHA256"
            ):
                self.cert_variable_calls += 1
                cert = (
                    self.release_environment_cert
                    if self.cert_variable_calls == 1
                    else self.final_release_environment_cert
                )
                return self._result({
                    "name": "ANDROID_RELEASE_CERT_SHA256",
                    "value": cert,
                })
            if endpoint.endswith(f"/actions/runs/{RUN_ID}"):
                self.run_calls += 1
                return self._result(self._run_record())
            if endpoint.endswith("/artifacts?per_page=100"):
                record = {
                    "total_count": 2,
                    "artifacts": [
                        {
                            "id": 1,
                            "name": "clipvault-windows-release-artifacts",
                            "expired": False,
                            "size_in_bytes": 100,
                            "digest": f"sha256:{'1' * 64}",
                        },
                        {
                            "id": 2,
                            "name": "clipvault-android-signed-release-artifacts",
                            "expired": False,
                            "size_in_bytes": 200,
                            "digest": f"sha256:{'2' * 64}",
                        },
                    ],
                }
                for row in record["artifacts"]:
                    row.update(self.workflow_artifact_overrides)
                return self._result(record)
            if endpoint.endswith("/releases?per_page=100"):
                self.release_calls += 1
                return self._result([self._release_record()])
            if endpoint.endswith("/releases/tags/v1.6.0") and self.release_is_published:
                self.release_calls += 1
                return self._result(self._release_record())
        if args[0] == str(self.gh) and args[1:3] == ["attestation", "verify"]:
            artifact = Path(args[3])
            digest = self.attestation_digest_override or _sha256(artifact)
            return self._result([
                {
                    "verificationResult": {
                        "signature": {
                            "certificate": {
                                "runInvocationURI": self.attestation_invocation,
                            }
                        },
                        "statement": {
                            "subject": [{"name": artifact.name, "digest": {"sha256": digest}}]
                        },
                    }
                }
            ])
        raise AssertionError(f"unexpected command: {args!r}")


def _collect(tmp_path: Path, runner: FakeRunner | None = None):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = runner or FakeRunner(release, apksigner)
    report = evidence.collect_final_draft_evidence(
        windows_dir=windows,
        android_dir=android,
        draft_release_dir=release,
        gh=runner.gh,
        apksigner=apksigner,
        java=runner.java,
        version="v1.6.0",
        commit=COMMIT,
        run_url=RUN_URL,
        expected_android_cert_sha256=OWNER_CERT,
        runner=runner,
        now_fn=lambda: "2026-07-13T00:00:00Z",
    )
    return report, runner, (windows, android, release, apksigner)


def _published_fixture(tmp_path: Path, *, prepublication_tag_present: bool = False):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    draft_runner = FakeRunner(release, apksigner)
    if prepublication_tag_present:
        draft_runner.release_tag_object = {"type": "commit", "sha": COMMIT}
    draft_report = evidence.collect_final_draft_evidence(
        windows_dir=windows,
        android_dir=android,
        draft_release_dir=release,
        gh=draft_runner.gh,
        apksigner=apksigner,
        java=draft_runner.java,
        version="v1.6.0",
        commit=COMMIT,
        run_url=RUN_URL,
        expected_android_cert_sha256=OWNER_CERT,
        runner=draft_runner,
        now_fn=lambda: "2026-07-13T00:00:00Z",
    )

    runner = FakeRunner(release, apksigner)
    runner.release_is_published = True
    runner.release_tag_object = {"type": "commit", "sha": COMMIT}
    return (
        runner,
        (windows, android, release, apksigner),
        draft_report["artifact_binding_sha256"],
    )


def _run_published(
    runner: FakeRunner,
    paths: tuple[Path, Path, Path, Path],
    owner_binding: str,
):
    windows, android, release, apksigner = paths
    report = evidence.collect_published_release_evidence(
        windows_dir=windows,
        android_dir=android,
        published_release_dir=release,
        gh=runner.gh,
        apksigner=apksigner,
        java=runner.java,
        version="v1.6.0",
        commit=COMMIT,
        run_url=RUN_URL,
        expected_android_cert_sha256=OWNER_CERT,
        owner_approved_binding=owner_binding,
        runner=runner,
        now_fn=lambda: "2026-07-13T01:00:00Z",
    )
    return report


def _collect_published(tmp_path: Path, *, prepublication_tag_present: bool = False):
    runner, paths, owner_binding = _published_fixture(
        tmp_path,
        prepublication_tag_present=prepublication_tag_present,
    )
    report = _run_published(runner, paths, owner_binding)
    return report, runner, paths, owner_binding


def test_live_evidence_binds_exact_run_draft_bytes_attestations_and_signer(tmp_path):
    report, runner, paths = _collect(tmp_path)

    assert report["artifact_gate_status"] == (
        "snapshot_verified_live_revalidation_required"
    )
    assert report["target_commit"] == COMMIT
    assert report["workflow_run"]["id"] == RUN_ID
    assert report["workflow_run"]["attempt"] == 1
    assert report["workflow_run"]["workflow"] == "Release artifact build"
    assert report["workflow_run"]["path"] == ".github/workflows/release.yml"
    assert (
        report["workflow_run"]["display_title"]
        == "Release artifacts v1.6.0 from main draft=true"
    )
    assert report["draft_release"]["is_draft"] is True
    assert report["release_tag"] == {
        "ref": "refs/tags/v1.6.0",
        "state": "absent",
        "commit_sha": None,
    }
    assert report["android_signer"] == {
        "expected_cert_sha256": OWNER_CERT,
        "observed_cert_sha256": OWNER_CERT,
        "signer_count": 1,
        "apksigner_verified": True,
        "trust_anchor_source": "github_release_environment_variable_and_owner_input_match",
        "release_environment": "release",
        "release_environment_variable": "ANDROID_RELEASE_CERT_SHA256",
    }
    assert len(report["artifacts"]) == 9
    assert all(row["attestation_verified"] for row in report["artifacts"])
    assert all(row["matching_invocation_count"] == 1 for row in report["artifacts"])
    assert {
        (row["role"], row["workflow_bundle"], row["workflow_name"], row["release_name"])
        for row in report["artifacts"]
    } == {
        (spec.role, spec.workflow_bundle, spec.workflow_name, spec.release_name)
        for spec in evidence._asset_specs("v1.6.0")
    }
    assert len(report["artifact_binding_sha256"]) == 64
    serialized = json.dumps(report)
    for path in paths:
        assert str(path) not in serialized
    assert str(paths[3]) not in evidence.render_final_draft_issue_comment(report)
    assert runner.branch_calls == 2
    assert runner.run_calls == 2
    assert runner.release_calls == 2
    assert runner.cert_variable_calls == 2
    assert runner.release_tag_calls == 2


def test_live_collector_output_is_accepted_by_manual_qa_binding_projection(tmp_path):
    report, _, paths = _collect(tmp_path)

    projection = evidence.build_final_draft_manual_qa_binding_projection(report)

    assert projection == {
        "artifact_evidence_type": "clipvault.issue36.final_draft_artifacts",
        "artifact_binding_sha256": report["artifact_binding_sha256"],
        "target_commit": COMMIT,
        "workflow_run": {
            "id": RUN_ID,
            "attempt": 1,
            "url": RUN_URL,
        },
        "draft_release": {
            "id": 77,
            "url": (
                "https://github.com/selinyi123/clipvault-personal/"
                "releases/tag/untagged-77"
            ),
            "tag_name": "v1.6.0",
        },
        "android_signed_apk": {
            "name": "ClipVault-Android-v1.6.0-release-signed.apk",
            "sha256": next(
                row["sha256"]
                for row in report["artifacts"]
                if row["role"] == "android_signed_apk"
            ),
        },
    }
    serialized = json.dumps(projection)
    assert all(str(path) not in serialized for path in paths)


def test_live_evidence_uses_all_identity_flags_and_explicit_apksigner(tmp_path):
    _, runner, paths = _collect(tmp_path)
    api_calls = [
        args
        for args, _ in runner.calls
        if args[0] == str(runner.gh) and args[1:4] == ["api", "-X", "GET"]
    ]
    assert api_calls
    assert all(args[args.index("--hostname") + 1] == "github.com" for args in api_calls)
    attestations = [
        args
        for args, _ in runner.calls
        if args[0] == str(runner.gh) and args[1:3] == ["attestation", "verify"]
    ]
    assert len(attestations) == 9
    for args in attestations:
        assert args[args.index("--repo") + 1] == "selinyi123/clipvault-personal"
        assert args[args.index("--hostname") + 1] == "github.com"
        assert args[args.index("--cert-identity") + 1].endswith(
            "/.github/workflows/release.yml@refs/heads/main"
        )
        assert "--signer-workflow" not in args
        assert args[args.index("--source-ref") + 1] == "refs/heads/main"
        assert args[args.index("--source-digest") + 1] == COMMIT
        assert args[args.index("--signer-digest") + 1] == COMMIT
        assert "--deny-self-hosted-runners" in args
        assert args[args.index("--predicate-type") + 1] == "https://slsa.dev/provenance/v1"
    apksigner_calls = [
        args
        for args, _ in runner.calls
        if args[:3] == [str(runner.java), "-jar", str(paths[3])]
    ]
    assert apksigner_calls == [[
        str(runner.java),
        "-jar",
        str(paths[3]),
        "verify",
        "--verbose",
        "-Werr",
        "--print-certs",
        str(paths[2] / "ClipVault-Android-v1.6.0-release-signed.apk"),
    ]]


def test_tool_paths_reject_batch_and_workspace_shims(tmp_path):
    batch_gh = tmp_path / "gh.cmd"
    batch_gh.write_text("@echo off\n", encoding="ascii")
    batch_apksigner = tmp_path / "apksigner.bat"
    batch_apksigner.write_text("@echo off\n", encoding="ascii")

    with pytest.raises(ValueError, match="real executable"):
        evidence._validate_gh_path(batch_gh)
    with pytest.raises(ValueError, match="batch apksigner"):
        evidence._apksigner_command(batch_apksigner, None)
    with pytest.raises(ValueError, match="repository workspace"):
        evidence._validate_gh_path(ROOT / "README.md")
    with pytest.raises(ValueError, match="absolute"):
        evidence._validate_gh_path(Path("gh.exe"))

    jar = tmp_path / "apksigner.jar"
    jar.write_bytes(b"jar")
    with pytest.raises(ValueError, match="absolute"):
        evidence._apksigner_command(jar, Path("java.exe"))
    with pytest.raises(ValueError, match="repository workspace"):
        evidence._apksigner_command(jar, ROOT / "README.md")


def test_tool_path_rejects_symlinked_parent_component(tmp_path):
    real_dir = tmp_path / "real-tools"
    real_dir.mkdir()
    (real_dir / "gh.exe").write_bytes(b"exe")
    alias_dir = tmp_path / "tool-alias"
    try:
        alias_dir.symlink_to(real_dir, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable in this test environment")

    with pytest.raises(ValueError, match="must not traverse a reparse point"):
        evidence._validate_gh_path(alias_dir / "gh.exe")


@pytest.mark.skipif(os.name != "nt", reason="Windows device namespaces are platform-specific")
def test_tool_path_rejects_windows_device_namespace_alias():
    device_path = Path("\\\\?\\" + str(ROOT / "README.md"))

    with pytest.raises(ValueError, match="device namespace"):
        evidence._validate_tool_path(device_path, label="test tool")


def test_apksigner_jar_requires_explicit_real_java(tmp_path):
    jar = tmp_path / "apksigner.jar"
    jar.write_bytes(b"jar")
    java = tmp_path / "java.exe"
    java.write_bytes(b"exe")

    with pytest.raises(ValueError, match="requires an explicit Java"):
        evidence._apksigner_command(jar, None)
    assert evidence._apksigner_command(jar, java) == [
        str(java),
        "-jar",
        str(jar),
    ]


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("name", "Release artifact build", "name mismatch"),
        ("name", "Release artifacts v1.6.0 from main draft=false", "name mismatch"),
        ("path", ".github/workflows/other.yml", "path mismatch"),
        ("event", "push", "event mismatch"),
        ("head_branch", "feature", "head_branch mismatch"),
        ("head_sha", "d" * 40, "head_sha mismatch"),
        ("conclusion", "failure", "conclusion mismatch"),
        ("display_title", "Release artifacts v1.6.0 from main draft=false", "display_title mismatch"),
    ],
)
def test_live_evidence_rejects_wrong_run_identity(tmp_path, key, value, message):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.run_overrides[key] = value

    with pytest.raises(ValueError, match=message):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_attestation_from_another_run(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.attestation_invocation = f"{RUN_URL}/attempts/2"

    with pytest.raises(ValueError, match="exact workflow run attempt"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_attestation_subject_digest_mismatch(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.attestation_digest_override = "f" * 64

    with pytest.raises(ValueError, match="exact workflow run attempt"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_independent_signer_mismatch(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.apksigner_cert = "de" * 32

    with pytest.raises(ValueError, match="certificates must match"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_release_environment_certificate_mismatch(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.release_environment_cert = "de" * 32

    with pytest.raises(ValueError, match="environment and Owner Android certificates"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_release_environment_certificate_change(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.final_release_environment_cert = "de" * 32

    with pytest.raises(ValueError, match="environment and Owner Android certificates"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_release_api_digest_mismatch(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.release_digest_override = ("ClipVault-Setup-v1.6.0.exe", "0" * 64)

    with pytest.raises(ValueError, match="API bytes differ"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("draft", False, "non-prerelease draft"),
        ("prerelease", True, "non-prerelease draft"),
        ("target_commitish", "d" * 40, "target commit mismatch"),
    ],
)
def test_live_evidence_rejects_wrong_draft_release_identity(
    tmp_path, key, value, message
):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.release_overrides[key] = value

    with pytest.raises(ValueError, match=message):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"expired": True}, "expired"),
        ({"digest": None}, "must be a sha256 digest"),
    ],
)
def test_live_evidence_rejects_invalid_workflow_artifact_bundle(
    tmp_path, overrides, message
):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.workflow_artifact_overrides = overrides

    with pytest.raises(ValueError, match=message):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_run_or_release_change_during_collection(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path / "run")
    runner = FakeRunner(release, apksigner)
    runner.final_run_overrides["run_attempt"] = 2
    with pytest.raises(ValueError, match="workflow run changed"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )

    windows, android, release, apksigner = _build_fixture(tmp_path / "release")
    runner = FakeRunner(release, apksigner)
    runner.final_release_overrides["id"] = 78
    with pytest.raises(ValueError, match="draft Release changed"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_local_actions_and_release_byte_mismatch(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    (release / "ClipVault-Setup-v1.6.0.exe").write_bytes(b"changed")
    runner = FakeRunner(release, apksigner)

    with pytest.raises(ValueError, match="Actions and draft Release bytes differ"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_extra_local_file(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    (windows / "unexpected.txt").write_text("unexpected", encoding="ascii")
    runner = FakeRunner(release, apksigner)

    with pytest.raises(ValueError, match="mismatch"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_fails_if_main_moves_during_collection(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.final_main_sha = "e" * 40

    with pytest.raises(ValueError, match="not the current main"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_rejects_release_tag_bound_to_different_commit(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.release_tag_object = {"type": "commit", "sha": "d" * 40}

    with pytest.raises(ValueError, match="release tag points to a different commit"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_live_evidence_resolves_annotated_release_tag_to_target_commit(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    tag_object_sha = "a" * 40
    runner.release_tag_object = {"type": "tag", "sha": tag_object_sha}
    runner.annotated_tag_objects[tag_object_sha] = {"type": "commit", "sha": COMMIT}

    report = evidence.collect_final_draft_evidence(
        windows_dir=windows,
        android_dir=android,
        draft_release_dir=release,
        gh=runner.gh,
        apksigner=apksigner,
        java=runner.java,
        version="v1.6.0",
        commit=COMMIT,
        run_url=RUN_URL,
        expected_android_cert_sha256=OWNER_CERT,
        runner=runner,
    )

    assert report["release_tag"] == {
        "ref": "refs/tags/v1.6.0",
        "state": "present",
        "commit_sha": COMMIT,
    }


def test_live_evidence_rejects_release_tag_change_during_collection(tmp_path):
    windows, android, release, apksigner = _build_fixture(tmp_path)
    runner = FakeRunner(release, apksigner)
    runner.final_release_tag_object = {"type": "commit", "sha": COMMIT}

    with pytest.raises(ValueError, match="release tag changed"):
        evidence.collect_final_draft_evidence(
            windows_dir=windows,
            android_dir=android,
            draft_release_dir=release,
            gh=runner.gh,
            apksigner=apksigner,
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            runner=runner,
        )


def test_binding_is_stable_across_validation_time(tmp_path):
    report_a, _, _ = _collect(tmp_path / "a")
    report_b, _, _ = _collect(tmp_path / "b")
    report_b["validated_at"] = "2099-01-01T00:00:00Z"

    assert report_a["artifact_binding_sha256"] == (
        "47fee9fc970ac007863c5aac0bd9bbbe96b9afcd7577f51a98e3cecb9a93c383"
    )
    assert report_a["artifact_binding_sha256"] == evidence._compute_binding_sha256(report_a)
    assert evidence._compute_binding_sha256(report_a) == evidence._compute_binding_sha256(report_b)


def test_binding_changes_for_every_security_identity_field(tmp_path):
    report, _, _ = _collect(tmp_path)
    original = report["artifact_binding_sha256"]

    variants = []
    for mutate in (
        lambda row: row.__setitem__("target_commit", "d" * 40),
        lambda row: row["workflow_run"].__setitem__("id", RUN_ID + 1),
        lambda row: row["workflow_run"].__setitem__("attempt", 2),
        lambda row: row["draft_release"].__setitem__("id", 78),
        lambda row: row.__setitem__(
            "release_tag",
            {"ref": "refs/tags/v1.6.0", "state": "present", "commit_sha": COMMIT},
        ),
        lambda row: row["android_signer"].__setitem__("expected_cert_sha256", "de" * 32),
        lambda row: row["artifacts"][0].__setitem__("release_name", "renamed.bin"),
        lambda row: row["artifacts"][0].__setitem__("release_asset_id", 999),
        lambda row: row["artifacts"][0].__setitem__("sha256", "f" * 64),
        lambda row: row["artifacts"][0].__setitem__("size_bytes", 999),
    ):
        variant = json.loads(json.dumps(report))
        mutate(variant)
        variants.append(variant)

    assert all(evidence._compute_binding_sha256(row) != original for row in variants)


def test_publication_projection_recomputes_binding_and_rejects_tampered_snapshot(tmp_path):
    report, _, paths = _collect(tmp_path)
    projection = evidence.build_owner_approved_publication_projection(
        report,
        owner_approved_binding=report["artifact_binding_sha256"],
    )

    assert projection["projection_status"] == "owner_approved_live_snapshot"
    assert projection["artifact_binding_sha256"] == report["artifact_binding_sha256"]
    assert projection["draft_release"]["id"] == report["draft_release"]["id"]
    assert projection["release_tag"] == report["release_tag"]
    assert len(projection["artifacts"]) == 9
    serialized = json.dumps(projection)
    assert all(str(path) not in serialized for path in paths)

    tampered = json.loads(json.dumps(report))
    tampered["artifacts"][0]["release_asset_id"] += 1
    with pytest.raises(ValueError, match="canonical contents"):
        evidence.build_owner_approved_publication_projection(
            tampered,
            owner_approved_binding=report["artifact_binding_sha256"],
        )
    with pytest.raises(ValueError, match="Owner approval"):
        evidence.build_owner_approved_publication_projection(
            report,
            owner_approved_binding="f" * 64,
        )


@pytest.mark.parametrize("prepublication_tag_present", [False, True])
def test_published_evidence_recovers_owner_binding_and_revalidates_live_state(
    tmp_path,
    prepublication_tag_present,
):
    report, runner, paths, owner_binding = _collect_published(
        tmp_path,
        prepublication_tag_present=prepublication_tag_present,
    )

    assert report["publication_gate_status"] == (
        "published_release_verified_live_revalidation_required"
    )
    assert report["owner_approved_artifact_binding_sha256"] == owner_binding
    assert report["owner_approved_prepublication_release_tag"] == {
        "ref": "refs/tags/v1.6.0",
        "state": "present" if prepublication_tag_present else "absent",
        "commit_sha": COMMIT if prepublication_tag_present else None,
    }
    assert report["published_release"] == {
        "id": 77,
        "url": "https://github.com/selinyi123/clipvault-personal/releases/tag/v1.6.0",
        "tag_name": "v1.6.0",
        "name": "ClipVault Personal v1.6.0",
        "is_draft": False,
        "is_prerelease": False,
        "target_commitish": COMMIT,
        "published_at": "2026-07-13T01:00:00Z",
    }
    assert report["release_tag"] == {
        "ref": "refs/tags/v1.6.0",
        "state": "present",
        "commit_sha": COMMIT,
        "ref_object_type": "commit",
        "ref_object_sha": COMMIT,
    }
    assert len(report["artifacts"]) == 9
    assert all(row["attestation_verified"] for row in report["artifacts"])
    assert report["publication_closure_binding_sha256"] == (
        evidence._compute_publication_closure_sha256(report)
    )
    serialized = json.dumps(report)
    comment = evidence.render_published_release_issue_comment(report)
    assert all(str(path) not in serialized for path in paths)
    assert all(str(path) not in comment for path in paths)
    assert "does not replace manual QA" in comment
    assert runner.branch_calls == 2
    assert runner.run_calls == 2
    assert runner.release_calls == 2
    assert runner.cert_variable_calls == 2
    assert runner.release_tag_calls == 2
    api_calls = [
        args
        for args, _ in runner.calls
        if args[0] == str(runner.gh) and args[1] == "api"
    ]
    assert api_calls
    assert all(args[1:4] == ["api", "-X", "GET"] for args in api_calls)


def test_published_evidence_rejects_wrong_owner_binding(tmp_path):
    runner, paths, _ = _published_fixture(tmp_path)

    with pytest.raises(ValueError, match="does not reproduce the Owner-approved"):
        _run_published(runner, paths, "f" * 64)

    with pytest.raises(ValueError, match="64 lowercase hex"):
        evidence.collect_published_release_evidence(
            windows_dir=paths[0],
            android_dir=paths[1],
            published_release_dir=paths[2],
            gh=runner.gh,
            apksigner=paths[3],
            java=runner.java,
            version="v1.6.0",
            commit=COMMIT,
            run_url=RUN_URL,
            expected_android_cert_sha256=OWNER_CERT,
            owner_approved_binding=None,
            runner=runner,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda runner: runner.release_overrides.update({"id": 78}),
        lambda runner: setattr(runner, "release_asset_id_offset", 1000),
    ],
)
def test_published_evidence_rejects_replaced_release_or_asset_identity(tmp_path, mutate):
    runner, paths, owner_binding = _published_fixture(tmp_path)
    mutate(runner)

    with pytest.raises(ValueError, match="does not reproduce the Owner-approved"):
        _run_published(runner, paths, owner_binding)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("draft", True, "published and non-prerelease"),
        ("prerelease", True, "published and non-prerelease"),
        ("tag_name", "v1.5.10", "published Release tag mismatch"),
        ("name", "ClipVault v1.6.0", "published Release title mismatch"),
        ("target_commitish", "d" * 40, "published Release target commit mismatch"),
        ("html_url", "https://example.invalid/release", "published Release URL mismatch"),
        ("published_at", None, "RFC3339 UTC timestamp"),
        ("published_at", "not-a-timestamp", "RFC3339 UTC timestamp"),
    ],
)
def test_published_evidence_rejects_wrong_release_metadata(
    tmp_path,
    field,
    value,
    expected,
):
    runner, paths, owner_binding = _published_fixture(tmp_path)
    runner.release_overrides[field] = value

    with pytest.raises(ValueError, match=expected):
        _run_published(runner, paths, owner_binding)


def test_published_evidence_rejects_absent_or_wrong_release_tag(tmp_path):
    runner, paths, owner_binding = _published_fixture(tmp_path / "absent")
    runner.release_tag_object = None
    with pytest.raises(ValueError, match="tag is absent"):
        _run_published(runner, paths, owner_binding)

    runner, paths, owner_binding = _published_fixture(tmp_path / "wrong")
    runner.release_tag_object = {"type": "commit", "sha": "d" * 40}
    with pytest.raises(ValueError, match="points to a different commit"):
        _run_published(runner, paths, owner_binding)


def test_published_evidence_accepts_annotated_release_tag(tmp_path):
    runner, paths, owner_binding = _published_fixture(tmp_path)
    tag_sha = "e" * 40
    runner.release_tag_object = {"type": "tag", "sha": tag_sha}
    runner.annotated_tag_objects[tag_sha] = {"type": "commit", "sha": COMMIT}

    report = _run_published(runner, paths, owner_binding)

    assert report["release_tag"]["commit_sha"] == COMMIT
    assert report["release_tag"]["ref_object_sha"] == tag_sha


def test_published_evidence_rejects_annotated_tag_object_swap(tmp_path):
    runner, paths, owner_binding = _published_fixture(tmp_path)
    first_tag = "e" * 40
    second_tag = "f" * 40
    runner.release_tag_object = {"type": "tag", "sha": first_tag}
    runner.final_release_tag_object = {"type": "tag", "sha": second_tag}
    runner.annotated_tag_objects[first_tag] = {"type": "commit", "sha": COMMIT}
    runner.annotated_tag_objects[second_tag] = {"type": "commit", "sha": COMMIT}

    with pytest.raises(ValueError, match="tag changed"):
        _run_published(runner, paths, owner_binding)


def test_published_evidence_rejects_tag_or_release_change_during_collection(tmp_path):
    runner, paths, owner_binding = _published_fixture(tmp_path / "tag")
    runner.final_release_tag_object = None
    with pytest.raises(ValueError, match="tag changed"):
        _run_published(runner, paths, owner_binding)

    runner, paths, owner_binding = _published_fixture(tmp_path / "release")
    runner.final_release_overrides["id"] = 78
    with pytest.raises(ValueError, match="published Release changed"):
        _run_published(runner, paths, owner_binding)


def test_published_evidence_rejects_run_or_owner_certificate_change(tmp_path):
    runner, paths, owner_binding = _published_fixture(tmp_path / "run")
    runner.final_run_overrides["run_attempt"] = 2
    with pytest.raises(ValueError, match="workflow run changed"):
        _run_published(runner, paths, owner_binding)

    runner, paths, owner_binding = _published_fixture(tmp_path / "cert")
    runner.final_release_environment_cert = "cd" * 32
    with pytest.raises(ValueError, match="Owner Android certificates must match"):
        _run_published(runner, paths, owner_binding)


def test_published_evidence_rejects_main_or_local_byte_change(tmp_path):
    runner, paths, owner_binding = _published_fixture(tmp_path / "main")
    runner.final_main_sha = "d" * 40
    with pytest.raises(ValueError, match="not the current main"):
        _run_published(runner, paths, owner_binding)

    runner, paths, owner_binding = _published_fixture(tmp_path / "bytes")
    paths[2].joinpath("ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Actions and published Release bytes differ"):
        _run_published(runner, paths, owner_binding)


def test_publication_closure_binding_is_stable_and_identity_sensitive(tmp_path):
    report_a, _, _, _ = _collect_published(tmp_path / "a")
    report_b, _, _, _ = _collect_published(tmp_path / "b")
    report_b["validated_at"] = "2099-01-01T00:00:00Z"
    original = report_a["publication_closure_binding_sha256"]

    assert original == evidence._compute_publication_closure_sha256(report_a)
    assert evidence._compute_publication_closure_sha256(report_b) == original
    for mutate in (
        lambda row: row.__setitem__(
            "owner_approved_artifact_binding_sha256", "f" * 64
        ),
        lambda row: row["published_release"].__setitem__("id", 78),
        lambda row: row["published_release"].__setitem__(
            "url", "https://example.invalid/release"
        ),
        lambda row: row["published_release"].__setitem__(
            "published_at", "2099-01-01T00:00:00Z"
        ),
        lambda row: row["release_tag"].__setitem__("commit_sha", "d" * 40),
        lambda row: row["artifacts"][0].__setitem__("release_asset_id", 999),
        lambda row: row["artifacts"][0].__setitem__("sha256", "e" * 64),
        lambda row: row["artifacts"][0].__setitem__("attestation_verified", False),
        lambda row: row["artifacts"][0].__setitem__("matching_invocation_count", 2),
    ):
        variant = json.loads(json.dumps(report_a))
        mutate(variant)
        assert evidence._compute_publication_closure_sha256(variant) != original


def test_live_cli_forbids_no_fail_and_requires_strict_outputs(capsys):
    base = [
        "--windows-dir", "windows",
        "--android-dir", "android",
        "--commit", COMMIT,
        "--run-url", RUN_URL,
        "--expected-android-cert-sha256", OWNER_CERT,
        "--require-live-final-draft",
    ]
    with pytest.raises(SystemExit, match="2"):
        evidence.main([*base, "--no-fail"])
    assert "forbids --no-fail" in capsys.readouterr().err

    with pytest.raises(SystemExit, match="2"):
        evidence.main(base)
    assert "--draft-release-dir" in capsys.readouterr().err

    complete = [
        *base,
        "--draft-release-dir", "release",
        "--gh", "gh.exe",
        "--apksigner", "apksigner.jar",
        "--evidence-output", "evidence.json",
        "--comment-output", "comment.md",
    ]
    with pytest.raises(SystemExit, match="2"):
        evidence.main([*complete, "--publication-projection-stdout"])
    assert "must be used together" in capsys.readouterr().err


def test_published_cli_is_strict_and_mutually_exclusive(capsys):
    base = [
        "--windows-dir", "windows",
        "--android-dir", "android",
        "--commit", COMMIT,
        "--run-url", RUN_URL,
        "--expected-android-cert-sha256", OWNER_CERT,
        "--require-live-published-release",
    ]
    with pytest.raises(SystemExit, match="2"):
        evidence.main([*base, "--no-fail"])
    assert "forbids --no-fail" in capsys.readouterr().err

    with pytest.raises(SystemExit, match="2"):
        evidence.main(base)
    error = capsys.readouterr().err
    assert "--published-release-dir" in error
    assert "--owner-approved-binding" in error

    with pytest.raises(SystemExit, match="2"):
        evidence.main([*base, "--require-live-final-draft"])
    assert "mutually exclusive" in capsys.readouterr().err


def test_published_cli_success_writes_exact_evidence_outputs(tmp_path, monkeypatch):
    runner, paths, owner_binding = _published_fixture(tmp_path)
    windows, android, release, apksigner = paths
    output_dir = tmp_path / "evidence-output"
    output_dir.mkdir()
    evidence_output = output_dir / "published-release.json"
    comment_output = output_dir / "issue-comment.md"
    monkeypatch.setattr(evidence, "SubprocessRunner", lambda: runner)

    result = evidence.main([
        "--windows-dir", str(windows),
        "--android-dir", str(android),
        "--commit", COMMIT,
        "--run-url", RUN_URL,
        "--expected-android-cert-sha256", OWNER_CERT,
        "--require-live-published-release",
        "--published-release-dir", str(release),
        "--gh", str(runner.gh),
        "--apksigner", str(apksigner),
        "--java", str(runner.java),
        "--owner-approved-binding", owner_binding,
        "--evidence-output", str(evidence_output),
        "--comment-output", str(comment_output),
    ])

    assert result == 0
    report = json.loads(evidence_output.read_text(encoding="utf-8"))
    assert report["owner_approved_artifact_binding_sha256"] == owner_binding
    assert report["published_release"]["id"] == 77
    assert report["publication_closure_binding_sha256"] == (
        evidence._compute_publication_closure_sha256(report)
    )
    assert "does not replace manual QA" in comment_output.read_text(encoding="utf-8")


def test_strict_output_writer_refuses_overwrite_without_partial_write(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    comment_path = tmp_path / "comment.md"
    comment_path.write_text("owner notes", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        evidence._write_new_outputs([
            (evidence_path, "{}\n"),
            (comment_path, "comment\n"),
        ])

    assert not evidence_path.exists()
    assert comment_path.read_text(encoding="utf-8") == "owner notes"


def test_strict_output_writer_cleans_current_partial_file_on_write_failure(
    tmp_path,
    monkeypatch,
):
    evidence_path = tmp_path / "evidence.json"
    comment_path = tmp_path / "comment.md"
    original_open = Path.open

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def write(self, content):
            self.stream.write(content[:1])
            self.stream.flush()
            raise OSError("simulated partial write")

        def __exit__(self, exc_type, exc, traceback):
            return self.stream.__exit__(exc_type, exc, traceback)

    def flaky_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return FailingWriter(stream) if path == comment_path else stream

    monkeypatch.setattr(Path, "open", flaky_open)

    with pytest.raises(ValueError, match="could not write evidence outputs"):
        evidence._write_new_outputs([
            (evidence_path, "{}\n"),
            (comment_path, "comment\n"),
        ])

    assert not evidence_path.exists()
    assert not comment_path.exists()


def test_live_outputs_must_not_pollute_verified_artifact_directories(tmp_path):
    windows, android, release, _ = _build_fixture(tmp_path)

    with pytest.raises(ValueError, match="outside verified artifact directories"):
        evidence._validate_output_locations(
            [release / "evidence.json", tmp_path / "comment.md"],
            artifact_dirs=[windows, android, release],
        )

    evidence._validate_output_locations(
        [tmp_path / "evidence.json", tmp_path / "comment.md"],
        artifact_dirs=[windows, android, release],
    )
