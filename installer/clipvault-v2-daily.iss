; ClipVault Personal v2 daily-use installer.
; This is intentionally independent from clipvault.iss (the frozen v1.6 line).

#define AppName "ClipVault Personal v2"
#ifndef AppVersion
  ; Keep this fallback synchronized with contracts/v2_candidate_version.json.
  ; desktop/tests/test_v2_candidate_version.py fails on any drift.
  #define AppVersion "2.2.0-dev"
#endif
#define AppPublisher "ClipVault"
#define AppExe "clipvault.exe"
#ifndef ClipVaultImeV2PackageDir
  #define ClipVaultImeV2PackageDir "..\windows\ime\out\package"
#endif

[Setup]
AppId={{D185AA02-C32D-4CB8-8F74-2DBE25F8BC20}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\ClipVault
DefaultGroupName=ClipVault
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=dist-v2
OutputBaseFilename=ClipVault-v2-Daily-Setup-v{#AppVersion}
SetupIconFile=..\desktop\packaging\clipvault.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "cn"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\desktop\dist\clipvault.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\third_party\RELINKING_V1_6_0.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\third_party\licenses\CPython-3.11.9-Windows-LICENSE.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "..\desktop\packaging\runtime-notices\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

#include "ClipVaultImeV2Package.iss.inc"

[Files]
; The final file entry is a temporary lifecycle sentinel. InstallV2Stack uses
; an explicit fail-closed state and custom exit code; Inno may retain the
; disabled payload for repair. deleteafterinstall removes only the sentinel.
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}\ime"; DestName: ".install-transaction"; Flags: ignoreversion deleteafterinstall; AfterInstall: InstallV2Stack

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
; OTP expands the trusted device surface and is never selected on a fresh install.
Name: "otp_relay"; Description: "Enable authorized phone OTP relay to this PC"; GroupDescription: "Optional security features:"; Flags: unchecked

[Icons]
Name: "{group}\ClipVault Personal v2"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall ClipVault Personal v2"; Filename: "{uninstallexe}"
Name: "{commondesktop}\ClipVault Personal v2"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  InstallerContextKey = 'Software\ClipVault\InstallerContext';
  MachineStateKey = 'Software\ClipVault\ImeV2';
  TsfClsidRegistryKey = 'Software\Classes\CLSID\{C5CEE00A-05AD-4ABA-93BB-6E76932AF126}';
  HostRunValue = 'ClipVaultImeHostV2';
  RuntimeRunValue = 'ClipVaultRuntimeV2';
  BrokerRunValue = 'ClipVaultOtpBrokerV1';

var
  OwnerSid: String;
  PreparedUpgradeOwnerSid: String;
  InstallFailedClosed: Boolean;
  RepairMessage: String;

function HostExePath(): String;
begin
  Result := ExpandConstant('{app}\ime\host-x64\ClipVaultImeHost.exe');
end;

function RuntimeExePath(): String;
begin
  Result := ExpandConstant('{app}\{#AppExe}');
end;

function PhysicalImeRegistrationPresent(): Boolean;
begin
  Result := RegKeyExists(HKLM64, TsfClsidRegistryKey) or
    RegKeyExists(HKLM32, TsfClsidRegistryKey);
end;

function ImeScriptPath(const ScriptName: String): String;
begin
  Result := ExpandConstant('{app}\ime\scripts\') + ScriptName;
end;

function RunAndWait(const FileName, Parameters: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(FileName, Parameters, '', SW_HIDE, ewWaitUntilTerminated,
    ResultCode) and (ResultCode = 0);
end;

function RunAndWaitAsOriginalUser(const FileName, Parameters: String): Boolean;
var
  ResultCode: Integer;
begin
  try
    Result := ExecAsOriginalUser(FileName, Parameters, '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  except
    Result := False;
  end;
end;

function PowerShellScript(const ScriptName, Arguments: String): Boolean;
var
  Parameters: String;
begin
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ImeScriptPath(ScriptName) + '" ' + Arguments;
  Result := RunAndWait(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Parameters);
end;

function PowerShellScriptAsOriginalUser(
  const ScriptName, Arguments: String): Boolean;
var
  Parameters: String;
begin
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ImeScriptPath(ScriptName) + '" ' + Arguments;
  Result := RunAndWaitAsOriginalUser(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Parameters);
end;

function DeleteOwnerRunValue(const ValueName: String): Boolean;
var
  OwnerRunKey: String;
begin
  OwnerRunKey := OwnerSid + '\' + RunKey;
  if not RegValueExists(HKU, OwnerRunKey, ValueName) then
    Result := True
  else
    Result := RegDeleteValue(HKU, OwnerRunKey, ValueName);
end;

function RemoveOwnerAutostart(): Boolean;
begin
  if (OwnerSid = '') or not RegKeyExists(HKU, OwnerSid) then
  begin
    Result := False;
    Exit;
  end;
  Result := DeleteOwnerRunValue(HostRunValue);
  if not DeleteOwnerRunValue(RuntimeRunValue) then Result := False;
  if not DeleteOwnerRunValue(BrokerRunValue) then Result := False;
end;

function StopOwnerProcesses(): Boolean;
begin
  Result := PowerShellScript('Stop-ClipVaultImeOwnerProcesses.ps1',
    '-PackageDirectory "' + ExpandConstant('{app}\ime') +
    '" -RuntimeExecutable "' + RuntimeExePath() +
    '" -OwnerSid "' + OwnerSid + '" -NoConfirm');
end;

function RollbackImeRegistration(): Boolean;
begin
  Result := PowerShellScript('Unregister-ClipVaultIme.ps1',
    '-PackageDirectory "' + ExpandConstant('{app}\ime') +
    '" -AllowMachineWideRegistration -NoConfirm');
end;

function ConfigureOriginalUser(): Boolean;
var
  Parameters: String;
begin
  Parameters := '-Mode Install -PackageDirectory "' +
    ExpandConstant('{app}\ime') + '" -RuntimeExecutable "' +
    RuntimeExePath() + '" -NoConfirm';
  if WizardIsTaskSelected('otp_relay') then
    Parameters := Parameters + ' -EnableOtpRelay';
  Result := PowerShellScriptAsOriginalUser(
    'Configure-ClipVaultImeUser.ps1', Parameters);
end;

function DeployRimeForOriginalUser(): Boolean;
begin
  { Deploy-RimeData resolves LOCALAPPDATA in this unelevated original-user
    process. ExpectedOwnerSid prevents a different interactive account from
    receiving the deployment if setup elevation crosses an account boundary. }
  Result := PowerShellScriptAsOriginalUser(
    'Deploy-RimeData.ps1',
    '-HostDirectory "' + ExpandConstant('{app}\ime\host-x64') +
    '" -ExpectedOwnerSid "' + OwnerSid +
    '" -NoConfirm -AllowBuiltInAdministratorOwner');
end;

function CleanupOriginalUser(): Boolean;
begin
  Result := PowerShellScriptAsOriginalUser(
    'Configure-ClipVaultImeUser.ps1',
    '-Mode Uninstall -PackageDirectory "' +
    ExpandConstant('{app}\ime') + '" -RuntimeExecutable "' +
    RuntimeExePath() + '" -NoConfirm');
  if not Result then
  begin
    Result := RemoveOwnerAutostart();
    if not StopOwnerProcesses() then Result := False;
  end;
end;

function CaptureOriginalUser(): Boolean;
var
  Nonce: String;
  MarkerValue: String;
  CandidateSid: String;
  Hives: TArrayOfString;
  Index: Integer;
  Matches: Integer;
begin
  Log('ClipVault v2 owner capture started.');
  Result := False;
  OwnerSid := '';
  Log('ClipVault v2 owner nonce generation started.');
  { This is a correlation nonce, not an authentication secret. Keep it as a
    32-character lowercase-hex string without Pascal Script numeric or ANSI
    coercion; those conversions are runtime-only type errors in Inno 6.7.3. }
  Nonce := GetDateTimeString('yyyymmddhhnnsszzz', '-', ':') +
    'c11f5a017e1a5e7';
  Log('ClipVault v2 owner nonce generated.');
  if not PowerShellScriptAsOriginalUser(
    'Configure-ClipVaultImeUser.ps1',
    '-Mode Identify -PackageDirectory "' + ExpandConstant('{app}\ime') +
    '" -Nonce "' + Nonce +
    '" -NoConfirm -AllowBuiltInAdministratorOwner') then
  begin
    Log('ClipVault v2 original-user identity probe failed.');
    Exit;
  end;
  Log('ClipVault v2 original-user identity probe completed.');

  if not RegGetSubkeyNames(HKU, '', Hives) then Exit;
  Matches := 0;
  for Index := 0 to GetArrayLength(Hives) - 1 do
  begin
    CandidateSid := Hives[Index];
    if RegQueryStringValue(HKU,
      CandidateSid + '\' + InstallerContextKey, Nonce, MarkerValue) and
      (MarkerValue = 'clipvault-v2-owner') then
    begin
      Matches := Matches + 1;
      OwnerSid := CandidateSid;
      RegDeleteValue(HKU, CandidateSid + '\' + InstallerContextKey, Nonce);
    end;
  end;
  if Matches <> 1 then
  begin
    OwnerSid := '';
    Exit;
  end;

  if not PowerShellScript('Test-ClipVaultInstallerOwner.ps1',
    '-ExpectedOwnerSid "' + OwnerSid + '"') then
  begin
    OwnerSid := '';
    Exit;
  end;
  if RegQueryStringValue(HKLM64, MachineStateKey, 'OwnerSid', CandidateSid) and
    (CompareText(CandidateSid, OwnerSid) <> 0) then
  begin
    OwnerSid := '';
    Exit;
  end;
  Result := True;
end;

procedure WriteRepairMarker(
  const FailureCode: String; RegistrationState: Integer);
var
  MarkerDirectory: String;
  MarkerPath: String;
  Payload: String;
begin
  MarkerDirectory := ExpandConstant('{commonappdata}\ClipVault');
  MarkerPath := MarkerDirectory + '\v2-ime-repair-required.json';
  ForceDirectories(MarkerDirectory);
  Payload := '{"version":1,"state":"repair-required","code":"' +
    FailureCode + '"}';
  SaveStringToFile(MarkerPath, Payload, False);
  RegWriteDWordValue(HKLM64, MachineStateKey, 'RegistrationSchema', 2);
  RegWriteStringValue(HKLM64, MachineStateKey, 'PackageDirectory',
    ExpandConstant('{app}\ime'));
  RegWriteDWordValue(HKLM64, MachineStateKey, 'RegistrationPresent',
    RegistrationState);
  if OwnerSid <> '' then
    RegWriteStringValue(HKLM64, MachineStateKey, 'OwnerSid', OwnerSid);
  RegWriteDWordValue(HKLM64, MachineStateKey, 'RepairRequired', 1);
end;

procedure FailClosedBeforeMachineMutation(
  const FailureCode, SafeMessage: String);
var
  ExistingRegistrationState: Cardinal;
  ExistingRegistrationStateKnown: Boolean;
  PhysicalRegistrationPresent: Boolean;
  RecordedOwnerSid: String;
  UserClean: Boolean;
  MachineClean: Boolean;
begin
  { AfterInstall exceptions do not reliably make a silent Inno install return
    non-zero. Record an explicit disabled repair state and let
    GetCustomSetupExitCode surface the failure instead. If this is an upgrade,
    the [Files] payload has already been replaced; disable the prior owner and
    machine registration rather than leaving the new payload active. }
  InstallFailedClosed := True;
  RepairMessage := SafeMessage;
  ExistingRegistrationState := 0;
  ExistingRegistrationStateKnown := RegQueryDWordValue(
    HKLM64, MachineStateKey, 'RegistrationPresent',
    ExistingRegistrationState);
  PhysicalRegistrationPresent := PhysicalImeRegistrationPresent();
  if ((not ExistingRegistrationStateKnown) or
      (ExistingRegistrationState = 0)) and
      not PhysicalRegistrationPresent then
  begin
    WriteRepairMarker(FailureCode, 0);
    Exit;
  end;

  { CaptureOriginalUser clears OwnerSid on failure. For an upgrade, clean only
    the owner recorded by the existing machine state; never target the current
    interactive account by inference. }
  RecordedOwnerSid := '';
  if RegQueryStringValue(HKLM64, MachineStateKey, 'OwnerSid',
      RecordedOwnerSid) and (Trim(RecordedOwnerSid) <> '') then
    OwnerSid := Trim(RecordedOwnerSid)
  else
    OwnerSid := '';

  { A missing/invalid state value, or a state claiming clean while the COM
    class still exists, is not clean. Publish an explicit repair state before
    invoking the unregistration helper so it cannot take its state-0 fast
    path and leave the physical registration active. }
  if (not ExistingRegistrationStateKnown) or
      (ExistingRegistrationState = 0) then
    WriteRepairMarker(FailureCode + '_REGISTRATION_STATE_DRIFT', 2);

  UserClean := False;
  if OwnerSid <> '' then
  begin
    UserClean := RemoveOwnerAutostart();
    if not StopOwnerProcesses() then UserClean := False;
  end;
  MachineClean := RollbackImeRegistration();
  if UserClean and MachineClean then
    WriteRepairMarker(FailureCode, 0)
  else
    WriteRepairMarker(FailureCode + '_CLEANUP_INCOMPLETE', 2);
end;

procedure ClearRepairMarker();
begin
  DeleteFile(ExpandConstant(
    '{commonappdata}\ClipVault\v2-ime-repair-required.json'));
  RegDeleteValue(HKLM64, MachineStateKey, 'RepairRequired');
end;

procedure FailClosedAfterMachineMutation(
  const FailureCode, SafeMessage: String);
var
  UserClean: Boolean;
  MachineClean: Boolean;
begin
  UserClean := CleanupOriginalUser();
  MachineClean := RollbackImeRegistration();
  InstallFailedClosed := True;
  RepairMessage := SafeMessage;
  if UserClean and MachineClean then
    WriteRepairMarker(FailureCode, 0)
  else
    WriteRepairMarker(FailureCode + '_CLEANUP_INCOMPLETE', 2);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExistingRegistrationState: Cardinal;
  ExistingRegistrationSchema: Cardinal;
  RecordedOwnerSid: String;
  RecordedPackageDirectory: String;
  MachineStateExists: Boolean;
  PhysicalRegistrationPresent: Boolean;
  UserClean: Boolean;
  MachineClean: Boolean;
begin
  Result := '';
  MachineStateExists := RegKeyExists(HKLM64, MachineStateKey);
  PhysicalRegistrationPresent := PhysicalImeRegistrationPresent();
  if not MachineStateExists then
  begin
    if PhysicalRegistrationPresent then
      Result := 'ClipVault physical TSF registration exists without trusted machine state. Repair it before installing.';
    Exit;
  end;

  ExistingRegistrationState := 0;
  if not RegQueryDWordValue(HKLM64, MachineStateKey,
      'RegistrationPresent', ExistingRegistrationState) then
  begin
    Result := 'ClipVault registration state is missing or invalid. Repair it before upgrading.';
    Exit;
  end;
  if ExistingRegistrationState > 2 then
  begin
    Result := 'ClipVault registration state is unsupported. Repair it before upgrading.';
    Exit;
  end;

  ExistingRegistrationSchema := 0;
  if not RegQueryDWordValue(HKLM64, MachineStateKey,
      'RegistrationSchema', ExistingRegistrationSchema) or
      (ExistingRegistrationSchema <> 2) then
  begin
    Result := 'ClipVault registration schema is unsupported. Repair it before upgrading.';
    Exit;
  end;

  RecordedPackageDirectory := '';
  if not RegQueryStringValue(HKLM64, MachineStateKey, 'PackageDirectory',
      RecordedPackageDirectory) or
      (CompareText(RecordedPackageDirectory,
        ExpandConstant('{app}\ime')) <> 0) then
  begin
    Result := 'ClipVault registration belongs to a different package directory. Repair that installation before upgrading.';
    Exit;
  end;

  if ExistingRegistrationState = 0 then
  begin
    if PhysicalRegistrationPresent then
      Result := 'ClipVault registration state says clean while the TSF class remains registered. Repair it before upgrading.';
    Exit;
  end;

  { Disable an existing installation before Restart Manager closes Explorer.
    Otherwise Explorer may restart, replay the owner Run value, and reopen the
    Host while [Files] is replacing it. Only the package recorded as this
    install location is eligible for upgrade cleanup. }
  RecordedOwnerSid := '';
  if not RegQueryStringValue(HKLM64, MachineStateKey, 'OwnerSid',
      RecordedOwnerSid) or (Trim(RecordedOwnerSid) = '') then
  begin
    { A state claiming an active registration without an owner cannot be left
      active after an upgrade attempt. The owner Run value cannot be targeted
      safely, but the machine TSF registration can still be disabled. Preserve
      state 2 even when rollback succeeds so repair must reconstruct ownership
      before a later uninstall. }
    OwnerSid := '';
    MachineClean := RollbackImeRegistration();
    if MachineClean then
      WriteRepairMarker('UPGRADE_OWNER_STATE_MISSING', 2)
    else
      WriteRepairMarker('UPGRADE_OWNER_STATE_MISSING_CLEANUP_INCOMPLETE', 2);
    Result := 'ClipVault owner state is missing. Repair the installation before upgrading.';
    Exit;
  end;
  RecordedOwnerSid := Trim(RecordedOwnerSid);

  { Bind the unelevated owner while the original shell token is still stable.
    Restart Manager may close and recreate Explorer before InstallV2Stack. }
  if not CaptureOriginalUser() or
      (CompareText(OwnerSid, RecordedOwnerSid) <> 0) then
  begin
    { Capture can fail before any upgrade cleanup has started.  The recorded
      owner is still the only identity permitted for cleanup; use it to stop
      the old processes and remove the old TSF registration now, otherwise an
      early upgrade failure would leave the previous stack active while the
      installer reports an error. }
    OwnerSid := RecordedOwnerSid;
    UserClean := RemoveOwnerAutostart();
    if not StopOwnerProcesses() then UserClean := False;
    MachineClean := RollbackImeRegistration();
    if UserClean and MachineClean then
      WriteRepairMarker('UPGRADE_OWNER_BIND_FAILED', 0)
    else
      WriteRepairMarker('UPGRADE_OWNER_BIND_FAILED_CLEANUP_INCOMPLETE', 2);
    OwnerSid := '';
    Result := 'ClipVault upgrade must be run by the recorded installation owner.';
    Exit;
  end;
  PreparedUpgradeOwnerSid := OwnerSid;

  UserClean := False;
  if OwnerSid <> '' then
  begin
    UserClean := RemoveOwnerAutostart();
    if not StopOwnerProcesses() then UserClean := False;
  end;
  MachineClean := RollbackImeRegistration();
  if not (UserClean and MachineClean) then
  begin
    WriteRepairMarker('UPGRADE_PREPARE_CLEANUP_INCOMPLETE', 2);
    Result := 'ClipVault could not safely disable the existing input stack. Repair the installation before upgrading.';
    Exit;
  end;
  WriteRepairMarker('UPGRADE_IN_PROGRESS', 0);
  Log('ClipVault v2 existing stack disabled before file replacement.');
end;

procedure InstallV2Stack();
begin
  Log('ClipVault v2 stack installation started.');
  InstallFailedClosed := False;
  RepairMessage := '';

  { Bind elevation to one unelevated interactive user before any per-user
    deployment or machine mutation. }
  if PreparedUpgradeOwnerSid <> '' then
  begin
    OwnerSid := PreparedUpgradeOwnerSid;
    Log('ClipVault v2 stack is reusing the owner bound before upgrade cleanup.');
  end;
  if PreparedUpgradeOwnerSid = '' then
    Log('ClipVault v2 stack is capturing the original user.');
  if (PreparedUpgradeOwnerSid = '') and not CaptureOriginalUser() then
  begin
    FailClosedBeforeMachineMutation('OWNER_CAPTURE_FAILED',
      'ClipVault setup could not bind elevation to one interactive owner.');
    Exit;
  end;

  Log('ClipVault v2 stack is deploying Rime data.');
  if not DeployRimeForOriginalUser() then
  begin
    FailClosedBeforeMachineMutation('RIME_DEPLOY_FAILED',
      'ClipVault Rime deployment failed for the bound interactive owner.');
    Exit;
  end;

  Log('ClipVault v2 stack is registering TSF.');
  if not PowerShellScript('Register-ClipVaultIme.ps1',
    '-PackageDirectory "' + ExpandConstant('{app}\ime') +
    '" -AllowMachineWideRegistration -NoConfirm') then
  begin
    FailClosedAfterMachineMutation('REGISTER_FAILED',
      'ClipVault TSF registration failed. The payload was retained disabled for repair.');
    Exit;
  end;

  if not RegWriteStringValue(HKLM64, MachineStateKey, 'OwnerSid', OwnerSid) then
  begin
    FailClosedAfterMachineMutation('OWNER_STATE_FAILED',
      'ClipVault owner binding failed. The payload was retained disabled for repair.');
    Exit;
  end;

  if not ConfigureOriginalUser() then
  begin
    FailClosedAfterMachineMutation('USER_BOOTSTRAP_FAILED',
      'ClipVault user configuration failed. The payload was retained disabled for repair.');
    Exit;
  end;
  ClearRepairMarker();
end;

function InitializeUninstall(): Boolean;
var
  HasRecordedOwner: Boolean;
  HasValidSchema: Boolean;
  HasValidPackageDirectory: Boolean;
  HasKnownRegistrationState: Boolean;
  RegistrationSchema: Cardinal;
  RegistrationState: Cardinal;
  RecordedPackageDirectory: String;
begin
  Result := False;
  OwnerSid := '';
  HasRecordedOwner := RegQueryStringValue(HKLM64, MachineStateKey,
    'OwnerSid', OwnerSid) and (Trim(OwnerSid) <> '');
  RegistrationSchema := 0;
  HasValidSchema := RegQueryDWordValue(HKLM64, MachineStateKey,
    'RegistrationSchema', RegistrationSchema) and
    (RegistrationSchema = 2);
  RecordedPackageDirectory := '';
  HasValidPackageDirectory := RegQueryStringValue(HKLM64, MachineStateKey,
    'PackageDirectory', RecordedPackageDirectory) and
    (CompareText(RecordedPackageDirectory,
      ExpandConstant('{app}\ime')) = 0);
  RegistrationState := 3;
  HasKnownRegistrationState := RegQueryDWordValue(HKLM64, MachineStateKey,
    'RegistrationPresent', RegistrationState) and
    (RegistrationState <= 2);

  Result := HasRecordedOwner and HasValidSchema and
    HasValidPackageDirectory and HasKnownRegistrationState;
  if Result then
    OwnerSid := Trim(OwnerSid)
  else
    OwnerSid := '';

  { An empty owner or an untrusted machine-state binding is never sufficient
    authorization for uninstall, including a disabled/repair-required state.
    Repair must reconstruct schema, package and owner before uninstall can
    remove user-scoped processes or launch state. }
  if not Result then
    MsgBox('ClipVault owner or registration state is invalid. Repair the installation before uninstalling.',
      mbError, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    { A failed cleanup aborts uninstall before Inno removes registered files. }
    if (OwnerSid <> '') and not RemoveOwnerAutostart() then
    begin
      WriteRepairMarker('UNINSTALL_OWNER_HIVE_UNAVAILABLE', 1);
      RaiseException('ClipVault owner launch state could not be removed.');
    end;
    if (OwnerSid <> '') and not StopOwnerProcesses() then
    begin
      WriteRepairMarker('UNINSTALL_PROCESS_STOP_FAILED', 1);
      RaiseException('ClipVault owner processes could not be stopped.');
    end;
    if not RollbackImeRegistration() then
    begin
      WriteRepairMarker('UNINSTALL_REGISTRATION_FAILED', 2);
      RaiseException('ClipVault TSF unregistration failed.');
    end;
    ClearRepairMarker();
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and InstallFailedClosed then
    SuppressibleMsgBox(RepairMessage, mbError, MB_OK, IDOK);
end;

function GetCustomSetupExitCode(): Integer;
begin
  if InstallFailedClosed then
    Result := 7
  else
    Result := 0;
end;

// There is intentionally no [UninstallDelete] entry for
// {localappdata}\ClipVault. User data, Rime state and pairing metadata remain.
