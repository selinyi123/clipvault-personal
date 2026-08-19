[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory)]
    [string]$X64BinaryDirectory,
    [Parameter(Mandatory)]
    [string]$X86BinaryDirectory,
    [Parameter(Mandatory)]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$x64BinaryDirectory = [System.IO.Path]::GetFullPath($X64BinaryDirectory)
$x86BinaryDirectory = [System.IO.Path]::GetFullPath($X86BinaryDirectory)
$outputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$imeRoot = Split-Path -Parent $PSScriptRoot
$librimeLicense = Join-Path $imeRoot 'rime\LICENSE-librime.txt'
$required = @(
    (Join-Path $x64BinaryDirectory 'ClipVaultTextService.dll'),
    (Join-Path $x64BinaryDirectory 'ClipVaultImeHost.exe'),
    (Join-Path $x64BinaryDirectory 'ClipVaultOtpBroker.exe'),
    (Join-Path $x64BinaryDirectory 'rime.dll'),
    (Join-Path $x64BinaryDirectory 'rime-data'),
    (Join-Path $x86BinaryDirectory 'ClipVaultTextService.dll'),
    $librimeLicense
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing package input: $path" }
}

if ($PSCmdlet.ShouldProcess($outputDirectory,
        'Create the isolated x64 Host plus x64/x86 TSF package layout')) {
    $hostDirectory = Join-Path $outputDirectory 'host-x64'
    $otpBrokerDirectory = Join-Path $outputDirectory 'otp-broker'
    $x64 = Join-Path $outputDirectory 'x64'
    $x86 = Join-Path $outputDirectory 'x86'
    $scripts = Join-Path $outputDirectory 'scripts'
    $licenses = Join-Path $outputDirectory 'licenses'
    foreach ($directory in @($hostDirectory, $otpBrokerDirectory, $x64, $x86, $scripts, $licenses)) {
        $resolvedParent = [System.IO.Path]::GetFullPath(
            (Split-Path -Parent $directory))
        if ($resolvedParent -ne $outputDirectory) {
            throw "Refusing to clean package path outside output directory: $directory"
        }
        if (Test-Path -LiteralPath $directory) {
            Remove-Item -LiteralPath $directory -Recurse -Force
        }
    }
    New-Item -ItemType Directory -Path $hostDirectory, $otpBrokerDirectory, $x64, $x86, $scripts, $licenses -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $x64BinaryDirectory 'ClipVaultImeHost.exe') `
        -Destination $hostDirectory -Force
    Copy-Item -LiteralPath (Join-Path $x64BinaryDirectory 'rime.dll') `
        -Destination $hostDirectory -Force
    Copy-Item -LiteralPath (Join-Path $x64BinaryDirectory 'ClipVaultOtpBroker.exe') `
        -Destination $otpBrokerDirectory -Force
    Copy-Item -LiteralPath (Join-Path $x64BinaryDirectory 'rime-data') `
        -Destination $hostDirectory -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $x64BinaryDirectory 'ClipVaultTextService.dll') `
        -Destination $x64 -Force
    Copy-Item -LiteralPath (Join-Path $x86BinaryDirectory 'ClipVaultTextService.dll') `
        -Destination $x86 -Force
    foreach ($script in @('Register-ClipVaultIme.ps1',
                           'Unregister-ClipVaultIme.ps1',
                           'Deploy-RimeData.ps1',
                           'Enable-ClipVaultOtpBroker.ps1',
                           'Disable-ClipVaultOtpBroker.ps1',
                           'Test-ProductionDependencies.ps1')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $script) `
            -Destination $scripts -Force
    }
    Copy-Item -LiteralPath $librimeLicense `
        -Destination (Join-Path $licenses 'librime-BSD-3-Clause.txt') -Force
    Copy-Item -LiteralPath (Join-Path $x64BinaryDirectory 'rime-data\LICENSE') `
        -Destination (Join-Path $licenses 'rime-pinyin-simp-Apache-2.0.txt') -Force
}
