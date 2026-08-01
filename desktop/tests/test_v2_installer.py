from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V1_INSTALLER = ROOT / "installer" / "clipvault.iss"
V2_INSTALLER = ROOT / "installer" / "clipvault-v2-daily.iss"
V2_PACKAGE_INCLUDE = ROOT / "installer" / "ClipVaultImeV2Package.iss.inc"
LEGACY_LIFECYCLE_INCLUDE = (
    ROOT / "windows" / "ime" / "installer" / "ClipVaultImeV2.iss.inc"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _procedure_body(script: str, name: str, next_name: str) -> str:
    start = script.index(name)
    end = script.index(next_name, start)
    return script[start:end]


def test_v2_has_one_admin_lifecycle_owner_and_v1_fails_closed_on_v2_macro():
    v1 = _read(V1_INSTALLER)
    v2 = _read(V2_INSTALLER)
    legacy = _read(LEGACY_LIFECYCLE_INCLUDE)

    assert "PrivilegesRequired=admin" in v2
    assert "DefaultDirName={autopf}\\ClipVault" in v2
    assert '#include "ClipVaultImeV2Package.iss.inc"' in v2
    assert "PrivilegesRequired=lowest" in v1
    assert "DefaultDirName={localappdata}\\Programs\\ClipVault" in v1
    assert "ClipVaultImeV2.iss.inc" not in v1
    assert "frozen v1 installer cannot own v2 IME" in v1
    assert legacy.lstrip().startswith("#error")
    assert "legacy v2 lifecycle include is retired" in legacy


def test_v2_files_include_has_package_layout_but_no_lifecycle_actions():
    include = _read(V2_PACKAGE_INCLUDE)

    for relative in (
        "host-x64\\*",
        "otp-broker\\*",
        "x64\\*",
        "x86\\*",
        "scripts\\*",
        "licenses\\*",
    ):
        assert relative in include
    assert "ClipVaultImeV2PackageDir" in include
    for forbidden in ("[Run]", "[Registry]", "[UninstallRun]", "regsvr32"):
        assert forbidden not in include


def test_v2_installer_static_sections_and_code_blocks_are_balanced():
    script = _read(V2_INSTALLER)
    include = _read(V2_PACKAGE_INCLUDE)

    for section in ("Setup", "Languages", "Files", "Tasks", "Icons", "Code"):
        assert f"[{section}]" in script
    assert include.count("[Files]") == 1
    code = script.split("[Code]", 1)[1]
    assert len(re.findall(r"\bbegin\b", code, re.IGNORECASE)) + len(
        re.findall(r"\btry\b", code, re.IGNORECASE)
    ) == len(
        re.findall(r"\bend\s*;", code, re.IGNORECASE)
    )
    assert (ROOT / "desktop" / "packaging" / "clipvault.ico").is_file()
    assert (ROOT / "THIRD_PARTY_NOTICES.md").is_file()


def test_install_binds_original_user_before_machine_registration_then_activation():
    script = _read(V2_INSTALLER)
    install = _procedure_body(
        script,
        "procedure InstallV2Stack",
        "function InitializeUninstall",
    )
    deploy_original = _procedure_body(
        script,
        "function DeployRimeForOriginalUser",
        "function CleanupOriginalUser",
    )

    capture = install.index("CaptureOriginalUser()")
    deploy = install.index("DeployRimeForOriginalUser()")
    register = install.index("Register-ClipVaultIme.ps1")
    owner = install.index("'OwnerSid'")
    configure = install.index("ConfigureOriginalUser()")
    clear = install.index("ClearRepairMarker()")
    assert capture < deploy < register < owner < configure < clear
    assert "PowerShellScriptAsOriginalUser" in deploy_original
    assert "Deploy-RimeData.ps1" in deploy_original
    assert "-ExpectedOwnerSid" in deploy_original
    assert "PowerShellScript('Deploy-RimeData.ps1'" not in script
    assert "-AllowMachineWideRegistration" in install
    assert "-AllowSystemWideTsfRegistration" not in install


def test_admin_installer_has_no_direct_per_user_mutation_or_elevated_launch():
    script = _read(V2_INSTALLER)

    assert "ExecAsOriginalUser" in script
    assert "Configure-ClipVaultImeUser.ps1" in script
    assert "InstallerContextKey" in script
    assert "RegGetSubkeyNames(HKU" in script
    assert "Test-ClipVaultInstallerOwner.ps1" in script
    assert "{userdesktop}" not in script
    assert "RegWriteStringValue(HKCU" not in script
    assert "RegDeleteValue(HKCU" not in script
    assert "[Run]" not in script
    task = next(
        line for line in script.splitlines() if line.startswith('Name: "otp_relay"')
    )
    assert "Flags: unchecked" in task


def test_rime_deployment_rejects_elevation_cross_account_and_foreign_user_data():
    deploy = _read(
        ROOT / "windows" / "ime" / "scripts" / "Deploy-RimeData.ps1"
    )

    for token in (
        "ExpectedOwnerSid",
        "WindowsIdentity]::GetCurrent()",
        "actualOwnerSid",
        "WindowsBuiltInRole]::Administrator",
        "ownerLocalAppData",
        "userDataDirectory.StartsWith",
    ):
        assert token in deploy
    identity_check = deploy.index("actualOwnerSid")
    elevation_check = deploy.index("WindowsBuiltInRole]::Administrator")
    path_check = deploy.index("userDataDirectory.StartsWith")
    mutation = deploy.index("New-Item -ItemType Directory")
    assert identity_check < elevation_check < path_check < mutation


def test_windows_otp_tsf_fails_closed_in_remote_sessions_and_remains_explicit():
    service = _read(ROOT / "windows" / "ime" / "tsf" / "text_service.cpp")
    context = _procedure_body(
        service,
        "bool TextService::BuildOtpContext",
        "void TextService::InsertLatestOtp",
    )
    insert = _procedure_body(
        service,
        "void TextService::InsertLatestOtp",
        "void TextService::ResetOtpContext",
    )

    assert "GetSystemMetrics(SM_REMOTESESSION) != 0" in context
    assert insert.index("BuildOtpContext") < insert.index("engine_.InsertOtp")
    assert service.count("InsertLatestOtp(context)") == 1
    assert "OnPreservedKey" in service
    assert "SendInput" not in service


def test_post_registration_failure_is_disabled_repair_required_not_file_deletion():
    script = _read(V2_INSTALLER)
    failure = _procedure_body(
        script,
        "procedure FailClosedAfterMachineMutation",
        "function PrepareToInstall",
    )

    assert "CleanupOriginalUser()" in failure
    assert "RollbackImeRegistration()" in failure
    assert "InstallFailedClosed := True" in failure
    assert "WriteRepairMarker" in failure
    assert "RaiseException" not in failure
    assert "RepairRequired" in script
    assert "GetCustomSetupExitCode" in script
    assert "Result := 7" in script
    assert "deleteafterinstall" in script


def test_uninstall_targets_recorded_owner_and_aborts_before_registered_file_removal():
    script = _read(V2_INSTALLER)
    uninstall = _procedure_body(
        script,
        "procedure CurUninstallStepChanged",
        "procedure CurStepChanged",
    )

    remove = uninstall.index("RemoveOwnerAutostart()")
    stop = uninstall.index("StopOwnerProcesses()")
    unregister = uninstall.index("RollbackImeRegistration()")
    clear = uninstall.index("ClearRepairMarker()")
    assert remove < stop < unregister < clear
    assert uninstall.count("RaiseException") == 3
    assert "WriteRepairMarker" in uninstall
    assert "OwnerSid" in script
    assert re.search(r"(?m)^\[UninstallDelete\]$", script) is None
    assert "{localappdata}\\ClipVault" in script


def test_machine_registration_contract_has_unique_x64_profile_owner_and_error_flow():
    registration = _read(ROOT / "windows" / "ime" / "tsf" / "registration.cpp")
    cmake = _read(ROOT / "windows" / "ime" / "CMakeLists.txt")
    register = _read(
        ROOT / "windows" / "ime" / "scripts" / "Register-ClipVaultIme.ps1"
    )
    unregister = _read(
        ROOT / "windows" / "ime" / "scripts" / "Unregister-ClipVaultIme.ps1"
    )

    for token in (
        "HKEY_LOCAL_MACHINE",
        "KEY_WOW64_64KEY",
        "KEY_WOW64_32KEY",
        "CLIPVAULT_TSF_PROFILE_OWNER",
        "return result",
    ):
        assert token in registration
    assert "HKEY_CURRENT_USER" not in registration
    assert "CLIPVAULT_TSF_PROFILE_OWNER=1" in cmake
    assert "CLIPVAULT_TSF_PROFILE_OWNER=0" in cmake
    assert register.index(
        "Invoke-Regsvr32 -Executable $regsvr32 -Dll $x86Dll"
    ) < register.index("Invoke-Regsvr32 -Executable $regsvr64 -Dll $x64Dll")
    assert unregister.index(
        "Invoke-Unregister -Executable $regsvr64 -Dll $x64Dll"
    ) < unregister.index(
        "Invoke-Unregister -Executable $regsvr32 -Dll $x86Dll"
    )
    assert "RepairRequired" in register
    assert "RepairRequired" in unregister
