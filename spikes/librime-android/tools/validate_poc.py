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
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FLOATING = {"main", "master", "latest", "head", "develop", "dev"}
DATA_STATUS = "PINNED_PROJECT_OWNED_LICENSE_REVIEW_PENDING"
SOURCE_STATUS = (
    "SOURCE_AND_PATCH_CLOSURE_LOCKED_LICENSE_REVIEW_PENDING_BUILD_NOT_PROVEN"
)
EXPECTED_POLICY = {
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
    "OpenCC_ENABLE_DARTS": "OFF",
    "OpenCC_ENABLE_GTEST": "OFF",
    "OpenCC_ENABLE_BENCHMARK": "OFF",
    "OpenCC_BUILD_PYTHON": "OFF",
    "OpenCC_BUILD_TOOLS": "OFF",
    "OpenCC_BUILD_DATA": "OFF",
    "OpenCC_LIBRARY_ONLY_PATCH": "LOCKED_REPLAY_REQUIRED",
}


class ValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {relative}: {exc}") from exc
    require(isinstance(value, dict), f"{relative} must contain an object")
    return value


def locked_file(relative: Any, label: str) -> Path:
    require(isinstance(relative, str) and bool(relative), f"{label}.path is missing")
    path = (ROOT / relative).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ValidationError(f"{label}.path escapes the PoC directory") from exc
    require(path.is_file(), f"{label}.path is not a file: {relative}")
    return path


def require_sha(value: Any, label: str, pattern: re.Pattern[str]) -> None:
    require(isinstance(value, str) and pattern.fullmatch(value) is not None,
            f"{label} has an invalid digest")


def verify_digest(item: dict[str, Any], label: str) -> str:
    expected = item.get("content_sha256")
    require_sha(expected, f"{label}.content_sha256", SHA256)
    actual = hashlib.sha256(locked_file(item.get("path"), label).read_bytes()).hexdigest()
    require(actual == expected,
            f"{label} digest mismatch: expected {expected}, got {actual}")
    return actual


def validate_source_entry(item: Any, label: str) -> dict[str, Any]:
    require(isinstance(item, dict), f"{label} must be an object")
    repository = item.get("repository")
    require(isinstance(repository, str) and "/" in repository,
            f"{label}.repository is invalid")
    require_sha(item.get("sha"), f"{label}.sha", SHA40)
    tag = item.get("tag")
    if tag is not None:
        require(isinstance(tag, str) and tag.strip().lower() not in FLOATING,
                f"{label}.tag is floating or invalid")
    require(isinstance(item.get("license"), str) and bool(item["license"]),
            f"{label}.license is missing")
    return item


def validate_data(lock: dict[str, Any]) -> dict[str, str]:
    data = lock.get("data_inputs")
    require(isinstance(data, dict), "data_inputs must be an object")
    hashes: dict[str, str] = {}
    for name in ("schema", "dictionary", "default_config"):
        item = data.get(name)
        require(isinstance(item, dict), f"data_inputs.{name} must be an object")
        require(item.get("status") == DATA_STATUS,
                f"data_inputs.{name} has an unexpected status")
        require(isinstance(item.get("license"), str) and bool(item["license"]),
                f"data_inputs.{name}.license is missing")
        hashes[name] = verify_digest(item, f"data_inputs.{name}")

    schema = locked_file(data["schema"]["path"], "data_inputs.schema").read_text(
        encoding="utf-8"
    )
    dictionary = locked_file(
        data["dictionary"]["path"], "data_inputs.dictionary"
    ).read_text(encoding="utf-8")
    require("schema_id: clipvault_poc" in schema,
            "schema_id clipvault_poc is missing")
    require("dictionary: clipvault_poc" in schema,
            "schema dictionary binding is missing")
    require("enable_user_dict: false" in schema,
            "user dictionary learning must remain disabled")
    require("name: clipvault_poc" in dictionary,
            "dictionary name clipvault_poc is missing")
    return hashes


def validate_source_lock(lock: dict[str, Any]) -> None:
    source = load("A_ROUTE_SOURCE_LOCK.json")
    require(source.get("route") == "A_custom_librime_jni", "wrong A-route name")
    require(source.get("status") == SOURCE_STATUS,
            "A-route source lock overstates or understates evidence")

    librime = validate_source_entry(source.get("librime"), "source.librime")
    latest = lock["tracks"]["A_custom_librime_jni"]["latest_stable"]
    for field in ("repository", "tag", "sha", "license"):
        require(librime.get(field) == latest.get(field),
                f"source.librime.{field} disagrees with POC_LOCK")

    dependencies = source.get("dependencies")
    require(isinstance(dependencies, list) and bool(dependencies),
            "source.dependencies must be non-empty")
    names: set[str] = set()
    runtime = 0
    for index, raw in enumerate(dependencies):
        item = validate_source_entry(raw, f"source.dependencies[{index}]")
        name = item.get("name")
        require(isinstance(name, str) and bool(name) and name not in names,
                f"source dependency {index} has an invalid or duplicate name")
        names.add(name)
        included = item.get("runtime_included")
        require(isinstance(included, bool), f"{name}.runtime_included must be boolean")
        if included:
            runtime += 1
        else:
            require(isinstance(item.get("exclusion"), str) and bool(item["exclusion"]),
                    f"excluded dependency {name} needs a reason")
    require(runtime > 0, "A route has no runtime dependencies")

    patches = source.get("patches")
    require(isinstance(patches, list) and bool(patches), "source.patches must be non-empty")
    patch_names: set[str] = set()
    for index, raw in enumerate(patches):
        require(isinstance(raw, dict), f"source.patches[{index}] must be an object")
        name = raw.get("name")
        require(isinstance(name, str) and bool(name) and name not in patch_names,
                f"source patch {index} has an invalid or duplicate name")
        patch_names.add(name)
        require(raw.get("target_repository") == "BYVoid/OpenCC",
                f"source patch {name} has an unexpected target")
        require_sha(raw.get("target_sha"), f"source.patches[{index}].target_sha", SHA40)
        verify_digest(raw, f"source.patches[{index}]")
        require(raw.get("upstream_issue") != "UNFILED_POC_ONLY" or
                name == "opencc-library-only",
                "only the isolated OpenCC patch may be temporarily unfiled")

    require(source.get("planned_build_policy") == EXPECTED_POLICY,
            "A-route build policy drifted")
    require(isinstance(source.get("unresolved_items"), list) and
            bool(source["unresolved_items"]),
            "A-route unresolved evidence must remain explicit")


def validate_bridge() -> None:
    bridge = load("bridge/BRIDGE_CONTRACT_LOCK.json")
    require(bridge.get("format_version") == 2, "bridge lock format must be 2")
    require(bridge.get("status") == "HOST_CONTRACT_AND_PUBLIC_API_BACKEND_SOURCE",
            "bridge status is incorrect")
    require(bridge.get("native_librime_linked") is False,
            "bridge must not claim native linkage")
    require(bridge.get("production_integration_allowed") is False,
            "bridge must not allow production integration")
    require(bridge.get("language_standard") == "c++17",
            "bridge must use C++17")

    api = bridge.get("librime_api_source")
    require(isinstance(api, dict), "bridge.librime_api_source must be an object")
    require(api.get("repository") == "rime/librime" and
            api.get("path") == "src/rime_api.h",
            "bridge must bind the official public API header")
    require_sha(api.get("ref"), "bridge.librime_api_source.ref", SHA40)
    require_sha(api.get("git_blob_sha"), "bridge.librime_api_source.git_blob_sha", SHA40)
    require(api.get("syntax_check_required") is True and
            api.get("linked_runtime_proven") is False,
            "bridge API evidence flags are incorrect")

    contract = bridge.get("contract")
    require(isinstance(contract, dict), "bridge.contract must be an object")
    required_true = (
        "failed_initialize_calls_shutdown",
        "reset_requires_empty_state",
        "backend_uses_public_c_api_only",
        "reset_drains_unread_commit",
    )
    for key in required_true:
        require(contract.get(key) is True, f"bridge contract invariant missing: {key}")
    require(contract.get("unhandled_key_is_error") is False,
            "unhandled keys must not be engine errors")

    files = bridge.get("files")
    require(isinstance(files, list) and bool(files), "bridge.files must be non-empty")
    seen: set[str] = set()
    for index, item in enumerate(files):
        require(isinstance(item, dict), f"bridge.files[{index}] must be an object")
        path = item.get("path")
        require(isinstance(path, str) and path not in seen,
                f"bridge.files[{index}] has an invalid or duplicate path")
        seen.add(path)
        verify_digest(item, f"bridge.files[{index}]")


def validate_vectors(data_hashes: dict[str, str]) -> None:
    vectors = load("vectors/rime-vectors.json")
    privacy = vectors.get("privacy")
    require(isinstance(privacy, dict), "vectors.privacy must be an object")
    require(privacy.get("contains_personal_data") is False,
            "vectors must contain no personal data")
    require(privacy.get("typed_text_persistence_allowed") is False,
            "typed-text persistence must remain disabled")

    activation = vectors.get("activation")
    require(isinstance(activation, dict), "vectors.activation must be an object")
    require(activation.get("status") == "READY_FOR_LOCAL_BUILD_ONLY",
            "vectors must remain local-build-only")
    require(activation.get("required_schema_sha256") == data_hashes["schema"],
            "vector schema hash mismatch")
    require(activation.get("required_dictionary_sha256") == data_hashes["dictionary"],
            "vector dictionary hash mismatch")

    cases = vectors.get("vectors")
    require(isinstance(cases, list) and bool(cases), "vectors.vectors must be non-empty")
    ids: set[str] = set()
    for index, case in enumerate(cases):
        require(isinstance(case, dict), f"vector {index} must be an object")
        vector_id = case.get("id")
        require(isinstance(vector_id, str) and bool(vector_id) and vector_id not in ids,
                f"vector {index} has an invalid or duplicate id")
        ids.add(vector_id)
        keys = case.get("keys")
        require(isinstance(keys, list) and all(
            isinstance(key, str) and len(key) == 1 and key.isascii() for key in keys
        ), f"{vector_id}.keys must contain one-character ASCII strings")


def validate() -> None:
    lock = load("POC_LOCK.json")
    require(lock.get("format_version") == 2, "POC_LOCK format must be 2")
    clipvault = lock.get("clipvault")
    require(isinstance(clipvault, dict), "clipvault must be an object")
    require_sha(clipvault.get("base_sha"), "clipvault.base_sha", SHA40)
    require(clipvault.get("production_integration_allowed") is False,
            "production integration must remain disabled")

    gate = lock.get("license_gate")
    require(isinstance(gate, dict) and isinstance(gate.get("unresolved_items"), list),
            "license gate is malformed")
    if gate["unresolved_items"]:
        require(clipvault.get("binary_artifact_upload_allowed") is False,
                "binary upload must remain disabled while license items are unresolved")

    tracks = lock.get("tracks")
    require(isinstance(tracks, dict) and bool(tracks), "tracks must be non-empty")
    for track_name, track in tracks.items():
        require(isinstance(track, dict), f"track {track_name} must be an object")
        for release_name in ("latest_stable", "previous_stable", "stable"):
            if release_name in track:
                validate_source_entry(track[release_name],
                                      f"tracks.{track_name}.{release_name}")

    validate_source_lock(lock)
    validate_bridge()
    validate_vectors(validate_data(lock))


def main() -> int:
    try:
        validate()
    except ValidationError as exc:
        print(f"POC STATIC VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    print("POC STATIC VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
