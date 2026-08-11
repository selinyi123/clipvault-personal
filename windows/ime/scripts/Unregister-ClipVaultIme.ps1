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
$registrationSchema = 2
$tsfClsid = '{C5CEE00A-05AD-4ABA-93BB-6E76932AF126}'
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
function Test-PhysicalImeRegistration {
    foreach ($view in @(
            [Microsoft.Win32.RegistryView]::Registry64,
            [Microsoft.Win32.RegistryView]::Registry32)) {
        $base = $null
        $key = $null
        try {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
                [Microsoft.Win32.RegistryHive]::LocalMachine, $view)
            $key = $base.OpenSubKey(
                "Software\Classes\CLSID\$tsfClsid", $false)
            if ($key -ne $null) { return $true }
        } finally {
            if ($key -ne $null) { $key.Dispose() }
            if ($base -ne $null) { $base.Dispose() }
        }
    }
    return $false
}

if (-not $WhatIfPreference) {
    $physicalRegistrationPresent = Test-PhysicalImeRegistration
    $stateExists = Test-Path -LiteralPath $stateKey
    if (-not $stateExists) {
        if ($physicalRegistrationPresent) {
            throw 'Refusing to unregister a physical ClipVault TSF registration without trusted machine state.'
        }
        Write-Host 'ClipVault Input v2 machine registration was already clean.'
        return
    }

    $state = Get-ItemProperty -LiteralPath $stateKey
    if ($state.RegistrationSchema -ne $registrationSchema -or
        -not $state.PackageDirectory -or
        -not [string]::Equals(
            [System.IO.Path]::GetFullPath([string]$state.PackageDirectory),
            $packageDirectory,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to unregister a machine profile with unsupported or different ownership state.'
    }
    if ($state.RegistrationPresent -notin @(0, 1, 2)) {
        throw 'Refusing to unregister a machine profile with unsupported registration state.'
    }
    if ($state.RegistrationPresent -eq 0 -and -not $physicalRegistrationPresent) {
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
