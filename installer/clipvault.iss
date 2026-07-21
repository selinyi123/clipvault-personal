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
ArchitecturesInstallIn64BitMode=x64compatible
AppMutex=Local\ClipVaultPersonal
CloseApplications=no
RestartApplications=no

[Languages]
Name: "cn"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\desktop\dist\clipvault.exe"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"
Name: "startup"; Description: "开机自动启动 ClipVault（后台托盘运行）"; GroupDescription: "启动选项:"

[Icons]
Name: "{group}\ClipVault Personal"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 ClipVault"; Filename: "{uninstallexe}"
Name: "{userdesktop}\ClipVault Personal"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\ClipVault Personal"; Filename: "{app}\{#AppExe}"; Parameters: "--no-open"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 ClipVault"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C exit /B 0"; Flags: runhidden; RunOnceId: "killcv"
