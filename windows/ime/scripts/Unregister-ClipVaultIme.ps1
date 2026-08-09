[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory,
    [switch]$AllowMachineWideRegistration,
    [switch]$NoConfirm
)

$ErrorActionPreference = 'Stop'
if ($NoConfirm) { $ConfirmPreference = 'None' }
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$x64Dll = Join-Path $packageDirectory 'x64\ClipVaultTextService.dll'
$x86Dll = Join-Path $packageDirectory 'x86\ClipVaultTextService.dll'
$stateKey = 'HKLM:\Software\ClipVault\ImeV2'
foreach ($required in @($x64Dll, $x86Dll)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing v2 IME package file: $required"
    }
}
if (-not $AllowMachineWideRegistration -and -not $WhatIfPreference) {
    throw 'Machine-wide TSF unregistration requires explicit -AllowMachineWideRegistration authorization.'
}
$principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $WhatIfPreference -and
    -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Machine-wide TSF unregistration requires an elevated administrator token.'
}
if (-not $WhatIfPreference -and (Test-Path -LiteralPath $stateKey)) {
    $state = Get-ItemProperty -LiteralPath $stateKey
    if ($state.PackageDirectory -and
        -not [string]::Equals(
            [System.IO.Path]::GetFullPath([string]$state.PackageDirectory),
            $packageDirectory,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to unregister a machine profile owned by another package directory.'
    }
    if ($state.RegistrationPresent -eq 0) {
        Remove-Item -LiteralPath $stateKey -Recurse -Force
        Write-Host 'ClipVault Input v2 machine registration was already clean.'
        return
    }
}

$regsvr64 = Join-Path $env:WINDIR 'System32\regsvr32.exe'
$regsvr32 = Join-Path $env:WINDIR 'SysWOW64\regsvr32.exe'

function Invoke-Unregister {
    param([string]$Executable, [string]$Dll)
    $process = Start-Process -FilePath $Executable `
        -ArgumentList @('/s', '/u', ('"' + $Dll + '"')) `
        -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "regsvr32 /u failed with exit code $($process.ExitCode)"
    }
}

if ($PSCmdlet.ShouldProcess($packageDirectory,
        'Unregister the x64-owned TSF profile and both HKLM COM views')) {
    $cleanupErrors = [System.Collections.Generic.List[string]]::new()
    # Remove the shared profile first so any later failure leaves it disabled.
    try {
        Invoke-Unregister -Executable $regsvr64 -Dll $x64Dll
    } catch {
        $cleanupErrors.Add('x64')
    }
    try {
        Invoke-Unregister -Executable $regsvr32 -Dll $x86Dll
    } catch {
        $cleanupErrors.Add('x86')
    }
    if ($cleanupErrors.Count -ne 0) {
        New-Item -Path $stateKey -Force | Out-Null
        New-ItemProperty -LiteralPath $stateKey -Name PackageDirectory `
            -Value $packageDirectory -PropertyType String -Force | Out-Null
        New-ItemProperty -LiteralPath $stateKey -Name RegistrationPresent `
            -Value 2 -PropertyType DWord -Force | Out-Null
        New-ItemProperty -LiteralPath $stateKey -Name RepairRequired `
            -Value 1 -PropertyType DWord -Force | Out-Null
        throw "ClipVault machine unregistration incomplete; failed_steps=$($cleanupErrors.Count)"
    }
    Remove-Item -LiteralPath $stateKey -Recurse -Force `
        -ErrorAction SilentlyContinue
    Write-Host 'ClipVault Input v2 machine profile and both COM views were unregistered.'
}
