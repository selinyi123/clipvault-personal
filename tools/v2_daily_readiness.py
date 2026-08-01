#!/usr/bin/env python3
"""Read-only readiness report for the ClipVault v2 daily-use candidate.

The report separates repository-local source gates, one receipt-bound CI
candidate, and Owner-controlled release evidence. It never builds, signs,
installs, registers a TSF profile, talks to a device, reads secrets, calls
GitHub, or publishes artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
ANDROID_NS = "http://schemas.android.com/apk/res/android"
OWNER_EVIDENCE = "artifacts/v2-daily/owner-evidence.json"
DEFAULT_CANDIDATE_DIR = "artifacts/v2-daily/bundle"
THIRD_PARTY_VALIDATOR = Path(__file__).with_name("validate_v2_third_party.py")
CANDIDATE_VERIFIER = Path(__file__).with_name("v2_daily_candidate.py")
ANDROID_SIGNATURE_VERIFIER = ROOT / "scripts" / "verify_release_manifest.py"
WINDOWS_SIGNED_PACKAGE_MEMBERS = frozenset(
    {
        "host-x64/ClipVaultImeHost.exe",
        "otp-broker/ClipVaultOtpBroker.exe",
        "x64/ClipVaultTextService.dll",
        "x86/ClipVaultTextService.dll",
    }
)
OWNER_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_id",
        "candidate_commit",
        "source_candidate_dir",
        "source_manifest_path",
        "source_manifest_sha256",
        "build_receipt_path",
        "build_receipt_sha256",
        "workflow_run_url",
        "android_ime_path",
        "android_ime_sha256",
        "android_ime_apksigner_report_path",
        "android_ime_apksigner_report_sha256",
        "android_runtime_path",
        "android_runtime_sha256",
        "android_runtime_apksigner_report_path",
        "android_runtime_apksigner_report_sha256",
        "android_signing_cert_sha256",
        "desktop_executable_path",
        "desktop_executable_sha256",
        "windows_package_path",
        "windows_package_sha256",
        "windows_installer_path",
        "windows_installer_sha256",
        "windows_authenticode_report_path",
        "windows_authenticode_report_sha256",
        "windows_signing_thumbprint",
        "android_manual_pass",
        "windows_manual_pass",
        "otp_manual_pass",
        "seven_day_daily_use_pass",
        "license_and_notices_approved",
        "owner_approved",
        "owner_name",
        "decision_at_utc",
        "evidence_location",
    }
)


@dataclass(frozen=True)
class Gate:
    lane: str
    name: str
    status: str
    detail: str
    evidence: tuple[str, ...] = ()
    next_step: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "lane": self.lane,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "next_step": self.next_step,
        }


@dataclass(frozen=True)
class ProjectLicenseState:
    status: str | None
    valid: bool
    decision_complete: bool
    distribution_allowed: bool


def _pass(lane: str, name: str, detail: str, *evidence: str) -> Gate:
    return Gate(lane, name, "pass", detail, tuple(evidence))


def _blocked(
    lane: str,
    name: str,
    detail: str,
    *evidence: str,
    next_step: str = "",
) -> Gate:
    return Gate(lane, name, "blocked", detail, tuple(evidence), next_step)


def _text(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _android_attr(name: str) -> str:
    return f"{{{ANDROID_NS}}}{name}"


def _missing(root: Path, paths: Iterable[str]) -> list[str]:
    return [path for path in paths if not (root / path).is_file()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"module spec has no loader: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_candidate_bundle(root: Path, directory: Path, commit: str):
    module = _load_module(
        "clipvault_v2_daily_candidate_verifier", CANDIDATE_VERIFIER
    )
    return module.verify_bundle(root, directory, expected_commit=commit)


def _evidence_target(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip())
    return candidate if candidate.is_absolute() else root / candidate


def _project_license_state(
    root: Path, project_license: object
) -> ProjectLicenseState:
    if not isinstance(project_license, dict):
        return ProjectLicenseState(None, False, False, False)
    status = project_license.get("status")
    license_file = project_license.get("license_file")
    distribution_allowed = project_license.get("distribution_allowed")
    if status == "owner_decision_required":
        valid = license_file is None and distribution_allowed is False
        return ProjectLicenseState(status, valid, False, False)
    if status == "internal_only":
        valid = license_file is None and distribution_allowed is False
        return ProjectLicenseState(status, valid, valid, False)
    if status == "approved" and isinstance(license_file, str) and license_file:
        candidate = Path(license_file)
        try:
            resolved = (root / candidate).resolve()
            resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            valid_file = False
        else:
            valid_file = not candidate.is_absolute() and resolved.is_file()
        valid = valid_file and distribution_allowed is True
        return ProjectLicenseState(status, valid, valid, valid)
    return ProjectLicenseState(
        status if isinstance(status, str) else None, False, False, False
    )


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    head = result.stdout.strip().lower()
    return head if re.fullmatch(r"[0-9a-f]{40}", head) else None


def _git_worktree_clean(root: Path) -> bool | None:
    """Return whether candidate sources exactly match HEAD, ignoring ignored build output."""

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return not result.stdout.strip()


def check_foundation(root: Path) -> Gate:
    required = (
        "THIRD_PARTY_MANIFEST.yaml",
        "contracts/v2_candidate_version.json",
        "contracts/otp_relay_wire_v1.schema.json",
        "contracts/v2_daily_owner_evidence.schema.json",
        "contracts/v2_windows_authenticode_evidence.schema.json",
        "contracts/vectors/engine_protocol_v2_assertions.tsv",
        "contracts/vectors/input_foundation_v2.json",
        "contracts/vectors/otp_aead_v1.json",
        "contracts/vectors/runtime_snapshot_v1.json",
        "docs/CONTRACTS_INPUT_ENGINE_V2.md",
        "docs/CONTRACTS_OTP_RELAY.md",
        "docs/CONTRACTS_RUNTIME_SNAPSHOT_V1.md",
        "docs/THREAT_MODEL_OTP_RELAY.md",
        "docs/V2_DAILY_USE_ACCEPTANCE.md",
        "docs/V2_DAILY_CANDIDATE_WORKFLOW.md",
        "docs/V2_DAILY_OWNER_HANDOFF.md",
        "docs/V2_LICENSE_RELEASE_GATE.md",
        "docs/V2_DAILY_USE_MANUAL_QA.md",
        "scripts/release_candidate_manifest.py",
        "scripts/verify_release_manifest.py",
        "tools/v2_daily_candidate.py",
        "tools/Collect-V2WindowsAuthenticodeEvidence.ps1",
        ".github/workflows/v2-daily-candidate.yml",
    )
    missing = _missing(root, required)
    if missing:
        return _blocked(
            "foundation",
            "versioned contracts",
            "Required v2 protocol, threat-model, or acceptance assets are missing.",
            *missing,
            next_step="Restore the versioned foundation before building either platform.",
        )
    acceptance = _text(root, "docs/V2_DAILY_USE_ACCEPTANCE.md")
    gate_ids = {
        *(f"V2D-A{number:02d}" for number in range(1, 12)),
        *(f"V2D-W{number:02d}" for number in range(1, 10)),
        *(f"V2D-O{number:02d}" for number in range(1, 9)),
    }
    absent = sorted(gate for gate in gate_ids if f"`{gate}`" not in acceptance)
    if absent:
        return _blocked(
            "foundation",
            "versioned contracts",
            "The daily-use acceptance contract is incomplete.",
            *absent,
            next_step="Restore every frozen V2D gate ID.",
        )
    try:
        manifest = json.loads(_text(root, "THIRD_PARTY_MANIFEST.yaml"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _blocked(
            "foundation",
            "versioned contracts",
            f"The third-party production manifest is unreadable: {exc.__class__.__name__}.",
            "THIRD_PARTY_MANIFEST.yaml",
            next_step="Restore the JSON-compatible YAML manifest from locked production inputs.",
        )
    project_license = manifest.get("project_license") if isinstance(manifest, dict) else None
    license_state = _project_license_state(root, project_license)
    if (
        not isinstance(manifest, dict)
        or manifest.get("format_version") != 1
        or not isinstance(manifest.get("components"), list)
        or not manifest["components"]
        or not license_state.valid
    ):
        return _blocked(
            "foundation",
            "versioned contracts",
            "The project license state is not a valid pending, internal-only, or file-backed approval state.",
            "THIRD_PARTY_MANIFEST.yaml",
            next_step="Record one exact fail-closed pending/internal-only state, or a file-backed distribution approval.",
        )
    return _pass(
        "foundation",
        "versioned contracts",
        "Engine V2, OTP wire/AEAD, threat model, acceptance, and manual QA assets exist.",
        *required,
    )


def check_third_party_governance(root: Path) -> Gate:
    if not THIRD_PARTY_VALIDATOR.is_file():
        return _blocked(
            "foundation",
            "third-party lock and notice consistency",
            "The read-only v2 third-party validator is missing.",
            "tools/validate_v2_third_party.py",
            next_step="Restore the stdlib validator; do not infer distribution approval from lock files.",
        )
    try:
        spec = importlib.util.spec_from_file_location(
            "clipvault_v2_third_party_validator", THIRD_PARTY_VALIDATOR
        )
        if spec is None or spec.loader is None:
            raise ImportError("validator module spec has no loader")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        errors = module.validate(root)
    except Exception as exc:  # validator failures must block rather than disappear
        return _blocked(
            "foundation",
            "third-party lock and notice consistency",
            f"The read-only v2 third-party validator could not run: {exc.__class__.__name__}.",
            "tools/validate_v2_third_party.py",
            next_step="Repair the validator or its JSON-compatible lock inputs.",
        )
    if errors:
        return _blocked(
            "foundation",
            "third-party lock and notice consistency",
            "Third-party commits, hashes, package assets, or Owner distribution blockers drifted.",
            *errors,
            next_step="Reconcile production locks, notices, and the explicit pending/internal-only/approved project-license state.",
        )
    return _pass(
        "foundation",
        "third-party lock and notice consistency",
        "The four production locks, notice assets, and project-license state agree.",
        "THIRD_PARTY_MANIFEST.yaml",
        "android/rime-engine-android/RIME_PRODUCTION_LOCK.json",
        "android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json",
        "shared-input/rime/RIME_ASSET_LOCK.json",
        "windows/ime/rime/RIME_SDK_LOCK.json",
        "THIRD_PARTY_NOTICES.md",
    )


def check_candidate_evidence(
    root: Path, candidate_dir: Path | None = None
) -> Gate:
    """Verify one immutable CI candidate against the current checkout.

    Source inspection and successful platform builds are necessary but do not
    prove that the downloaded files came from the same commit and workflow
    run.  The BUILD_RECEIPT/RELEASE_MANIFEST pair closes that gap.
    """

    relative = candidate_dir or Path(DEFAULT_CANDIDATE_DIR)
    directory = relative if relative.is_absolute() else root / relative
    if not directory.is_dir():
        return _blocked(
            "candidate",
            "immutable candidate provenance",
            "No unified v2 candidate bundle was supplied for offline verification.",
            directory.as_posix(),
            next_step=(
                "Download the unified CI candidate and rerun with --candidate-dir; "
                "a source-only pass is not candidate readiness."
            ),
        )
    if not CANDIDATE_VERIFIER.is_file():
        return _blocked(
            "candidate",
            "immutable candidate provenance",
            "The read-only v2 candidate verifier is missing.",
            "tools/v2_daily_candidate.py",
            next_step="Restore the receipt/manifest verifier before accepting a bundle.",
        )
    head = _git_head(root)
    if head is None:
        return _blocked(
            "candidate",
            "immutable candidate provenance",
            "The current checkout commit could not be resolved.",
            directory.as_posix(),
            next_step="Verify the candidate from a Git checkout at its exact source commit.",
        )
    try:
        verified = _verify_candidate_bundle(root, directory, head)
    except (OSError, ValueError, ImportError) as exc:
        return _blocked(
            "candidate",
            "immutable candidate provenance",
            f"Candidate receipt, manifest, hashes, or source binding failed: {exc}",
            directory.as_posix(),
            next_step="Rebuild the unified bundle from a clean checkout in one CI run.",
        )
    receipt = verified["receipt"]
    return _pass(
        "candidate",
        "immutable candidate provenance",
        "The unified unsigned candidate is bound to this commit, one successful CI run, locked inputs, and exact artifact hashes.",
        (directory / "BUILD_RECEIPT.json").as_posix(),
        (directory / "RELEASE_MANIFEST.json").as_posix(),
        str(receipt["workflow"]["url"]),
    )


def check_android_production(root: Path) -> Gate:
    required = (
        "android/ime-app/build.gradle.kts",
        "android/ime-app/src/main/AndroidManifest.xml",
        "android/ime-app/src/main/res/xml/ime_isolated_config.xml",
        "android/ime-app/src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
        "android/ime-app/src/main/kotlin/com/clipvault/imeapp/BackspaceRepeatController.kt",
        "android/ime-app/src/test/kotlin/com/clipvault/imeapp/BackspaceRepeatControllerTest.kt",
        "android/ime-app/src/test/kotlin/com/clipvault/imeapp/InputMethodSwitchSourceTest.kt",
        "android/ime-engine/src/main/kotlin/com/clipvault/ime/engine/EngineProtocolV2.kt",
        "android/rime-engine-android/build.gradle.kts",
        "android/rime-engine-android/src/main/cpp/CMakeLists.txt",
        "android/scripts/build-v2-ime.ps1",
        "android/scripts/run-v2-ime-device-tests.ps1",
        "android/scripts/verify-otp-sms-negative-gate.ps1",
        "android/ime-app/src/androidTest/kotlin/com/clipvault/imeapp/NativeRimeDeviceTest.kt",
        "android/app/src/main/kotlin/com/clipvault/app/otp/OtpUserConsentActivity.kt",
        "android/app/src/main/kotlin/com/clipvault/app/otp/OtpUserConsentSessionController.kt",
        "android/app/src/test/kotlin/com/clipvault/app/otp/OtpUserConsentBoundarySourceTest.kt",
        "android/app/src/test/kotlin/com/clipvault/app/privacy/RuntimeImeRemovalSourceTest.kt",
        ".github/workflows/v2-ime-production.yml",
    )
    missing = _missing(root, required)
    if missing:
        return _blocked(
            "android",
            "standalone native IME",
            "The production standalone Android IME source is incomplete.",
            *missing,
            next_step="Integrate the no-network IME, Engine V2, JNI, and fail-closed build entrypoint.",
        )

    problems: list[str] = []
    manifest_path = root / "android/ime-app/src/main/AndroidManifest.xml"
    try:
        manifest = ET.parse(manifest_path).getroot()
    except ET.ParseError as exc:
        problems.append(f"IME manifest parse error: {exc}")
        manifest = ET.Element("manifest")

    forbidden_permissions = {
        "android.permission.INTERNET",
        "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.READ_SMS",
        "android.permission.RECEIVE_SMS",
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.READ_CLIPBOARD_IN_BACKGROUND",
    }
    permissions = {
        element.attrib.get(_android_attr("name"), "")
        for element in manifest.findall("uses-permission")
    }
    leaked = sorted(forbidden_permissions & permissions)
    if leaked:
        problems.append("forbidden IME permissions: " + ", ".join(leaked))

    application = manifest.find("application")
    services = [] if application is None else application.findall("service")
    input_services = [
        item
        for item in services
        if item.attrib.get(_android_attr("permission"))
        == "android.permission.BIND_INPUT_METHOD"
        and item.attrib.get(_android_attr("enabled"), "true") != "false"
    ]
    if len(input_services) != 1:
        problems.append(f"enabled IME service count is {len(input_services)}, expected 1")
    notification_listeners = [
        item.attrib.get(_android_attr("name"), "<unnamed>")
        for item in services
        if item.attrib.get(_android_attr("permission"))
        == "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE"
    ]
    if notification_listeners:
        problems.append("IME package declares notification listeners: " + ", ".join(notification_listeners))

    app_gradle = _text(root, "android/ime-app/build.gradle.kts")
    rime_gradle = _text(root, "android/rime-engine-android/build.gradle.kts")
    cmake = _text(root, "android/rime-engine-android/src/main/cpp/CMakeLists.txt")
    build_script = _text(root, "android/scripts/build-v2-ime.ps1")
    workflow = _text(root, ".github/workflows/v2-ime-production.yml")
    device_test = _text(
        root,
        "android/ime-app/src/androidTest/kotlin/com/clipvault/imeapp/NativeRimeDeviceTest.kt",
    )
    device_runner = _text(root, "android/scripts/run-v2-ime-device-tests.ps1")
    negative_gate = _text(root, "android/scripts/verify-otp-sms-negative-gate.ps1")
    runtime_gradle = _text(root, "android/app/build.gradle.kts")
    user_consent = _text(
        root,
        "android/app/src/main/kotlin/com/clipvault/app/otp/OtpUserConsentActivity.kt",
    )
    source = _text(
        root,
        "android/ime-app/src/main/kotlin/com/clipvault/imeapp/ClipVaultIsolatedImeService.kt",
    )
    try:
        input_method_xml = ET.parse(
            root / "android/ime-app/src/main/res/xml/ime_isolated_config.xml"
        ).getroot()
        if input_method_xml.attrib.get(_android_attr("supportsInlineSuggestions")) != "true":
            problems.append("IME XML does not declare supportsInlineSuggestions=true")
    except ET.ParseError as exc:
        problems.append(f"IME XML parse error: {exc}")
    markers = {
        "compileSdk API 36": "compileSdk = 36" in app_gradle,
        "targetSdk API 36": "targetSdk = 36" in app_gradle,
        "production verification task": "buildProductionIme" in app_gradle,
        "native build opt-in": "clipvaultRimeNativeEnabled" in rime_gradle,
        "production build invokes fail-closed task": "buildProductionIme" in build_script,
        "production CI invokes fail-closed script": (
            "android/scripts/build-v2-ime.ps1"
            in workflow.replace("\\", "/").casefold()
        ),
        "16 KiB max page": "max-page-size=16384" in cmake,
        "16 KiB common page": "common-page-size=16384" in cmake,
        "inline Autofill host": "onInlineSuggestionsResponse" in source,
        "sensitive app policy": "sensitiveAppPolicy.isSensitive" in source,
        "runtime snapshot generation": "cancelPending" in source,
        "cold Rime buffer": "bufferWhileWaiting" in source,
        "continuous press-hold backspace": (
            "backspaceRepeater.press()" in source
            and "backspaceRepeater.release()" in source
            and "MotionEvent.ACTION_CANCEL" in source
        ),
        "explicit input-method switch": (
            "switchToNextInputMethod(false)" in source
            and "showInputMethodPicker()" in source
        ),
        "connected native-device workflow": (
            "clipvault-android-device" in workflow
            and "run-v2-ime-device-tests.ps1" in workflow
        ),
        "native long-sentence vector": "jintianxiawuwomenqukaihui" in device_test,
        "native apostrophe vector": '"xi\'an"' in device_test,
        "native paging vector": "pageCandidates" in device_test,
        "native cancel/recovery vector": "cancelComposition" in device_test,
        "device runner fails closed": (
            "devices.Count -ne 1" in device_runner
            and "tests -lt 6" in device_runner
            and "skipped -ne 0" in device_runner
        ),
        "SMS User Consent foreground fallback": (
            "startSmsUserConsent(null)" in user_consent
            and "SmsRetriever.SEND_PERMISSION" in user_consent
            and "relayUserConsentedMessage" in user_consent
        ),
        "restricted artifact tasks are approval-gated": (
            '"assembleOtpSmsRelay"' in runtime_gradle
            and '"bundleOtpSmsRelay"' in runtime_gradle
            and "dependsOn(otpSmsApprovalGate)" in runtime_gradle
        ),
        "unauthorized restricted assembly negative gate": (
            ":app:assembleOtpSmsRelay" in negative_gate
            and "gradleExit -eq 0" in negative_gate
            and "Get-RestrictedInstallables" in negative_gate
            and "verify-otp-sms-negative-gate.ps1" in workflow
        ),
    }
    problems.extend(label for label, present in markers.items() if not present)
    if (
        "spikes/" in cmake.replace("\\", "/")
        or "spikes/" in build_script.replace("\\", "/")
        or "spikes/" in workflow.replace("\\", "/")
    ):
        problems.append("production Android build references spikes/")

    legacy_manifest = root / "android/app/src/main/AndroidManifest.xml"
    if legacy_manifest.is_file():
        try:
            legacy = ET.parse(legacy_manifest).getroot()
            legacy_app = legacy.find("application")
            legacy_services = [] if legacy_app is None else legacy_app.findall("service")
            declared_legacy = [
                item.attrib.get(_android_attr("name"), "<unnamed>")
                for item in legacy_services
                if item.attrib.get(_android_attr("permission"))
                == "android.permission.BIND_INPUT_METHOD"
            ]
            if declared_legacy:
                problems.append(
                    "networked Runtime still declares IME services: "
                    + ", ".join(declared_legacy)
                )
        except ET.ParseError as exc:
            problems.append(f"Runtime manifest parse error: {exc}")

    if problems:
        return _blocked(
            "android",
            "standalone native IME",
            "Android production source violates one or more daily-use invariants.",
            *problems,
            next_step="Fix the standalone package, native build, privacy, or single-entrypoint invariant.",
        )
    return _pass(
        "android",
        "standalone native IME",
        "Source exposes one no-network API-36 IME with Engine V2, native Rime, 16 KiB flags, Autofill, and fail-closed production build.",
        *required,
    )


def check_windows_production(root: Path) -> Gate:
    required = (
        "windows/ime/CMakeLists.txt",
        "windows/ime/host/main.cpp",
        "windows/ime/host/replay_ledger.h",
        "windows/ime/host/replay_ledger.cpp",
        "windows/ime/host/rime_engine.cpp",
        "windows/ime/common/protocol.h",
        "windows/ime/common/protocol.cpp",
        "windows/ime/tsf/text_service.cpp",
        "windows/ime/tsf/candidate_layout.h",
        "windows/ime/tsf/candidate_layout.cpp",
        "windows/ime/tsf/candidate_window.cpp",
        "windows/ime/scripts/Build-ProductionIme.ps1",
        "windows/ime/scripts/Package-ClipVaultIme.ps1",
        "windows/ime/scripts/Register-ClipVaultIme.ps1",
        "windows/ime/scripts/Unregister-ClipVaultIme.ps1",
        "windows/ime/scripts/Deploy-RimeData.ps1",
        "windows/ime/tests/engine_v2_semantics.cpp",
        "windows/ime/tests/replay_ledger_tests.cpp",
        "windows/ime/tests/candidate_layout_tests.cpp",
        "installer/clipvault-v2-daily.iss",
        ".github/workflows/windows-ime-native-slice.yml",
    )
    missing = _missing(root, required)
    if missing:
        return _blocked(
            "windows",
            "native TSF and external Host",
            "The production Windows IME source/build/package path is incomplete.",
            *missing,
            next_step="Integrate the TSF DLL, x64 Host, x86/x64 packaging, and production CI path.",
        )

    cmake = _text(root, "windows/ime/CMakeLists.txt")
    text_service = _text(root, "windows/ime/tsf/text_service.cpp")
    candidate_window = _text(root, "windows/ime/tsf/candidate_window.cpp")
    candidate_layout_tests = _text(root, "windows/ime/tests/candidate_layout_tests.cpp")
    host = _text(root, "windows/ime/host/main.cpp")
    protocol_header = _text(root, "windows/ime/common/protocol.h")
    protocol_source = _text(root, "windows/ime/common/protocol.cpp")
    production = _text(root, "windows/ime/scripts/Build-ProductionIme.ps1")
    package = _text(root, "windows/ime/scripts/Package-ClipVaultIme.ps1")
    workflow = _text(root, ".github/workflows/windows-ime-native-slice.yml")
    replay = _text(root, "windows/ime/host/replay_ledger.cpp")
    semantics = _text(root, "windows/ime/tests/engine_v2_semantics.cpp")
    installer = _text(root, "installer/clipvault-v2-daily.iss")
    deploy_rime = _text(root, "windows/ime/scripts/Deploy-RimeData.ps1")
    problems: list[str] = []
    markers = {
        "Rime-required CMake mode": "CLIPVAULT_REQUIRE_RIME_RUNTIME" in cmake,
        "30 ms key connect budget": "kEnsureEngineBudgetMilliseconds = 30" in text_service,
        "literal preedit recovery": "PreservePreeditLiteral" in text_service,
        "echo-to-Rime promotion": "PromoteEchoSessionToRime" in host,
        "versioned InputContext type": "struct InputContext" in protocol_header,
        "StartSession carries InputContext": "InputContext context" in protocol_header,
        "strict InputContext decode": (
            "DecodeStartSession" in protocol_source
            and "expected_context_fields" in protocol_source
            and "private_context" in protocol_source
        ),
        "Host applies learning policy": "learning_allowed" in host,
        "SetOption protocol": (
            "struct SetOptionRequest" in protocol_header
            and "EncodeSetOption" in protocol_source
            and "DecodeSetOption" in protocol_source
            and "DecodeSetOption" in host
        ),
        "EndSession/SessionEnded protocol": (
            "struct EndSessionRequest" in protocol_header
            and "struct SessionEnded" in protocol_header
            and "EncodeSessionEnded" in protocol_source
            and "DecodeEndSession" in host
        ),
        "authenticated response acknowledgement": (
            "struct ResponseAck" in protocol_header
            and "EncodeResponseAck" in protocol_source
            and "DecodeResponseAck" in host
        ),
        "byte-identical duplicate response cache": (
            "LookupResponse" in replay
            and "CacheResponse" in replay
            and "WipeResponseLocked" in replay
            and "duplicate commit returns cached transition" in semantics
        ),
        "ack/deadline cache cleanup": (
            "Acknowledge" in replay
            and "retry_deadline_milliseconds" in replay
            and "ENG2-V008" in semantics
        ),
        "ENG2 duplicate and end-session integration": (
            "ENG2-V003" in semantics
            and "EncodeEndSession" in semantics
            and "duplicate EndSession is idempotent" in semantics
        ),
        "production Rime requirement": "RequireRime" in production,
        "Debug x64/x86 build gates": (
            "-Configuration Debug -Architecture x64" in production
            and "-Configuration Debug -Architecture x86" in production
        ),
        "x64 package payload": "x64" in package,
        "x86 package payload": "x86" in package,
        "production workflow path": "windows/ime" in workflow.replace("\\", "/"),
        "original-user Rime deployment": (
            "procedure InstallV2Stack" in installer
            and "CaptureOriginalUser()" in installer
            and "DeployRimeForOriginalUser()" in installer
            and "ExecAsOriginalUser" in installer
            and "-ExpectedOwnerSid" in installer
            and "ExpectedOwnerSid" in deploy_rime
            and "WindowsIdentity]::GetCurrent()" in deploy_rime
        ),
        "RDP OTP hard deny": "GetSystemMetrics(SM_REMOTESESSION) != 0" in text_service,
        "candidate DPI/work-area/hit-test library": (
            "clipvault_candidate_layout" in cmake
            and "clipvault-ime-candidate-layout" in cmake
            and "ScaleMetrics" in candidate_window
            and "MeasureWindow" in candidate_window
            and "PlaceWindow" in candidate_window
            and "HitTest" in candidate_window
            and "ScaleMetrics(144)" in candidate_layout_tests
            and "left_monitor" in candidate_layout_tests
            and "HitTest" in candidate_layout_tests
        ),
    }
    problems.extend(label for label, present in markers.items() if not present)
    install_scope = installer[
        installer.find("procedure InstallV2Stack") :
    ]
    if (
        "CaptureOriginalUser()" in install_scope
        and "DeployRimeForOriginalUser()" in install_scope
        and install_scope.index("CaptureOriginalUser()")
        > install_scope.index("DeployRimeForOriginalUser()")
    ):
        problems.append("installer deploys Rime data before capturing original user")
    if "spikes/windows-ime" in production.replace("\\", "/"):
        problems.append("production build still invokes spikes/windows-ime")
    if problems:
        return _blocked(
            "windows",
            "native TSF and external Host",
            "Windows production source violates one or more daily-use invariants.",
            *problems,
            next_step="Fix real-Rime production packaging, recovery, architecture, or CI boundaries.",
        )
    return _pass(
        "windows",
        "native TSF and external Host",
        "Source contains a real-Rime external Host, thin TSF client, recovery path, candidate UI, and x86/x64 production package flow.",
        *required,
    )


def check_windows_clipvault_snapshot(root: Path) -> Gate:
    desktop_publishers = _sources_containing(
        root,
        "desktop/clipvault",
        ".py",
        ("RuntimeSnapshotPublisher", "secret_guard.scan", "MAX_RESPONSE_BYTES"),
        exclude_parts=frozenset({"tests", "__pycache__"}),
    )
    host_cache = _sources_containing(
        root,
        "windows/ime",
        ".cpp",
        ("RuntimeSnapshot", "generation", "clipvault_allowed"),
        exclude_parts=frozenset({"tests", "out"}),
    )
    required_tests = (
        "desktop/tests/test_runtime_snapshot.py",
        "desktop/tests/test_runtime_snapshot_windows.py",
        "desktop/tests/test_runtime_snapshot_worker.py",
        "windows/ime/tests/runtime_snapshot_tests.cpp",
        "windows/ime/tests/runtime_snapshot_pipe_tests.cpp",
    )
    tests = [] if _missing(root, required_tests) else list(required_tests)
    if tests:
        combined = "\n".join(_text(root, path) for path in required_tests)
        if not all(f"SNAP-V00{number}" in combined for number in range(1, 9)):
            tests = []
    if not desktop_publishers or not host_cache or not tests:
        missing = []
        if not desktop_publishers:
            missing.append("Desktop Secret-Guarded bounded snapshot publisher")
        if not host_cache:
            missing.append("Windows Host in-memory snapshot cache and context suppression")
        if not tests:
            missing.append("snapshot ACL/generation/timeout/password/stale tests")
        return _blocked(
            "windows",
            "ClipVault candidate snapshot",
            "Windows has no proven asynchronous privacy-filtered ClipVault candidate surface.",
            *missing,
            next_step="Add a separate per-user local snapshot channel; keep Python, DB, and network outside the key path.",
        )
    return _pass(
        "windows",
        "ClipVault candidate snapshot",
        "Desktop publishes a bounded filtered snapshot and the Host consumes only a context-scoped memory cache.",
        *(desktop_publishers + host_cache + tests),
    )


def check_otp_desktop_ingress(root: Path) -> Gate:
    required = (
        "desktop/clipvault/otp/ingress.py",
        "desktop/tests/test_otp_ingress.py",
        "contracts/otp_relay_wire_v1.schema.json",
        "contracts/vectors/otp_aead_v1.json",
    )
    missing = _missing(root, required)
    if missing:
        return _blocked(
            "otp",
            "opaque Desktop ingress",
            "The strictly-online opaque OTP ingress is missing.",
            *missing,
            next_step="Integrate the authenticated bounded ingress before enabling any producer.",
        )
    source = _text(root, "desktop/clipvault/otp/ingress.py")
    markers = (
        "OTP_RELAY_ROUTE = \"/api/otp/relay\"",
        "canonical_otp_aad",
        "DisabledOtpOpaqueIngressPort",
        "deadline_monotonic",
    )
    absent = [marker for marker in markers if marker not in source]
    if absent:
        return _blocked(
            "otp",
            "opaque Desktop ingress",
            "The OTP ingress no longer preserves its fail-closed contract.",
            *absent,
            next_step="Restore exact route, AAD, deadline, and default-disabled broker behavior.",
        )
    return _pass(
        "otp",
        "opaque Desktop ingress",
        "Desktop accepts only authenticated, bounded, online opaque envelopes and defaults to a disabled broker.",
        *required,
    )


def _sources_containing(
    root: Path,
    directory: str,
    suffix: str,
    needles: tuple[str, ...],
    *,
    exclude_parts: frozenset[str] = frozenset(),
) -> list[str]:
    base = root / directory
    if not base.is_dir():
        return []
    matches: list[str] = []
    for path in base.rglob(f"*{suffix}"):
        relative_parts = set(path.relative_to(base).parts)
        if relative_parts & exclude_parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if all(needle in source for needle in needles):
            matches.append(path.relative_to(root).as_posix())
    return matches


def check_otp_android_producer(root: Path) -> Gate:
    producers = _sources_containing(
        root,
        "android/app/src/main/kotlin",
        ".kt",
        ("AES/GCM/NoPadding", "ClipVault OTP Relay AEAD v1", "authentication_tag"),
    )
    transports = _sources_containing(
        root,
        "android/app/src/main/kotlin",
        ".kt",
        ("/otp/relay", "Authorization", "application/json"),
    )
    authorization = _sources_containing(
        root,
        "android/app/src/main/kotlin",
        ".kt",
        ("Otp", "Disabled", "targetDevice"),
    )
    tests = _sources_containing(
        root,
        "android/app/src/test",
        ".kt",
        ("otp_aead_v1.json", "authentication_tag", "OTP-AEAD-V001"),
    )
    if not producers or not transports or not authorization or not tests:
        missing = []
        if not producers:
            missing.append("Android Runtime JCA AES-GCM producer")
        if not transports:
            missing.append("Android strictly-online /api/otp/relay transport")
        if not authorization:
            missing.append("Android default-disabled explicit authorization/material port")
        if not tests:
            missing.append("Android OTP AEAD golden-vector test")
        return _blocked(
            "otp",
            "Android Runtime producer",
            "A real Runtime-side JCA producer has not been proven.",
            *missing,
            next_step="Implement the default-off, explicitly authorized, strictly-online Runtime producer; never add it to the IME package.",
        )
    return _pass(
        "otp",
        "Android Runtime producer",
        "Android Runtime has a JCA producer and cross-platform golden-vector coverage.",
        *(producers + transports + authorization + tests),
    )


def check_otp_windows_broker(root: Path) -> Gate:
    crypto = _sources_containing(
        root,
        "windows/otp-relay/crypto",
        ".cpp",
        (
            "BCRYPT_CHAIN_MODE_GCM",
            "BCryptDecrypt",
            "BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO",
        ),
        exclude_parts=frozenset({"tests", "out"}),
    )
    broker = _sources_containing(
        root,
        "windows/otp-relay/broker",
        ".cpp",
        ("CreateNamedPipeW", "PIPE_REJECT_REMOTE_CLIENTS", "CancelIoEx"),
        exclude_parts=frozenset({"tests", "out"}),
    )
    sink = _sources_containing(
        root,
        "windows/ime/tsf",
        ".cpp",
        ("InsertLatestOtp", "BuildOtpContext", "IsCurrentContext"),
        exclude_parts=frozenset({"tests", "out"}),
    )
    tests = _sources_containing(
        root,
        "windows/otp-relay/tests",
        ".cpp",
        ("OTP-AEAD-V001", "authentication tag"),
    )
    if not crypto or not broker or not sink or not tests:
        missing = []
        if not crypto:
            missing.append("Windows production CNG AES-GCM implementation")
        if not broker:
            missing.append("Windows per-user bounded Named Pipe OTP broker")
        if not sink:
            missing.append("Windows armed/context-bound TSF OTP sink")
        if not tests:
            missing.append("Windows OTP AEAD golden-vector test")
        return _blocked(
            "otp",
            "Windows CNG broker and TSF sink",
            "The Windows platform decrypt/consume boundary has not been proven.",
            *missing,
            next_step="Implement CNG verification/decryption, replay/TTL checks, bounded Pipe cancellation, and a context-bound TSF sink.",
        )
    return _pass(
        "otp",
        "Windows CNG broker and TSF sink",
        "Windows has a CNG broker implementation and cross-platform golden-vector coverage.",
        *(crypto + broker + sink + tests),
    )


def _verify_android_apksigner_report(path: Path, expected_cert: str) -> None:
    if path.stat().st_size > 64 * 1024:
        raise ValueError("apksigner report exceeds 64 KiB")
    text = path.read_text(encoding="utf-8-sig")
    lines = set(text.splitlines())
    if "Verifies" not in lines:
        raise ValueError("apksigner report does not record a successful verification")
    if re.search(
        r"^Verified using v(?:2|3) scheme .*: true$", text, re.MULTILINE
    ) is None:
        raise ValueError("apksigner report has no successful v2/v3 scheme")
    verifier = _load_module(
        "clipvault_v2_android_signature_verifier", ANDROID_SIGNATURE_VERIFIER
    )
    observed = verifier.parse_android_signer_cert_sha256(text)
    if observed.casefold() != expected_cert.casefold():
        raise ValueError("apksigner certificate does not match Owner trust anchor")


def _zip_member_sha256(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    if info.file_size > 64 * 1024 * 1024:
        raise ValueError(f"signed package member is unexpectedly large: {info.filename}")
    digest = hashlib.sha256()
    with archive.open(info, "r") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_windows_authenticode_report(
    root: Path,
    evidence: dict[str, object],
    report_path: Path,
) -> None:
    if report_path.stat().st_size > 1024 * 1024:
        raise ValueError("Authenticode report exceeds 1 MiB")
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    if not isinstance(report, dict) or set(report) != {
        "schema_version",
        "signing_thumbprint",
        "top_level",
        "package",
    }:
        raise ValueError("Authenticode report has unexpected fields")
    if report.get("schema_version") != 1:
        raise ValueError("Authenticode report schema_version must be 1")
    thumbprint = evidence["windows_signing_thumbprint"]
    if (
        not isinstance(thumbprint, str)
        or report.get("signing_thumbprint", "").casefold() != thumbprint.casefold()
    ):
        raise ValueError("Authenticode report trust anchor mismatch")

    expected_top = {
        "desktop_executable": (
            "desktop_executable_path",
            "desktop_executable_sha256",
        ),
        "windows_installer": (
            "windows_installer_path",
            "windows_installer_sha256",
        ),
    }
    top_level = report.get("top_level")
    if not isinstance(top_level, list) or len(top_level) != len(expected_top):
        raise ValueError("Authenticode top-level evidence is incomplete")
    seen_roles: set[str] = set()
    for entry in top_level:
        if not isinstance(entry, dict) or set(entry) != {
            "role",
            "path",
            "sha256",
            "status",
            "signing_thumbprint",
        }:
            raise ValueError("Authenticode top-level entry is invalid")
        role = entry.get("role")
        if role not in expected_top or role in seen_roles:
            raise ValueError("Authenticode top-level role is invalid")
        seen_roles.add(role)
        path_key, hash_key = expected_top[role]
        observed_path = _evidence_target(root, entry.get("path"))
        expected_path = _evidence_target(root, evidence[path_key])
        if (
            observed_path is None
            or expected_path is None
            or observed_path.resolve() != expected_path.resolve()
            or entry.get("sha256", "").casefold()
            != str(evidence[hash_key]).casefold()
            or entry.get("status") != "Valid"
            or entry.get("signing_thumbprint", "").casefold()
            != thumbprint.casefold()
        ):
            raise ValueError(f"Authenticode evidence mismatch for {role}")

    package = report.get("package")
    if not isinstance(package, dict) or set(package) != {"path", "sha256", "members"}:
        raise ValueError("Authenticode package evidence is invalid")
    package_path = _evidence_target(root, package.get("path"))
    expected_package_path = _evidence_target(root, evidence["windows_package_path"])
    if (
        package_path is None
        or expected_package_path is None
        or package_path.resolve() != expected_package_path.resolve()
        or package.get("sha256", "").casefold()
        != str(evidence["windows_package_sha256"]).casefold()
    ):
        raise ValueError("Authenticode package identity mismatch")
    members = package.get("members")
    if not isinstance(members, list) or len(members) != len(WINDOWS_SIGNED_PACKAGE_MEMBERS):
        raise ValueError("Authenticode package-member evidence is incomplete")
    by_name: dict[str, dict[str, object]] = {}
    for entry in members:
        if not isinstance(entry, dict) or set(entry) != {
            "archive_path",
            "sha256",
            "status",
            "signing_thumbprint",
        }:
            raise ValueError("Authenticode package-member entry is invalid")
        name = entry.get("archive_path")
        if not isinstance(name, str) or name in by_name:
            raise ValueError("Authenticode package-member path is invalid")
        by_name[name] = entry
    if set(by_name) != WINDOWS_SIGNED_PACKAGE_MEMBERS:
        raise ValueError("Authenticode signed package-member set is invalid")

    with zipfile.ZipFile(package_path, "r") as archive:
        infos = archive.infolist()
        for name, entry in by_name.items():
            matches = [info for info in infos if info.filename == name]
            if len(matches) != 1:
                raise ValueError(f"signed package member is missing or duplicated: {name}")
            actual = _zip_member_sha256(archive, matches[0])
            if (
                actual.casefold() != str(entry.get("sha256", "")).casefold()
                or entry.get("status") != "Valid"
                or str(entry.get("signing_thumbprint", "")).casefold()
                != thumbprint.casefold()
            ):
                raise ValueError(f"Authenticode package-member mismatch: {name}")


def check_owner_evidence(root: Path, evidence_path: Path | None = None) -> Gate:
    relative = evidence_path or Path(OWNER_EVIDENCE)
    path = relative if relative.is_absolute() else root / relative
    if not path.is_file():
        return _blocked(
            "owner",
            "signed artifacts and manual daily-use evidence",
            "Owner-controlled signing, device/application matrix, seven-day use, license, and approval evidence is not recorded.",
            path.as_posix(),
            next_step="Execute docs/V2_DAILY_USE_MANUAL_QA.md on one immutable signed candidate and write a redacted evidence manifest.",
        )
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _blocked(
            "owner",
            "signed artifacts and manual daily-use evidence",
            f"Owner evidence is unreadable: {exc.__class__.__name__}.",
            path.as_posix(),
            next_step="Replace it with a valid, redacted JSON object; never put secrets or OTP text in evidence.",
        )
    required_true = (
        "android_manual_pass",
        "windows_manual_pass",
        "otp_manual_pass",
        "seven_day_daily_use_pass",
        "license_and_notices_approved",
        "owner_approved",
    )
    required_text = (
        "candidate_id",
        "workflow_run_url",
        "owner_name",
        "decision_at_utc",
        "evidence_location",
    )
    required_sha256 = (
        "source_manifest_sha256",
        "build_receipt_sha256",
        "android_ime_sha256",
        "android_ime_apksigner_report_sha256",
        "android_runtime_sha256",
        "android_runtime_apksigner_report_sha256",
        "android_signing_cert_sha256",
        "desktop_executable_sha256",
        "windows_package_sha256",
        "windows_installer_sha256",
        "windows_authenticode_report_sha256",
    )
    artifact_pairs = (
        ("source_manifest_path", "source_manifest_sha256"),
        ("build_receipt_path", "build_receipt_sha256"),
        ("android_ime_path", "android_ime_sha256"),
        (
            "android_ime_apksigner_report_path",
            "android_ime_apksigner_report_sha256",
        ),
        ("android_runtime_path", "android_runtime_sha256"),
        (
            "android_runtime_apksigner_report_path",
            "android_runtime_apksigner_report_sha256",
        ),
        ("desktop_executable_path", "desktop_executable_sha256"),
        ("windows_package_path", "windows_package_sha256"),
        ("windows_installer_path", "windows_installer_sha256"),
        (
            "windows_authenticode_report_path",
            "windows_authenticode_report_sha256",
        ),
    )
    commit = evidence.get("candidate_commit") if isinstance(evidence, dict) else None
    failures = [key for key in required_true if not isinstance(evidence, dict) or evidence.get(key) is not True]
    if not isinstance(evidence, dict) or set(evidence) != OWNER_EVIDENCE_FIELDS:
        failures.append("schema_fields")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 3:
        failures.append("schema_version")
    if (
        not isinstance(commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or commit == "0" * 40
    ):
        failures.append("candidate_commit")
    head = _git_head(root)
    if head is None or commit != head:
        failures.append("candidate_commit_current_head")
    if _git_worktree_clean(root) is not True:
        failures.append("candidate_source_tree_clean")
    for key in required_text:
        value = evidence.get(key) if isinstance(evidence, dict) else None
        if not isinstance(value, str) or not value.strip():
            failures.append(key)
    decision = evidence.get("decision_at_utc") if isinstance(evidence, dict) else None
    if isinstance(decision, str) and decision.strip():
        try:
            parsed_decision = datetime.fromisoformat(decision.replace("Z", "+00:00"))
        except ValueError:
            failures.append("decision_at_utc")
        else:
            if parsed_decision.tzinfo is None:
                failures.append("decision_at_utc")
    for key in required_sha256:
        value = evidence.get(key) if isinstance(evidence, dict) else None
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9A-Fa-f]{64}", value) is None
            or value == "0" * 64
        ):
            failures.append(key)
    resolved_artifacts: list[Path] = []
    resolved_by_key: dict[str, Path] = {}
    for path_key, hash_key in artifact_pairs:
        artifact = _evidence_target(
            root, evidence.get(path_key) if isinstance(evidence, dict) else None
        )
        if artifact is None or not artifact.is_file():
            failures.append(path_key)
            continue
        resolved_artifacts.append(artifact.resolve())
        resolved_by_key[path_key] = artifact.resolve()
        expected = evidence.get(hash_key)
        try:
            actual = _sha256(artifact)
        except OSError:
            failures.append(path_key)
        else:
            if not isinstance(expected, str) or actual.lower() != expected.lower():
                failures.append(f"{hash_key}_mismatch")
    if len(set(resolved_artifacts)) != len(resolved_artifacts):
        failures.append("artifact_paths_distinct")

    source_dir = _evidence_target(
        root, evidence.get("source_candidate_dir") if isinstance(evidence, dict) else None
    )
    if source_dir is None or not source_dir.is_dir():
        failures.append("source_candidate_dir")
    else:
        expected_manifest = (source_dir / "RELEASE_MANIFEST.json").resolve()
        expected_receipt = (source_dir / "BUILD_RECEIPT.json").resolve()
        if resolved_by_key.get("source_manifest_path") != expected_manifest:
            failures.append("source_manifest_path_candidate_dir")
        if resolved_by_key.get("build_receipt_path") != expected_receipt:
            failures.append("build_receipt_path_candidate_dir")
        if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
            try:
                verified = _verify_candidate_bundle(root, source_dir, commit)
            except (OSError, ValueError, ImportError, zipfile.BadZipFile) as exc:
                failures.append(f"source_candidate_verification:{exc}")
            else:
                workflow = verified["receipt"].get("workflow", {})
                if evidence.get("workflow_run_url") != workflow.get("url"):
                    failures.append("workflow_run_url_candidate_receipt")

    expected_android_cert = (
        evidence.get("android_signing_cert_sha256")
        if isinstance(evidence, dict)
        else None
    )
    if isinstance(expected_android_cert, str) and re.fullmatch(
        r"(?!0{64})[0-9A-Fa-f]{64}", expected_android_cert
    ):
        for prefix, key in (
            ("android_ime", "android_ime_apksigner_report_path"),
            ("android_runtime", "android_runtime_apksigner_report_path"),
        ):
            report_path = resolved_by_key.get(key)
            if report_path is None:
                continue
            try:
                _verify_android_apksigner_report(report_path, expected_android_cert)
            except (OSError, UnicodeDecodeError, ValueError, ImportError) as exc:
                failures.append(f"{prefix}_signature_report:{exc}")

    evidence_location = _evidence_target(
        root, evidence.get("evidence_location") if isinstance(evidence, dict) else None
    )
    if evidence_location is None or not evidence_location.exists():
        failures.append("evidence_location_exists")
    try:
        manifest = json.loads(_text(root, "THIRD_PARTY_MANIFEST.yaml"))
        project_license = manifest.get("project_license", {})
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        project_license = {}
    license_state = _project_license_state(root, project_license)
    if not license_state.valid or not license_state.decision_complete:
        failures.append("project_license_decided")
    thumbprint = evidence.get("windows_signing_thumbprint") if isinstance(evidence, dict) else None
    if (
        not isinstance(thumbprint, str)
        or re.fullmatch(r"[0-9A-Fa-f]{40,128}", thumbprint) is None
        or set(thumbprint) == {"0"}
    ):
        failures.append("windows_signing_thumbprint")
    else:
        authenticode_report = resolved_by_key.get("windows_authenticode_report_path")
        if authenticode_report is not None:
            try:
                _verify_windows_authenticode_report(root, evidence, authenticode_report)
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
                zipfile.BadZipFile,
            ) as exc:
                failures.append(f"windows_authenticode_report:{exc}")
    if failures:
        return _blocked(
            "owner",
            "signed artifacts and manual daily-use evidence",
            "Owner evidence is present but incomplete or not approved.",
            *dict.fromkeys(failures),
            next_step="Complete every applicable manual QA row against the same signed candidate.",
        )
    return _pass(
        "owner",
        "signed artifacts and manual daily-use evidence",
        "The redacted Owner manifest binds the CI receipt, signed-artifact verification reports, platform/OTP matrices, seven-day use, the project-license decision, and explicit approval.",
        path.as_posix(),
    )


def build_report(
    root: Path = ROOT,
    evidence_path: Path | None = None,
    candidate_dir: Path | None = None,
) -> dict[str, object]:
    source_gates = [
        check_foundation(root),
        check_third_party_governance(root),
        check_android_production(root),
        check_windows_production(root),
        check_windows_clipvault_snapshot(root),
        check_otp_desktop_ingress(root),
        check_otp_android_producer(root),
        check_otp_windows_broker(root),
    ]
    candidate = check_candidate_evidence(root, candidate_dir)
    owner = check_owner_evidence(root, evidence_path)
    try:
        license_manifest = json.loads(_text(root, "THIRD_PARTY_MANIFEST.yaml"))
        raw_project_license = license_manifest.get("project_license", {})
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raw_project_license = {}
    project_license = _project_license_state(root, raw_project_license)
    source_ready = all(gate.status == "pass" for gate in source_gates)
    automated_ready = source_ready and candidate.status == "pass"
    daily_use_ready = automated_ready and owner.status == "pass"
    gates = [*source_gates, candidate, owner]
    return {
        "status": "ready" if daily_use_ready else "blocked",
        "source_status": "ready" if source_ready else "blocked",
        "candidate_status": candidate.status,
        "automated_status": "ready" if automated_ready else "blocked",
        "owner_status": owner.status,
        "project_license_status": project_license.status,
        "distribution_allowed": project_license.distribution_allowed,
        "blocked": sum(gate.status == "blocked" for gate in gates),
        "gates": [gate.as_dict() for gate in gates],
        "scope_note": (
            "Read-only source/candidate/evidence aggregation. Automated readiness requires "
            "one receipt-bound CI bundle, but still does not prove signing, installation, "
            "real-device behavior, seven-day stability, or Owner approval. A daily-use "
            "ready result does not authorize external distribution when distribution_allowed "
            "is false."
        ),
    }


def _render_text(report: dict[str, object]) -> str:
    lines = [
        "ClipVault v2 daily-use readiness",
        f"status: {report['status']}",
        f"source: {report['source_status']}",
        f"candidate: {report['candidate_status']}",
        f"automated: {report['automated_status']}",
        f"owner: {report['owner_status']}",
        f"project license: {report.get('project_license_status')}",
        f"distribution allowed: {str(bool(report.get('distribution_allowed'))).lower()}",
        "",
    ]
    for raw_gate in report["gates"]:
        gate = dict(raw_gate)
        mark = "[x]" if gate["status"] == "pass" else "[ ]"
        lines.append(f"- {mark} {gate['lane']} / {gate['name']}: {gate['detail']}")
        for item in gate["evidence"]:
            lines.append(f"  evidence: {item}")
        if gate["next_step"]:
            lines.append(f"  next: {gate['next_step']}")
    lines.extend(("", str(report["scope_note"])))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--candidate-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--automated-only",
        action="store_true",
        help="base the exit code on source gates plus a verified candidate bundle",
    )
    selection.add_argument(
        "--source-only",
        action="store_true",
        help="base the exit code on source gates only (pre-bundle CI use)",
    )
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(args.root, args.evidence, args.candidate_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_text(report), end="")
    if args.no_fail:
        return 0
    if args.source_only:
        selected = report["source_status"]
    elif args.automated_only:
        selected = report["automated_status"]
    else:
        selected = report["status"]
    return 0 if selected == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
