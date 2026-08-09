[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory,
    [Alias('AllowSystemWideTsfRegistration')]
    [switch]$AllowMachineWideRegistration,
    [switch]$NoConfirm
)

$ErrorActionPreference = 'Stop'
if ($NoConfirm) { $ConfirmPreference = 'None' }
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$x64Dll = Join-Path $packageDirectory 'x64\ClipVaultTextService.dll'
$x86Dll = Join-Path $packageDirectory 'x86\ClipVaultTextService.dll'
$imeHostExe = Join-Path $packageDirectory 'host-x64\ClipVaultImeHost.exe'
$otpBrokerExe = Join-Path $packageDirectory 'otp-broker\ClipVaultOtpBroker.exe'
$stateKey = 'HKLM:\Software\ClipVault\ImeV2'
$registrationSchema = 2

foreach ($required in @($x64Dll, $x86Dll, $imeHostExe, $otpBrokerExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing v2 IME package file: $required"
    }
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'ClipVault v2 requires 64-bit Windows with x64 and x86 TSF clients.'
}
if (-not $AllowMachineWideRegistration -and -not $WhatIfPreference) {
    throw 'Machine-wide TSF registration requires explicit -AllowMachineWideRegistration authorization.'
}
$principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $WhatIfPreference -and
    -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Machine-wide TSF registration requires an elevated administrator token.'
}

if (-not $WhatIfPreference -and (Test-Path -LiteralPath $stateKey)) {
    $state = Get-ItemProperty -LiteralPath $stateKey
    if ($state.RegistrationSchema -ne $registrationSchema -or
        -not $state.PackageDirectory -or
        -not [string]::Equals(
            [System.IO.Path]::GetFullPath([string]$state.PackageDirectory),
            $packageDirectory,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'A different or unsupported ClipVault IME registration owns the machine profile; repair is required.'
    }
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
        throw "regsvr32 failed with exit code $($process.ExitCode)"
    }
}

if ($PSCmdlet.ShouldProcess($packageDirectory,
        'Register x86/x64 HKLM COM views and the x64-owned Windows TSF profile')) {
    $x86Attempted = $false
    $x64Attempted = $false
    try {
        # The shared profile is the transaction's last machine mutation.
        $x86Attempted = $true
        Invoke-Regsvr32 -Executable $regsvr32 -Dll $x86Dll
        $x64Attempted = $true
        Invoke-Regsvr32 -Executable $regsvr64 -Dll $x64Dll

        New-Item -Path $stateKey -Force | Out-Null
        New-ItemProperty -LiteralPath $stateKey -Name RegistrationSchema `
            -Value $registrationSchema -PropertyType DWord -Force | Out-Null
        New-ItemProperty -LiteralPath $stateKey -Name PackageDirectory `
            -Value $packageDirectory -PropertyType String -Force | Out-Null
        New-ItemProperty -LiteralPath $stateKey -Name RegistrationPresent `
            -Value 1 -PropertyType DWord -Force | Out-Null
        Remove-ItemProperty -LiteralPath $stateKey -Name RepairRequired `
            -ErrorAction SilentlyContinue
    } catch {
        $rollbackErrors = [System.Collections.Generic.List[string]]::new()
        if ($x64Attempted) {
            try {
                Invoke-Regsvr32 -Executable $regsvr64 -Dll $x64Dll -Unregister
            } catch {
                $rollbackErrors.Add('x64')
            }
        }
        if ($x86Attempted) {
            try {
                Invoke-Regsvr32 -Executable $regsvr32 -Dll $x86Dll -Unregister
            } catch {
                $rollbackErrors.Add('x86')
            }
        }
        if ($rollbackErrors.Count -eq 0) {
            Remove-Item -LiteralPath $stateKey -Recurse -Force `
                -ErrorAction SilentlyContinue
        } else {
            New-Item -Path $stateKey -Force | Out-Null
            New-ItemProperty -LiteralPath $stateKey -Name RegistrationSchema `
                -Value $registrationSchema -PropertyType DWord -Force | Out-Null
            New-ItemProperty -LiteralPath $stateKey -Name PackageDirectory `
                -Value $packageDirectory -PropertyType String -Force | Out-Null
            New-ItemProperty -LiteralPath $stateKey -Name RegistrationPresent `
                -Value 2 -PropertyType DWord -Force | Out-Null
            New-ItemProperty -LiteralPath $stateKey -Name RepairRequired `
                -Value 1 -PropertyType DWord -Force | Out-Null
        }
        throw "ClipVault machine registration failed; rollback_errors=$($rollbackErrors.Count)"
    }
    Write-Host 'ClipVault Input v2 machine COM views and x64-owned TSF profile were registered.'
}
