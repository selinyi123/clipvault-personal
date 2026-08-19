[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory)]
    [string]$HostDirectory,
    [string]$SharedDataDirectory = '',
    [string]$UserDataDirectory = ''
)

$ErrorActionPreference = 'Stop'
$hostDirectory = [System.IO.Path]::GetFullPath($HostDirectory)
$hostExe = Join-Path $hostDirectory 'ClipVaultImeHost.exe'
$rimeDll = Join-Path $hostDirectory 'rime.dll'
if (-not $SharedDataDirectory) { $SharedDataDirectory = Join-Path $hostDirectory 'rime-data' }
if (-not $UserDataDirectory) {
    $UserDataDirectory = Join-Path $env:LOCALAPPDATA 'ClipVault\Rime'
}
$sharedDataDirectory = [System.IO.Path]::GetFullPath($SharedDataDirectory)
$userDataDirectory = [System.IO.Path]::GetFullPath($UserDataDirectory)

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
