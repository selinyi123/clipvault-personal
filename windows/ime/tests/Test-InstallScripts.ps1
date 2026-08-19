[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$package = Join-Path ([System.IO.Path]::GetTempPath()) `
    "ClipVaultImeWhatIf-$PID-$([Guid]::NewGuid().ToString('N'))"
try {
    foreach ($relative in @('x64', 'x86', 'host-x64', 'otp-broker')) {
        New-Item -ItemType Directory -Path (Join-Path $package $relative) `
            -Force | Out-Null
    }
    foreach ($relative in @('x64\ClipVaultTextService.dll',
                             'x86\ClipVaultTextService.dll',
                             'host-x64\ClipVaultImeHost.exe',
                             'otp-broker\ClipVaultOtpBroker.exe')) {
        New-Item -ItemType File -Path (Join-Path $package $relative) `
            -Force | Out-Null
    }
    & (Join-Path $root 'scripts\Register-ClipVaultIme.ps1') `
        -PackageDirectory $package -WhatIf
    & (Join-Path $root 'scripts\Unregister-ClipVaultIme.ps1') `
        -PackageDirectory $package -WhatIf
    & (Join-Path $root 'scripts\Disable-ClipVaultOtpBroker.ps1') `
        -PackageDirectory $package -WhatIf
    $unsignedRejected = $false
    try {
        & (Join-Path $root 'scripts\Enable-ClipVaultOtpBroker.ps1') `
            -PackageDirectory $package -WhatIf
    } catch {
        if ($_.Exception.Message -like '*unsigned or untrusted OTP Broker*') {
            $unsignedRejected = $true
        } else {
            throw
        }
    }
    if (-not $unsignedRejected) {
        throw 'Unsigned OTP Broker enablement did not fail closed.'
    }
} finally {
    if (Test-Path -LiteralPath $package) {
        Remove-Item -LiteralPath $package -Recurse -Force
    }
}
