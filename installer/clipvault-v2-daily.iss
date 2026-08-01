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
; The final file entry is a temporary transaction sentinel. AfterInstall still
; runs before the uninstaller log is finalized, so an exception rolls files
; back; deleteafterinstall removes the sentinel on success or abort.
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
  HostRunValue = 'ClipVaultImeHostV2';
  RuntimeRunValue = 'ClipVaultRuntimeV2';
  BrokerRunValue = 'ClipVaultOtpBrokerV1';

var
  OwnerSid: String;
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
    '" -OwnerSid "' + OwnerSid + '" -Confirm:$false');
end;

function RollbackImeRegistration(): Boolean;
begin
  Result := PowerShellScript('Unregister-ClipVaultIme.ps1',
    '-PackageDirectory "' + ExpandConstant('{app}\ime') +
    '" -AllowMachineWideRegistration -Confirm:$false');
end;

function ConfigureOriginalUser(): Boolean;
var
  Parameters: String;
begin
  Parameters := '-Mode Install -PackageDirectory "' +
    ExpandConstant('{app}\ime') + '" -RuntimeExecutable "' +
    RuntimeExePath() + '" -Confirm:$false';
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
    '" -ExpectedOwnerSid "' + OwnerSid + '" -Confirm:$false');
end;

function CleanupOriginalUser(): Boolean;
begin
  Result := PowerShellScriptAsOriginalUser(
    'Configure-ClipVaultImeUser.ps1',
    '-Mode Uninstall -PackageDirectory "' +
    ExpandConstant('{app}\ime') + '" -RuntimeExecutable "' +
    RuntimeExePath() + '" -Confirm:$false');
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
  Result := False;
  OwnerSid := '';
  Nonce := GetMD5OfString(GetDateTimeString('yyyymmddhhnnsszzz', '', '') +
    IntToStr(Random(2147483647)));
  if not PowerShellScriptAsOriginalUser(
    'Configure-ClipVaultImeUser.ps1',
    '-Mode Identify -PackageDirectory "' + ExpandConstant('{app}\ime') +
    '" -Nonce "' + Nonce + '" -Confirm:$false') then Exit;

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
begin
  { Inno Restart Manager closes exact package files before replacement. }
  Result := '';
end;

procedure InstallV2Stack();
begin
  InstallFailedClosed := False;
  RepairMessage := '';

  { Bind elevation to one unelevated interactive user before any per-user
    deployment or machine mutation. }
  if not CaptureOriginalUser() then
    RaiseException(
      'ClipVault setup could not bind elevation to one interactive owner.');

  if not DeployRimeForOriginalUser() then
    RaiseException(
      'ClipVault Rime deployment failed for the bound interactive owner.');

  if not PowerShellScript('Register-ClipVaultIme.ps1',
    '-PackageDirectory "' + ExpandConstant('{app}\ime') +
    '" -AllowMachineWideRegistration -Confirm:$false') then
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
begin
  Result := RegQueryStringValue(HKLM64, MachineStateKey, 'OwnerSid', OwnerSid);
  if not Result then
    MsgBox('ClipVault owner state is missing. Repair the installation before uninstalling.',
      mbError, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    { A failed cleanup aborts uninstall before Inno removes registered files. }
    if not RemoveOwnerAutostart() then
    begin
      WriteRepairMarker('UNINSTALL_OWNER_HIVE_UNAVAILABLE', 1);
      RaiseException('ClipVault owner launch state could not be removed.');
    end;
    if not StopOwnerProcesses() then
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
