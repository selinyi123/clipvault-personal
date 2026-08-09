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
    assert "-AllowBuiltInAdministratorOwner" in deploy_original
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


def test_installer_uses_file_compatible_noninteractive_switches():
    script = _read(V2_INSTALLER)

    assert "-Confirm:$false" not in script
    assert script.count("-NoConfirm") == 7
    for relative in (
        "windows/ime/scripts/Register-ClipVaultIme.ps1",
        "windows/ime/scripts/Unregister-ClipVaultIme.ps1",
        "windows/ime/scripts/Stop-ClipVaultImeOwnerProcesses.ps1",
        "windows/ime/scripts/Configure-ClipVaultImeUser.ps1",
        "windows/ime/scripts/Deploy-RimeData.ps1",
    ):
        helper = _read(ROOT / relative)
        assert "[switch]$NoConfirm" in helper
        assert "$ConfirmPreference = 'None'" in helper


def test_owner_nonce_avoids_pascal_integer_coercion():
    script = _read(V2_INSTALLER)

    assert "GetDateTimeString('yyyymmddhhnnsszzz', '-', ':')" in script
    assert "GetDateTimeString('yyyymmddhhnnsszzz', '', '')" not in script
    assert "c11f5a017e1a5e7" in script
    assert "GetMD5OfString" not in script
    assert "Random(" not in script


def test_rime_deployment_rejects_cross_account_and_limits_elevated_owner_exception():
    deploy = _read(
        ROOT / "windows" / "ime" / "scripts" / "Deploy-RimeData.ps1"
    )

    for token in (
        "ExpectedOwnerSid",
        "WindowsIdentity]::GetCurrent()",
        "actualOwnerSid",
        "WindowsBuiltInRole]::Administrator",
        "AllowBuiltInAdministratorOwner",
        "EndsWith('-500'",
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


def test_upgrade_disables_existing_stack_before_restart_manager_file_replacement():
    script = _read(V2_INSTALLER)
    prepare = _procedure_body(
        script,
        "function PrepareToInstall",
        "procedure InstallV2Stack",
    )

    assert "RegistrationPresent" in prepare
    assert "MachineStateExists := RegKeyExists" in prepare
    assert "PhysicalImeRegistrationPresent()" in prepare
    assert "registration state is missing or invalid" in prepare
    assert "ExistingRegistrationState > 2" in prepare
    assert "ExistingRegistrationSchema <> 2" in prepare
    assert "state says clean while the TSF class remains registered" in prepare
    assert "PackageDirectory" in prepare
    assert "CompareText(RecordedPackageDirectory" in prepare
    assert "OwnerSid" in prepare
    bind = prepare.index("CaptureOriginalUser()")
    remember = prepare.index("PreparedUpgradeOwnerSid := OwnerSid")
    remove = prepare.index("RemoveOwnerAutostart()", remember)
    stop = prepare.index("StopOwnerProcesses()", remove)
    unregister = prepare.index("RollbackImeRegistration()", stop)
    outcome = prepare.index("if not (UserClean and MachineClean)", unregister)
    assert bind < remember < remove < stop < unregister < outcome
    assert "WriteRepairMarker('UPGRADE_IN_PROGRESS', 0)" in prepare
    assert "UPGRADE_PREPARE_CLEANUP_INCOMPLETE" in prepare
    missing_owner = prepare.index("UPGRADE_OWNER_STATE_MISSING")
    owner_error = prepare.index("ClipVault owner state is missing")
    missing_owner_rollback = prepare.rfind(
        "MachineClean := RollbackImeRegistration()", 0, missing_owner
    )
    assert missing_owner_rollback != -1
    assert missing_owner_rollback < missing_owner < owner_error
    assert "WriteRepairMarker('UPGRADE_OWNER_STATE_MISSING', 2)" in prepare
    assert "UPGRADE_OWNER_STATE_MISSING_CLEANUP_INCOMPLETE" in prepare
    bind_failure = prepare.index("Capture can fail before any upgrade cleanup")
    assert prepare.index("OwnerSid := RecordedOwnerSid", bind_failure) < bind_failure + 500
    assert prepare.index("RemoveOwnerAutostart()", bind_failure) > bind_failure
    assert prepare.index("StopOwnerProcesses()", bind_failure) > bind_failure
    assert prepare.index("RollbackImeRegistration()", bind_failure) > bind_failure
    assert "UPGRADE_OWNER_BIND_FAILED_CLEANUP_INCOMPLETE" in prepare
    assert "Restart Manager" in prepare
    install = _procedure_body(
        script,
        "procedure InstallV2Stack",
        "function InitializeUninstall",
    )
    assert "PreparedUpgradeOwnerSid <> ''" in install
    assert "OwnerSid := PreparedUpgradeOwnerSid" in install
    assert "(PreparedUpgradeOwnerSid = '') and not CaptureOriginalUser()" in install


def test_pre_registration_failures_use_custom_nonzero_exit_and_repair_state():
    script = _read(V2_INSTALLER)
    failure = _procedure_body(
        script,
        "procedure FailClosedBeforeMachineMutation",
        "procedure ClearRepairMarker",
    )
    install = _procedure_body(
        script,
        "procedure InstallV2Stack",
        "function InitializeUninstall",
    )

    assert "InstallFailedClosed := True" in failure
    assert "RegistrationPresent" in failure
    assert "ExistingRegistrationStateKnown" in failure
    assert "PhysicalImeRegistrationPresent()" in failure
    assert "_REGISTRATION_STATE_DRIFT" in failure
    assert "RecordedOwnerSid" in failure
    assert "RemoveOwnerAutostart()" in failure
    assert "StopOwnerProcesses()" in failure
    assert "RollbackImeRegistration()" in failure
    assert "ExistingRegistrationState := 0" in failure
    assert "ExistingRegistrationStateKnown := RegQueryDWordValue" in failure
    assert "if not RegQueryDWordValue" not in failure
    assert "RegistrationState := ExistingRegistrationState" not in failure
    assert "_CLEANUP_INCOMPLETE" in failure
    assert "WriteRepairMarker(FailureCode, 0)" in failure
    assert "WriteRepairMarker(FailureCode + '_CLEANUP_INCOMPLETE', 2)" in failure
    assert "WriteRepairMarker" in failure
    assert "RaiseException" not in failure
    remove = failure.index("RemoveOwnerAutostart()")
    stop = failure.index("StopOwnerProcesses()")
    rollback = failure.index("RollbackImeRegistration()")
    outcome = failure.index("if UserClean and MachineClean")
    assert remove < stop < rollback < outcome
    assert "OWNER_CAPTURE_FAILED" in install
    assert "RIME_DEPLOY_FAILED" in install
    assert "RaiseException" not in install
    assert install.count("FailClosedBeforeMachineMutation") == 2
    assert install.count("Exit;") >= 5


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
    initialize = _procedure_body(
        script,
        "function InitializeUninstall",
        "procedure CurUninstallStepChanged",
    )
    assert "HasRecordedOwner" in initialize
    assert "(Trim(OwnerSid) <> '')" in initialize
    assert "RegistrationSchema" in initialize
    assert "RegistrationSchema = 2" in initialize
    assert "PackageDirectory" in initialize
    assert "CompareText(RecordedPackageDirectory" in initialize
    assert "ExpandConstant('{app}\\ime')" in initialize
    assert "RegistrationPresent" in initialize
    assert "(RegistrationState <= 2)" in initialize
    assert "Result := HasRecordedOwner and HasValidSchema" in initialize
    assert "HasValidPackageDirectory and HasKnownRegistrationState" in initialize
    assert "Result := False" in initialize
    assert "RegistrationState = 0" not in initialize
    assert "RepairRequired = 1" not in initialize
    assert "(OwnerSid <> '') and not RemoveOwnerAutostart()" in uninstall
    assert "(OwnerSid <> '') and not StopOwnerProcesses()" in uninstall
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
        "EnableLanguageProfile",
    ):
        assert token in registration
    assert registration.index("AddLanguageProfile") < registration.index(
        "EnableLanguageProfile"
    )
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
