#!/usr/bin/env python3
"""Fail-closed static validation for the isolated ClipVault librime PoC."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "POC_LOCK.json"
VECTORS_PATH = ROOT / "vectors" / "rime-vectors.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
FLOATING_REFS = {"main", "master", "latest", "head", "develop", "dev"}


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


def require_sha(value: Any, label: str) -> None:
    if not isinstance(value, str) or SHA40.fullmatch(value) is None:
        raise ValidationError(f"{label} must be a lowercase 40-character Git SHA")


def reject_floating_ref(value: Any, label: str) -> None:
    if isinstance(value, str) and value.strip().lower() in FLOATING_REFS:
        raise ValidationError(f"{label} uses forbidden floating ref {value!r}")


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


def validate_lock(lock: dict[str, Any]) -> None:
    clipvault = lock.get("clipvault")
    if not isinstance(clipvault, dict):
        raise ValidationError("clipvault must be an object")
    require_sha(clipvault.get("base_sha"), "clipvault.base_sha")

    if clipvault.get("production_integration_allowed") is not False:
        raise ValidationError("production integration must remain disabled in P0")

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

    for track_name, release_name, release in iter_release_locks(lock):
        repository = release.get("repository")
        tag = release.get("tag")
        if not isinstance(repository, str) or "/" not in repository:
            raise ValidationError(
                f"tracks.{track_name}.{release_name}.repository is invalid"
            )
        if not isinstance(tag, str) or not tag.strip():
            raise ValidationError(f"tracks.{track_name}.{release_name}.tag is missing")
        reject_floating_ref(tag, f"tracks.{track_name}.{release_name}.tag")
        require_sha(release.get("sha"), f"tracks.{track_name}.{release_name}.sha")
        if not isinstance(release.get("license"), str):
            raise ValidationError(
                f"tracks.{track_name}.{release_name}.license is missing"
            )

    data_inputs = lock.get("data_inputs")
    if not isinstance(data_inputs, dict):
        raise ValidationError("data_inputs must be an object")
    for name in ("schema", "dictionary"):
        item = data_inputs.get(name)
        if not isinstance(item, dict):
            raise ValidationError(f"data_inputs.{name} must be an object")
        reject_floating_ref(item.get("tag_or_commit"), f"data_inputs.{name}.tag_or_commit")
        if item.get("status") != "BLOCKED_UNTIL_PINNED_AND_LICENSE_APPROVED":
            raise ValidationError(f"data_inputs.{name} must remain blocked in P0")
        if item.get("sha") is not None:
            require_sha(item["sha"], f"data_inputs.{name}.sha")


def validate_vectors(vectors: dict[str, Any]) -> None:
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
    if activation.get("status") != "BLOCKED_UNTIL_SCHEMA_AND_DICTIONARY_ARE_PINNED":
        raise ValidationError("vectors must remain inactive until data inputs are pinned")

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
        validate_lock(load_json(LOCK_PATH))
        validate_vectors(load_json(VECTORS_PATH))
    except ValidationError as exc:
        print(f"POC STATIC VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    print("POC STATIC VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
