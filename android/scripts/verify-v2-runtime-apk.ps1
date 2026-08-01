[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Apk
)

$ErrorActionPreference = 'Stop'
$apkPath = (Resolve-Path -LiteralPath $Apk).Path
$androidSdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
$buildTools = Get-ChildItem -LiteralPath (Join-Path $androidSdk 'build-tools') -Directory |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (-not $buildTools) { throw 'Android build-tools are unavailable.' }

$aapt2 = Join-Path $buildTools.FullName 'aapt2.exe'
$zipalign = Join-Path $buildTools.FullName 'zipalign.exe'
$apkanalyzer = Join-Path $androidSdk 'cmdline-tools\latest\bin\apkanalyzer.bat'
@($aapt2, $zipalign, $apkanalyzer) | ForEach-Object {
    if (-not (Test-Path -LiteralPath $_)) { throw "Required verifier is missing: $_" }
}

$permissionDump = @(& $aapt2 dump permissions $apkPath 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect Runtime APK permissions.' }
$actualPermissions = @(
    $permissionDump | ForEach-Object {
        if ($_ -match "uses-permission(?:-sdk-\d+)?: name='([^']+)'" ) { $Matches[1] }
    } | Sort-Object -Unique
)
$allowedPermissions = @(
    'android.permission.ACCESS_NETWORK_STATE',
    'android.permission.FOREGROUND_SERVICE',
    'android.permission.INTERNET',
    'android.permission.RECEIVE_BOOT_COMPLETED',
    'android.permission.WAKE_LOCK',
    'com.clipvault.app.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION'
) | Sort-Object
$permissionDifference = @(Compare-Object $allowedPermissions $actualPermissions)
if ($permissionDifference.Count -ne 0) {
    throw "Runtime APK permissions differ from the release allowlist: $($permissionDifference | Out-String)"
}

$manifest = @(& $apkanalyzer manifest print $apkPath 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect merged Runtime APK manifest.' }
$manifestText = $manifest -join "`n"
if ($manifestText -notmatch '(?s)<permission\s+[^>]*android:name="com\.clipvault\.permission\.RUNTIME_SNAPSHOT"[^>]*android:protectionLevel="(?:signature|0x2)"\s*/>') {
    throw 'Runtime snapshot permission must be declared with signature protection.'
}
foreach ($forbidden in @('android.permission.READ_SMS', 'android.permission.RECEIVE_SMS', 'android.service.notification.NotificationListenerService')) {
    if ($manifestText.Contains($forbidden)) { throw "Forbidden default Runtime capability: $forbidden" }
}
foreach ($forbiddenImeMarker in @(
    'android.permission.BIND_INPUT_METHOD',
    'android.view.InputMethod',
    'ClipVaultPanelImeService',
    'ClipVaultFullKeyboardService'
)) {
    if ($manifestText.Contains($forbiddenImeMarker)) {
        throw "The networked Runtime APK must not declare a legacy IME component: $forbiddenImeMarker"
    }
}

$definedDexPackages = @(& $apkanalyzer dex packages --defined-only $apkPath 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect Runtime APK DEX classes.' }
$definedDexText = $definedDexPackages -join "`n"
foreach ($forbiddenClass in @(
    'com.clipvault.app.ime.ClipVaultPanelImeService',
    'com.clipvault.app.ime.ClipVaultFullKeyboardService'
)) {
    if ($definedDexText.Contains($forbiddenClass)) {
        throw "The Runtime APK still contains a legacy IME class: $forbiddenClass"
    }
}

$badging = @(& $aapt2 dump badging $apkPath 2>&1)
if ($LASTEXITCODE -ne 0 -or -not (($badging -join "`n") -match "targetSdkVersion:'36'")) {
    throw 'The Runtime APK must target Android API 36.'
}

& $zipalign -c -P 16 -v 4 $apkPath
if ($LASTEXITCODE -ne 0) { throw 'Runtime APK 16 KB zip alignment failed.' }

Write-Host "Default Runtime APK verification passed: $apkPath"
