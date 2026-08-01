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
VECTORS_PATH = ROOT / "vectors" / "rime-vectors.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FLOATING_REFS = {"main", "master", "latest", "head", "develop", "dev"}
PINNED_DATA_STATUS = "PINNED_PROJECT_OWNED_LICENSE_REVIEW_PENDING"
SESSION_VECTOR_HEADER = (
    "case_id\tstep\taction\targument\trequest_seq\trequest_revision\t"
    "expected_revision\texpected_preedit\texpected_caret_utf16\t"
    "expected_segments\texpected_page_index\texpected_has_previous\t"
    "expected_has_next\texpected_candidate_ids\texpected_commit\t"
    "expected_mode\texpected_error"
)
SESSION_VECTOR_ACTIONS = {
    "START",
    "START_DUPLICATE",
    "KEY",
    "KEYS",
    "PAGE_NEXT",
    "PAGE_PREVIOUS",
    "SELECT",
    "COMMIT",
    "CANCEL",
    "SET_OPTION",
    "SNAPSHOT",
    "SNAPSHOT_INVALID_SESSION",
    "SNAPSHOT_RETIRED_SESSION_CURRENT_EPOCH",
    "END_SESSION",
    "RESTART_HOST",
    "NEW_HOST",
    "ASSERT_SANITIZED",
    "ASSERT_COMMIT_MUTATIONS",
    "ASSERT_PROJECTED_EDITOR",
    "CHURN_ENDED",
    "ASSERT_TOMBSTONES_BOUNDED",
    "ASSERT_PRIVACY_CONTEXT",
    "ASSERT_OPTION",
    "ASSERT_VALUE_GUARDS",
}
REQUIRED_SESSION_CASES = {
    "start_idempotency",
    "selection_and_paging",
    "duplicate_commit",
    "duplicate_cancel",
    "duplicate_end",
    "ordering_and_stale",
    "page_bounds",
    "host_reboot",
    "new_host_isolation",
    "cleanup_and_tombstones",
    "no_composition",
    "invalid_candidate_session",
    "utf16_non_bmp",
    "cancel",
    "context_privacy",
    "set_option",
    "value_guards",
}
REQUIRED_SESSION_ERRORS = {
    "OUT_OF_ORDER_REQUEST",
    "STALE_REVISION",
    "PAGE_OUT_OF_RANGE",
    "STALE_SESSION",
    "SESSION_ENDED",
    "NO_COMPOSITION",
    "INVALID_CANDIDATE",
    "INVALID_SESSION",
}
REQUIRED_CASE_ACTIONS = {
    "start_idempotency": {"START", "START_DUPLICATE", "KEY"},
    "selection_and_paging": {"PAGE_NEXT", "PAGE_PREVIOUS", "SELECT"},
    "duplicate_commit": {
        "COMMIT",
        "ASSERT_COMMIT_MUTATIONS",
        "ASSERT_PROJECTED_EDITOR",
        "SNAPSHOT",
    },
    "duplicate_cancel": {"CANCEL", "SNAPSHOT"},
    "duplicate_end": {"END_SESSION", "ASSERT_SANITIZED", "SNAPSHOT"},
    "ordering_and_stale": {"KEY", "SNAPSHOT"},
    "page_bounds": {"PAGE_NEXT", "PAGE_PREVIOUS", "SNAPSHOT"},
    "host_reboot": {"RESTART_HOST", "ASSERT_SANITIZED", "SNAPSHOT"},
    "new_host_isolation": {
        "NEW_HOST",
        "SNAPSHOT",
        "SNAPSHOT_RETIRED_SESSION_CURRENT_EPOCH",
    },
    "cleanup_and_tombstones": {
        "END_SESSION",
        "ASSERT_SANITIZED",
        "CHURN_ENDED",
        "ASSERT_TOMBSTONES_BOUNDED",
    },
    "no_composition": {"COMMIT", "CANCEL", "SNAPSHOT"},
    "invalid_candidate_session": {"SELECT", "SNAPSHOT_INVALID_SESSION"},
    "utf16_non_bmp": {"KEY", "COMMIT", "SNAPSHOT"},
    "cancel": {"CANCEL", "SNAPSHOT"},
    "context_privacy": {"ASSERT_PRIVACY_CONTEXT", "END_SESSION"},
    "set_option": {"SET_OPTION", "ASSERT_OPTION", "CANCEL"},
    "value_guards": {"ASSERT_VALUE_GUARDS"},
}
REQUIRED_CASE_ERRORS = {
    "ordering_and_stale": {"OUT_OF_ORDER_REQUEST", "STALE_REVISION"},
    "page_bounds": {"PAGE_OUT_OF_RANGE"},
    "host_reboot": {"STALE_SESSION"},
    "new_host_isolation": {"STALE_SESSION"},
    "cleanup_and_tombstones": {"SESSION_ENDED"},
    "no_composition": {"NO_COMPOSITION"},
    "invalid_candidate_session": {"INVALID_CANDIDATE", "INVALID_SESSION"},
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


def resolve_locked_data(relative_path: Any, label: str) -> Path:
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


def validate_data_inputs(lock: dict[str, Any]) -> dict[str, str]:
    data_inputs = lock.get("data_inputs")
    if not isinstance(data_inputs, dict):
        raise ValidationError("data_inputs must be an object")

    observed: dict[str, str] = {}
    for name in (
        "schema",
        "dictionary",
        "default_config",
        "session_contract_vectors",
        "android_ime_slice_vectors",
        "foundation_engine_assertions",
    ):
        item = data_inputs.get(name)
        if not isinstance(item, dict):
            raise ValidationError(f"data_inputs.{name} must be an object")
        if item.get("status") != PINNED_DATA_STATUS:
            raise ValidationError(f"data_inputs.{name} has an unexpected status")
        if not isinstance(item.get("license"), str) or not item["license"]:
            raise ValidationError(f"data_inputs.{name}.license is missing")
        expected = item.get("content_sha256")
        require_sha256(expected, f"data_inputs.{name}.content_sha256")
        path = resolve_locked_data(item.get("path"), f"data_inputs.{name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValidationError(
                f"data_inputs.{name} digest mismatch: expected {expected}, got {actual}"
            )
        observed[name] = actual

    schema_text = resolve_locked_data(
        data_inputs["schema"]["path"], "data_inputs.schema"
    ).read_text(encoding="utf-8")
    dictionary_text = resolve_locked_data(
        data_inputs["dictionary"]["path"], "data_inputs.dictionary"
    ).read_text(encoding="utf-8")
    if "schema_id: clipvault_poc" not in schema_text:
        raise ValidationError("schema does not declare schema_id clipvault_poc")
    if "dictionary: clipvault_poc" not in schema_text:
        raise ValidationError("schema does not bind dictionary clipvault_poc")
    if "name: clipvault_poc" not in dictionary_text:
        raise ValidationError("dictionary does not declare name clipvault_poc")
    validate_session_contract_vectors(
        resolve_locked_data(
            data_inputs["session_contract_vectors"]["path"],
            "data_inputs.session_contract_vectors",
        )
    )
    canonical_assertions = validate_foundation_engine_assertions(
        resolve_locked_data(
            data_inputs["foundation_engine_assertions"]["path"],
            "data_inputs.foundation_engine_assertions",
        )
    )
    validate_android_ime_slice_vectors(
        resolve_locked_data(
            data_inputs["android_ime_slice_vectors"]["path"],
            "data_inputs.android_ime_slice_vectors",
        ),
        canonical_assertions,
    )
    return observed


def validate_foundation_engine_assertions(path: Path) -> dict[str, list[str]]:
    lines = [
        line.rstrip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.rstrip()
    ]
    if lines[:2] != [
        "# format_version=1",
        "# source=clipvault-input-foundation",
    ]:
        raise ValidationError("unexpected foundation assertion metadata")
    if len(lines) < 4 or lines[2] != "semantic_id\tassertion_id\tassertion":
        raise ValidationError("unexpected foundation assertion header")
    result: dict[str, list[str]] = {}
    for line_number, line in enumerate(lines[3:], 4):
        columns = line.split("\t")
        if len(columns) != 3 or not all(columns):
            raise ValidationError(
                f"invalid foundation assertion row at line {line_number}"
            )
        semantic_id, assertion_id, assertion = columns
        if not re.fullmatch(r"ENG2-V00[1-8]", semantic_id):
            raise ValidationError(f"invalid semantic ID at line {line_number}")
        expected_prefix = semantic_id + "-A"
        if not assertion_id.startswith(expected_prefix):
            raise ValidationError(f"invalid assertion ID at line {line_number}")
        ids = result.setdefault(semantic_id, [])
        expected_id = f"{semantic_id}-A{len(ids) + 1:02d}"
        if assertion_id != expected_id or assertion in {"", "-"}:
            raise ValidationError(f"non-contiguous assertion at line {line_number}")
        ids.append(assertion_id)
    expected_semantics = [f"ENG2-V{index:03d}" for index in range(1, 9)]
    if list(result) != expected_semantics or any(len(ids) < 3 for ids in result.values()):
        raise ValidationError("foundation assertions must cover ENG2-V001..V008")
    return result


def validate_android_ime_slice_vectors(
    path: Path,
    canonical_assertions: dict[str, list[str]],
) -> None:
    metadata: dict[str, str] = {}
    rows: list[tuple[str, str, list[str]]] = []
    saw_header = False
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("#"):
            key_value = line.removeprefix("#").strip().split("=", 1)
            if len(key_value) != 2:
                raise ValidationError(
                    f"invalid Android semantic metadata at line {line_number}"
                )
            metadata[key_value[0]] = key_value[1]
            continue
        if not saw_header:
            if line != "semantic_id\tscenario\tassertion_ids":
                raise ValidationError("unexpected Android semantic vector header")
            saw_header = True
            continue
        columns = line.split("\t")
        if len(columns) != 3 or not all(columns):
            raise ValidationError(
                f"invalid Android semantic vector row at line {line_number}"
            )
        rows.append((columns[0], columns[1], columns[2].split(",")))

    expected = [
        ("ENG2-V001", "selection_commit", canonical_assertions["ENG2-V001"]),
        ("ENG2-V002", "paging_stale_ids", canonical_assertions["ENG2-V002"]),
        ("ENG2-V003", "response_ledger", canonical_assertions["ENG2-V003"]),
        ("ENG2-V004", "ambiguous_editor", canonical_assertions["ENG2-V004"]),
        ("ENG2-V005", "session_loss", canonical_assertions["ENG2-V005"]),
        ("ENG2-V006", "utf16_projection", canonical_assertions["ENG2-V006"]),
        ("ENG2-V007", "privacy_surfaces", canonical_assertions["ENG2-V007"]),
        ("ENG2-V008", "ack_cleanup_bounds", canonical_assertions["ENG2-V008"]),
    ]
    if metadata != {
        "format_version": "1",
        "source": "project-authored-synthetic-fixtures",
        "contains_personal_data": "false",
        "typed_text_persistence_allowed": "false",
    }:
        raise ValidationError("unexpected Android semantic vector metadata")
    if not saw_header or rows != expected:
        raise ValidationError("Android semantic vectors must map exactly ENG2-V001..V008")


def validate_session_contract_vectors(path: Path) -> None:
    metadata: dict[str, str] = {}
    saw_header = False
    actions: set[str] = set()
    steps: set[tuple[str, int]] = set()
    rows_by_case: dict[str, list[list[str]]] = {}
    observed_errors: set[str] = set()
    has_valid_non_bmp_utf16 = False

    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("#"):
            key_value = line.removeprefix("#").strip().split("=", 1)
            if len(key_value) != 2:
                raise ValidationError(
                    f"invalid session vector metadata at line {line_number}"
                )
            metadata[key_value[0]] = key_value[1]
            continue
        if not saw_header:
            if line != SESSION_VECTOR_HEADER:
                raise ValidationError("unexpected session vector header")
            saw_header = True
            continue

        columns = line.split("\t")
        if len(columns) != 17:
            raise ValidationError(f"invalid session vector row at line {line_number}")
        case_id, step_text, action = columns[:3]
        if not case_id:
            raise ValidationError(f"session vector case is blank at line {line_number}")
        try:
            step = int(step_text)
        except ValueError as exc:
            raise ValidationError(
                f"invalid session vector step at line {line_number}"
            ) from exc
        if step <= 0 or (case_id, step) in steps:
            raise ValidationError(
                f"duplicate or non-positive session vector step at line {line_number}"
            )
        if action not in SESSION_VECTOR_ACTIONS:
            raise ValidationError(
                f"unknown session vector action {action!r} at line {line_number}"
            )
        steps.add((case_id, step))
        actions.add(action)
        rows_by_case.setdefault(case_id, []).append(columns)
        if columns[16] != "~":
            observed_errors.add(columns[16])
        expected_preedit = columns[7]
        expected_caret = columns[8]
        if expected_preedit not in {"-", "<empty>"} and any(
            ord(character) > 0xFFFF for character in expected_preedit
        ):
            try:
                utf16_length = len(expected_preedit.encode("utf-16-le")) // 2
                has_valid_non_bmp_utf16 = int(expected_caret) == utf16_length
            except ValueError:
                has_valid_non_bmp_utf16 = False

    if metadata.get("format_version") != "3":
        raise ValidationError("unsupported session vector format")
    if metadata.get("source") != "project-authored-synthetic-fixtures":
        raise ValidationError("session vectors must be project-authored synthetic fixtures")
    if metadata.get("contains_personal_data") != "false":
        raise ValidationError("session vectors must contain no personal data")
    if metadata.get("typed_text_persistence_allowed") != "false":
        raise ValidationError("session vectors must forbid typed-text persistence")
    if not saw_header or not steps:
        raise ValidationError("session vectors are empty")
    missing = SESSION_VECTOR_ACTIONS - actions
    if missing:
        raise ValidationError(
            "session vectors do not cover required actions: " + ", ".join(sorted(missing))
        )
    missing_cases = REQUIRED_SESSION_CASES - rows_by_case.keys()
    if missing_cases:
        raise ValidationError(
            "session vectors do not cover required cases: "
            + ", ".join(sorted(missing_cases))
        )
    missing_errors = REQUIRED_SESSION_ERRORS - observed_errors
    if missing_errors:
        raise ValidationError(
            "session vectors do not cover required errors: "
            + ", ".join(sorted(missing_errors))
        )
    for case_id, rows in rows_by_case.items():
        observed_steps = [int(row[1]) for row in rows]
        if observed_steps != list(range(1, len(rows) + 1)):
            raise ValidationError(f"{case_id} steps must be contiguous and start at 1")
        for row in rows:
            if row[2] in {"START", "START_DUPLICATE"} and row[4] != "1":
                raise ValidationError(
                    f"{case_id} {row[2]} must use request_seq 1"
                )
    for case_id, required_actions in REQUIRED_CASE_ACTIONS.items():
        case_actions = {row[2] for row in rows_by_case[case_id]}
        missing_case_actions = required_actions - case_actions
        if missing_case_actions:
            raise ValidationError(
                f"{case_id} is missing actions: "
                + ", ".join(sorted(missing_case_actions))
            )
    for case_id, required_errors in REQUIRED_CASE_ERRORS.items():
        case_errors = {row[16] for row in rows_by_case[case_id] if row[16] != "~"}
        missing_case_errors = required_errors - case_errors
        if missing_case_errors:
            raise ValidationError(
                f"{case_id} is missing errors: "
                + ", ".join(sorted(missing_case_errors))
            )
    duplicate_commits = [
        row for row in rows_by_case["duplicate_commit"] if row[2] == "COMMIT"
    ]
    if (
        len(duplicate_commits) < 2
        or duplicate_commits[0][4:6] != duplicate_commits[1][4:6]
        or duplicate_commits[0][14] == "~"
        or duplicate_commits[0][14] != duplicate_commits[1][14]
    ):
        raise ValidationError("duplicate_commit must repeat the same committing request")
    start_requests = [
        row
        for row in rows_by_case["start_idempotency"]
        if row[2] in {"START", "START_DUPLICATE"}
    ]
    if (
        len(start_requests) != 2
        or start_requests[0][3:6] != start_requests[1][3:6]
    ):
        raise ValidationError(
            "start_idempotency must repeat the same start request at sequence one"
        )
    for case_id, action in (
        ("duplicate_cancel", "CANCEL"),
        ("duplicate_end", "END_SESSION"),
    ):
        duplicates = [row for row in rows_by_case[case_id] if row[2] == action]
        if len(duplicates) < 2 or duplicates[0][4:6] != duplicates[1][4:6]:
            raise ValidationError(
                f"{case_id} must repeat the same {action.lower()} request"
            )
    if not has_valid_non_bmp_utf16:
        raise ValidationError("utf16_non_bmp must lock a non-BMP UTF-16 caret vector")


def validate_lock(lock: dict[str, Any]) -> dict[str, str]:
    clipvault = lock.get("clipvault")
    if not isinstance(clipvault, dict):
        raise ValidationError("clipvault must be an object")
    require_sha40(clipvault.get("base_sha"), "clipvault.base_sha")

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
        require_sha40(release.get("sha"), f"tracks.{track_name}.{release_name}.sha")
        if not isinstance(release.get("license"), str):
            raise ValidationError(
                f"tracks.{track_name}.{release_name}.license is missing"
            )
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
