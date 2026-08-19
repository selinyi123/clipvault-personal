; ClipVault v2 IME installer include.
; The parent installer must define ClipVaultImeV2PackageDir to a package created
; by scripts/Package-ClipVaultIme.ps1. Registration remains an explicit,
; rollback-capable installer step and is never run by the build.

#ifndef ClipVaultImeV2PackageDir
  #error ClipVaultImeV2PackageDir must point to the packaged v2 IME tree
#endif

[Files]
Source: "{#ClipVaultImeV2PackageDir}\host-x64\*"; DestDir: "{app}\ime\host-x64"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ClipVaultImeV2PackageDir}\otp-broker\*"; DestDir: "{app}\ime\otp-broker"; Flags: ignoreversion
Source: "{#ClipVaultImeV2PackageDir}\x64\*"; DestDir: "{app}\ime\x64"; Flags: ignoreversion
Source: "{#ClipVaultImeV2PackageDir}\x86\*"; DestDir: "{app}\ime\x86"; Flags: ignoreversion
Source: "{#ClipVaultImeV2PackageDir}\scripts\*"; DestDir: "{app}\ime\scripts"; Flags: ignoreversion
Source: "{#ClipVaultImeV2PackageDir}\licenses\*"; DestDir: "{app}\ime\licenses"; Flags: ignoreversion

[Tasks]
Name: "clipvaultotpbroker"; Description: "Enable encrypted phone OTP relay on this PC"; GroupDescription: "Optional ClipVault input features:"; Flags: unchecked

[Registry]
; Prewarm the out-of-process Host at sign-in. The Host creates its bounded
; control pipe before asynchronously opening Rime; TSF DLLs never own this key.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ClipVaultImeHostV2"; ValueData: """{app}\ime\host-x64\ClipVaultImeHost.exe"""; Flags: uninsdeletevalue

; The parent installer owns elevation, failure handling, and Owner approval.
; Deploy dictionaries before registration so Profile activation never performs
; maintenance. The registration script rolls x64 back if x86 registration fails.
[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$ConfirmPreference='None'; & '{app}\ime\scripts\Deploy-RimeData.ps1' -HostDirectory '{app}\ime\host-x64'"""; Flags: runhidden waituntilterminated; StatusMsg: "Deploying ClipVault input dictionaries..."
Filename: "{app}\ime\host-x64\ClipVaultImeHost.exe"; Flags: runhidden nowait; StatusMsg: "Starting ClipVault input engine..."
; OTP Broker is intentionally opt-in. The installer invokes the signed
; Enable-ClipVaultOtpBroker.ps1 action only when the explicit task is
; selected; installation alone never starts SMS/OTP relay.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$ConfirmPreference='None'; & '{app}\ime\scripts\Enable-ClipVaultOtpBroker.ps1' -PackageDirectory '{app}\ime'"""; Flags: runhidden waituntilterminated; Tasks: clipvaultotpbroker; StatusMsg: "Enabling ClipVault OTP relay..."
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$ConfirmPreference='None'; & '{app}\ime\scripts\Register-ClipVaultIme.ps1' -PackageDirectory '{app}\ime' -AllowSystemWideTsfRegistration"""; Flags: runhidden waituntilterminated; StatusMsg: "Registering ClipVault input method..."

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$ConfirmPreference='None'; & '{app}\ime\scripts\Disable-ClipVaultOtpBroker.ps1' -PackageDirectory '{app}\ime'"""; Flags: runhidden waituntilterminated; RunOnceId: "ClipVaultOtpBrokerV1Disable"
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$ConfirmPreference='None'; & '{app}\ime\scripts\Unregister-ClipVaultIme.ps1' -PackageDirectory '{app}\ime'"""; Flags: runhidden waituntilterminated; RunOnceId: "ClipVaultImeV2Unregister"
