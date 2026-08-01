; Files-only adapter for the package produced by
; windows/ime/scripts/Package-ClipVaultIme.ps1. The parent v2 installer owns
; lifecycle, registration, rollback, configuration and autostart ordering.

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
