#!/usr/bin/env python3
"""Fail-closed static checks for the project-authored Windows native slice."""

from __future__ import annotations

import re
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[1]


def fail(message: str) -> None:
    raise RuntimeError(message)


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        fail(f"missing native source: {relative}")
    return path.read_text(encoding="utf-8")


def require(text: str, tokens: tuple[str, ...], label: str) -> None:
    missing = [token for token in tokens if token not in text]
    if missing:
        fail(f"{label} missing required boundary tokens: {missing}")


def main() -> int:
    protocol = read("common/protocol.cpp")
    host = read("host/main.cpp")
    snapshot = read("host/runtime_snapshot.cpp")
    otp_host = read("host/otp_broker_client.cpp")
    rime = read("host/rime_engine.cpp")
    service = read("tsf/text_service.cpp")
    candidates = read("tsf/candidate_window.cpp")
    registration = read("tsf/registration.cpp")
    cmake = read("CMakeLists.txt")
    build_script = read("scripts/Build-NativeSlice.ps1")
    read("scripts/Register-ClipVaultIme.ps1")
    read("scripts/Unregister-ClipVaultIme.ps1")
    read("tests/protocol_tests.cpp")
    read("tests/host_smoke.cpp")
    read("tests/host_restart_smoke.cpp")
    read("tests/host_ready_smoke.cpp")
    read("tests/host_rime_upgrade_smoke.cpp")
    read("tests/pipe_deadline_tests.cpp")
    read("tests/runtime_snapshot_tests.cpp")
    read("tests/runtime_snapshot_pipe_tests.cpp")
    read("tests/Test-InstallScripts.ps1")
    production_build = read("scripts/Build-ProductionIme.ps1")
    dependency_audit = read("scripts/Test-ProductionDependencies.ps1")
    installer_gate = read("scripts/Test-InstallerInclude.ps1")
    read("tests/ClipVaultImeV2Syntax.iss")
    production_data = read("scripts/Prepare-ProductionRimeData.ps1")
    read("scripts/Package-ClipVaultIme.ps1")
    read("scripts/Deploy-RimeData.ps1")
    read("scripts/Enable-ClipVaultOtpBroker.ps1")
    read("scripts/Disable-ClipVaultOtpBroker.ps1")
    installer_include = read("installer/ClipVaultImeV2.iss.inc")
    root_installer = (REPOSITORY_ROOT / "installer" / "clipvault.iss").read_text(
        encoding="utf-8"
    )
    prepare_rime = read("scripts/Prepare-RimeSdk.ps1")
    rime_lock = json.loads(read("rime/RIME_SDK_LOCK.json"))
    read("rime/LICENSE-librime.txt")
    otp_root = REPOSITORY_ROOT / "windows" / "otp-relay"
    otp_credential = "\n".join(
        (otp_root / "authority" / name).read_text(encoding="utf-8")
        for name in ("pair_credential.h", "pair_credential.cpp")
    )
    otp_server = (otp_root / "broker" / "broker_server.cpp").read_text(
        encoding="utf-8"
    )
    otp_core = (otp_root / "broker" / "otp_broker_core.cpp").read_text(
        encoding="utf-8"
    )
    otp_prompt = (otp_root / "broker" / "otp_prompt.cpp").read_text(
        encoding="utf-8"
    )
    asset_lock_path = REPOSITORY_ROOT / "shared-input" / "rime" / "RIME_ASSET_LOCK.json"
    try:
        asset_lock = json.loads(asset_lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read canonical Rime asset lock: {exc}")

    require(protocol, ("kMaximumFrameBytes", "ReadFrame", "WriteFrame",
                       "EncodeClientHello", "DecodeHostHello", "WaitNamedPipeW",
                       "EncodeSelectCandidate", "EncodePageCandidates",
                       "EncodeCommitComposition", "EncodeCancelComposition",
                       "EncodeInsertOtp", "DecodeInsertOtp",
                       "ReadFrameUntil", "WriteFrameUntil", "CancelIoEx",
                       "FILE_FLAG_OVERLAPPED"),
            "wire implementation")
    require(host, ("CreateNamedPipeW", "PIPE_REJECT_REMOTE_CLIENTS",
                   "PIPE_UNLIMITED_INSTANCES",
                   "ConvertSidToStringSidW", "SDDL_REVISION_1", "ConnectNamedPipe",
                   "std::jthread", "--deploy-rime", "PromoteEchoSessionToRime"),
            "external Host")
    require(host, ("RuntimeSnapshotCoordinator", "BeginSession",
                   "SelectSnapshotCandidate", "snapshot_surface"),
            "Host Runtime Snapshot integration")
    require(host, ("OtpBrokerInsertClient", "ConsumeLatest",
                   "SecureZeroMemory", "kInsertOtp"),
            "Host OTP insertion integration")
    require(otp_host, ("EncodeArmLatest", "EncodeConsume", "ConnectUntil",
                       "BrokerStatus::kConsumed", "SecureZeroMemory"),
            "bounded Host OTP broker client")
    require(snapshot, ("ClipVaultRuntimeSnapshotV1-", "GetNamedPipeServerProcessId",
                       "ProcessUserMatchesCurrent", "QueryFullProcessImageNameW",
                       "WinVerifyTrust", "kRuntimeSnapshotDeadlineMilliseconds",
                       "FILE_FLAG_OVERLAPPED", "CancelIoEx", "publisher_epoch",
                       "retired_epochs", "WipeSurface", "Consume"),
            "Runtime Snapshot V1 client")
    require(rime, ("LoadLibraryExW", "rime_get_api", "process_key",
                   "select_candidate_on_current_page", "change_page",
                   "commit_composition", "clear_composition", "set_option",
                   "select_schema", "clipvault_pinyin_private"),
            "librime adapter")
    require(service, ("AdviseKeyEventSink", "RequestEditSession", "TF_ES_SYNC",
                      "StartComposition", "EndComposition", "CreateProcessW",
                      "SelectCandidate", "ChangeCandidatePage", "RecoverPlainKey",
                      "IS_PASSWORD", "RemainingBudget", "ToUnicodeEx",
                      "GetTextExt", "GetScreenExt", "IsCurrentContext",
                      "PreservePreeditLiteral", "ReplayBufferedPreedit",
                      "kLocalBufferUpdated", "SelectSnapshotCandidate"),
            "TSF text service")
    require(service, ("PreserveKey", "kOtpInsertPreservedKey",
                      "BuildOtpContext", "GetGUIThreadInfo",
                      "InputDesktopIsUnlocked", "InsertOtp", "ApplyState"),
            "TSF explicit OTP insertion")
    require(service, ("ITfInputScope", "IS_PASSWORD", "IS_PRIVATE",
                      "ClassifyInputContext", "learning_allowed",
                      "clipvault_allowed"), "editor privacy classification")
    require(candidates, ("WS_EX_NOACTIVATE", "SWP_NOACTIVATE", "WM_LBUTTONDOWN",
                         "PgUp", "PgDn", "ClipVault", "snapshot_surface_"),
            "candidate window")
    require(registration, ("HKEY_CURRENT_USER", "AddLanguageProfile",
                           "GUID_TFCAT_TIP_KEYBOARD", "DllRegisterServer",
                           "DllUnregisterServer"), "per-user TSF registration")
    require(cmake, ("ClipVaultImeHost", "ClipVaultTextService", "host-smoke",
                    "host-restart-smoke", "rime-host-smoke",
                    "connect-budget", "pipe-deadline", "rime-upgrade-from-echo",
                    "clipvault_runtime_snapshot_host", "runtime-snapshot-v1",
                    "runtime-snapshot-pipe",
                    "ClipVaultOtpBroker", "clipvault_otp_broker",
                    "otp-wincred-cvpk-v1", "otp-broker-pipe",
                    "CLIPVAULT_RIME_SDK_DIR", "CLIPVAULT_REQUIRE_RIME_RUNTIME",
                    "CMP0091", "CMAKE_MSVC_RUNTIME_LIBRARY", "MultiThreaded",
                    "/W4", "/WX"),
            "native CMake build")
    require(build_script, ("Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                           "ctest.exe", "--output-on-failure", "RimeSdkDirectory",
                           "RimeDataDirectory", "Architecture", "RequireRime"),
            "native build script")
    require(production_build, ("Prepare-ProductionRimeData.ps1",
                               "RimeDictionaryDirectory", "RequireRime",
                               "Package-ClipVaultIme.ps1",
                               "Test-ProductionDependencies.ps1",
                               "Test-InstallerInclude.ps1"),
            "production build entry")
    require(dependency_audit, ("dumpbin.exe", "msvcp.*", "vcruntime.*",
                               "ClipVaultOtpBroker.exe", "CredReadW",
                               "BCryptDecrypt", "CLEAN-MACHINE"),
            "clean-machine dependency audit")
    require(installer_gate, ("ISCC.exe", "ClipVaultImeV2Syntax.iss",
                             "CLIPVAULT V2 INSTALLER INCLUDE COMPILE PASSED"),
            "v2 installer compile gate")
    require(root_installer, ("ClipVaultImeV2PackageDir",
                             "ClipVaultImeV2.iss.inc",
                             "PrivilegesRequired=admin"),
            "root v2 installer integration")
    require(production_data, ("RIME_ASSET_LOCK.json", "Get-FileHash",
                              "allowed_staged_files", "BuildRoot",
                              "Unapproved Rime dependency"),
            "production Rime staging gate")
    register_script = read("scripts/Register-ClipVaultIme.ps1")
    require(register_script, ("AllowSystemWideTsfRegistration", "Start-Process",
                              "-WindowStyle Hidden", "-Wait", "SysWOW64",
                              "host-x64", "PackageDirectory"),
            "explicit mixed-scope registration")
    require(installer_include, ("ClipVaultImeHostV2", "UninstallRun",
                                "Deploy-RimeData.ps1", "Register-ClipVaultIme.ps1",
                                "otp-broker", "clipvaultotpbroker",
                                "Enable-ClipVaultOtpBroker.ps1",
                                "Disable-ClipVaultOtpBroker.ps1"),
            "v2 installer include")
    require(otp_credential, ("CVPK", "CredReadW", "CredWriteW",
                             "kPairCredentialBytes", "Read-after-write",
                             "AdvanceHighSequence"),
            "current-user CVPK authority")
    require(otp_server, ("PIPE_REJECT_REMOTE_CLIENTS", "CancelIoEx",
                         "BrokerClientRole::kOpaqueDesktopOffer",
                         "BrokerClientRole::kImeHostControl",
                         "DecodeArmLatest"),
            "local opaque OTP broker")
    require(otp_core, ("DecryptOtp", "AdvanceHighSequence",
                       "BrokerStatus::kUnavailable", "ArmLatest",
                       "kClaimTtlMilliseconds"),
            "fail-closed OTP core")
    require(otp_prompt, ("WS_EX_NOACTIVATE", "SWP_NOACTIVATE",
                         "WDA_EXCLUDEFROMCAPTURE", "Ctrl+Alt+O"),
            "non-activating OTP prompt")
    require(prepare_rime, ("e17c1bb4acc9934669e7a62003aef3f8b56d0afa89e5d893ed7dbf34546abb6e",
                           "Get-FileHash", "Invoke-WebRequest", "-E tar xf"),
            "pinned librime SDK preparation")
    if rime_lock.get("official_windows_asset", {}).get("sha256") != \
            "e17c1bb4acc9934669e7a62003aef3f8b56d0afa89e5d893ed7dbf34546abb6e":
        fail("official librime x64 SDK hash drifted")

    canonical_assets = asset_lock.get("canonical_assets")
    dictionary_assets = asset_lock.get("dictionary_source", {}).get("assets")
    allowed_files = asset_lock.get("allowed_staged_files")
    if not isinstance(canonical_assets, dict) or not isinstance(dictionary_assets, dict):
        fail("canonical Rime asset lock is incomplete")
    expected_files = sorted([*canonical_assets, *dictionary_assets])
    if sorted(allowed_files or []) != expected_files:
        fail("canonical Rime allowed staged file set drifted")
    canonical_root = asset_lock_path.parent
    for name, expected_hash in canonical_assets.items():
        path = canonical_root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            fail(f"canonical Rime asset hash drifted: {name}")
    if asset_lock.get("dictionary_source", {}).get("commit") != \
            "0c6861ef7420ee780270ca6d993d18d4101049d0":
        fail("rime-pinyin-simp production commit drifted")

    tsf_tree = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "tsf").glob("*"))
        if path.suffix.lower() in {".cpp", ".h", ".def"}
    ).lower()
    forbidden = ("python.h", "sqlite3", "winsock", "winhttp", "wininet",
                 "libcurl", "librime", "websocket", "http://", "https://")
    present = [token for token in forbidden if token in tsf_tree]
    if present:
        fail(f"TSF DLL boundary contains forbidden dependency tokens: {present}")
    otp_forbidden = ("credreadw", "credwritew", "bcrypt", "aes-256",
                     "pair_verifier", "clipvault/otp/pair", "sendinput")
    otp_present = [token for token in otp_forbidden if token in tsf_tree]
    if otp_present:
        fail(f"TSF DLL contains forbidden OTP authority/injection tokens: {otp_present}")

    if not re.search(r"kMaximumFrameBytes\s*=\s*1'048'576", read("common/protocol.h")):
        fail("native maximum frame bound drifted from 1,048,576 bytes")
    print("WINDOWS IME PRODUCTION NATIVE STATIC VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"WINDOWS IME NATIVE SKELETON VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
