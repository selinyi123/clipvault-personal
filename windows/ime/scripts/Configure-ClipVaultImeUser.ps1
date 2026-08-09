[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Identify', 'Install', 'Uninstall')]
    [string]$Mode,
    [Parameter(Mandatory)]
    [string]$PackageDirectory,
    [string]$RuntimeExecutable = '',
    [string]$Nonce = '',
    [switch]$EnableOtpRelay,
    [switch]$NoConfirm,
    [switch]$AllowBuiltInAdministratorOwner
)

$ErrorActionPreference = 'Stop'
if ($NoConfirm) { $ConfirmPreference = 'None' }
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$contextKey = 'HKCU:\Software\ClipVault\InstallerContext'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$hostRunValue = 'ClipVaultImeHostV2'
$runtimeRunValue = 'ClipVaultRuntimeV2'
$hostExe = Join-Path $packageDirectory 'host-x64\ClipVaultImeHost.exe'
$brokerExe = Join-Path $packageDirectory 'otp-broker\ClipVaultOtpBroker.exe'
$scriptRoot = Join-Path $packageDirectory 'scripts'

if ($Mode -eq 'Identify') {
    if ($Nonce -notmatch '^[a-f0-9]{32}$') {
        throw 'Installer identity nonce is invalid.'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator) -and
        (-not $AllowBuiltInAdministratorOwner -or
            -not $identity.User.Value.EndsWith('-500',
                [StringComparison]::Ordinal))) {
        throw 'The original-user probe received an elevated token.'
    }
    if ($PSCmdlet.ShouldProcess($contextKey, 'Publish the one-time installer owner marker')) {
        New-Item -Path $contextKey -Force | Out-Null
        New-ItemProperty -Path $contextKey -Name $Nonce `
            -Value 'clipvault-v2-owner' -PropertyType String -Force | Out-Null
    }
    return
}

if (-not $RuntimeExecutable) {
    throw 'RuntimeExecutable is required for install and uninstall modes.'
}
$runtimeExecutable = [System.IO.Path]::GetFullPath($RuntimeExecutable)
foreach ($required in @($runtimeExecutable, $hostExe, $brokerExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing installed ClipVault user component: $required"
    }
}

function Stop-ExactUserProcess {
    param([string]$ExecutablePath)
    $canonical = [System.IO.Path]::GetFullPath($ExecutablePath)
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ExecutablePath -and
            [System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $canonical
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

function Disable-UserLaunchPoints {
    Remove-ItemProperty -Path $runKey -Name $hostRunValue `
        -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $runKey -Name $runtimeRunValue `
        -ErrorAction SilentlyContinue
    & (Join-Path $scriptRoot 'Disable-ClipVaultOtpBroker.ps1') `
        -PackageDirectory $packageDirectory -Confirm:$false
    Stop-ExactUserProcess -ExecutablePath $hostExe
    Stop-ExactUserProcess -ExecutablePath $runtimeExecutable
}

if (-not $PSCmdlet.ShouldProcess($packageDirectory,
        "$Mode the current-user ClipVault v2 launch and configuration state")) {
    return
}

if ($Mode -eq 'Uninstall') {
    Disable-UserLaunchPoints
    return
}

$configPath = Join-Path $env:LOCALAPPDATA 'ClipVault\config.toml'
$backupPath = Join-Path $env:TEMP `
    "ClipVault-v2-config-$PID-$([Guid]::NewGuid().ToString('N')).bak"
$hadConfig = Test-Path -LiteralPath $configPath -PathType Leaf
try {
    if ($hadConfig) {
        Copy-Item -LiteralPath $configPath -Destination $backupPath -Force
    }
    $arguments = '--configure-v2-ime-host "' + $hostExe + '"'
    if ($EnableOtpRelay) { $arguments += ' --enable-otp-relay' }
    $process = Start-Process -FilePath $runtimeExecutable `
        -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Runtime v2 configuration failed with exit code $($process.ExitCode)"
    }

    if ($EnableOtpRelay) {
        & (Join-Path $scriptRoot 'Enable-ClipVaultOtpBroker.ps1') `
            -PackageDirectory $packageDirectory -Confirm:$false
    } else {
        & (Join-Path $scriptRoot 'Disable-ClipVaultOtpBroker.ps1') `
            -PackageDirectory $packageDirectory -Confirm:$false
    }

    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -Path $runKey -Name $hostRunValue `
        -Value ('"' + $hostExe + '"') -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $runKey -Name $runtimeRunValue `
        -Value ('"' + $runtimeExecutable + '" --headless') `
        -PropertyType String -Force | Out-Null
} catch {
    try { Disable-UserLaunchPoints } catch { }
    if ($hadConfig -and (Test-Path -LiteralPath $backupPath -PathType Leaf)) {
        Copy-Item -LiteralPath $backupPath -Destination $configPath -Force
    } elseif (-not $hadConfig) {
        Remove-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
    }
    throw 'Current-user ClipVault v2 configuration failed and was rolled back.'
} finally {
    Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
}
