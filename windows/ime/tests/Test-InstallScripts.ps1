[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$package = Join-Path ([System.IO.Path]::GetTempPath()) `
    "ClipVaultImeWhatIf-$PID-$([Guid]::NewGuid().ToString('N'))"
try {
    foreach ($relative in @('x64', 'x86', 'host-x64', 'otp-broker', 'scripts')) {
        New-Item -ItemType Directory -Path (Join-Path $package $relative) `
            -Force | Out-Null
    }
    foreach ($relative in @('x64\ClipVaultTextService.dll',
                             'x86\ClipVaultTextService.dll',
                             'clipvault.exe',
                             'host-x64\ClipVaultImeHost.exe',
                             'host-x64\rime.dll',
                             'otp-broker\ClipVaultOtpBroker.exe')) {
        New-Item -ItemType File -Path (Join-Path $package $relative) `
            -Force | Out-Null
    }
    New-Item -ItemType Directory -Path (Join-Path $package 'host-x64\rime-data') `
        -Force | Out-Null
    New-Item -ItemType File -Path `
        (Join-Path $package 'host-x64\rime-data\clipvault_pinyin.schema.yaml') `
        -Force | Out-Null
    & (Join-Path $root 'scripts\Register-ClipVaultIme.ps1') `
        -PackageDirectory $package -WhatIf
    & (Join-Path $root 'scripts\Unregister-ClipVaultIme.ps1') `
        -PackageDirectory $package -WhatIf
    & (Join-Path $root 'scripts\Configure-ClipVaultImeUser.ps1') `
        -Mode Uninstall -PackageDirectory $package `
        -RuntimeExecutable (Join-Path $package 'clipvault.exe') -WhatIf
    $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & (Join-Path $root 'scripts\Stop-ClipVaultImeOwnerProcesses.ps1') `
        -PackageDirectory $package `
        -RuntimeExecutable (Join-Path $package 'clipvault.exe') `
        -OwnerSid $ownerSid -WhatIf
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

    $deployScript = Join-Path $root 'scripts\Deploy-RimeData.ps1'
    $actualSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $differentSid = if ($actualSid -eq 'S-1-5-18') {
        'S-1-5-21-1000000001-1000000002-1000000003-1001'
    } else {
        'S-1-5-18'
    }
    $differentOwnerRejected = $false
    try {
        & $deployScript -HostDirectory (Join-Path $package 'host-x64') `
            -ExpectedOwnerSid $differentSid -WhatIf
    } catch {
        if ($_.Exception.Message -like '*captured interactive owner*') {
            $differentOwnerRejected = $true
        } else {
            throw
        }
    }
    if (-not $differentOwnerRejected) {
        throw 'Cross-account Rime deployment did not fail closed.'
    }
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentPrincipal = [Security.Principal.WindowsPrincipal]::new(
        $currentIdentity)
    if ($currentPrincipal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $elevatedOwnerRejected = $false
        try {
            & $deployScript -HostDirectory (Join-Path $package 'host-x64') `
                -ExpectedOwnerSid $actualSid -WhatIf
        } catch {
            if ($_.Exception.Message -like '*elevated token*') {
                $elevatedOwnerRejected = $true
            } else {
                throw
            }
        }
        if (-not $elevatedOwnerRejected) {
            throw 'Elevated Rime deployment did not fail closed.'
        }
    } else {
        & $deployScript -HostDirectory (Join-Path $package 'host-x64') `
            -ExpectedOwnerSid $actualSid -WhatIf
    }
    $deployText = Get-Content -LiteralPath $deployScript -Raw
    foreach ($requiredToken in @('ExpectedOwnerSid',
                                  'WindowsBuiltInRole]::Administrator',
                                  'ownerLocalAppData',
                                  'userDataDirectory.StartsWith')) {
        if (-not $deployText.Contains($requiredToken)) {
            throw "Original-user Rime deployment contract is missing: $requiredToken"
        }
    }

    $registerText = Get-Content -LiteralPath `
        (Join-Path $root 'scripts\Register-ClipVaultIme.ps1') -Raw
    if ($registerText.IndexOf('Invoke-Regsvr32 -Executable $regsvr32 -Dll $x86Dll') `
        -gt $registerText.IndexOf('Invoke-Regsvr32 -Executable $regsvr64 -Dll $x64Dll')) {
        throw 'Machine registration no longer orders x86 COM before x64 profile ownership.'
    }
    $unregisterText = Get-Content -LiteralPath `
        (Join-Path $root 'scripts\Unregister-ClipVaultIme.ps1') -Raw
    if ($unregisterText.IndexOf('Invoke-Unregister -Executable $regsvr64 -Dll $x64Dll') `
        -gt $unregisterText.IndexOf('Invoke-Unregister -Executable $regsvr32 -Dll $x86Dll')) {
        throw 'Machine unregistration no longer removes the x64 profile first.'
    }
} finally {
    if (Test-Path -LiteralPath $package) {
        Remove-Item -LiteralPath $package -Recurse -Force
    }
}
