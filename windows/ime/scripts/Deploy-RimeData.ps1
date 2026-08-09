[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory)]
    [string]$HostDirectory,
    [Parameter(Mandatory)]
    [ValidatePattern('^S-1-5-')]
    [string]$ExpectedOwnerSid,
    [string]$SharedDataDirectory = '',
    [string]$UserDataDirectory = '',
    [switch]$NoConfirm,
    [switch]$AllowBuiltInAdministratorOwner
)

$ErrorActionPreference = 'Stop'
if ($NoConfirm) { $ConfirmPreference = 'None' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$actualOwnerSid = $identity.User.Value
if (-not [string]::Equals($actualOwnerSid, $ExpectedOwnerSid,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Rime deployment did not run as the captured interactive owner.'
}
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator) -and
    (-not $AllowBuiltInAdministratorOwner -or
        -not $actualOwnerSid.EndsWith('-500', [StringComparison]::Ordinal))) {
    throw 'Rime deployment received an elevated token instead of the original-user token.'
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'The original-user LOCALAPPDATA directory is unavailable.'
}
$ownerLocalAppData = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar)
$hostDirectory = [System.IO.Path]::GetFullPath($HostDirectory)
$hostExe = Join-Path $hostDirectory 'ClipVaultImeHost.exe'
$rimeDll = Join-Path $hostDirectory 'rime.dll'
if (-not $SharedDataDirectory) { $SharedDataDirectory = Join-Path $hostDirectory 'rime-data' }
if (-not $UserDataDirectory) {
    $UserDataDirectory = Join-Path $ownerLocalAppData 'ClipVault\Rime'
}
$sharedDataDirectory = [System.IO.Path]::GetFullPath($SharedDataDirectory)
$userDataDirectory = [System.IO.Path]::GetFullPath($UserDataDirectory)
$ownerDataPrefix = $ownerLocalAppData + [System.IO.Path]::DirectorySeparatorChar
if (-not $userDataDirectory.StartsWith(
        $ownerDataPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Rime user data must remain under the captured owner LOCALAPPDATA directory.'
}

if (-not (Test-Path -LiteralPath $hostExe -PathType Leaf)) { throw "Missing Host: $hostExe" }
if (-not (Test-Path -LiteralPath $rimeDll -PathType Leaf)) { throw "Missing rime.dll: $rimeDll" }
if (-not (Test-Path -LiteralPath $sharedDataDirectory -PathType Container)) {
    throw "Missing Rime shared data: $sharedDataDirectory"
}
if (-not (Get-ChildItem -LiteralPath $sharedDataDirectory -Filter '*.schema.yaml' -File)) {
    throw "No Rime schema is present in $sharedDataDirectory"
}

if ($PSCmdlet.ShouldProcess($userDataDirectory,
        'Predeploy ClipVault Rime dictionaries outside the TSF activation/key path')) {
    New-Item -ItemType Directory -Path $userDataDirectory -Force | Out-Null
    $priorData = $env:CLIPVAULT_RIME_DATA_DIR
    $priorUser = $env:CLIPVAULT_RIME_USER_DIR
    try {
        $env:CLIPVAULT_RIME_DATA_DIR = $sharedDataDirectory
        $env:CLIPVAULT_RIME_USER_DIR = $userDataDirectory
        $process = Start-Process -FilePath $hostExe -ArgumentList '--deploy-rime' `
            -WorkingDirectory $hostDirectory -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw "Rime deployment failed with exit code $($process.ExitCode)"
        }
    } finally {
        $env:CLIPVAULT_RIME_DATA_DIR = $priorData
        $env:CLIPVAULT_RIME_USER_DIR = $priorUser
    }
}
