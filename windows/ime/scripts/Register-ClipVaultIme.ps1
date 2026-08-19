[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory,
    [switch]$AllowSystemWideTsfRegistration
)

$ErrorActionPreference = 'Stop'
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$x64Dll = Join-Path $packageDirectory 'x64\ClipVaultTextService.dll'
$x86Dll = Join-Path $packageDirectory 'x86\ClipVaultTextService.dll'
$hostExe = Join-Path $packageDirectory 'host-x64\ClipVaultImeHost.exe'
$otpBrokerExe = Join-Path $packageDirectory 'otp-broker\ClipVaultOtpBroker.exe'
foreach ($required in @($x64Dll, $x86Dll, $hostExe, $otpBrokerExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing v2 IME package file: $required"
    }
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'ClipVault v2 requires 64-bit Windows with x64 and x86 TSF clients.'
}
if (-not $AllowSystemWideTsfRegistration -and -not $WhatIfPreference) {
    throw 'Windows TSF profile registration writes under HKLM. Re-run only with explicit -AllowSystemWideTsfRegistration authorization.'
}

$regsvr64 = Join-Path $env:WINDIR 'System32\regsvr32.exe'
$regsvr32 = Join-Path $env:WINDIR 'SysWOW64\regsvr32.exe'

function Invoke-Regsvr32 {
    param([string]$Executable, [string]$Dll, [switch]$Unregister)
    $arguments = @('/s')
    if ($Unregister) { $arguments += '/u' }
    $arguments += ('"' + $Dll + '"')
    $process = Start-Process -FilePath $Executable -ArgumentList $arguments `
        -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "regsvr32 failed for $Dll with exit code $($process.ExitCode)"
    }
}

if ($PSCmdlet.ShouldProcess($packageDirectory,
        'Register x64/x86 HKCU COM servers and the system-wide Windows TSF profile')) {
    Invoke-Regsvr32 -Executable $regsvr64 -Dll $x64Dll
    try {
        Invoke-Regsvr32 -Executable $regsvr32 -Dll $x86Dll
    } catch {
        Invoke-Regsvr32 -Executable $regsvr64 -Dll $x64Dll -Unregister
        throw
    }
    Write-Host 'ClipVault Input v2 x64/x86 COM servers and TSF profile were registered.'
}
