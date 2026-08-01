#!/usr/bin/env python3
"""Fail-closed source validation for the isolated ClipVault librime JNI contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "POC_LOCK.json"

REQUIRED_FILES = {
    "header": ROOT / "native/include/clipvault_rime_engine.h",
    "engine": ROOT / "native/src/clipvault_rime_engine.cpp",
    "jni": ROOT / "native/src/clipvault_rime_jni.cpp",
    "cmake": ROOT / "native/CMakeLists.txt",
    "kotlin": (
        ROOT
        / "android/src/main/kotlin/com/clipvault/poc/rime/RimeNativeEngine.kt"
    ),
}

REQUIRED_NATIVE_METHODS = {
    "nativeOpen",
    "nativeClose",
    "nativeReset",
    "nativeProcessKey",
    "nativeSnapshot",
    "nativeSelectCandidate",
    "nativeTakeCommit",
    "nativeEngineVersion",
}

ALLOWED_RIME_API_CALLS = {
    "setup",
    "initialize",
    "finalize",
    "start_maintenance",
    "join_maintenance_thread",
    "create_session",
    "destroy_session",
    "process_key",
    "clear_composition",
    "get_context",
    "free_context",
    "get_commit",
    "free_commit",
    "select_schema",
    "select_candidate_on_current_page",
    "get_version",
}

FORBIDDEN_SOURCE_PATTERNS = {
    "network API": r"\b(?:socket|connect|sendto|recvfrom|OkHttp|HttpUrlConnection)\b",
    "persistence API": (
        r"\b(?:Room|SharedPreferences|SQLiteDatabase|ofstream|fopen|fwrite)\b"
    ),
    "clipboard API": r"\b(?:ClipboardManager|ClipData|clipboard)\b",
    "Rime sync/user config": r"\b(?:sync_user_data|user_config_open)\b",
    "direct typed-data logging": (
        r"\b(?:__android_log_print|printf|fprintf|std::cout|std::cerr|LOG\s*\()"
    ),
    "source acquisition": r"\b(?:FetchContent|ExternalProject|git clone|curl|wget)\b",
}

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ValidationError(ValueError):
    pass


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValidationError(f"missing required native-contract file: {path}") from exc


def load_lock() -> dict[str, Any]:
    try:
        value = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {LOCK_PATH}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("POC_LOCK.json must contain an object")
    return value


def validate_lock(lock: dict[str, Any]) -> None:
    track = lock.get("tracks", {}).get("A_custom_librime_jni")
    if not isinstance(track, dict):
        raise ValidationError("A_custom_librime_jni lock is missing")
    profile = track.get("native_build_profile")
    if not isinstance(profile, dict):
        raise ValidationError("A native_build_profile is missing")

    required_flags = {
        "BUILD_SHARED_LIBS": True,
        "BUILD_STATIC": True,
        "BUILD_DATA": False,
        "BUILD_TEST": False,
        "ENABLE_LOGGING": False,
        "ENABLE_TIMESTAMP": False,
        "ENABLE_EXTERNAL_PLUGINS": False,
    }
    flags = profile.get("librime_cmake_flags")
    if flags != required_flags:
        raise ValidationError("A librime CMake flags drifted from the frozen profile")
    if profile.get("transitive_closure_status") != "INCOMPLETE_FAIL_CLOSED":
        raise ValidationError("A transitive dependency closure must remain fail-closed")

    dependencies = profile.get("direct_dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValidationError("A direct dependency lock must be non-empty")
    names: set[str] = set()
    for item in dependencies:
        if not isinstance(item, dict):
            raise ValidationError("dependency entries must be objects")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValidationError("dependency name is missing")
        if name in names:
            raise ValidationError(f"duplicate dependency lock: {name}")
        names.add(name)
        if item.get("source_kind") == "git":
            sha = item.get("sha")
            if not isinstance(sha, str) or SHA40.fullmatch(sha) is None:
                raise ValidationError(f"{name} must use an exact lowercase Git SHA")
        elif item.get("source_kind") == "archive":
            digest = item.get("archive_sha256")
            if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                raise ValidationError(f"{name} archive SHA-256 is invalid")
        else:
            raise ValidationError(f"{name} has unsupported source_kind")
        if not isinstance(item.get("spdx"), str) or not item["spdx"]:
            raise ValidationError(f"{name} SPDX expression is missing")
        if not isinstance(item.get("included_in_poc_binary"), bool):
            raise ValidationError(f"{name} inclusion status must be boolean")

    expected_names = {
        "boost",
        "glog",
        "leveldb",
        "yaml-cpp",
        "googletest",
        "marisa-trie",
        "opencc",
    }
    if names != expected_names:
        raise ValidationError(
            f"A direct dependency set mismatch: expected {sorted(expected_names)}, "
            f"got {sorted(names)}"
        )


def validate_source_contract(texts: dict[str, str]) -> None:
    combined = "\n".join(texts.values())
    for label, pattern in FORBIDDEN_SOURCE_PATTERNS.items():
        if re.search(pattern, combined, flags=re.IGNORECASE):
            raise ValidationError(f"forbidden {label} found in native contract")

    engine_calls = set(re.findall(r"api_->([a-zA-Z0-9_]+)\s*\(", texts["engine"]))
    unexpected_calls = engine_calls - ALLOWED_RIME_API_CALLS
    if unexpected_calls:
        raise ValidationError(
            f"unapproved librime API calls: {sorted(unexpected_calls)}"
        )
    missing_calls = {
        "process_key",
        "get_context",
        "select_candidate_on_current_page",
        "get_commit",
        "clear_composition",
    } - engine_calls
    if missing_calls:
        raise ValidationError(
            f"required librime API calls are missing: {sorted(missing_calls)}"
        )

    kotlin_methods = set(
        re.findall(r"private\s+external\s+fun\s+(native[A-Za-z0-9_]+)", texts["kotlin"])
    )
    if kotlin_methods != REQUIRED_NATIVE_METHODS:
        raise ValidationError(
            f"Kotlin JNI surface mismatch: expected {sorted(REQUIRED_NATIVE_METHODS)}, "
            f"got {sorted(kotlin_methods)}"
        )
    jni_methods = set(
        re.findall(
            r"Java_com_clipvault_poc_rime_RimeNativeEngine_(native[A-Za-z0-9_]+)",
            texts["jni"],
        )
    )
    if jni_methods != REQUIRED_NATIVE_METHODS:
        raise ValidationError(
            f"C++ JNI surface mismatch: expected {sorted(REQUIRED_NATIVE_METHODS)}, "
            f"got {sorted(jni_methods)}"
        )

    required_kotlin_guards = {
        "Thread.currentThread().id",
        "canonicalFile",
        "data_directories_must_not_overlap",
        "shared.isDirectory",
        "user.mkdirs()",
        'System.loadLibrary("clipvault_rime_poc")',
        "private var nativeHandle",
        "override fun close()",
    }
    for guard in required_kotlin_guards:
        if guard not in texts["kotlin"]:
            raise ValidationError(f"missing Kotlin lifecycle guard: {guard}")

    for engine_guard in (
        "g_engine_active",
        "pending_commit",
        'traits.log_dir = ""',
        'select_schema(session_id_, "clipvault_poc")',
    ):
        if engine_guard not in texts["engine"]:
            raise ValidationError(f"missing native lifecycle/privacy guard: {engine_guard}")

    cmake = texts["cmake"]
    for requirement in (
        "max-page-size=16384",
        "common-page-size=16384",
        "CLIPVAULT_RIME_INCLUDE_DIR",
        "CLIPVAULT_RIME_LIBRARY",
        "add_library(clipvault_librime SHARED IMPORTED GLOBAL)",
    ):
        if requirement not in cmake:
            raise ValidationError(f"missing CMake contract: {requirement}")

    if "ClipVaultFullKeyboardService" in combined or "settings.gradle" in combined:
        raise ValidationError("native contract must remain detached from production IME")


def main() -> int:
    try:
        lock = load_lock()
        validate_lock(lock)
        texts = {name: read_text(path) for name, path in REQUIRED_FILES.items()}
        validate_source_contract(texts)
    except (OSError, UnicodeError, ValidationError) as exc:
        print(f"POC NATIVE CONTRACT VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    print("POC NATIVE CONTRACT VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
