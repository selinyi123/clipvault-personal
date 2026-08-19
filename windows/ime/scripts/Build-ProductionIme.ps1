[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$RimeSdkDirectory,
    [Parameter(Mandatory)]
    [Alias('RimeDataDirectory')]
    [string]$RimeDictionaryDirectory,
    [string]$BuildRoot = '',
    [string]$PackageDirectory = ''
)

$ErrorActionPreference = 'Stop'
$imeRoot = Split-Path -Parent $PSScriptRoot
if (-not $BuildRoot) { $BuildRoot = Join-Path $imeRoot 'out\production' }
if (-not $PackageDirectory) { $PackageDirectory = Join-Path $imeRoot 'out\package' }
$buildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$x64Build = Join-Path $buildRoot 'x64'
$x86Build = Join-Path $buildRoot 'x86'
$rimeData = Join-Path $buildRoot 'rime-data-stage'

& (Join-Path $PSScriptRoot 'Prepare-ProductionRimeData.ps1') `
    -RimeDictionaryDirectory $RimeDictionaryDirectory `
    -BuildRoot $buildRoot -OutputDirectory $rimeData

& (Join-Path $PSScriptRoot 'Build-NativeSlice.ps1') `
    -Configuration Release -Architecture x64 -BuildDirectory $x64Build `
    -RimeSdkDirectory $RimeSdkDirectory -RimeDataDirectory $rimeData `
    -RequireRime
& (Join-Path $PSScriptRoot 'Build-NativeSlice.ps1') `
    -Configuration Release -Architecture x86 -BuildDirectory $x86Build
& (Join-Path $PSScriptRoot 'Package-ClipVaultIme.ps1') `
    -X64BinaryDirectory (Join-Path $x64Build 'bin') `
    -X86BinaryDirectory (Join-Path $x86Build 'bin') `
    -OutputDirectory $packageDirectory -Confirm:$false
& (Join-Path $imeRoot 'tests\Test-InstallScripts.ps1')
& (Join-Path $PSScriptRoot 'Test-ProductionDependencies.ps1') `
    -PackageDirectory $packageDirectory
& (Join-Path $PSScriptRoot 'Test-InstallerInclude.ps1') `
    -PackageDirectory $packageDirectory

$required = @(
    'host-x64\ClipVaultImeHost.exe',
    'otp-broker\ClipVaultOtpBroker.exe',
    'host-x64\rime.dll',
    'host-x64\rime-data',
    'x64\ClipVaultTextService.dll',
    'x86\ClipVaultTextService.dll',
    'scripts\Register-ClipVaultIme.ps1',
    'scripts\Unregister-ClipVaultIme.ps1',
    'scripts\Deploy-RimeData.ps1',
    'scripts\Enable-ClipVaultOtpBroker.ps1',
    'scripts\Disable-ClipVaultOtpBroker.ps1',
    'scripts\Test-ProductionDependencies.ps1',
    'licenses\librime-BSD-3-Clause.txt',
    'licenses\rime-pinyin-simp-Apache-2.0.txt'
)
foreach ($relative in $required) {
    $path = Join-Path $packageDirectory $relative
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Production IME package is incomplete: $relative"
    }
}
Write-Host "Production IME package: $packageDirectory"
