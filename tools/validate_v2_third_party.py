#!/usr/bin/env python3
"""Validate v2 third-party locks and notices without granting distribution.

The manifest is intentionally JSON syntax stored as ``.yaml`` so it remains
valid YAML 1.2 while this check needs only the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "THIRD_PARTY_MANIFEST.yaml"
ANDROID_LOCK_PATH = "android/rime-engine-android/RIME_PRODUCTION_LOCK.json"
ANDROID_USER_CONSENT_LOCK_PATH = "android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json"
SHARED_LOCK_PATH = "shared-input/rime/RIME_ASSET_LOCK.json"
WINDOWS_LOCK_PATH = "windows/ime/rime/RIME_SDK_LOCK.json"
PRODUCTION_LOCKS = frozenset(
    {
        ANDROID_LOCK_PATH,
        ANDROID_USER_CONSENT_LOCK_PATH,
        SHARED_LOCK_PATH,
        WINDOWS_LOCK_PATH,
    }
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _read_json(root: Path, relative: str, errors: list[str]) -> Any | None:
    path = root / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing JSON-compatible lock/manifest: {relative}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"unreadable JSON-compatible lock/manifest {relative}: {exc}")
    return None


def _safe_relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_equal(
    errors: list[str], label: str, values: dict[str, object]
) -> None:
    distinct = {json.dumps(value, sort_keys=True) for value in values.values()}
    if len(distinct) != 1:
        rendered = ", ".join(f"{key}={value!r}" for key, value in values.items())
        errors.append(f"{label} drift: {rendered}")


def _component_map(manifest: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    raw_components = manifest.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        errors.append("manifest components must be a non-empty list")
        return {}
    components: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_components):
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            errors.append(f"manifest component {index} is not a named object")
            continue
        name = raw["name"]
        if name in components:
            errors.append(f"duplicate manifest component: {name}")
            continue
        components[name] = raw
    return components


def _validate_manifest_paths(
    root: Path,
    components: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    referenced_locks: set[str] = set()
    android_ime_build = root / "android/ime-app/build.gradle.kts"
    android_runtime_build = root / "android/app/build.gradle.kts"
    windows_build = root / "windows/ime/scripts/Build-ProductionIme.ps1"
    windows_package = root / "windows/ime/scripts/Package-ClipVaultIme.ps1"
    try:
        android_text = (
            android_ime_build.read_text(encoding="utf-8")
            + "\n"
            + android_runtime_build.read_text(encoding="utf-8")
        )
        windows_text = (
            windows_build.read_text(encoding="utf-8")
            + "\n"
            + windows_package.read_text(encoding="utf-8")
        ).replace("\\", "/")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"cannot inspect package license wiring: {exc}")
        android_text = ""
        windows_text = ""

    for name, component in components.items():
        lock_files = component.get("lock_files")
        if not isinstance(lock_files, list) or not lock_files:
            errors.append(f"{name}: lock_files must be a non-empty list")
        else:
            for raw_path in lock_files:
                path = _safe_relative_path(raw_path)
                if path is None:
                    errors.append(f"{name}: unsafe lock path {raw_path!r}")
                    continue
                referenced_locks.add(path)
                if path not in PRODUCTION_LOCKS:
                    errors.append(f"{name}: non-production lock referenced: {path}")
                if not (root / path).is_file():
                    errors.append(f"{name}: referenced lock is missing: {path}")

        assets = component.get("license_assets")
        if assets is None:
            allowed_non_payload_statuses = {
                "repository_not_shipped; extracted_archives_are_listed_and_hash_locked_separately",
                "owner_google_sdk_terms_and_notice_review_required; no local license text is asserted or redistributed",
            }
            if component.get("distribution_status") not in allowed_non_payload_statuses:
                errors.append(f"{name}: missing license_assets without a non-payload status")
            continue
        if not isinstance(assets, dict):
            errors.append(f"{name}: license_assets must be an object")
            continue
        repository = _safe_relative_path(assets.get("repository"))
        expected_hash = assets.get("repository_sha256")
        if repository is None or not isinstance(expected_hash, str):
            errors.append(f"{name}: repository license path/hash is incomplete")
        else:
            path = root / repository
            if not path.is_file():
                errors.append(f"{name}: repository license asset missing: {repository}")
            elif SHA256_RE.fullmatch(expected_hash) is None:
                errors.append(f"{name}: repository license SHA-256 is invalid")
            elif _sha256(path) != expected_hash:
                errors.append(f"{name}: repository license asset hash drifted: {repository}")

        android_asset = assets.get("android_package")
        if android_asset is not None:
            if _safe_relative_path(android_asset) is None:
                errors.append(f"{name}: unsafe Android package asset {android_asset!r}")
            elif android_asset not in android_text:
                errors.append(f"{name}: Android package asset is not verified by the build: {android_asset}")
        windows_asset = assets.get("windows_package")
        if windows_asset is not None:
            if _safe_relative_path(windows_asset) is None:
                errors.append(f"{name}: unsafe Windows package asset {windows_asset!r}")
            elif windows_asset not in windows_text:
                errors.append(f"{name}: Windows package asset is not staged: {windows_asset}")

    if referenced_locks != set(PRODUCTION_LOCKS):
        errors.append(
            "manifest must collectively reference exactly the production locks: "
            f"found {sorted(referenced_locks)}"
        )


def _validate_lock_consistency(
    root: Path,
    components: dict[str, dict[str, Any]],
    android: dict[str, Any],
    shared: dict[str, Any],
    windows: dict[str, Any],
    user_consent: dict[str, Any],
    errors: list[str],
) -> None:
    required_components = {
        "librime",
        "rime-pinyin-simp",
        "yaml-cpp",
        "LevelDB",
        "OpenCC",
        "marisa-trie",
        "fcitx5-android/prebuilt source",
        "com.google.android.gms:play-services-auth-api-phone",
    }
    missing = required_components - set(components)
    extra = set(components) - required_components
    if missing:
        errors.append(f"manifest missing production components: {sorted(missing)}")
    if extra:
        errors.append(f"manifest has unrecognized production component claims: {sorted(extra)}")
    if missing:
        return

    librime = components["librime"]
    android_librime = android.get("sources", {}).get("librime", {})
    windows_librime = windows.get("source", {})
    _require_equal(
        errors,
        "librime version",
        {
            "manifest": librime.get("version"),
            "android": android_librime.get("tag"),
            "windows": windows_librime.get("tag"),
        },
    )
    _require_equal(
        errors,
        "librime commit",
        {
            "manifest": librime.get("commit"),
            "android": android_librime.get("commit"),
            "windows": windows_librime.get("commit"),
        },
    )
    _require_equal(
        errors,
        "librime license",
        {
            "manifest": librime.get("license"),
            "android": android_librime.get("license"),
            "windows": windows_librime.get("license"),
        },
    )
    if COMMIT_RE.fullmatch(str(librime.get("commit", ""))) is None:
        errors.append("librime commit is not a full lowercase Git SHA")

    manifest_win = librime.get("artifacts", {}).get("windows_official_runtime", {})
    lock_win = windows.get("official_windows_asset", {})
    for key in ("name", "sha256", "size_bytes"):
        _require_equal(
            errors,
            f"Windows librime artifact {key}",
            {"manifest": manifest_win.get(key), "windows": lock_win.get(key)},
        )
    if lock_win.get("transitive_composition_status") != "not_enumerated_by_this_lock":
        errors.append("Windows librime lock must not imply an enumerated transitive closure")

    dictionary = components["rime-pinyin-simp"]
    android_dictionary = android.get("sources", {}).get("rime_pinyin_simp", {})
    shared_dictionary = shared.get("dictionary_source", {})
    for key in ("commit", "archive_sha256", "license"):
        _require_equal(
            errors,
            f"rime-pinyin-simp {key}",
            {
                "manifest": dictionary.get(key),
                "android": android_dictionary.get(key),
                "shared": shared_dictionary.get(key),
            },
        )
    if COMMIT_RE.fullmatch(str(dictionary.get("commit", ""))) is None:
        errors.append("rime-pinyin-simp commit is not a full lowercase Git SHA")
    if SHA256_RE.fullmatch(str(dictionary.get("archive_sha256", ""))) is None:
        errors.append("rime-pinyin-simp archive SHA-256 is invalid")

    shared_asset = shared_dictionary.get("repository_license_asset")
    shared_hash = shared_dictionary.get("repository_license_sha256")
    dictionary_assets = dictionary.get("license_assets", {})
    _require_equal(
        errors,
        "rime-pinyin-simp repository license asset",
        {"manifest": dictionary_assets.get("repository"), "shared": shared_asset},
    )
    _require_equal(
        errors,
        "rime-pinyin-simp repository license hash",
        {"manifest": dictionary_assets.get("repository_sha256"), "shared": shared_hash},
    )

    android_license_rows = {
        row.get("component"): row
        for row in android.get("licenses", [])
        if isinstance(row, dict) and isinstance(row.get("component"), str)
    }
    for name in ("librime", "rime-pinyin-simp", "yaml-cpp", "LevelDB", "OpenCC", "marisa-trie"):
        component = components[name]
        row = android_license_rows.get(name)
        if row is None:
            errors.append(f"Android lock is missing the {name} license row")
            continue
        selected_license = component.get("selected_distribution_option", component.get("license"))
        _require_equal(
            errors,
            f"Android {name} selected license",
            {"manifest": selected_license, "android": row.get("spdx")},
        )
        if component.get("version") is not None:
            _require_equal(
                errors,
                f"Android {name} version",
                {"manifest": component.get("version"), "android": row.get("version")},
            )
        if component.get("commit") is not None and name == "rime-pinyin-simp":
            _require_equal(
                errors,
                f"Android {name} commit",
                {"manifest": component.get("commit"), "android": row.get("commit")},
            )
        expected_asset = f"assets/rime/{row.get('asset')}"
        _require_equal(
            errors,
            f"Android {name} package license asset",
            {
                "manifest": component.get("license_assets", {}).get("android_package"),
                "android": expected_asset,
            },
        )

    marisa_row = android_license_rows.get("marisa-trie", {})
    if marisa_row.get("upstream_expression") != components["marisa-trie"].get("license"):
        errors.append("marisa-trie dual-license expression drifted")

    fcitx_manifest = components["fcitx5-android/prebuilt source"]
    fcitx_lock = android.get("sources", {}).get("fcitx5_android_prebuilt", {})
    _require_equal(
        errors,
        "fcitx5-android/prebuilt commit",
        {"manifest": fcitx_manifest.get("commit"), "android": fcitx_lock.get("commit")},
    )
    _require_equal(
        errors,
        "fcitx5-android parent commit",
        {"manifest": fcitx_manifest.get("parent_commit"), "android": fcitx_lock.get("parent_commit")},
    )
    if fcitx_lock.get("usage") != "archive_source_only_not_fcitx_runtime":
        errors.append("Android lock no longer proves that Fcitx runtime code is excluded")

    consent_name = "com.google.android.gms:play-services-auth-api-phone"
    consent_manifest = components[consent_name]
    if user_consent.get("format_version") != 1:
        errors.append("SMS User Consent dependency lock format_version must be 1")
    _require_equal(
        errors,
        "SMS User Consent component",
        {"manifest": consent_name, "lock": user_consent.get("component")},
    )
    _require_equal(
        errors,
        "SMS User Consent version",
        {
            "manifest": consent_manifest.get("version"),
            "lock": user_consent.get("version"),
        },
    )
    consent_version = str(consent_manifest.get("version", ""))
    expected_coordinate = f"{consent_name}:{consent_version}"
    try:
        runtime_gradle = (root / "android/app/build.gradle.kts").read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"cannot inspect Android Runtime dependencies: {exc}")
        runtime_gradle = ""
    if f'implementation("{expected_coordinate}")' not in runtime_gradle:
        errors.append(
            "Android Runtime does not use the exact locked SMS User Consent dependency: "
            f"{expected_coordinate}"
        )
    dependency_gate_markers = (
        "verifySmsUserConsentDependency",
        f'"{expected_coordinate}@aar"',
        f'"{expected_coordinate}@pom"',
        "smsUserConsentAarSha256",
        "smsUserConsentPomSha256",
        'setOf("preDebugBuild", "preReleaseBuild", "preOtpSmsRelayBuild", "check")',
    )
    missing_gate_markers = [
        marker for marker in dependency_gate_markers if marker not in runtime_gradle
    ]
    if missing_gate_markers:
        errors.append(
            "Android Runtime SMS User Consent dependency hash gate is incomplete: "
            + ", ".join(missing_gate_markers)
        )

    consent_artifacts = consent_manifest.get("artifacts", {})
    for key, lock_key in (("aar", "artifact"), ("pom", "pom")):
        manifest_artifact = consent_artifacts.get(key, {})
        lock_artifact = user_consent.get(lock_key, {})
        for manifest_key, lock_field in (
            ("name", "file"),
            ("sha256", "sha256"),
            ("size_bytes", "size_bytes"),
        ):
            _require_equal(
                errors,
                f"SMS User Consent {key.upper()} {manifest_key}",
                {
                    "manifest": manifest_artifact.get(manifest_key),
                    "lock": lock_artifact.get(lock_field),
                },
            )
        if SHA256_RE.fullmatch(str(lock_artifact.get("sha256", ""))) is None:
            errors.append(f"SMS User Consent {key.upper()} SHA-256 is invalid")
        if not isinstance(lock_artifact.get("size_bytes"), int) or lock_artifact.get(
            "size_bytes", 0
        ) <= 0:
            errors.append(f"SMS User Consent {key.upper()} size is invalid")

    consent_license = user_consent.get("license", {})
    consent_license_source = consent_manifest.get("license_source", {})
    _require_equal(
        errors,
        "SMS User Consent license name",
        {
            "manifest": consent_manifest.get("license"),
            "lock": consent_license.get("name"),
        },
    )
    _require_equal(
        errors,
        "SMS User Consent terms URL",
        {
            "manifest": consent_license_source.get("terms_url"),
            "lock": consent_license.get("url"),
        },
    )
    expected_pom_url = (
        consent_manifest.get("upstream", "")
        + consent_artifacts.get("pom", {}).get("name", "")
    )
    if consent_license_source.get("pom") != expected_pom_url:
        errors.append("SMS User Consent POM provenance URL drifted")
    if consent_license_source.get("repository_copy") is not None:
        errors.append("SMS User Consent manifest must not assert a local SDK terms copy")
    if consent_license.get("source") != "artifact POM":
        errors.append("SMS User Consent lock must identify its license source as artifact POM")
    if user_consent.get("repository") != "https://dl.google.com/dl/android/maven2/":
        errors.append("SMS User Consent lock must use the official Google Maven repository")
    if not isinstance(user_consent.get("selection_reason"), str) or not user_consent.get(
        "selection_reason", ""
    ).strip():
        errors.append("SMS User Consent lock is missing its version selection reason")
    if user_consent.get("purpose") != "Explicit, one-message SMS User Consent fallback only":
        errors.append("SMS User Consent dependency purpose drifted")
    if set(user_consent.get("forbidden_permissions", [])) != {
        "android.permission.READ_SMS",
        "android.permission.RECEIVE_SMS",
    }:
        errors.append("SMS User Consent lock must forbid READ_SMS and RECEIVE_SMS")
    if consent_manifest.get("distribution_status") != (
        "owner_google_sdk_terms_and_notice_review_required; "
        "no local license text is asserted or redistributed"
    ):
        errors.append("SMS User Consent distribution must remain Owner-blocked")

    native_archives = android.get("native_archives")
    if not isinstance(native_archives, list) or not native_archives:
        errors.append("Android native archive hash inventory is empty")
    else:
        seen_paths: set[str] = set()
        for row in native_archives:
            if not isinstance(row, dict):
                errors.append("Android native archive row is not an object")
                continue
            path = row.get("path")
            digest = row.get("sha256")
            if not isinstance(path, str) or path in seen_paths:
                errors.append(f"Android native archive path is invalid or duplicated: {path!r}")
            else:
                seen_paths.add(path)
            if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
                errors.append(f"Android native archive SHA-256 is invalid: {path!r}")

    canonical_assets = shared.get("canonical_assets")
    dictionary_assets_locked = shared_dictionary.get("assets")
    allowed = shared.get("allowed_staged_files")
    if not isinstance(canonical_assets, dict) or not isinstance(dictionary_assets_locked, dict):
        errors.append("shared Rime lock asset maps are incomplete")
    else:
        expected_allowed = sorted([*canonical_assets, *dictionary_assets_locked])
        if sorted(allowed or []) != expected_allowed:
            errors.append("shared Rime allowed staged file set drifted")
        shared_root = root / "shared-input/rime"
        for name, digest in {**canonical_assets, **dictionary_assets_locked}.items():
            path = shared_root / name
            if path.is_file() and _sha256(path) != digest:
                errors.append(f"shared Rime asset hash drifted: {name}")

    windows_license = windows.get("license_asset", {})
    _require_equal(
        errors,
        "Windows librime repository license asset",
        {
            "manifest": librime.get("license_assets", {}).get("repository"),
            "windows": windows_license.get("path"),
        },
    )
    _require_equal(
        errors,
        "Windows librime repository license hash",
        {
            "manifest": librime.get("license_assets", {}).get("repository_sha256"),
            "windows": windows_license.get("sha256"),
        },
    )

    for name in ("yaml-cpp", "LevelDB", "OpenCC", "marisa-trie"):
        mode = components[name].get("mode", {}).get("windows")
        if mode != "not-enumerated-by-current-Windows-binary-lock":
            errors.append(f"{name}: Windows composition must remain explicitly unasserted")


def _validate_project_license(
    root: Path, project_license: object, errors: list[str]
) -> bool:
    """Validate the explicit Owner-license governance states.

    ``internal_only`` is a completed Owner decision for local use, but it does
    not grant a license or permit distribution.  ``approved`` remains the only
    distribution-enabled state and is valid only when its license is a real
    repository file.  A pending decision also remains fail-closed.
    """

    if not isinstance(project_license, dict):
        errors.append("manifest project_license must be an object")
        return False
    status = project_license.get("status")
    license_file = project_license.get("license_file")
    distribution_allowed = project_license.get("distribution_allowed")
    if status == "owner_decision_required":
        if license_file is not None:
            errors.append("pending project license_file must be null")
        if distribution_allowed is not False:
            errors.append("pending project distribution_allowed must be false")
        return False
    if status == "internal_only":
        if license_file is not None:
            errors.append("internal-only project license_file must be null")
        if distribution_allowed is not False:
            errors.append("internal-only project distribution_allowed must be false")
        return False
    if status == "approved":
        relative = _safe_relative_path(license_file)
        if relative is None:
            errors.append("approved project license_file must be a safe repository path")
        elif not (root / relative).is_file():
            errors.append(f"approved project license file is missing: {relative}")
        if distribution_allowed is not True:
            errors.append("approved project distribution_allowed must be true")
        return relative is not None and (root / relative).is_file() and distribution_allowed is True
    errors.append(
        "project license status must be owner_decision_required, internal_only, or approved"
    )
    return False


def _validate_notices(
    root: Path,
    project_license: dict[str, Any],
    distribution_allowed: bool,
    errors: list[str],
) -> None:
    try:
        root_notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        android_notice = (
            root / "android/rime-engine-android/src/main/assets/third_party/NOTICE.txt"
        ).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"cannot read v2 notice assets: {exc}")
        return
    root_markers = (
        "ClipVault Personal v1.6.0 Windows artifacts",
        "## v2 daily-use candidate status",
        "### v2 Android Runtime candidate",
        "com.google.android.gms:play-services-auth-api-phone:18.2.0",
        "https://developer.android.com/studio/terms.html",
        "does not assert that those web terms may be copied or redistributed",
        "### v2 Android IME candidate",
        "### v2 Windows IME candidate",
        "### v2 Owner-controlled internal daily-use and distribution gates",
    )
    for marker in root_markers:
        if marker not in root_notice:
            errors.append(f"root third-party notice is missing status marker: {marker}")
    status = project_license.get("status")
    license_file = project_license.get("license_file")
    expected_status = f"project_license.status: {status}"
    expected_license_file = f"license_file: {license_file or 'null'}"
    for marker in (expected_status, expected_license_file):
        if marker not in root_notice:
            errors.append(
                "root third-party notice does not match the project license state: "
                f"{marker}"
            )
    expected_distribution = f"distribution_allowed: {str(distribution_allowed).lower()}"
    if expected_distribution not in root_notice:
        errors.append(
            "root third-party notice does not match the project distribution state: "
            f"{expected_distribution}"
        )
    if not distribution_allowed and "not an approval to distribute" not in root_notice:
        errors.append("root third-party notice is missing the pending-approval warning")
    if distribution_allowed and "not an approval to distribute" in root_notice:
        errors.append("root third-party notice still claims that approved distribution is blocked")
    if distribution_allowed and "does not yet have an Owner-selected root license file" in root_notice:
        errors.append("root third-party notice still claims that the approved license is missing")
    internal_warning = "No license is granted to third parties"
    if status == "internal_only" and internal_warning not in root_notice:
        errors.append("root third-party notice is missing the internal-only license warning")
    if status == "approved" and internal_warning in root_notice:
        errors.append("root third-party notice still claims that no third-party license is granted")
    android_markers = (
        "assets/rime/third_party",
        "BSD-2-Clause OR LGPL-2.1-or-later",
        "Fcitx5 itself is not linked",
        "Owner notice review remain release gates",
    )
    for marker in android_markers:
        if marker not in android_notice:
            errors.append(f"Android third-party notice is missing marker: {marker}")
    if not (root / "third_party/licenses/README-v2-rime.md").is_file():
        errors.append("missing v2 Rime license review-copy provenance note")


def validate(root: Path = ROOT) -> list[str]:
    """Return deterministic validation errors; an empty list is governance-valid."""

    root = root.resolve()
    errors: list[str] = []
    manifest_raw = _read_json(root, MANIFEST_PATH, errors)
    android_raw = _read_json(root, ANDROID_LOCK_PATH, errors)
    user_consent_raw = _read_json(root, ANDROID_USER_CONSENT_LOCK_PATH, errors)
    shared_raw = _read_json(root, SHARED_LOCK_PATH, errors)
    windows_raw = _read_json(root, WINDOWS_LOCK_PATH, errors)
    if not all(
        isinstance(value, dict)
        for value in (
            manifest_raw,
            android_raw,
            user_consent_raw,
            shared_raw,
            windows_raw,
        )
    ):
        return errors
    manifest = manifest_raw
    android = android_raw
    user_consent = user_consent_raw
    shared = shared_raw
    windows = windows_raw
    if manifest.get("format_version") != 1:
        errors.append("manifest format_version must be 1")
    if manifest.get("maintained_against_locked_inputs") is not True:
        errors.append("manifest must state that it is maintained against locked inputs")
    if manifest.get("generated_from_locked_inputs") is True:
        errors.append("manifest must not claim machine generation when it is review-maintained")
    distribution_allowed = _validate_project_license(
        root, manifest.get("project_license"), errors
    )

    components = _component_map(manifest, errors)
    _validate_manifest_paths(root, components, errors)
    _validate_lock_consistency(
        root,
        components,
        android,
        shared,
        windows,
        user_consent,
        errors,
    )
    project_license = manifest.get("project_license")
    _validate_notices(
        root,
        project_license if isinstance(project_license, dict) else {},
        distribution_allowed,
        errors,
    )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    errors = validate(args.root)
    manifest_errors: list[str] = []
    manifest = _read_json(args.root.resolve(), MANIFEST_PATH, manifest_errors)
    project_license_status = (
        manifest.get("project_license", {}).get("status")
        if isinstance(manifest, dict)
        and isinstance(manifest.get("project_license"), dict)
        else None
    )
    distribution_allowed = bool(
        isinstance(manifest, dict)
        and isinstance(manifest.get("project_license"), dict)
        and manifest["project_license"].get("status") == "approved"
        and manifest["project_license"].get("distribution_allowed") is True
        and isinstance(manifest["project_license"].get("license_file"), str)
        and (args.root.resolve() / manifest["project_license"]["license_file"]).is_file()
    )
    report = {
        "status": "pass" if not errors else "blocked",
        "project_license_status": project_license_status,
        "distribution_allowed": distribution_allowed,
        "errors": errors,
        "scope_note": (
            "A pass proves repository lock/notice and project-license-state consistency only. "
            "It does not sign artifacts or replace candidate-specific Owner approval."
        ),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif errors:
        print("V2 THIRD-PARTY GOVERNANCE VALIDATION BLOCKED")
        for error in errors:
            print(f"- {error}")
        print(report["scope_note"])
    else:
        print("V2 THIRD-PARTY GOVERNANCE LOCKS/NOTICES CONSISTENT")
        print(
            "Project license state: "
            f"{project_license_status}; distribution_allowed: "
            f"{str(distribution_allowed).lower()}"
        )
        print(report["scope_note"])
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
