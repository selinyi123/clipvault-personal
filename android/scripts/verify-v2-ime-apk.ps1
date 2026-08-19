[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Apk,
    [string]$RuntimeApk
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
$apksigner = Join-Path $buildTools.FullName 'apksigner.bat'
$apkanalyzer = Join-Path $androidSdk 'cmdline-tools\latest\bin\apkanalyzer.bat'
$llvmObjdump = Join-Path $androidSdk 'ndk\28.0.13004108\toolchains\llvm\prebuilt\windows-x86_64\bin\llvm-objdump.exe'
@($aapt2, $zipalign, $apkanalyzer, $llvmObjdump) | ForEach-Object {
    if (-not (Test-Path -LiteralPath $_)) { throw "Required verifier is missing: $_" }
}

$permissions = @(& $aapt2 dump permissions $apkPath 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect APK permissions.' }
$permissionText = $permissions -join "`n"
foreach ($forbidden in @(
    'android.permission.INTERNET',
    'android.permission.ACCESS_NETWORK_STATE',
    'android.permission.READ_SMS',
    'android.permission.RECEIVE_SMS',
    'android.permission.BIND_NOTIFICATION_LISTENER_SERVICE'
)) {
    if ($permissionText.Contains($forbidden)) { throw "Forbidden IME permission: $forbidden" }
}
if (-not $permissionText.Contains('com.clipvault.permission.RUNTIME_SNAPSHOT')) {
    throw 'The signature-protected Runtime snapshot permission is missing.'
}

$manifest = @(& $apkanalyzer manifest print $apkPath 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect merged APK manifest.' }
$manifestText = $manifest -join "`n"
if (([regex]::Matches($manifestText, 'android\.view\.InputMethod')).Count -ne 1) {
    throw 'The standalone APK must export exactly one InputMethod service.'
}
if (([regex]::Matches($manifestText, 'android\.permission\.BIND_INPUT_METHOD')).Count -ne 1) {
    throw 'The standalone APK must contain exactly one BIND_INPUT_METHOD service.'
}

$badging = @(& $aapt2 dump badging $apkPath 2>&1)
if ($LASTEXITCODE -ne 0 -or -not (($badging -join "`n") -match "targetSdkVersion:'36'")) {
    throw 'The standalone IME must target Android API 36.'
}

& $zipalign -c -P 16 -v 4 $apkPath
if ($LASTEXITCODE -ne 0) { throw 'APK 16 KB zip alignment failed.' }

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$extractRoot = Join-Path $tempRoot ("clipvault-ime-verify-" + [guid]::NewGuid().ToString('N'))
if (-not ([System.IO.Path]::GetFullPath($extractRoot).StartsWith($tempRoot)) -or
    -not ([System.IO.Path]::GetFileName($extractRoot).StartsWith('clipvault-ime-verify-'))) {
    throw 'Unsafe verification temporary path.'
}
New-Item -ItemType Directory -Path $extractRoot | Out-Null
try {
    $zipCopy = Join-Path $extractRoot 'ime.zip'
    Copy-Item -LiteralPath $apkPath -Destination $zipCopy
    Expand-Archive -LiteralPath $zipCopy -DestinationPath (Join-Path $extractRoot 'apk')
    $libraries = Get-ChildItem -LiteralPath (Join-Path $extractRoot 'apk\lib') -Recurse -Filter '*.so'
    $abis = $libraries | ForEach-Object { $_.Directory.Name } | Sort-Object -Unique
    if (@($abis).Count -ne 2 -or 'arm64-v8a' -notin $abis -or 'x86_64' -notin $abis) {
        throw "Expected exactly arm64-v8a and x86_64, found: $abis"
    }
    foreach ($library in $libraries) {
        $headers = @(& $llvmObjdump -p $library.FullName 2>&1)
        if ($LASTEXITCODE -ne 0) { throw "Unable to inspect ELF: $($library.FullName)" }
        $alignments = @(
            $headers | ForEach-Object {
                if ($_ -match '^\s*LOAD\b.*\balign\s+2\*\*(\d+)\s*$') { [int]$Matches[1] }
            }
        )
        if ($alignments.Count -eq 0 -or @($alignments | Where-Object { $_ -lt 14 }).Count -gt 0) {
            throw "ELF is not 16 KB aligned: $($library.FullName) ($alignments)"
        }
    }
} finally {
    Remove-Item -LiteralPath $extractRoot -Recurse -Force
}

if ($RuntimeApk) {
    if (-not (Test-Path -LiteralPath $apksigner)) { throw "apksigner is missing: $apksigner" }
    $runtimePath = (Resolve-Path -LiteralPath $RuntimeApk).Path
    $imeCert = @(& $apksigner verify --print-certs $apkPath 2>&1) |
        Where-Object { $_ -match 'Signer #1 certificate SHA-256 digest:' }
    $runtimeCert = @(& $apksigner verify --print-certs $runtimePath 2>&1) |
        Where-Object { $_ -match 'Signer #1 certificate SHA-256 digest:' }
    if ($imeCert.Count -ne 1 -or $runtimeCert.Count -ne 1 -or $imeCert[0] -ne $runtimeCert[0]) {
        throw 'IME and Runtime APKs are not signed by the same certificate.'
    }
}

Write-Host "Standalone IME APK verification passed: $apkPath"
