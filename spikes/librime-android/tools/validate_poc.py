#!/usr/bin/env python3
"""Fail-closed static validation for the isolated ClipVault librime PoC."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "POC_LOCK.json"
SOURCE_LOCK_PATH = ROOT / "A_ROUTE_SOURCE_LOCK.json"
VECTORS_PATH = ROOT / "vectors" / "rime-vectors.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FLOATING_REFS = {"main", "master", "latest", "head", "develop", "dev"}
PINNED_DATA_STATUS = "PINNED_PROJECT_OWNED_LICENSE_REVIEW_PENDING"
EXPECTED_BUILD_POLICY = {
    "BUILD_SHARED_LIBS": "OFF",
    "BUILD_STATIC": "ON",
    "BUILD_TEST": "OFF",
    "BUILD_DATA": "OFF",
    "BUILD_SEPARATE_LIBS": "OFF",
    "ENABLE_LOGGING": "OFF",
    "ENABLE_TIMESTAMP": "OFF",
    "CMAKE_POSITION_INDEPENDENT_CODE": "ON",
    "CMAKE_DISABLE_FIND_PACKAGE_Snappy": "TRUE",
    "OpenCC_USE_SYSTEM_MARISA": "ON",
}


class ValidationError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path} must contain a JSON object")
    return value


def require_sha40(value: Any, label: str) -> None:
    if not isinstance(value, str) or SHA40.fullmatch(value) is None:
        raise ValidationError(f"{label} must be a lowercase 40-character Git SHA")


def require_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValidationError(f"{label} must be a lowercase SHA-256 digest")


def reject_floating_ref(value: Any, label: str) -> None:
    if isinstance(value, str) and value.strip().lower() in FLOATING_REFS:
        raise ValidationError(f"{label} uses forbidden floating ref {value!r}")


def resolve_locked_file(relative_path: Any, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValidationError(f"{label}.path is missing")
    candidate = (ROOT / relative_path).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise ValidationError(f"{label}.path escapes the PoC directory") from exc
    if not candidate.is_file():
        raise ValidationError(f"{label}.path is not a regular file: {relative_path}")
    return candidate


def iter_release_locks(lock: dict[str, Any]):
    tracks = lock.get("tracks")
    if not isinstance(tracks, dict) or not tracks:
        raise ValidationError("tracks must be a non-empty object")
    for track_name, track in tracks.items():
        if not isinstance(track, dict):
            raise ValidationError(f"track {track_name} must be an object")
        for release_name in ("latest_stable", "previous_stable", "stable"):
            release = track.get(release_name)
            if release is not None:
                if not isinstance(release, dict):
                    raise ValidationError(
                        f"tracks.{track_name}.{release_name} must be an object"
                    )
                yield track_name, release_name, release


def validate_data_inputs(lock: dict[str, Any]) -> dict[str, str]:
    data_inputs = lock.get("data_inputs")
    if not isinstance(data_inputs, dict):
        raise ValidationError("data_inputs must be an object")

    observed: dict[str, str] = {}
    for name in ("schema", "dictionary", "default_config"):
        item = data_inputs.get(name)
        if not isinstance(item, dict):
            raise ValidationError(f"data_inputs.{name} must be an object")
        if item.get("status") != PINNED_DATA_STATUS:
            raise ValidationError(f"data_inputs.{name} has an unexpected status")
        if not isinstance(item.get("license"), str) or not item["license"]:
            raise ValidationError(f"data_inputs.{name}.license is missing")
        expected = item.get("content_sha256")
        require_sha256(expected, f"data_inputs.{name}.content_sha256")
        path = resolve_locked_file(item.get("path"), f"data_inputs.{name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValidationError(
                f"data_inputs.{name} digest mismatch: expected {expected}, got {actual}"
            )
        observed[name] = actual

    schema_text = resolve_locked_file(
        data_inputs["schema"]["path"], "data_inputs.schema"
    ).read_text(encoding="utf-8")
    dictionary_text = resolve_locked_file(
        data_inputs["dictionary"]["path"], "data_inputs.dictionary"
    ).read_text(encoding="utf-8")
    if "schema_id: clipvault_poc" not in schema_text:
        raise ValidationError("schema does not declare schema_id clipvault_poc")
    if "dictionary: clipvault_poc" not in schema_text:
        raise ValidationError("schema does not bind dictionary clipvault_poc")
    if "enable_user_dict: false" not in schema_text:
        raise ValidationError("schema must keep user dictionary learning disabled")
    if "name: clipvault_poc" not in dictionary_text:
        raise ValidationError("dictionary does not declare name clipvault_poc")
    return observed


def validate_source_entry(entry: dict[str, Any], label: str) -> None:
    repository = entry.get("repository")
    if not isinstance(repository, str) or "/" not in repository:
        raise ValidationError(f"{label}.repository is invalid")
    require_sha40(entry.get("sha"), f"{label}.sha")
    tag = entry.get("tag")
    if tag is not None:
        if not isinstance(tag, str) or not tag.strip():
            raise ValidationError(f"{label}.tag is invalid")
        reject_floating_ref(tag, f"{label}.tag")
    license_value = entry.get("license")
    if not isinstance(license_value, str) or not license_value:
        raise ValidationError(f"{label}.license is missing")


def validate_source_lock(lock: dict[str, Any], source: dict[str, Any]) -> None:
    if source.get("route") != "A_custom_librime_jni":
        raise ValidationError("A_ROUTE_SOURCE_LOCK.json has the wrong route")
    if source.get("status") != "SOURCE_CLOSURE_IDENTIFIED_BUILD_NOT_PROVEN":
        raise ValidationError("A source lock must not claim a native build result")

    librime = source.get("librime")
    if not isinstance(librime, dict):
        raise ValidationError("A source lock librime entry must be an object")
    validate_source_entry(librime, "source_lock.librime")

    track = lock["tracks"]["A_custom_librime_jni"]
    latest = track["latest_stable"]
    for field in ("repository", "tag", "sha", "license"):
        if librime.get(field) != latest.get(field):
            raise ValidationError(
                f"source_lock.librime.{field} does not match POC_LOCK latest_stable"
            )

    dependencies = source.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValidationError("A source lock dependencies must be a non-empty array")
    names: set[str] = set()
    runtime_count = 0
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, dict):
            raise ValidationError(f"source dependency {index} must be an object")
        name = dependency.get("name")
        if not isinstance(name, str) or not name:
            raise ValidationError(f"source dependency {index} has no name")
        if name in names:
            raise ValidationError(f"duplicate source dependency: {name}")
        names.add(name)
        validate_source_entry(dependency, f"source_lock.dependencies[{index}]")
        runtime_included = dependency.get("runtime_included")
        if not isinstance(runtime_included, bool):
            raise ValidationError(f"{name}.runtime_included must be boolean")
        if runtime_included:
            runtime_count += 1
        elif not isinstance(dependency.get("exclusion"), str):
            raise ValidationError(f"excluded dependency {name} needs an exclusion reason")
    if runtime_count == 0:
        raise ValidationError("A source lock must identify runtime dependencies")

    policy = source.get("planned_build_policy")
    if policy != EXPECTED_BUILD_POLICY:
        raise ValidationError("A planned build policy drifted from the minimal closure")
    unresolved = source.get("unresolved_items")
    if not isinstance(unresolved, list) or not unresolved:
        raise ValidationError("A source lock must retain unresolved native evidence items")


def validate_bridge_contract(lock: dict[str, Any]) -> None:
    bridge_ref = lock.get("bridge_contract")
    if not isinstance(bridge_ref, dict):
        raise ValidationError("bridge_contract must be an object")
    path = resolve_locked_file(bridge_ref.get("path"), "bridge_contract")
    bridge = load_json(path)
    if bridge.get("status") != "HOST_CONTRACT_ONLY":
        raise ValidationError("bridge must remain explicitly host-contract-only")
    if bridge.get("native_librime_linked") is not False:
        raise ValidationError("bridge must not claim native librime linkage")
    if bridge.get("production_integration_allowed") is not False:
        raise ValidationError("bridge must not allow production integration")
    if bridge.get("language_standard") != "c++17":
        raise ValidationError("bridge language standard must match librime C++17")

    contract = bridge.get("contract")
    if not isinstance(contract, dict):
        raise ValidationError("bridge contract description is missing")
    if contract.get("unhandled_key_is_error") is not False:
        raise ValidationError("an unhandled Rime key must not be modeled as an engine error")
    if contract.get("failed_initialize_calls_shutdown") is not True:
        raise ValidationError("failed initialization cleanup invariant is missing")
    if contract.get("reset_requires_empty_state") is not True:
        raise ValidationError("reset invariant is missing")

    files = bridge.get("files")
    if not isinstance(files, list) or not files:
        raise ValidationError("bridge locked files must be a non-empty array")
    seen: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValidationError(f"bridge file {index} must be an object")
        relative = item.get("path")
        if not isinstance(relative, str) or relative in seen:
            raise ValidationError(f"bridge file {index} has an invalid or duplicate path")
        seen.add(relative)
        expected = item.get("content_sha256")
        require_sha256(expected, f"bridge.files[{index}].content_sha256")
        actual_path = resolve_locked_file(relative, f"bridge.files[{index}]")
        actual = hashlib.sha256(actual_path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValidationError(
                f"bridge file digest mismatch for {relative}: expected {expected}, got {actual}"
            )


def validate_lock(lock: dict[str, Any]) -> dict[str, str]:
    if lock.get("format_version") != 2:
        raise ValidationError("POC_LOCK.json format_version must be 2")
    clipvault = lock.get("clipvault")
    if not isinstance(clipvault, dict):
        raise ValidationError("clipvault must be an object")
    require_sha40(clipvault.get("base_sha"), "clipvault.base_sha")

    if clipvault.get("production_integration_allowed") is not False:
        raise ValidationError("production integration must remain disabled")

    license_gate = lock.get("license_gate")
    if not isinstance(license_gate, dict):
        raise ValidationError("license_gate must be an object")
    unresolved = license_gate.get("unresolved_items")
    if not isinstance(unresolved, list):
        raise ValidationError("license_gate.unresolved_items must be an array")
    if unresolved and clipvault.get("binary_artifact_upload_allowed") is not False:
        raise ValidationError(
            "binary artifact upload must remain disabled while license items are unresolved"
        )

    tracks = lock.get("tracks")
    if not isinstance(tracks, dict):
        raise ValidationError("tracks must be an object")
    for track_name, release_name, release in iter_release_locks(lock):
        validate_source_entry(release, f"tracks.{track_name}.{release_name}")

    validate_source_lock(lock, load_json(SOURCE_LOCK_PATH))
    validate_bridge_contract(lock)
    return validate_data_inputs(lock)


def validate_vectors(vectors: dict[str, Any], data_hashes: dict[str, str]) -> None:
    privacy = vectors.get("privacy")
    if not isinstance(privacy, dict):
        raise ValidationError("vectors.privacy must be an object")
    if privacy.get("contains_personal_data") is not False:
        raise ValidationError("PoC vectors must contain no personal data")
    if privacy.get("typed_text_persistence_allowed") is not False:
        raise ValidationError("typed-text persistence must remain disabled")

    activation = vectors.get("activation")
    if not isinstance(activation, dict):
        raise ValidationError("vectors.activation must be an object")
    if activation.get("status") != "READY_FOR_LOCAL_BUILD_ONLY":
        raise ValidationError("vectors must be limited to local builds")
    if activation.get("required_schema_sha256") != data_hashes["schema"]:
        raise ValidationError("vector schema digest does not match POC_LOCK.json")
    if activation.get("required_dictionary_sha256") != data_hashes["dictionary"]:
        raise ValidationError("vector dictionary digest does not match POC_LOCK.json")

    cases = vectors.get("vectors")
    if not isinstance(cases, list) or not cases:
        raise ValidationError("vectors.vectors must be a non-empty array")

    ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValidationError(f"vector at index {index} must be an object")
        vector_id = case.get("id")
        if not isinstance(vector_id, str) or not vector_id:
            raise ValidationError(f"vector at index {index} has no id")
        if vector_id in ids:
            raise ValidationError(f"duplicate vector id: {vector_id}")
        ids.add(vector_id)
        keys = case.get("keys")
        if not isinstance(keys, list) or not all(
            isinstance(key, str) and len(key) == 1 and key.isascii() for key in keys
        ):
            raise ValidationError(f"{vector_id}.keys must be one-character ASCII strings")


def main() -> int:
    try:
        lock = load_json(LOCK_PATH)
        data_hashes = validate_lock(lock)
        validate_vectors(load_json(VECTORS_PATH), data_hashes)
    except (OSError, UnicodeError, ValidationError) as exc:
        print(f"POC STATIC VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    print("POC STATIC VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
