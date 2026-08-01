[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$androidRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$appBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $androidRoot 'app\build'))

function Get-RestrictedInstallables {
    if (-not (Test-Path -LiteralPath $appBuildRoot -PathType Container)) { return @() }
    return @(
        Get-ChildItem -LiteralPath $appBuildRoot -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Extension -in @('.apk', '.aab') -and
                $_.FullName -match '(?i)otpSmsRelay|otp[-_]?sms[-_]?relay'
            }
    )
}

function Remove-RestrictedInstallables {
    param([System.IO.FileInfo[]]$Files)
    foreach ($file in $Files) {
        $resolved = [System.IO.Path]::GetFullPath($file.FullName)
        if (-not $resolved.StartsWith($appBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a restricted artifact outside app/build: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Force
    }
}

Remove-RestrictedInstallables (Get-RestrictedInstallables)
$gradleExit = 0
Push-Location $androidRoot
try {
    # Empty command-line properties override any developer-machine defaults, so
    # this invocation is unambiguously unauthorized even on an Owner workstation.
    & .\gradlew.bat :app:assembleOtpSmsRelay --no-daemon `
        '-PCLIPVAULT_PLAY_SMS_APPROVAL_REF=' `
        '-PCV_KEYSTORE='
    $gradleExit = $LASTEXITCODE
} finally {
    Pop-Location
}

$leaked = @(Get-RestrictedInstallables)
Remove-RestrictedInstallables $leaked
$remaining = @(Get-RestrictedInstallables)
if ($remaining.Count -ne 0) {
    throw "Unable to erase restricted installables: $($remaining.FullName -join ', ')"
}
if ($gradleExit -eq 0) {
    throw 'Unauthorized assembleOtpSmsRelay unexpectedly succeeded.'
}
if ($leaked.Count -ne 0) {
    throw "Unauthorized build leaked restricted installables; they were erased: $($leaked.Name -join ', ')"
}

Write-Host 'Restricted OTP artifact gate rejected unauthorized assembly and left zero APK/AAB files.'
