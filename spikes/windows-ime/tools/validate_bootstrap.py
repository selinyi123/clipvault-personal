#!/usr/bin/env python3
"""Offline contract validator for the isolated Windows IME v2 native slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[1]
LOCK_PATH = ROOT / "UPSTREAM_LOCK.json"
PROTO_PATH = ROOT / "protocol" / "engine_protocol_v2.proto"
VECTORS_PATH = ROOT / "vectors" / "engine-protocol-v2-frames.json"
INVALID_UTF16_PATH = ROOT / "vectors" / "invalid-utf16-split.json"
FRAMING_PATH = ROOT / "protocol" / "framing_v2.json"
RIME_SDK_LOCK_PATH = REPOSITORY_ROOT / "windows" / "ime" / "rime" / "RIME_SDK_LOCK.json"
PROTOCOL_VERSION = 2
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OPAQUE_CANDIDATE_ID = re.compile(r"^c_[0-9a-f]{16}$")


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read valid JSON from {path}: {exc}") from exc
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


EXPECTED_COMPONENTS = {
    "typeduc_windows_frontend": (
        "TypeDuck-HK/TypeDuck-Windows",
        "1ac3af3b44e7478a0f1c7c153bceabf6aa7efb3b",
        "MIT",
    ),
    "typeduc_windows_backend": (
        "TypeDuck-HK/TypeDuck-Windows-backend",
        "af3636a40c9081a7862664e422a6e34ac69fafd6",
        "MIT",
    ),
    "libime2": (
        "EasyIME/libIME2",
        "717b1901a417667405399cfbf25b25664efcf0e4",
        "LGPL-2.1",
    ),
    "librime": (
        "rime/librime",
        "de4700e9f6b75b109910613df907965e3cbe0567",
        "BSD-3-Clause",
    ),
}

EXPECTED_RIME_SUBMODULES = {
    "deps/glog": "7b134a5c82c0c0b5698bb6bf7a835b230c5638e4",
    "deps/googletest": "f8d7d77c06936315286eb55f8de22cd23c188571",
    "deps/leveldb": "99b3c03b3284f5886f9ef9a4ef703d57373e61be",
    "deps/leveldb/third_party/benchmark": "bf585a2789e30585b4e3ce6baf11ef2750b54677",
    "deps/leveldb/third_party/googletest": "c27acebba3b3c7d94209e0467b0a801db4af73ed",
    "deps/marisa-trie": "3e87d53b78e15f2f43783d5e376561a8c9722051",
    "deps/opencc": "556ed22496d650bd0b13b6c163be9814637970ae",
    "deps/yaml-cpp": "2f86d13775d119edbb69af52e5f566fd65c6953b",
}


def validate_lock(lock: dict[str, Any]) -> list[str]:
    require(lock.get("format_version") == 2, "UPSTREAM_LOCK format_version must be 2")
    clipvault = lock.get("clipvault")
    require(isinstance(clipvault, dict), "UPSTREAM_LOCK.clipvault must be an object")
    base_sha = clipvault.get("base_sha")
    require(isinstance(base_sha, str) and SHA40.fullmatch(base_sha) is not None,
            "ClipVault base_sha must be a lowercase 40-character SHA")
    authorization = clipvault.get("authorization")
    require(isinstance(authorization, dict), "authorization must be an object")
    require(authorization.get("development_integration_allowed") is True and
            authorization.get("local_binary_build_allowed") is True and
            authorization.get("local_hkcu_registration_allowed") is True,
            "authorized local v2 development/build/HKCU registration drifted")
    for flag in ("binary_artifact_upload_allowed", "system_wide_registration_allowed",
                 "vendor_upstream_source_allowed", "production_release_allowed"):
        require(authorization.get(flag) is False,
                f"release boundary requires authorization.{flag}=false")

    raw_components = lock.get("components")
    require(isinstance(raw_components, list), "UPSTREAM_LOCK.components must be an array")
    components: dict[str, dict[str, Any]] = {}
    for component in raw_components:
        require(isinstance(component, dict), "component locks must be objects")
        component_id = component.get("id")
        require(isinstance(component_id, str) and component_id,
                "every component lock needs an id")
        require(component_id not in components, f"duplicate component id: {component_id}")
        components[component_id] = component
    require(set(components) == set(EXPECTED_COMPONENTS),
            "component set drifted from the four reviewed upstream roots")

    blockers: list[str] = []
    for component_id, (repository, commit, license_id) in EXPECTED_COMPONENTS.items():
        component = components[component_id]
        require(component.get("repository") == repository,
                f"{component_id}.repository drifted")
        require(component.get("commit") == commit and SHA40.fullmatch(commit) is not None,
                f"{component_id}.commit drifted")
        require(component.get("license") == license_id,
                f"{component_id}.license drifted")
        boundary = component.get("submodule_boundary")
        require(isinstance(boundary, dict),
                f"{component_id} submodule boundary must be an object")
        if component_id == "librime":
            require(component.get("source_present") is True and
                    component.get("source_location_policy") == "external-cache-only" and
                    component.get("binary_lock") == "windows/ime/rime/RIME_SDK_LOCK.json" and
                    boundary.get("status") == "RESOLVED_RECURSIVE_EXTERNAL_CACHE",
                    "librime external-cache lock drifted")
            entries = boundary.get("entries")
            require(isinstance(entries, list), "librime submodule entries must be a list")
            actual = {
                entry.get("path"): entry.get("commit")
                for entry in entries if isinstance(entry, dict)
            }
            require(actual == EXPECTED_RIME_SUBMODULES,
                    "librime recursive submodule pins drifted")
            for entry in entries:
                require(bool(entry.get("license")),
                        f"librime submodule {entry.get('path')} needs a license")
        else:
            require(component.get("source_present") is False and
                    component.get("mode") == "reference-only-not-in-build" and
                    boundary.get("status") == "UNRESOLVED_REFERENCE_ONLY" and
                    boundary.get("entries") == [],
                    f"{component_id} must remain reference-only and out of the build")

    require(components["libime2"].get("license_variant_status") ==
            "EXACT_ONLY_OR_LATER_VARIANT_UNRESOLVED",
            "libIME2 exact LGPL variant must remain unresolved")
    expected_edges = {
        ("typeduc_windows_frontend", "libime2"),
        ("typeduc_windows_backend", "librime"),
        ("typeduc_windows_frontend", "typeduc_windows_backend"),
    }
    relationships = lock.get("relationship_boundaries")
    require(isinstance(relationships, list), "relationship_boundaries must be an array")
    edges: set[tuple[str, str]] = set()
    for relationship in relationships:
        require(isinstance(relationship, dict), "relationship boundaries must be objects")
        edge = (relationship.get("from"), relationship.get("to"))
        require(edge not in edges, f"duplicate relationship boundary: {edge}")
        edges.add(edge)
        require(relationship.get("status") == "REFERENCE_ONLY_NOT_IN_SELECTED_BUILD" and
                bool(relationship.get("required_evidence")),
                f"relationship {edge} must remain reference-only with required evidence")
    require(edges == expected_edges, "upstream relationship boundary set drifted")

    protocol_gate = lock.get("protocol_gate")
    require(isinstance(protocol_gate, dict) and
            protocol_gate.get("review_status") ==
            "PRODUCTION_SOURCE_IMPLEMENTED_RELEASE_HARDENING_OPEN",
            "protocol gate status drifted")
    protocol_unresolved = protocol_gate.get("unresolved_items")
    require(isinstance(protocol_unresolved, list) and protocol_unresolved,
            "protocol gate must list unresolved items")
    blockers.extend(f"protocol: {item}" for item in protocol_unresolved)

    license_gate = lock.get("license_gate")
    require(isinstance(license_gate, dict) and
            license_gate.get("review_status") == "LOCAL_POC_ONLY",
            "license gate must remain LOCAL_POC_ONLY")
    unresolved = license_gate.get("unresolved_items")
    require(isinstance(unresolved, list) and unresolved,
            "license gate must list unresolved items")
    blockers.extend(f"license: {item}" for item in unresolved)
    return blockers


def validate_rime_sdk_lock(lock: dict[str, Any]) -> None:
    require(lock.get("format_version") == 1, "RIME_SDK_LOCK format_version must be 1")
    source = lock.get("source")
    asset = lock.get("official_windows_asset")
    boundary = lock.get("runtime_boundary")
    require(isinstance(source, dict) and source.get("tag") == "1.16.1" and
            source.get("commit") == EXPECTED_COMPONENTS["librime"][1] and
            source.get("license") == "BSD-3-Clause",
            "RIME_SDK_LOCK source identity drifted")
    require(isinstance(asset, dict) and
            asset.get("name") == "rime-de4700e-Windows-msvc-x64.7z" and
            asset.get("sha256") ==
            "e17c1bb4acc9934669e7a62003aef3f8b56d0afa89e5d893ed7dbf34546abb6e" and
            isinstance(asset.get("size_bytes"), int) and asset["size_bytes"] > 0 and
            isinstance(asset.get("required_files"), list),
            "RIME_SDK_LOCK official x64 asset drifted")
    require(isinstance(boundary, dict) and
            boundary.get("loaded_by") == "ClipVaultImeHost.exe" and
            boundary.get("loaded_by_tsf_dll") is False and
            boundary.get("network_in_key_path") is False and
            boundary.get("typed_text_persistence_added") is False,
            "RIME_SDK_LOCK runtime boundary drifted")
    blockers = lock.get("release_blockers")
    require(isinstance(blockers, list) and blockers,
            "RIME_SDK_LOCK must preserve explicit release blockers")


EXPECTED_MESSAGE_FIELDS: dict[str, dict[str, int]] = {
    "Frame": {
        "protocol_version": 1,
        "client_hello": 10,
        "host_hello": 11,
        "start_session_request": 20,
        "process_key_request": 21,
        "select_candidate_request": 22,
        "page_candidates_request": 23,
        "commit_composition_request": 24,
        "cancel_composition_request": 25,
        "end_session_request": 26,
        "set_option_request": 27,
        "engine_state": 30,
        "session_ended": 31,
        "error_response": 32,
    },
    "ClientHello": {
        "client_instance_id": 1,
        "supported_protocol_versions": 2,
        "platform": 3,
        "architecture": 4,
        "build_id": 5,
    },
    "HostHello": {
        "host_instance_id": 1,
        "selected_protocol_version": 2,
        "engine_build_id": 3,
        "capabilities": 4,
    },
    "InputContext": {
        "platform": 1,
        "field_kind": 2,
        "action": 3,
        "incognito": 4,
        "learning_allowed": 5,
        "clipvault_allowed": 6,
        "app_scope": 7,
    },
    "StartSessionRequest": {
        "host_instance_id": 1, "session_id": 2, "request_seq": 3, "context": 4,
    },
    "KeyEvent": {
        "virtual_key": 1, "text": 2, "key_down": 3, "repeat": 4,
        "shift": 5, "control": 6, "alt": 7,
    },
    "ProcessKeyRequest": {
        "host_instance_id": 1, "session_id": 2, "request_seq": 3,
        "expected_revision": 4, "event": 5,
    },
    "SelectCandidateRequest": {
        "host_instance_id": 1, "session_id": 2, "request_seq": 3,
        "expected_revision": 4, "candidate_id": 5,
    },
    "PageCandidatesRequest": {
        "host_instance_id": 1, "session_id": 2, "request_seq": 3,
        "expected_revision": 4, "direction": 5,
    },
    "CommitCompositionRequest": {
        "host_instance_id": 1, "session_id": 2, "request_seq": 3,
        "expected_revision": 4,
    },
    "CancelCompositionRequest": {
        "host_instance_id": 1, "session_id": 2, "request_seq": 3,
        "expected_revision": 4,
    },
    "SetOptionRequest": {
        "host_instance_id": 1, "session_id": 2, "request_seq": 3,
        "expected_revision": 4, "option": 5, "enabled": 6,
    },
    "EndSessionRequest": {
        "host_instance_id": 1, "session_id": 2, "request_seq": 3,
    },
    "CompositionSegment": {"start_utf16": 1, "end_utf16": 2, "kind": 3},
    "EngineCandidate": {
        "candidate_id": 1, "text": 2, "comment": 3, "source": 4, "engine_score": 5,
    },
    "CandidatePage": {
        "page_index": 1, "page_size": 2,
        "has_previous_page": 3, "has_next_page": 4,
    },
    "EngineState": {
        "host_instance_id": 1, "session_id": 2, "ack_request_seq": 3,
        "revision": 4, "handled": 5, "preedit": 6, "caret_utf16": 7,
        "segments": 8, "candidates": 9, "page": 10, "commit_text": 11,
        "composition_active": 12, "mode": 13,
    },
    "SessionEnded": {
        "host_instance_id": 1, "session_id": 2, "ack_request_seq": 3,
    },
    "ErrorResponse": {
        "code": 1, "current_host_instance_id": 2, "session_id": 3,
        "ack_request_seq": 4, "current_revision": 5, "invalidates_session": 6,
    },
}

EXPECTED_ENUMS: dict[str, dict[str, int]] = {
    "InputPlatform": {
        "INPUT_PLATFORM_UNSPECIFIED": 0,
        "INPUT_PLATFORM_ANDROID": 1,
        "INPUT_PLATFORM_WINDOWS": 2,
    },
    "InputFieldKind": {
        "INPUT_FIELD_KIND_UNKNOWN": 0,
        "INPUT_FIELD_KIND_TEXT": 1,
        "INPUT_FIELD_KIND_MULTILINE": 2,
        "INPUT_FIELD_KIND_EMAIL": 3,
        "INPUT_FIELD_KIND_URL": 4,
        "INPUT_FIELD_KIND_NUMBER": 5,
        "INPUT_FIELD_KIND_PHONE": 6,
        "INPUT_FIELD_KIND_PASSWORD": 7,
        "INPUT_FIELD_KIND_OTP": 8,
    },
    "InputAction": {
        "INPUT_ACTION_NONE": 0,
        "INPUT_ACTION_ENTER": 1,
        "INPUT_ACTION_DONE": 2,
        "INPUT_ACTION_GO": 3,
        "INPUT_ACTION_NEXT": 4,
        "INPUT_ACTION_SEARCH": 5,
        "INPUT_ACTION_SEND": 6,
    },
    "PageDirection": {
        "PAGE_DIRECTION_UNSPECIFIED": 0,
        "PAGE_DIRECTION_PREVIOUS": 1,
        "PAGE_DIRECTION_NEXT": 2,
    },
    "CompositionSegmentKind": {
        "COMPOSITION_SEGMENT_KIND_UNSPECIFIED": 0,
        "COMPOSITION_SEGMENT_KIND_RAW": 1,
        "COMPOSITION_SEGMENT_KIND_CONVERTED": 2,
        "COMPOSITION_SEGMENT_KIND_SELECTED": 3,
    },
    "CandidateSource": {
        "CANDIDATE_SOURCE_UNSPECIFIED": 0,
        "CANDIDATE_SOURCE_ENGINE": 1,
    },
    "EngineMode": {
        "ENGINE_MODE_UNSPECIFIED": 0,
        "ENGINE_MODE_DIRECT": 1,
        "ENGINE_MODE_COMPOSING": 2,
        "ENGINE_MODE_SELECTING": 3,
        "ENGINE_MODE_DISABLED": 4,
    },
    "ErrorCode": {
        "ERROR_CODE_UNSPECIFIED": 0,
        "ERROR_CODE_UNSUPPORTED_PROTOCOL": 1,
        "ERROR_CODE_STALE_SESSION": 2,
        "ERROR_CODE_SESSION_NOT_FOUND": 3,
        "ERROR_CODE_STALE_REVISION": 4,
        "ERROR_CODE_OUT_OF_ORDER_REQUEST": 5,
        "ERROR_CODE_INVALID_CANDIDATE": 6,
        "ERROR_CODE_INVALID_ARGUMENT": 7,
        "ERROR_CODE_UNAVAILABLE": 8,
    },
}

FRAME_TYPES = {
    "ClientHello", "HostHello", "StartSessionRequest", "ProcessKeyRequest",
    "SelectCandidateRequest", "PageCandidatesRequest", "CommitCompositionRequest",
    "CancelCompositionRequest", "SetOptionRequest", "EndSessionRequest",
    "EngineState", "SessionEnded", "ErrorResponse",
}


def strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def extract_block(text: str, kind: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(kind)}\s+{re.escape(name)}\s*\{{", text)
    require(match is not None, f"missing {kind} {name} in protocol")
    depth = 1
    for index in range(match.end(), len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[match.end():index]
    raise ValidationError(f"unterminated {kind} {name} block")


def parse_fields(block: str) -> dict[str, int]:
    pattern = re.compile(
        r"(?m)^\s*(?:(?:optional|repeated|required)\s+)?"
        r"(?:[A-Za-z_][A-Za-z0-9_.]*|map\s*<[^>]+>)\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)\s*;"
    )
    fields: dict[str, int] = {}
    numbers: set[int] = set()
    for name, raw_number in pattern.findall(block):
        number = int(raw_number)
        require(name not in fields, f"duplicate protobuf field name: {name}")
        require(number not in numbers, f"duplicate protobuf field number: {number}")
        fields[name] = number
        numbers.add(number)
    return fields


def parse_enum(block: str) -> dict[str, int]:
    values = {
        name: int(number)
        for name, number in re.findall(
            r"(?m)^\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*;", block
        )
    }
    require(len(values) == len(set(values.values())), "protobuf enum numbers must be unique")
    return values


def validate_proto(proto: str) -> None:
    clean = strip_comments(proto)
    require('syntax = "proto3";' in clean, "protocol must use proto3")
    require("package clipvault.windows.ime.v2;" in clean, "protocol package drifted")
    require("option optimize_for = LITE_RUNTIME;" in clean,
            "protocol must remain suitable for a thin TSF DLL")
    for message, expected in EXPECTED_MESSAGE_FIELDS.items():
        actual = parse_fields(extract_block(clean, "message", message))
        require(actual == expected, f"protobuf field-number drift in {message}: {actual}")
    for enum, expected in EXPECTED_ENUMS.items():
        actual = parse_enum(extract_block(clean, "enum", enum))
        require(actual == expected, f"protobuf enum drift in {enum}: {actual}")
    for expression in (
        r"optional\s+string\s+app_scope\s*=\s*7\s*;",
        r"optional\s+string\s+comment\s*=\s*3\s*;",
        r"optional\s+string\s+commit_text\s*=\s*11\s*;",
    ):
        require(re.search(expression, clean) is not None,
                f"missing required proto3 optional presence: {expression}")
    require("CANDIDATE_SOURCE_CLIPVAULT" not in clean,
            "Engine Protocol V2 must not transport ClipVault candidates")
    end_session = extract_block(clean, "message", "EndSessionRequest")
    require(re.search(r'\breserved\s+4\s*;', end_session) is not None and
            re.search(r'\breserved\s+"expected_revision"\s*;', end_session) is not None,
            "EndSessionRequest must reserve the removed revision precondition")


def utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def utf16_boundaries(value: str) -> set[int]:
    boundaries = {0}
    offset = 0
    for character in value:
        offset += 2 if ord(character) > 0xFFFF else 1
        boundaries.add(offset)
    return boundaries


def validate_framing(framing: dict[str, Any]) -> None:
    require(framing == {
        "format_version": 1,
        "length_prefix": {
            "bytes": 4,
            "signed": False,
            "byte_order": "big-endian",
        },
        "protobuf_payload": {
            "min_bytes": 1,
            "max_bytes": 1048576,
            "exact_length_required": True,
            "zero_rejected": True,
            "truncated_rejected": True,
            "oversize_rejected": True,
            "trailing_bytes_rejected": True,
        },
        "handshake": {
            "per_connection_order": ["ClientHello", "HostHello"],
            "application_frames_after_ready_only": True,
            "restart_requires_new_connection": True,
        },
    }, "framed protobuf transport contract drifted")


def validate_invalid_utf16_fixture(fixture: dict[str, Any]) -> None:
    require(fixture.get("expected_result") == "reject",
            "invalid UTF-16 fixture must remain rejection evidence")
    preedit = fixture.get("preedit")
    caret = fixture.get("caret_utf16")
    segments = fixture.get("segments")
    require(isinstance(preedit, str) and isinstance(caret, int) and
            isinstance(segments, list), "invalid UTF-16 fixture shape drifted")
    boundaries = utf16_boundaries(preedit)
    split_detected = caret not in boundaries or any(
        isinstance(segment, dict) and (
            segment.get("start_utf16") not in boundaries or
            segment.get("end_utf16") not in boundaries
        )
        for segment in segments
    )
    require(split_detected, "negative UTF-16 vector no longer splits a surrogate pair")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


ResponseKey = tuple[str, str, int]


def project_engine_state(
    payload: dict[str, Any],
    applied_response_keys: set[ResponseKey],
    editor_commits: list[str],
    projected_commit_keys: list[ResponseKey],
) -> bool:
    """Project a validated Host state into the TSF client exactly once."""
    key = (
        payload["host_instance_id"],
        payload["session_id"],
        payload["ack_request_seq"],
    )
    if key in applied_response_keys:
        return False
    applied_response_keys.add(key)
    if "commit_text" in payload:
        editor_commits.append(payload["commit_text"])
        projected_commit_keys.append(key)
    return True


@dataclass
class Exchange:
    request_type: str
    request_payload: str
    response_type: str
    response_payload: str


@dataclass
class Session:
    host_instance_id: str
    session_id: str
    last_request_seq: int = 0
    revision: int = 0
    preedit: str = ""
    page_index: int = 0
    has_previous_page: bool = False
    has_next_page: bool = False
    current_candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_catalog: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    page_snapshots: dict[int, dict[str, tuple[Any, ...]]] = field(default_factory=dict)
    expired_candidate_ids: set[str] = field(default_factory=set)
    exchanges: dict[int, Exchange] = field(default_factory=dict)


@dataclass
class Coverage:
    start_revision_zero: bool = False
    duplicate_start_cached: bool = False
    conflicting_start_reuse: bool = False
    non_bmp_utf16: bool = False
    next_page: bool = False
    previous_page_stable: bool = False
    duplicate_commit_cached: bool = False
    duplicate_commit_projected_once: bool = False
    distinct_commit_projected: bool = False
    stale_revision: bool = False
    invalid_candidate: bool = False
    stale_candidate: bool = False
    conflicting_duplicate: bool = False
    lower_sequence: bool = False
    stale_session: bool = False
    password_context: bool = False
    incognito_context: bool = False
    set_option: bool = False
    end_session_idempotent: bool = False


REQUEST_TYPES = {
    "ProcessKeyRequest", "SelectCandidateRequest", "PageCandidatesRequest",
    "CommitCompositionRequest", "CancelCompositionRequest", "SetOptionRequest",
    "EndSessionRequest",
}


def validate_context(label: str, context: Any, coverage: Coverage) -> None:
    require(isinstance(context, dict), f"{label}: context must be an object")
    allowed = set(EXPECTED_MESSAGE_FIELDS["InputContext"])
    require(set(context) <= allowed, f"{label}: unknown InputContext field")
    required = allowed - {"app_scope"}
    require(required <= set(context), f"{label}: incomplete InputContext")
    require(context["platform"] == "INPUT_PLATFORM_WINDOWS",
            f"{label}: Windows vectors must declare WINDOWS platform")
    require(context["field_kind"] in EXPECTED_ENUMS["InputFieldKind"],
            f"{label}: invalid field_kind")
    require(context["action"] in EXPECTED_ENUMS["InputAction"],
            f"{label}: invalid input action")
    for flag in ("incognito", "learning_allowed", "clipvault_allowed"):
        require(isinstance(context[flag], bool), f"{label}: {flag} must be boolean")
    app_scope = context.get("app_scope")
    require(app_scope is None or
            (isinstance(app_scope, str) and re.fullmatch(r"scope-[a-z0-9-]+", app_scope)),
            f"{label}: app_scope must be an opaque synthetic local identifier")

    password = context["field_kind"] == "INPUT_FIELD_KIND_PASSWORD"
    incognito = context["incognito"]
    if password or incognito:
        require(context["learning_allowed"] is False and
                context["clipvault_allowed"] is False,
                f"{label}: password/incognito must disable learning and ClipVault")
    coverage.password_context |= password
    coverage.incognito_context |= incognito


def candidate_identity(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (candidate.get("text"), candidate.get("comment"), candidate.get("source"))


def validate_engine_state(
    label: str,
    payload: dict[str, Any],
    session: Session,
    pending: dict[str, Any],
    coverage: Coverage,
) -> None:
    require(payload["host_instance_id"] == session.host_instance_id,
            f"{label}: host instance mismatch")
    require(payload["session_id"] == session.session_id,
            f"{label}: session mismatch")
    require(payload["ack_request_seq"] == pending["request_seq"],
            f"{label}: acknowledgement sequence mismatch")
    expected_revision = (
        0 if pending["request_type"] == "StartSessionRequest"
        else session.revision + 1
    )
    require(payload["revision"] == expected_revision,
            f"{label}: response revision does not match the session transition")
    coverage.start_revision_zero |= (
        pending["request_type"] == "StartSessionRequest" and
        payload["revision"] == 0
    )
    require(isinstance(payload["handled"], bool), f"{label}: handled must be boolean")

    preedit = payload["preedit"]
    caret = payload["caret_utf16"]
    require(isinstance(preedit, str), f"{label}: preedit must be a string")
    boundaries = utf16_boundaries(preedit)
    require(isinstance(caret, int) and caret in boundaries,
            f"{label}: caret_utf16 is outside preedit or splits a surrogate pair")
    if not preedit:
        require(caret == 0, f"{label}: empty preedit requires caret 0")
    coverage.non_bmp_utf16 |= any(ord(character) > 0xFFFF for character in preedit)

    segments = payload["segments"]
    require(isinstance(segments, list), f"{label}: segments must be an array")
    previous_end = 0
    for segment in segments:
        require(isinstance(segment, dict), f"{label}: segment must be an object")
        require(set(segment) == set(EXPECTED_MESSAGE_FIELDS["CompositionSegment"]),
                f"{label}: segment fields drifted")
        start = segment["start_utf16"]
        end = segment["end_utf16"]
        require(isinstance(start, int) and isinstance(end, int),
                f"{label}: segment offsets must be integers")
        require(start == previous_end and start < end <= utf16_units(preedit) and
                start in boundaries and end in boundaries,
                f"{label}: segments must be contiguous, ordered, surrogate-safe, and in range")
        require(segment["kind"] in {
            "COMPOSITION_SEGMENT_KIND_RAW",
            "COMPOSITION_SEGMENT_KIND_CONVERTED",
            "COMPOSITION_SEGMENT_KIND_SELECTED",
        }, f"{label}: segment kind must be explicit")
        previous_end = end
    if preedit:
        require(bool(segments) and previous_end == utf16_units(preedit),
                f"{label}: segments must partition the entire preedit")
    else:
        require(segments == [], f"{label}: empty preedit must have no segments")

    candidates = payload["candidates"]
    require(isinstance(candidates, list), f"{label}: candidates must be an array")
    current: dict[str, dict[str, Any]] = {}
    identities: dict[str, tuple[Any, ...]] = {}
    for candidate in candidates:
        require(isinstance(candidate, dict), f"{label}: candidate must be an object")
        required_candidate_fields = set(EXPECTED_MESSAGE_FIELDS["EngineCandidate"]) - {"comment"}
        require(required_candidate_fields <= set(candidate) <=
                set(EXPECTED_MESSAGE_FIELDS["EngineCandidate"]),
                f"{label}: candidate fields drifted")
        candidate_id = candidate["candidate_id"]
        require(isinstance(candidate_id, str) and
                OPAQUE_CANDIDATE_ID.fullmatch(candidate_id) is not None,
                f"{label}: candidate_id must be opaque and must not encode page/index/text")
        require(candidate_id not in current, f"{label}: duplicate candidate_id")
        require(candidate_id not in session.expired_candidate_ids,
                f"{label}: candidate_id was reused across composition generations")
        require(candidate["source"] == "CANDIDATE_SOURCE_ENGINE",
                f"{label}: protocol may carry ENGINE candidates only")
        identity = candidate_identity(candidate)
        if candidate_id in session.candidate_catalog:
            require(session.candidate_catalog[candidate_id] == identity,
                    f"{label}: stable candidate_id changed identity")
        else:
            session.candidate_catalog[candidate_id] = identity
        current[candidate_id] = candidate
        identities[candidate_id] = identity

    page = payload["page"]
    require(isinstance(page, dict) and
            set(page) == set(EXPECTED_MESSAGE_FIELDS["CandidatePage"]),
            f"{label}: page fields drifted")
    page_index = page["page_index"]
    page_size = page["page_size"]
    require(isinstance(page_index, int) and page_index >= 0 and
            isinstance(page_size, int) and page_size > 0,
            f"{label}: invalid page index/size")
    require(len(candidates) <= page_size, f"{label}: candidates exceed page_size")
    require(page["has_previous_page"] is (page_index > 0),
            f"{label}: has_previous_page conflicts with page_index")

    request_type = pending["request_type"]
    if request_type == "PageCandidatesRequest":
        direction = pending["page_direction"]
        prior_page = pending["prior_page_index"]
        if direction == "PAGE_DIRECTION_NEXT":
            require(pending["prior_has_next"] is True and page_index == prior_page + 1,
                    f"{label}: NEXT must advance exactly one available page")
            coverage.next_page = True
        else:
            require(pending["prior_has_previous"] is True and page_index == prior_page - 1,
                    f"{label}: PREVIOUS must retreat exactly one available page")
            require(page_index in session.page_snapshots and
                    session.page_snapshots[page_index] == identities,
                    f"{label}: returning to a page must preserve opaque candidate IDs")
            coverage.previous_page_stable = True

    mode = payload["mode"]
    active = payload["composition_active"]
    require(mode in {
        "ENGINE_MODE_DIRECT", "ENGINE_MODE_COMPOSING",
        "ENGINE_MODE_SELECTING", "ENGINE_MODE_DISABLED",
    }, f"{label}: invalid engine mode")
    require(isinstance(active, bool) and
            active is (mode in {"ENGINE_MODE_COMPOSING", "ENGINE_MODE_SELECTING"}),
            f"{label}: composition_active conflicts with mode")
    if mode in {"ENGINE_MODE_DIRECT", "ENGINE_MODE_DISABLED"}:
        require(preedit == "" and candidates == [],
                f"{label}: direct/disabled mode cannot expose composition")
    elif mode == "ENGINE_MODE_COMPOSING":
        require(bool(preedit) and candidates == [],
                f"{label}: composing mode requires preedit without candidates")
    else:
        require(bool(preedit) and bool(candidates),
                f"{label}: selecting mode requires preedit and candidates")

    has_commit = "commit_text" in payload
    if has_commit:
        require(isinstance(payload["commit_text"], str) and payload["commit_text"],
                f"{label}: present commit_text must be non-empty")
        require(preedit == "" and segments == [] and candidates == [] and not active,
                f"{label}: commit must clear composition")
    if request_type == "SelectCandidateRequest":
        require(has_commit and payload["commit_text"] == pending["selected_text"],
                f"{label}: selection must commit the stable candidate ID's text")
    elif request_type == "CommitCompositionRequest":
        require(has_commit and payload["commit_text"] == pending["prior_preedit"],
                f"{label}: explicit commit must commit prior preedit")
    elif request_type == "CancelCompositionRequest":
        require(not has_commit and preedit == "" and candidates == [] and not active,
                f"{label}: cancel must clear without a commit")

    if active:
        if page_index in session.page_snapshots:
            require(session.page_snapshots[page_index] == identities,
                    f"{label}: page snapshot changed candidate identities")
        else:
            session.page_snapshots[page_index] = identities
    if has_commit or request_type == "CancelCompositionRequest":
        session.expired_candidate_ids.update(session.candidate_catalog)
        session.candidate_catalog.clear()
        session.page_snapshots.clear()

    session.revision = payload["revision"]
    session.preedit = preedit
    session.page_index = page_index
    session.has_previous_page = page["has_previous_page"]
    session.has_next_page = page["has_next_page"]
    session.current_candidates = current


def expected_payload_fields(message_type: str) -> tuple[set[str], set[str]]:
    fields = set(EXPECTED_MESSAGE_FIELDS[message_type])
    optional: set[str] = set()
    if message_type == "EngineState":
        optional.add("commit_text")
    return fields - optional, fields


def validate_error(
    label: str,
    payload: dict[str, Any],
    pending: dict[str, Any],
    current_host: str,
    sessions: dict[str, Session],
    coverage: Coverage,
) -> None:
    require(payload["code"] == pending["error_code"],
            f"{label}: wrong expected error code")
    require(payload["current_host_instance_id"] == current_host and
            payload["session_id"] == pending["session_id"] and
            payload["ack_request_seq"] == pending["request_seq"],
            f"{label}: error acknowledgement mismatch")
    require(payload["current_revision"] == pending["current_revision"],
            f"{label}: error must report current revision without mutation")
    require(payload["invalidates_session"] is pending["invalidates_session"],
            f"{label}: invalidates_session mismatch")

    code = payload["code"]
    coverage.stale_revision |= code == "ERROR_CODE_STALE_REVISION"
    coverage.conflicting_duplicate |= pending.get("ordering_kind") == "conflicting"
    coverage.conflicting_start_reuse |= pending.get("ordering_kind") == "conflicting-start"
    coverage.lower_sequence |= pending.get("ordering_kind") == "lower"
    coverage.stale_session |= code == "ERROR_CODE_STALE_SESSION"
    if code == "ERROR_CODE_INVALID_CANDIDATE":
        if pending.get("candidate_kind") == "stale":
            coverage.stale_candidate = True
        else:
            coverage.invalid_candidate = True

    if pending.get("cache_response"):
        session = sessions[pending["session_id"]]
        session.exchanges[pending["request_seq"]] = Exchange(
            pending["request_type"],
            pending["request_payload"],
            "ErrorResponse",
            canonical(payload),
        )


def validate_scenario(
    scenario: dict[str, Any],
    frame_ids: set[str],
    coverage: Coverage,
) -> set[str]:
    scenario_id = scenario.get("id")
    require(isinstance(scenario_id, str) and scenario_id,
            "each scenario needs a non-empty id")
    frames = scenario.get("frames")
    require(isinstance(frames, list) and frames,
            f"{scenario_id}: frames must be a non-empty array")
    expected_editor_commits = scenario.get("expected_editor_commits")
    require(isinstance(expected_editor_commits, list) and
            all(isinstance(value, str) and value for value in expected_editor_commits),
            f"{scenario_id}: expected_editor_commits must be an explicit string array")
    boundaries = scenario.get("connection_boundaries")
    require(isinstance(boundaries, list) and boundaries,
            f"{scenario_id}: explicit connection boundaries are required")
    boundary_by_frame: dict[str, str] = {}
    connection_ids: set[str] = set()
    for boundary in boundaries:
        require(isinstance(boundary, dict) and
                set(boundary) == {"before_frame", "connection_id"},
                f"{scenario_id}: invalid connection boundary shape")
        before_frame = boundary["before_frame"]
        connection_id = boundary["connection_id"]
        require(isinstance(before_frame, str) and before_frame and
                isinstance(connection_id, str) and
                re.fullmatch(r"pipe-[a-z0-9-]+", connection_id) is not None,
                f"{scenario_id}: invalid synthetic connection boundary")
        require(before_frame not in boundary_by_frame and
                connection_id not in connection_ids,
                f"{scenario_id}: connection boundaries must be unique")
        boundary_by_frame[before_frame] = connection_id
        connection_ids.add(connection_id)

    current_host: str | None = None
    connection_state = "closed"
    current_connection: str | None = None
    used_boundaries: set[str] = set()
    sessions: dict[str, Session] = {}
    invalidated: dict[str, Session] = {}
    ended: dict[str, Exchange] = {}
    pending: dict[str, Any] | None = None
    seen_types: set[str] = set()
    applied_response_keys: set[ResponseKey] = set()
    editor_commits: list[str] = []
    projected_commit_keys: list[ResponseKey] = []

    for frame in frames:
        require(isinstance(frame, dict), f"{scenario_id}: frame must be an object")
        frame_id = frame.get("id")
        require(isinstance(frame_id, str) and frame_id,
                f"{scenario_id}: every frame needs an id")
        require(frame_id not in frame_ids, f"duplicate golden frame id: {frame_id}")
        frame_ids.add(frame_id)
        label = f"{scenario_id}/{frame_id}"
        if frame_id in boundary_by_frame:
            require(pending is None and connection_state in {"closed", "ready"},
                    f"{label}: connection cannot change with a request pending")
            current_connection = boundary_by_frame[frame_id]
            used_boundaries.add(frame_id)
            connection_state = "expect-client-hello"
        require(frame.get("protocol_version") == PROTOCOL_VERSION,
                f"{label}: every frame must explicitly carry protocol v2")
        direction = frame.get("direction")
        message_type = frame.get("type")
        payload = frame.get("payload")
        require(direction in {"client_to_host", "host_to_client"},
                f"{label}: invalid direction")
        require(message_type in FRAME_TYPES, f"{label}: invalid frame type")
        require(isinstance(payload, dict), f"{label}: payload must be an object")
        required_fields, allowed_fields = expected_payload_fields(message_type)
        require(required_fields <= set(payload) <= allowed_fields,
                f"{label}: top-level payload fields drifted")
        seen_types.add(message_type)

        if message_type == "ClientHello":
            require(direction == "client_to_host" and pending is None and
                    connection_state == "expect-client-hello" and
                    current_connection is not None,
                    f"{label}: invalid ClientHello placement")
            require(PROTOCOL_VERSION in payload["supported_protocol_versions"] and
                    payload["platform"] == "INPUT_PLATFORM_WINDOWS" and
                    isinstance(payload["architecture"], str) and payload["architecture"],
                    f"{label}: invalid Windows protocol offer")
            connection_state = "expect-host-hello"
            continue

        if message_type == "HostHello":
            require(direction == "host_to_client" and pending is None and
                    connection_state == "expect-host-hello",
                    f"{label}: invalid HostHello placement")
            require(payload["selected_protocol_version"] == PROTOCOL_VERSION,
                    f"{label}: host must select protocol v2")
            host_id = payload["host_instance_id"]
            require(isinstance(host_id, str) and host_id,
                    f"{label}: host_instance_id is the non-empty host epoch")
            if current_host is not None and host_id != current_host:
                invalidated.update(sessions)
                sessions.clear()
                applied_response_keys.clear()
                ended.clear()
            current_host = host_id
            connection_state = "ready"
            continue

        require(connection_state == "ready",
                f"{label}: application frame arrived before ClientHello -> HostHello")

        if message_type == "StartSessionRequest":
            require(direction == "client_to_host" and pending is None,
                    f"{label}: invalid StartSessionRequest placement")
            require(payload["host_instance_id"] == current_host,
                    f"{label}: new session must bind to current host epoch")
            session_id = payload["session_id"]
            require(isinstance(session_id, str) and session_id,
                    f"{label}: session_id must be non-empty")
            require(payload["request_seq"] == 1,
                    f"{label}: new session starts at request_seq 1")
            validate_context(label, payload["context"], coverage)
            request_key = canonical(payload)
            existing = sessions.get(session_id)
            if existing is not None:
                cached = existing.exchanges.get(1)
                if (
                    cached is not None
                    and cached.request_type == message_type
                    and cached.request_payload == request_key
                ):
                    pending = {
                        "kind": "duplicate",
                        "request_type": message_type,
                        "request_seq": 1,
                        "session_id": session_id,
                        "cached": cached,
                        "duplicate_start": True,
                    }
                else:
                    pending = {
                        "kind": "error",
                        "error_code": "ERROR_CODE_OUT_OF_ORDER_REQUEST",
                        "request_type": message_type,
                        "request_payload": request_key,
                        "request_seq": 1,
                        "session_id": session_id,
                        "current_revision": existing.revision,
                        "invalidates_session": False,
                        "cache_response": False,
                        "ordering_kind": "conflicting-start",
                    }
                continue
            require(session_id not in invalidated,
                    f"{label}: session_id must be fresh for this host epoch")
            session = Session(current_host, session_id, last_request_seq=1)
            sessions[session_id] = session
            pending = {
                "kind": "state",
                "request_type": message_type,
                "request_payload": request_key,
                "request_seq": 1,
                "session_id": session_id,
                "frame_id": frame_id,
            }
            continue

        if message_type in REQUEST_TYPES:
            require(direction == "client_to_host" and pending is None,
                    f"{label}: request sent while another request is pending")
            session_id = payload["session_id"]
            request_seq = payload["request_seq"]
            require(isinstance(request_seq, int) and request_seq > 0,
                    f"{label}: request_seq must be positive")

            if payload["host_instance_id"] != current_host:
                stale = invalidated.get(session_id)
                require(stale is not None and
                        payload["host_instance_id"] == stale.host_instance_id,
                        f"{label}: unknown stale host/session pair")
                pending = {
                    "kind": "error",
                    "error_code": "ERROR_CODE_STALE_SESSION",
                    "request_type": message_type,
                    "request_payload": canonical(payload),
                    "request_seq": request_seq,
                    "session_id": session_id,
                    "current_revision": 0,
                    "invalidates_session": True,
                    "cache_response": False,
                }
                continue

            request_key = canonical(payload)
            session = sessions.get(session_id)
            if session is None and message_type == "EndSessionRequest":
                cached_end = ended.get(session_id)
                if (
                    cached_end is not None
                    and cached_end.request_type == message_type
                    and cached_end.request_payload == request_key
                ):
                    pending = {
                        "kind": "duplicate",
                        "request_type": message_type,
                        "request_seq": request_seq,
                        "session_id": session_id,
                        "cached": cached_end,
                    }
                    continue
            require(session is not None, f"{label}: unknown active session")
            cached = session.exchanges.get(request_seq)
            if (
                cached is not None
                and cached.request_type == message_type
                and cached.request_payload == request_key
            ):
                pending = {
                    "kind": "duplicate",
                    "request_type": message_type,
                    "request_seq": request_seq,
                    "session_id": session_id,
                    "cached": cached,
                }
                continue
            if request_seq <= session.last_request_seq or request_seq > session.last_request_seq + 1:
                ordering_kind = "conflicting" if request_seq == session.last_request_seq else "lower"
                pending = {
                    "kind": "error",
                    "error_code": "ERROR_CODE_OUT_OF_ORDER_REQUEST",
                    "request_type": message_type,
                    "request_payload": request_key,
                    "request_seq": request_seq,
                    "session_id": session_id,
                    "current_revision": session.revision,
                    "invalidates_session": False,
                    "cache_response": False,
                    "ordering_kind": ordering_kind,
                }
                continue

            session.last_request_seq = request_seq
            base_pending = {
                "request_type": message_type,
                "request_payload": request_key,
                "request_seq": request_seq,
                "session_id": session_id,
                "frame_id": frame_id,
            }
            if message_type == "EndSessionRequest":
                pending = {
                    **base_pending,
                    "kind": "end",
                    "prior_preedit": session.preedit,
                }
                continue

            expected_revision = payload["expected_revision"]
            if expected_revision != session.revision:
                pending = {
                    **base_pending,
                    "kind": "error",
                    "error_code": "ERROR_CODE_STALE_REVISION",
                    "current_revision": session.revision,
                    "invalidates_session": False,
                    "cache_response": True,
                }
                continue
            if (
                message_type == "SelectCandidateRequest"
                and payload["candidate_id"] not in session.current_candidates
            ):
                pending = {
                    **base_pending,
                    "kind": "error",
                    "error_code": "ERROR_CODE_INVALID_CANDIDATE",
                    "current_revision": session.revision,
                    "invalidates_session": False,
                    "cache_response": True,
                    "candidate_kind": (
                        "stale" if payload["candidate_id"] in session.expired_candidate_ids
                        else "invalid"
                    ),
                }
                continue

            pending = {
                **base_pending,
                "kind": "state",
                "prior_preedit": session.preedit,
            }
            if message_type == "SelectCandidateRequest":
                pending["selected_text"] = session.current_candidates[payload["candidate_id"]]["text"]
            elif message_type == "PageCandidatesRequest":
                require(payload["direction"] in {
                    "PAGE_DIRECTION_PREVIOUS", "PAGE_DIRECTION_NEXT"
                }, f"{label}: paging direction must be explicit")
                pending.update({
                    "page_direction": payload["direction"],
                    "prior_page_index": session.page_index,
                    "prior_has_previous": session.has_previous_page,
                    "prior_has_next": session.has_next_page,
                })
            elif message_type == "SetOptionRequest":
                require(isinstance(payload["option"], str) and payload["option"] and
                        isinstance(payload["enabled"], bool),
                        f"{label}: invalid SetOption payload")
                coverage.set_option = True
            continue

        if pending is not None and pending["kind"] == "duplicate":
            cached = pending["cached"]
            require(direction == "host_to_client" and message_type == cached.response_type and
                    canonical(payload) == cached.response_payload,
                    f"{label}: duplicate request must receive byte-equivalent cached result")
            if message_type == "EngineState" and "commit_text" in payload:
                commit_count = len(editor_commits)
                applied = project_engine_state(
                    payload,
                    applied_response_keys,
                    editor_commits,
                    projected_commit_keys,
                )
                require(not applied and len(editor_commits) == commit_count,
                        f"{label}: cached commit response must not project text twice")
                coverage.duplicate_commit_cached = True
                coverage.duplicate_commit_projected_once = True
            elif message_type == "SessionEnded":
                coverage.end_session_idempotent = True
            if pending.get("duplicate_start"):
                coverage.duplicate_start_cached = True
            pending = None
            continue

        if message_type == "EngineState":
            require(direction == "host_to_client" and pending is not None and
                    pending["kind"] == "state",
                    f"{label}: EngineState has no matching state request")
            session = sessions[pending["session_id"]]
            validate_engine_state(label, payload, session, pending, coverage)
            project_engine_state(
                payload,
                applied_response_keys,
                editor_commits,
                projected_commit_keys,
            )
            session.exchanges[pending["request_seq"]] = Exchange(
                pending["request_type"], pending["request_payload"],
                "EngineState", canonical(payload),
            )
            pending = None
            continue

        if message_type == "SessionEnded":
            require(direction == "host_to_client" and pending is not None and
                    pending["kind"] == "end",
                    f"{label}: SessionEnded must answer EndSessionRequest")
            session = sessions[pending["session_id"]]
            require(payload["host_instance_id"] == session.host_instance_id and
                    payload["session_id"] == session.session_id and
                    payload["ack_request_seq"] == pending["request_seq"],
                    f"{label}: SessionEnded acknowledgement mismatch")
            session_response_keys = {
                key
                for key in applied_response_keys
                if key[0] == session.host_instance_id and key[1] == session.session_id
            }
            applied_response_keys.difference_update(session_response_keys)
            ended[session.session_id] = Exchange(
                pending["request_type"],
                pending["request_payload"],
                "SessionEnded",
                canonical(payload),
            )
            del sessions[session.session_id]
            pending = None
            continue

        if message_type == "ErrorResponse":
            require(direction == "host_to_client" and pending is not None and
                    pending["kind"] == "error",
                    f"{label}: ErrorResponse has no matching expected error")
            validate_error(label, payload, pending, current_host, sessions, coverage)
            if pending["error_code"] == "ERROR_CODE_STALE_SESSION":
                invalidated.pop(pending["session_id"], None)
            pending = None
            continue

        raise ValidationError(f"{label}: unexpected response type")

    require(pending is None, f"{scenario_id}: final request has no response")
    require(used_boundaries == set(boundary_by_frame) and connection_state == "ready",
            f"{scenario_id}: every connection must complete ClientHello -> HostHello")
    require(not sessions, f"{scenario_id}: active sessions must be explicitly ended")
    require(editor_commits == expected_editor_commits,
            f"{scenario_id}: projected editor commits differ: {editor_commits}")
    require(not applied_response_keys,
            f"{scenario_id}: ended sessions must clear the applied-response ledger")
    for exchange in ended.values():
        require(set(json.loads(exchange.request_payload)) == {
                    "host_instance_id", "session_id", "request_seq"
                } and
                set(json.loads(exchange.response_payload)) == {
                    "host_instance_id", "session_id", "ack_request_seq"
                },
                f"{scenario_id}: end tombstone must be content-free")
    if len(expected_editor_commits) >= 2:
        require(len(projected_commit_keys) == len(expected_editor_commits) and
                len(set(projected_commit_keys)) == len(projected_commit_keys) and
                len({key[2] for key in projected_commit_keys}) >= 2 and
                len(set(expected_editor_commits)) >= 2,
                f"{scenario_id}: a new request_seq must be able to project a distinct commit")
        coverage.distinct_commit_projected = True
    return seen_types


def validate_vectors(vectors: dict[str, Any], proto: str) -> None:
    require(vectors.get("format_version") == 2, "golden vector format_version must be 2")
    require(vectors.get("protocol_version") == PROTOCOL_VERSION,
            "golden vectors must target protocol v2")
    recorded_digest = vectors.get("protocol_sha256")
    digest = hashlib.sha256(proto.encode("utf-8")).hexdigest()
    require(isinstance(recorded_digest, str) and SHA256.fullmatch(recorded_digest) is not None and
            recorded_digest == digest,
            "golden vectors are not bound to the current protocol file")
    privacy = vectors.get("privacy")
    require(isinstance(privacy, dict) and
            privacy.get("source") == "project-authored synthetic fixtures" and
            privacy.get("contains_personal_data") is False and
            privacy.get("typed_text_persistence_allowed") is False and
            privacy.get("network_allowed") is False,
            "golden fixtures must remain synthetic, offline, and non-persistent")

    required = vectors.get("required_scenarios")
    scenarios = vectors.get("scenarios")
    expected_scenarios = {
        "candidate-page-select", "explicit-commit-and-option", "cancel-composition",
        "error-contracts", "host-restart-invalidation", "privacy-contexts",
    }
    require(isinstance(required, list) and set(required) == expected_scenarios and
            len(required) == len(set(required)),
            "required scenario set drifted")
    require(isinstance(scenarios, list), "scenarios must be an array")
    scenario_map = {
        scenario.get("id"): scenario
        for scenario in scenarios
        if isinstance(scenario, dict)
    }
    require(set(scenario_map) == expected_scenarios and len(scenario_map) == len(scenarios),
            "scenario ids must be complete and unique")

    coverage = Coverage()
    frame_ids: set[str] = set()
    seen_types: set[str] = set()
    for scenario_id in required:
        seen_types.update(validate_scenario(scenario_map[scenario_id], frame_ids, coverage))
    require(FRAME_TYPES <= seen_types, "golden frames do not cover every protocol frame type")
    for field_name, value in vars(coverage).items():
        require(value is True, f"golden coverage missing: {field_name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-build-ready",
        action="store_true",
        help="fail unless source, dependency, relationship, protocol, and license gates are resolved",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        blockers = validate_lock(load_json(LOCK_PATH))
        validate_rime_sdk_lock(load_json(RIME_SDK_LOCK_PATH))
        try:
            proto = PROTO_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError(f"cannot read protocol file: {exc}") from exc
        validate_proto(proto)
        validate_framing(load_json(FRAMING_PATH))
        validate_invalid_utf16_fixture(load_json(INVALID_UTF16_PATH))
        validate_vectors(load_json(VECTORS_PATH), proto)
        if args.require_build_ready and blockers:
            raise ValidationError("build-ready gate remains blocked: " + "; ".join(blockers))
    except ValidationError as exc:
        print(f"WINDOWS IME BOOTSTRAP VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "WINDOWS IME BOOTSTRAP STATIC VALIDATION PASSED "
        "(production source + pinned external librime SDK; release hardening gates remain open)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
