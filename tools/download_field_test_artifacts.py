#!/usr/bin/env python3
"""Download and unpack v1.7 field-test candidate artifacts for Issue #82.

This helper bridges the gap between the read-only GitHub artifact inventory and
the local manifest/checksum verifier. It downloads only the expected Windows
and Android release-candidate artifacts for a workflow run, extracts flat ZIP
members into separate platform directories, and can optionally run the existing
dry-run manifest verification.

It does not trigger workflows, install apps, run device QA, edit GitHub issues,
sign or publish releases, close Issue #82/#36, or claim v1.7 stable.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "selinyi123/clipvault-personal"
WINDOWS_ARTIFACT_NAME = "clipvault-windows-release-candidate"
ANDROID_ARTIFACT_NAME = "clipvault-android-release-candidate"
EXPECTED_WINDOWS_ARTIFACT_NAME = WINDOWS_ARTIFACT_NAME
EXPECTED_ANDROID_ARTIFACT_NAME = ANDROID_ARTIFACT_NAME
ARTIFACT_TARGETS = {
    ANDROID_ARTIFACT_NAME: "android",
    WINDOWS_ARTIFACT_NAME: "windows",
}
URL_RE = re.compile(r"https://[^\s)>\]}\"']+")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
TOKEN_PARAM_RE = re.compile(r"(?i)([?&](?:token|sig|signature|access_token|jwt)=)[^&\s]+")


@dataclass(frozen=True)
class Artifact:
    name: str
    artifact_id: int
    archive_download_url: str
    digest: str | None = None
    expired: bool = False
    size_in_bytes: int | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class DownloadedArtifact:
    name: str
    artifact_id: int
    target_dir: Path
    zip_size_bytes: int
    extracted_files: tuple[str, ...]
    digest: str = ""
    digest_verified: bool = False
    manifest_verified: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "artifact_id": self.artifact_id,
            "target_dir": str(self.target_dir),
            "zip_size_bytes": self.zip_size_bytes,
            "extracted_files": list(self.extracted_files),
            "digest": self.digest,
            "digest_verified": self.digest_verified,
            "manifest_verified": self.manifest_verified,
        }


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: str = ""


Runner = Callable[[list[str]], CommandResult]


def scope_note() -> str:
    return (
        "Candidate artifact download only. When --verify-manifests is used, "
        "this also runs local dry-run manifest/checksum verification. It does "
        "not replace signed/final release artifacts, Owner/manual device QA, "
        "Issue #36 release evidence, or Owner approval. "
        "The Android unsigned release APK remains packaging evidence, not the "
        "signed install package."
    )


def _repo_path(repo: str) -> str:
    repo = repo.strip()
    if repo.count("/") != 1:
        raise ValueError("repo must be in owner/name form")
    owner, name = repo.split("/")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    if not owner or not name or any(ch not in allowed for ch in owner + name):
        raise ValueError("repo contains unsupported characters")
    return repo


def _redacted_error(exc: BaseException) -> str:
    text = str(exc)
    text = BEARER_RE.sub("Bearer <redacted>", text)
    text = TOKEN_PARAM_RE.sub(r"\1<redacted>", text)

    def redact_url(match: re.Match[str]) -> str:
        parsed = urllib.parse.urlsplit(match.group(0))
        if parsed.query or parsed.fragment:
            return urllib.parse.urlunsplit((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "<redacted>",
                "",
            ))
        return match.group(0)

    text = URL_RE.sub(redact_url, text)
    return text[:497] + "..." if len(text) > 500 else text


def _validate_download_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError("artifact download URL must use https")
    if parsed.username or parsed.password:
        raise ValueError("artifact download URL must not contain user info")
    return url


def _assert_read_only_gh_api(args: list[str]) -> None:
    if len(args) != 3 or args[0:2] != ["gh", "api"]:
        raise ValueError("artifact downloader only supports `gh api <path>` GET commands")
    path = args[2]
    if not (
        "/actions/runs/" in path and path.endswith("/artifacts?per_page=100")
        or "/actions/artifacts/" in path and path.endswith("/zip")
    ):
        raise ValueError(f"refusing unexpected gh api path: {path}")


def run_command(args: list[str]) -> CommandResult:
    _assert_read_only_gh_api(args)
    completed = subprocess.run(args, check=False, capture_output=True)
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr.decode("utf-8", errors="replace"),
    )


def _run_gh_api_bytes(
    runner: Runner,
    path: str,
    *,
    label: str,
    attempts: int = 3,
) -> bytes:
    last_error = ""
    for _ in range(max(1, attempts)):
        result = runner(["gh", "api", path])
        if result.returncode == 0:
            return result.stdout
        last_error = result.stderr.strip() or result.stdout.decode("utf-8", errors="replace").strip()
    raise RuntimeError(f"{label} failed after {max(1, attempts)} attempt(s): {last_error}")


def _run_gh_api_json(runner: Runner, path: str, *, label: str) -> object:
    payload = _run_gh_api_bytes(runner, path, label=label)
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} did not return valid JSON") from exc


def _artifact_from_json(row: Mapping[str, object], repo: str) -> Artifact | None:
    name = row.get("name")
    artifact_id = row.get("id")
    if not isinstance(name, str) or not isinstance(artifact_id, int):
        return None
    archive_url = row.get("archive_download_url")
    if not isinstance(archive_url, str) or not archive_url:
        archive_url = f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    size = row.get("size_in_bytes")
    return Artifact(
        name=name,
        artifact_id=artifact_id,
        archive_download_url=archive_url,
        digest=row.get("digest") if isinstance(row.get("digest"), str) else None,
        expired=row.get("expired") is True,
        size_in_bytes=size if isinstance(size, int) else None,
        expires_at=row.get("expires_at") if isinstance(row.get("expires_at"), str) else None,
    )


def fetch_artifacts(runner: Runner, *, repo: str, run_id: int) -> list[Artifact]:
    repo = _repo_path(repo)
    data = _run_gh_api_json(
        runner,
        f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100",
        label="workflow artifact inventory",
    )
    artifacts = data.get("artifacts") if isinstance(data, dict) else None
    if not isinstance(artifacts, list):
        raise RuntimeError("workflow artifact inventory JSON is missing artifacts[]")
    result: list[Artifact] = []
    for row in artifacts:
        if isinstance(row, dict):
            artifact = _artifact_from_json(row, repo)
            if artifact is not None:
                result.append(artifact)
    return result


def _as_artifact(row: Artifact | Mapping[str, object], repo: str = DEFAULT_REPO) -> Artifact | None:
    if isinstance(row, Artifact):
        return row
    if isinstance(row, Mapping):
        return _artifact_from_json(row, repo)
    return None


def select_required_artifacts(
    artifacts: Iterable[Artifact | Mapping[str, object]],
) -> dict[str, Artifact]:
    by_name: dict[str, list[Artifact]] = {name: [] for name in ARTIFACT_TARGETS}
    for row in artifacts:
        artifact = _as_artifact(row)
        if artifact is not None and artifact.name in by_name:
            by_name[artifact.name].append(artifact)

    selected: dict[str, Artifact] = {}
    problems: list[str] = []
    for name, rows in by_name.items():
        if not rows:
            problems.append(f"missing required artifact: {name}")
            continue
        usable = [row for row in rows if not row.expired]
        if not usable:
            problems.append(f"required artifact is expired: {name}")
            continue
        if len(usable) > 1:
            problems.append(f"duplicate required artifact name: {name}")
            continue
        selected[name] = usable[0]
    if problems:
        raise ValueError("; ".join(problems))
    return selected


def _expected_digest_hex(digest: str | None) -> str | None:
    if not digest:
        return None
    value = digest.lower().strip()
    if value.startswith("sha256:"):
        value = value.split(":", 1)[1]
    if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value):
        return value
    raise ValueError(f"unsupported artifact digest: {digest}")


def _verify_zip_digest(artifact: Artifact, zip_path: Path) -> bool:
    expected = _expected_digest_hex(artifact.digest)
    if expected is None:
        return False
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(
            f"ZIP digest mismatch for {artifact.name}: expected sha256:{expected}, got sha256:{actual}"
        )
    return True


def _prepare_output_dir(output_dir: Path, *, clean: bool) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        if not clean:
            raise ValueError(f"{output_dir} is not empty; pass --clean/--force to replace it")
        resolved = output_dir.resolve()
        if resolved == Path(resolved.anchor):
            raise ValueError(f"refusing to clean filesystem root: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def _safe_member_target(destination: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or len(pure.parts) != 1:
        raise ValueError(f"unsafe ZIP member path: {member_name!r}")
    if any(part in {"", ".", ".."} or ":" in part for part in pure.parts):
        raise ValueError(f"unsafe ZIP member path: {member_name!r}")
    target = destination.joinpath(*pure.parts).resolve()
    root = destination.resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root):
        raise ValueError(f"unsafe ZIP member path: {member_name!r}")
    return target


def safe_extract_flat_zip(zip_path: Path, destination: Path) -> list[str]:
    extracted: list[str] = []
    seen_targets: set[Path] = set()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"corrupt ZIP member: {bad_member}")
        for info in archive.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError(f"unsafe ZIP member path: {info.filename!r}")
            if info.is_dir():
                raise ValueError(f"unsafe ZIP member path: {info.filename!r}")
            target = _safe_member_target(destination, info.filename)
            if target in seen_targets:
                raise ValueError(f"duplicate ZIP member path: {info.filename!r}")
            seen_targets.add(target)
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(str(target.relative_to(destination)))
    if not extracted:
        raise ValueError("artifact ZIP did not contain any files")
    return sorted(extracted)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def resolve_artifact_download_url(
    archive_download_url: str,
    *,
    token: str,
    timeout: int = 60,
) -> str:
    request = urllib.request.Request(
        archive_download_url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            location = response.headers.get("Location")
            if location:
                return _validate_download_url(location)
            return _validate_download_url(archive_download_url)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            location = exc.headers.get("Location")
            if location:
                return _validate_download_url(location)
        raise


def _download_url_to_file(url: str, path: Path, *, timeout: int) -> None:
    url = _validate_download_url(url)
    deadline = time.monotonic() + timeout
    socket_timeout = max(1, min(timeout, 10))
    with urllib.request.urlopen(url, timeout=socket_timeout) as response, path.open("wb") as out:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"download exceeded {timeout} second timeout")
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def _download_with_retries(url: str, path: Path, *, timeout: int, retries: int) -> None:
    last_error: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            _download_url_to_file(url, path, timeout=timeout)
            return
        except Exception as exc:  # pragma: no cover - network boundary
            last_error = exc
    assert last_error is not None
    raise last_error


def _load_field_test_evidence():
    script = ROOT / "tools" / "field_test_evidence.py"
    spec = importlib.util.spec_from_file_location("field_test_evidence", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_manifests(
    *,
    output_dir: Path,
    source_version: str,
    target_commit: str | None,
) -> bool:
    if not target_commit:
        raise ValueError("--verify-manifests requires --target-commit")
    output_dir = output_dir.resolve()
    evidence = _load_field_test_evidence()
    evidence.verify_candidate_artifacts(
        windows_dir=output_dir / "windows",
        android_dir=output_dir / "android",
        source_version=source_version,
        commit=target_commit,
    )
    return True


def download_selected_artifacts(
    *,
    selected: Mapping[str, Artifact],
    output_dir: Path,
    token: str,
    timeout: int,
    retries: int,
    clean: bool,
    verify_zip_digest: bool,
    verify_manifests: bool,
    source_version: str,
    target_commit: str | None,
) -> list[DownloadedArtifact]:
    output_dir = output_dir.resolve()
    _prepare_output_dir(output_dir, clean=clean)
    results: list[DownloadedArtifact] = []
    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for name in sorted(ARTIFACT_TARGETS):
            artifact = selected[name]
            platform_dir = output_dir / ARTIFACT_TARGETS[name]
            platform_dir.mkdir(parents=True, exist_ok=True)
            zip_path = temp_dir / f"{name}.zip"
            signed_url = resolve_artifact_download_url(
                artifact.archive_download_url,
                token=token,
                timeout=timeout,
            )
            _download_with_retries(signed_url, zip_path, timeout=timeout, retries=retries)
            digest_verified = _verify_zip_digest(artifact, zip_path) if verify_zip_digest else False
            extracted = safe_extract_flat_zip(zip_path, platform_dir)
            results.append(DownloadedArtifact(
                name=name,
                artifact_id=artifact.artifact_id,
                target_dir=platform_dir,
                zip_size_bytes=zip_path.stat().st_size,
                extracted_files=tuple(extracted),
                digest=artifact.digest or "",
                digest_verified=digest_verified,
                manifest_verified=False,
            ))
    if verify_manifests:
        _verify_manifests(
            output_dir=output_dir,
            source_version=source_version,
            target_commit=target_commit,
        )
        results = [
            DownloadedArtifact(
                name=row.name,
                artifact_id=row.artifact_id,
                target_dir=row.target_dir,
                zip_size_bytes=row.zip_size_bytes,
                extracted_files=row.extracted_files,
                digest=row.digest,
                digest_verified=row.digest_verified,
                manifest_verified=True,
            )
            for row in results
        ]
    return results


def _gh_auth_token() -> str:
    completed = subprocess.run(
        ["gh", "auth", "token"],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "gh auth token failed")
    token = completed.stdout.strip()
    if not token:
        raise RuntimeError("gh auth token returned an empty token")
    return token


def build_summary(
    *,
    repo: str,
    run_id: int,
    output_dir: Path,
    artifacts: list[DownloadedArtifact],
    verification: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    return {
        "repo": repo,
        "run_id": run_id,
        "output_dir": str(output_dir),
        "artifacts": [artifact.as_dict() for artifact in artifacts],
        "verification": verification or {},
        "scope_note": scope_note(),
    }


def _render_text(summary: dict[str, object]) -> str:
    lines = [
        "ClipVault field-test artifact download",
        f"repo: {summary['repo']}",
        f"run_id: {summary['run_id']}",
        f"output_dir: {summary['output_dir']}",
        "",
        "Artifacts:",
    ]
    for artifact in summary["artifacts"]:
        assert isinstance(artifact, dict)
        lines.append(
            "- "
            f"{artifact['name']} -> {artifact['target_dir']} "
            f"({artifact['zip_size_bytes']} zip bytes)"
        )
        if artifact.get("digest"):
            lines.append(f"  digest: {artifact['digest']} verified={artifact['digest_verified']}")
        for file_name in artifact["extracted_files"]:
            lines.append(f"  - {file_name}")
    verification = summary.get("verification")
    if isinstance(verification, dict) and verification:
        lines.extend(["", "Verification:"])
        for key, names in verification.items():
            lines.append(f"- {key}: " + ", ".join(str(name) for name in names))
    lines.extend(["", str(summary["scope_note"])])
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download v1.7 field-test candidate artifacts from a GitHub Actions run.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--force", "--clean", dest="force", action="store_true")
    parser.add_argument("--verify-manifests", "--verify", dest="verify_manifests", action="store_true")
    parser.add_argument("--verify-zip-digest", action="store_true", default=True)
    parser.add_argument("--source-version", default="1.6.0")
    parser.add_argument("--target-commit", "--commit", dest="target_commit")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.verify_manifests and not args.target_commit:
        parser.error("--verify-manifests requires --target-commit")

    try:
        repo = _repo_path(args.repo)
        output_dir = args.output_dir.resolve()
        artifacts = fetch_artifacts(run_command, repo=repo, run_id=args.run_id)
        selected = select_required_artifacts(artifacts)
        downloaded = download_selected_artifacts(
            selected=selected,
            output_dir=output_dir,
            token=_gh_auth_token(),
            timeout=args.timeout,
            retries=args.retries,
            clean=args.force,
            verify_zip_digest=args.verify_zip_digest,
            verify_manifests=args.verify_manifests,
            source_version=args.source_version,
            target_commit=args.target_commit,
        )
        verification = None
        if args.verify_manifests:
            verification = {
                "manifest_verified_artifacts": [row.name for row in downloaded],
            }
        summary = build_summary(
            repo=repo,
            run_id=args.run_id,
            output_dir=output_dir,
            artifacts=downloaded,
            verification=verification,
        )
    except Exception as exc:  # pragma: no cover - CLI boundary
        print(f"field-test artifact download failed: {_redacted_error(exc)}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(_render_text(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
