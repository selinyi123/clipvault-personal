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
    replay = read("host/replay_ledger.cpp")
    snapshot = read("host/runtime_snapshot.cpp")
    otp_host = read("host/otp_broker_client.cpp")
    rime = read("host/rime_engine.cpp")
    service = read("tsf/text_service.cpp")
    candidates = read("tsf/candidate_window.cpp")
    candidate_layout = read("tsf/candidate_layout.cpp")
    candidate_layout_test = read("tests/candidate_layout_tests.cpp")
    evidence_editor = read("tests/tsf_evidence_editor.cpp")
    dbwin_capture = read("tests/dbwin_diagnostics_capture.cpp")
    registration = read("tsf/registration.cpp")
    cmake = read("CMakeLists.txt")
    build_script = read("scripts/Build-NativeSlice.ps1")
    read("scripts/Register-ClipVaultIme.ps1")
    read("scripts/Unregister-ClipVaultIme.ps1")
    read("scripts/Configure-ClipVaultImeUser.ps1")
    read("scripts/Test-ClipVaultInstallerOwner.ps1")
    read("scripts/Stop-ClipVaultImeOwnerProcesses.ps1")
    read("tests/protocol_tests.cpp")
    engine_v2_test = read("tests/engine_v2_semantics.cpp")
    replay_test = read("tests/replay_ledger_tests.cpp")
    engine_v2_vectors = read("tests/engine_v2_vectors.tsv")
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
    legacy_installer_include = read("installer/ClipVaultImeV2.iss.inc")
    root_installer = (REPOSITORY_ROOT / "installer" / "clipvault.iss").read_text(
        encoding="utf-8"
    )
    daily_installer = (
        REPOSITORY_ROOT / "installer" / "clipvault-v2-daily.iss"
    ).read_text(encoding="utf-8")
    package_include = (
        REPOSITORY_ROOT / "installer" / "ClipVaultImeV2Package.iss.inc"
    ).read_text(encoding="utf-8")
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
                       "EncodeSetOption", "EncodeEndSession",
                       "EncodeSessionEnded", "EncodeResponseAck",
                       "ResponseProjectionLedger::Reserve",
                       "EncodeInsertOtp", "DecodeInsertOtp",
                       "ReadFrameUntil", "WriteFrameUntil", "CancelIoEx",
                       "FILE_FLAG_OVERLAPPED"),
            "wire implementation")
    require(host, ("CreateNamedPipeW", "PIPE_REJECT_REMOTE_CLIENTS",
                   "PIPE_UNLIMITED_INSTANCES",
                   "ConvertSidToStringSidW", "SDDL_REVISION_1", "ConnectNamedPipe",
                   "std::jthread", "--deploy-rime", "PromoteEchoSessionToRime",
                   "kMaximumConcurrentConnections", "ReadClientFrame",
                   "StopWorkers", "FILE_FLAG_OVERLAPPED"),
            "external Host")
    if ".detach()" in host or "FlushFileBuffers(pipe)" in host:
        fail("external Host must use bounded joined workers and deadline I/O")
    require(host, ("ReplayLedger", "LookupResponse", "CacheResponse",
                   "RememberEnded", "DecodeResponseAck", "DecodeEndSession",
                   "Operation::kSetOption", "rime->SetOption"),
            "Host Engine V2 lifecycle")
    require(replay, ("SipHash24", "SecureZeroMemory", "maximum_responses_",
                     "maximum_total_response_bytes_", "PruneLocked",
                     "Acknowledge", "RememberEnded", "Reaper"),
            "bounded Engine V2 replay ledger")
    require(engine_v2_test, ("duplicate Start returns cached bytes",
                             "duplicate commit returns cached transition",
                             "cached commit projected at most once",
                             "duplicate EndSession is idempotent",
                             "ended session rejects old mutations"),
            "native ENG2-V003/V008 semantics")
    require(replay_test, ("authenticated acknowledgement wipes response cache",
                          "retry deadline wipes unacknowledged response",
                          "response state remains bounded",
                          "content-free tombstones remain bounded"),
            "native replay-ledger bounds")
    require(engine_v2_vectors,
            ("ENG2-V003-A01", "ENG2-V003-A02", "ENG2-V003-A03",
             "ENG2-V003-A04", "ENG2-V003-A05", "ENG2-V008-A01",
             "ENG2-V008-A02", "ENG2-V008-A03", "ENG2-V008-A04"),
            "Windows Engine V2 assertion mapping")
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
                       "retired_epochs", "WipeSurface", "Consume",
                       "RequestRefresh", "fetch_in_flight",
                       "kMaximumConcurrentSnapshotFetches"),
            "Runtime Snapshot V1 client")
    require(rime, ("LoadLibraryExW", "rime_get_api", "process_key",
                   "select_candidate_on_current_page", "change_page",
                   "commit_composition", "clear_composition", "set_option",
                   "select_schema", "clipvault_pinyin_private"),
            "librime adapter")
    require(service, ("AdviseKeyEventSink", "RequestEditSession", "TF_ES_SYNC",
                      "ITfInsertAtSelection", "TF_IAS_QUERYONLY",
                      "StartComposition", "EndComposition", "CreateProcessW",
                      "SelectCandidate", "ChangeCandidatePage", "RecoverPlainKey",
                      "IS_PASSWORD", "RemainingBudget", "ToUnicodeEx",
                      "GetTextExt", "GetScreenExt", "IsCurrentContext",
                      "PreservePreeditLiteral", "ReplayBufferedPreedit",
                      "kLocalBufferUpdated", "SelectSnapshotCandidate"),
            "TSF text service")
    require(service, ("PreserveKey", "kOtpInsertPreservedKey",
                      "BuildOtpContext", "GetGUIThreadInfo",
                      "InputDesktopIsUnlocked", "GetSystemMetrics",
                      "SM_REMOTESESSION", "InsertOtp", "ApplyState"),
            "TSF explicit OTP insertion")
    require(service, ("ITfInputScope", "IS_PASSWORD", "IS_PRIVATE",
                      "ClassifyInputContext", "learning_allowed",
                      "clipvault_allowed"), "editor privacy classification")
    require(candidates, ("WS_EX_NOACTIVATE", "SWP_NOACTIVATE", "WM_LBUTTONDOWN",
                         "PgUp", "PgDn", "ClipVault", "snapshot_surface_",
                         "ScaleMetrics", "MeasureWindow", "PlaceWindow",
                         "HitTest"),
            "candidate window")
    require(candidate_layout, ("ScaleMetrics", "MeasureWindow", "PlaceWindow",
                               "HitTest", "work_area.left",
                               "HitKind::kEngineCandidate",
                               "HitKind::kSnapshotCandidate"),
            "pure candidate layout")
    require(candidate_layout_test, ("TestDpiScaling", "TestWindowMeasurement",
                                    "TestWorkAreaPlacement", "TestHitTargets",
                                    "left_monitor", "dpi192"),
            "executable candidate layout coverage")
    require(evidence_editor, ("ClipVault TSF Evidence Editor", "CreateWindowExW",
                              "L\"EDIT\"", "ES_MULTILINE", "SetFocus"),
            "manual TSF evidence editor")
    require(dbwin_capture, ("DBWIN_BUFFER_READY", "DBWIN_DATA_READY",
                            "DBWIN_BUFFER", "ClipVaultIme event=",
                            "starts_with"),
            "content-free TSF DBWIN diagnostics capture")
    require(registration, ("HKEY_LOCAL_MACHINE", "KEY_WOW64_64KEY",
                           "KEY_WOW64_32KEY", "CLIPVAULT_TSF_PROFILE_OWNER",
                           "AddLanguageProfile", "EnableLanguageProfile",
                           "GUID_TFCAT_TIP_KEYBOARD",
                           "DllRegisterServer", "DllUnregisterServer",
                           "return result"), "machine TSF registration")
    require(cmake, ("ClipVaultImeHost", "ClipVaultTextService", "host-smoke",
                    "host-restart-smoke", "rime-host-smoke",
                    "connect-budget", "pipe-deadline", "rime-upgrade-from-echo",
                    "engine-v2-replay-ledger", "engine-v2-semantics",
                    "clipvault_runtime_snapshot_host", "runtime-snapshot-v1",
                    "runtime-snapshot-pipe",
                    "ClipVaultOtpBroker", "clipvault_otp_broker",
                    "otp-wincred-cvpk-v1", "otp-broker-pipe",
                    "CLIPVAULT_RIME_SDK_DIR", "CLIPVAULT_REQUIRE_RIME_RUNTIME",
                    "CMP0091", "CMAKE_MSVC_RUNTIME_LIBRARY", "MultiThreaded",
                    "CLIPVAULT_TSF_PROFILE_OWNER=1",
                    "CLIPVAULT_TSF_PROFILE_OWNER=0", "/W4", "/WX"),
            "native CMake build")
    require(cmake, ("clipvault_candidate_layout", "clipvault_candidate_layout_tests",
                    "clipvault-ime-candidate-layout",
                    "clipvault_tsf_evidence_editor",
                    "clipvault_dbwin_diagnostics_capture"),
            "candidate layout CTest wiring")
    require(build_script, ("Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                           "ctest.exe", "--output-on-failure", "RimeSdkDirectory",
                           "RimeDataDirectory", "Architecture", "RequireRime"),
            "native build script")
    require(production_build, ("Prepare-ProductionRimeData.ps1",
                               "RimeDictionaryDirectory", "RequireRime",
                               "-Configuration Release -Architecture x64",
                               "-Configuration Release -Architecture x86",
                               "-Configuration Debug -Architecture x64",
                               "-Configuration Debug -Architecture x86",
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
    require(root_installer, ("PrivilegesRequired=lowest",
                             "frozen v1 installer cannot own v2 IME"),
            "frozen v1 installer boundary")
    if "ClipVaultImeV2.iss.inc" in root_installer:
        fail("frozen v1 installer still owns a v2 lifecycle include")
    require(production_data, ("RIME_ASSET_LOCK.json", "Get-FileHash",
                              "allowed_staged_files", "BuildRoot",
                              "Unapproved Rime dependency"),
            "production Rime staging gate")
    register_script = read("scripts/Register-ClipVaultIme.ps1")
    require(register_script, ("AllowMachineWideRegistration", "Start-Process",
                              "-WindowStyle Hidden", "-Wait", "SysWOW64",
                              "RegistrationSchema", "RepairRequired",
                              "PackageDirectory"),
            "explicit machine registration")
    require(package_include, ("host-x64\\*", "otp-broker\\*", "x64\\*",
                              "x86\\*", "scripts\\*", "licenses\\*"),
            "files-only v2 package include")
    forbidden_lifecycle = ("[Run]", "[Registry]", "[UninstallRun]",
                           "Register-ClipVaultIme.ps1")
    if any(token in package_include for token in forbidden_lifecycle):
        fail("files-only v2 package include gained lifecycle actions")
    require(legacy_installer_include, ("#error", "legacy v2 lifecycle include is retired"),
            "retired v2 lifecycle include")
    require(daily_installer, ("ExecAsOriginalUser", "HKU", "OwnerSid",
                              "RepairRequired", "GetCustomSetupExitCode",
                              "AllowMachineWideRegistration",
                              "Configure-ClipVaultImeUser.ps1",
                              "DeployRimeForOriginalUser",
                              "-ExpectedOwnerSid"),
            "single v2 lifecycle installer")
    if "PowerShellScript('Deploy-RimeData.ps1'" in daily_installer:
        fail("v2 installer deploys Rime through the elevated setup identity")
    capture = daily_installer.index("CaptureOriginalUser()", daily_installer.index("procedure InstallV2Stack"))
    deploy = daily_installer.index("DeployRimeForOriginalUser()", capture)
    register = daily_installer.index("Register-ClipVaultIme.ps1", deploy)
    if not capture < deploy < register:
        fail("v2 installer no longer binds the original user before Rime deployment and machine registration")
    deploy_script = read("scripts/Deploy-RimeData.ps1")
    require(deploy_script, ("ExpectedOwnerSid", "WindowsIdentity]::GetCurrent()",
                            "WindowsBuiltInRole]::Administrator",
                            "ownerLocalAppData", "userDataDirectory.StartsWith"),
            "original-user Rime deployment")
    otp_context = service[service.index("bool TextService::BuildOtpContext"):
                          service.index("void TextService::InsertLatestOtp")]
    if "GetSystemMetrics(SM_REMOTESESSION) != 0" not in otp_context:
        fail("TSF OTP context does not reject Remote Desktop sessions")
    otp_insert = service[service.index("void TextService::InsertLatestOtp"):
                         service.index("void TextService::ResetOtpContext")]
    if otp_insert.index("BuildOtpContext") > otp_insert.index("engine_.InsertOtp"):
        fail("TSF consumes OTP before validating the local interactive context")
    require(otp_insert, ("const HRESULT projected", "ApplyOtpCommit",
                         "if (FAILED(projected))", "ResetEngine()"),
            "fail-closed observable OTP projection")
    if service.count("InsertLatestOtp(context)") != 1:
        fail("TSF OTP insertion is no longer limited to the preserved-key action")
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
