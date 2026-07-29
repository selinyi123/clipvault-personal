; ClipVault Personal — Windows installer (Inno Setup)
; Per-user install (no admin), creates Start Menu + Desktop shortcuts and an
; optional login autostart. Produces ClipVault-Setup-vX.Y.Z.exe.

#define AppName "ClipVault Personal"
#define AppVersion "1.6.0"
#define AppPublisher "ClipVault"
#define AppExe "clipvault.exe"

[Setup]
AppId={{B7E9C4A1-0F3D-4E2A-9C5B-CV11PERSONAL}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\ClipVault
DefaultGroupName=ClipVault
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=ClipVault-Setup-v{#AppVersion}
SetupIconFile=..\desktop\packaging\clipvault.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AppMutex=Local\ClipVaultPersonal
CloseApplications=no
RestartApplications=no

[Languages]
Name: "cn"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\desktop\dist\clipvault.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\third_party\RELINKING_V1_6_0.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\third_party\licenses\CPython-3.11.9-Windows-LICENSE.txt"; DestDir: "{app}\licenses"; DestName: "CPython-3.11.9-Windows-LICENSE.txt"; Flags: ignoreversion
Source: "..\desktop\packaging\runtime-notices\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"
; Fresh installs keep clipboard monitoring opt-in. Inno preserves prior task
; selections during an upgrade through its default UsePreviousTasks behavior.
Name: "startup"; Description: "开机自动启动 ClipVault（后台托盘运行）"; GroupDescription: "启动选项:"; Flags: unchecked

[Icons]
Name: "{group}\ClipVault Personal"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 ClipVault"; Filename: "{uninstallexe}"
Name: "{userdesktop}\ClipVault Personal"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\ClipVault Personal"; Filename: "{app}\{#AppExe}"; Parameters: "--no-open"; Tasks: startup

[Run]
; Starting the watcher from the finish page also requires an explicit choice.
Filename: "{app}\{#AppExe}"; Description: "启动 ClipVault（开始监听剪贴板）"; Flags: nowait postinstall skipifsilent unchecked

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C exit /B 0"; Flags: runhidden; RunOnceId: "killcv"
