[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$androidRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$codexRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$androidSdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
$adb = Join-Path $androidSdk 'platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $adb -PathType Leaf)) { throw "adb is unavailable: $adb" }

$deviceLines = @(& $adb devices 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'adb devices failed.' }
$devices = @(
    $deviceLines | ForEach-Object {
        if ($_ -match '^(\S+)\s+device$') { $Matches[1] }
    }
)
if ($devices.Count -ne 1) {
    throw "The clipvault-android-device runner must expose exactly one authorized device; found $($devices.Count)."
}
$env:ANDROID_SERIAL = $devices[0]
$api = ((& $adb -s $env:ANDROID_SERIAL shell getprop ro.build.version.sdk) | Out-String).Trim()
$model = ((& $adb -s $env:ANDROID_SERIAL shell getprop ro.product.model) | Out-String).Trim()
$pageSize = ((& $adb -s $env:ANDROID_SERIAL shell getconf PAGE_SIZE) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $api -notmatch '^\d+$' -or [int]$api -lt 26) {
    throw "Device API level is invalid or below minSdk 26: $api"
}
Write-Host "Running native IME instrumentation on serial=$env:ANDROID_SERIAL model=$model api=$api page_size=$pageSize"

$librimeSource = Join-Path $codexRoot 'third-party-cache\librime-de4700e9'
$librimeBuildArm64 = Join-Path $codexRoot 'third-party-build\librime-de4700e9-arm64'
$librimeBuildX8664 = Join-Path $codexRoot 'third-party-build\librime-de4700e9-x86_64'
$nativePrebuiltRoot = Join-Path $codexRoot 'third-party-cache\fcitx5-android-048f581c\lib\fcitx5\src\main\cpp\prebuilt'
$rimeDataDir = Join-Path $codexRoot 'third-party-cache\rime-pinyin-simp-0c6861ef'
@($librimeSource, $librimeBuildArm64, $librimeBuildX8664, $nativePrebuiltRoot, $rimeDataDir) |
    ForEach-Object { if (-not (Test-Path -LiteralPath $_)) { throw "Missing locked native input: $_" } }

$resultRoot = Join-Path $androidRoot 'ime-app\build\outputs\androidTest-results\connected\debug'
Push-Location $androidRoot
try {
    & .\gradlew.bat :ime-app:connectedDebugAndroidTest --no-daemon `
        '-PclipvaultRimeNativeEnabled=true' `
        "-PclipvaultLibrimeSource=$librimeSource" `
        "-PclipvaultLibrimeBuildArm64=$librimeBuildArm64" `
        "-PclipvaultLibrimeBuildX8664=$librimeBuildX8664" `
        "-PclipvaultNativePrebuiltRoot=$nativePrebuiltRoot" `
        "-PclipvaultRimeDataDir=$rimeDataDir" `
        '-Pandroid.testInstrumentationRunnerArguments.class=com.clipvault.imeapp.NativeRimeDeviceTest'
    if ($LASTEXITCODE -ne 0) { throw "Native IME device tests failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$reports = @(Get-ChildItem -LiteralPath $resultRoot -Recurse -Filter 'TEST-*.xml' -File -ErrorAction SilentlyContinue)
if ($reports.Count -eq 0) { throw "No connected-test XML was produced at $resultRoot" }
$tests = 0
$failures = 0
$errors = 0
$skipped = 0
foreach ($report in $reports) {
    [xml]$xml = Get-Content -LiteralPath $report.FullName -Raw -Encoding UTF8
    $tests += [int]$xml.testsuite.tests
    $failures += [int]$xml.testsuite.failures
    $errors += [int]$xml.testsuite.errors
    $skipped += [int]$xml.testsuite.skipped
}
if ($tests -lt 6 -or $failures -ne 0 -or $errors -ne 0 -or $skipped -ne 0) {
    throw "NativeRimeDeviceTest evidence is incomplete: tests=$tests failures=$failures errors=$errors skipped=$skipped"
}
Write-Host "NativeRimeDeviceTest passed on a real connected runner: tests=$tests"
