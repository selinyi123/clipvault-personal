[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$CacheDirectory,
    [string]$CMakePath = ''
)

$ErrorActionPreference = 'Stop'
$cacheDirectory = [System.IO.Path]::GetFullPath($CacheDirectory)
$assetName = 'rime-de4700e-Windows-msvc-x64.7z'
$assetUrl = 'https://github.com/rime/librime/releases/download/1.16.1/rime-de4700e-Windows-msvc-x64.7z'
$expectedSha256 = 'e17c1bb4acc9934669e7a62003aef3f8b56d0afa89e5d893ed7dbf34546abb6e'
$assetDirectory = Join-Path $cacheDirectory 'librime-1.16.1-msvc-x64'
$archive = Join-Path $assetDirectory $assetName
$extractRoot = Join-Path $assetDirectory 'extracted'
$sdkDirectory = Join-Path $extractRoot 'dist'

New-Item -ItemType Directory -Path $assetDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    Invoke-WebRequest -Uri $assetUrl -OutFile $archive
}
$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "librime archive SHA-256 mismatch: expected $expectedSha256, got $actualSha256"
}

$header = Join-Path $sdkDirectory 'include\rime_api.h'
$dll = Join-Path $sdkDirectory 'lib\rime.dll'
if (-not (Test-Path -LiteralPath $header -PathType Leaf) -or
    -not (Test-Path -LiteralPath $dll -PathType Leaf)) {
    if (Test-Path -LiteralPath $extractRoot) {
        throw "Incomplete extraction already exists at $extractRoot; inspect or remove that exact cache directory before retrying."
    }
    if (-not $CMakePath) {
        $cmakeCommand = Get-Command cmake.exe -ErrorAction SilentlyContinue
        if ($cmakeCommand) { $CMakePath = $cmakeCommand.Source }
    }
    if (-not $CMakePath -or -not (Test-Path -LiteralPath $CMakePath -PathType Leaf)) {
        throw 'CMake is required to extract the pinned .7z asset. Pass -CMakePath explicitly.'
    }
    New-Item -ItemType Directory -Path $extractRoot | Out-Null
    Push-Location $extractRoot
    try {
        & $CMakePath -E tar xf $archive
        if ($LASTEXITCODE -ne 0) { throw "CMake extraction failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $header -PathType Leaf) -or
    -not (Test-Path -LiteralPath $dll -PathType Leaf)) {
    throw "Pinned librime SDK is incomplete after extraction: $sdkDirectory"
}

[pscustomobject]@{
    RimeSdkDirectory = $sdkDirectory
    Archive = $archive
    Sha256 = $actualSha256
}
