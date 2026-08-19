; Compile-only harness for the maintained v2 IME installer include.
#define ClipVaultImeV2PackageDir GetEnv("CLIPVAULT_IME_TEST_PACKAGE")
#define ClipVaultImeV2TestOutputDir GetEnv("CLIPVAULT_IME_TEST_OUTPUT")

[Setup]
AppId={{8C4CC2A2-7D3C-45CA-9B4E-477535FB8E16}
AppName=ClipVault IME v2 syntax gate
AppVersion=2.0.0-test
DefaultDirName={tmp}\ClipVaultImeV2Syntax
PrivilegesRequired=admin
OutputDir={#ClipVaultImeV2TestOutputDir}
OutputBaseFilename=ClipVaultImeV2Syntax
Uninstallable=no
CreateAppDir=no
Compression=zip

#include "..\installer\ClipVaultImeV2.iss.inc"
