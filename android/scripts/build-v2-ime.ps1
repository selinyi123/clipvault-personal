[CmdletBinding()]
param(
    [string]$CandidateDirectory
)

$ErrorActionPreference = 'Stop'
$androidRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$codexRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$androidSdk = Join-Path $env:LOCALAPPDATA 'Android\Sdk'

$librimeSource = Join-Path $codexRoot 'third-party-cache\librime-de4700e9'
$librimeBuildArm64 = Join-Path $codexRoot 'third-party-build\librime-de4700e9-arm64'
$librimeBuildX8664 = Join-Path $codexRoot 'third-party-build\librime-de4700e9-x86_64'
$fcitxRoot = Join-Path $codexRoot 'third-party-cache\fcitx5-android-048f581c'
$nativePrebuiltRoot = Join-Path $fcitxRoot 'lib\fcitx5\src\main\cpp\prebuilt'
$rimeDataDir = Join-Path $codexRoot 'third-party-cache\rime-pinyin-simp-0c6861ef'
$rimeDataArchive = Join-Path $codexRoot 'third-party-cache\rime-pinyin-simp-0c6861ef.tar.gz'
$lockPath = Join-Path $androidRoot 'rime-engine-android\RIME_PRODUCTION_LOCK.json'
$assetLockPath = Join-Path $androidRoot '..\shared-input\rime\RIME_ASSET_LOCK.json'

@(
    $androidSdk,
    $librimeSource,
    $librimeBuildArm64,
    $librimeBuildX8664,
    $nativePrebuiltRoot,
    $rimeDataDir,
    $rimeDataArchive,
    $lockPath,
    $assetLockPath
) | ForEach-Object {
    if (-not (Test-Path -LiteralPath $_)) {
        throw "Required production input is missing: $_"
    }
}

$lock = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($lock.decision -cne 'A_custom_librime_jni') {
    throw "Unexpected production Rime decision: $($lock.decision)"
}

function Assert-GitHead {
    param([string]$Repository, [string]$Expected)
    $actual = (& git -C $Repository rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $actual -cne $Expected) {
        throw "Git lock mismatch for $Repository. Expected $Expected, got $actual"
    }
}

function Assert-FileHash {
    param([string]$Path, [string]$Expected)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Locked file is missing: $Path"
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $Expected) {
        throw "SHA-256 mismatch for $Path. Expected $Expected, got $actual"
    }
}

Assert-GitHead $librimeSource $lock.sources.librime.commit
Assert-GitHead $fcitxRoot $lock.sources.fcitx5_android_prebuilt.parent_commit
Assert-GitHead $nativePrebuiltRoot $lock.sources.fcitx5_android_prebuilt.commit
Assert-FileHash $rimeDataArchive $lock.sources.rime_pinyin_simp.archive_sha256
foreach ($entry in $lock.sources.rime_pinyin_simp.file_sha256.PSObject.Properties) {
    Assert-FileHash (Join-Path $rimeDataDir $entry.Name) ([string]$entry.Value)
}
foreach ($archive in $lock.native_archives) {
    Assert-FileHash (Join-Path $codexRoot ([string]$archive.path)) ([string]$archive.sha256)
}
Write-Host "Verified production Rime lock: $lockPath"

$env:ANDROID_HOME = $androidSdk
$env:ANDROID_SDK_ROOT = $androidSdk

Push-Location $androidRoot
try {
    & .\gradlew.bat :ime-app:buildProductionIme :app:assembleRelease --no-daemon `
        '-PclipvaultRimeNativeEnabled=true' `
        "-PclipvaultLibrimeSource=$librimeSource" `
        "-PclipvaultLibrimeBuildArm64=$librimeBuildArm64" `
        "-PclipvaultLibrimeBuildX8664=$librimeBuildX8664" `
        "-PclipvaultNativePrebuiltRoot=$nativePrebuiltRoot" `
        "-PclipvaultRimeDataDir=$rimeDataDir"
    if ($LASTEXITCODE -ne 0) {
        throw "Production IME build failed with exit code $LASTEXITCODE"
    }
    $releaseApk = @(Get-ChildItem -LiteralPath '.\ime-app\build\outputs\apk\release' -Filter '*.apk')
    if (@($releaseApk).Count -ne 1) {
        throw "Expected one release IME APK, found: $releaseApk"
    }
    & (Join-Path $PSScriptRoot 'verify-v2-ime-apk.ps1') -Apk $releaseApk[0].FullName
    if ($LASTEXITCODE -ne 0) {
        throw "Production IME APK verification failed with exit code $LASTEXITCODE"
    }
    & (Join-Path $PSScriptRoot 'verify-v2-rime-assets.ps1') `
        -Apk $releaseApk[0].FullName -AssetLock $assetLockPath
    if ($LASTEXITCODE -ne 0) {
        throw "Production IME Rime asset verification failed with exit code $LASTEXITCODE"
    }

    $runtimeApk = @(Get-ChildItem -LiteralPath '.\app\build\outputs\apk\release' -Filter '*.apk')
    if (@($runtimeApk).Count -ne 1) {
        throw "Expected one default Runtime release APK, found: $runtimeApk"
    }
    & (Join-Path $PSScriptRoot 'verify-v2-runtime-apk.ps1') -Apk $runtimeApk[0].FullName
    if ($LASTEXITCODE -ne 0) {
        throw "Default Runtime APK verification failed with exit code $LASTEXITCODE"
    }

    if ($CandidateDirectory) {
        $candidateRoot = [System.IO.Path]::GetFullPath($CandidateDirectory)
        New-Item -ItemType Directory -Path $candidateRoot -Force | Out-Null
        $imeCandidate = Join-Path $candidateRoot 'ClipVault-IME-v2-unsigned.apk'
        $runtimeCandidate = Join-Path $candidateRoot 'ClipVault-Runtime-v2-unsigned.apk'
        Copy-Item -LiteralPath $releaseApk[0].FullName -Destination $imeCandidate -Force
        Copy-Item -LiteralPath $runtimeApk[0].FullName -Destination $runtimeCandidate -Force
        $hashLines = @($imeCandidate, $runtimeCandidate) | ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $([System.IO.Path]::GetFileName($_))"
        }
        $hashLines | Set-Content -LiteralPath (Join-Path $candidateRoot 'SHA256SUMS.txt') -Encoding ASCII
        Write-Host "Unsigned Android v2 candidate staged at: $candidateRoot"
    }
} finally {
    Pop-Location
}
