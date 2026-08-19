[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$RimeDictionaryDirectory,
    [Parameter(Mandatory)]
    [string]$BuildRoot,
    [Parameter(Mandatory)]
    [string]$OutputDirectory,
    [string]$CanonicalDirectory = ''
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent (Split-Path -Parent `
    (Split-Path -Parent $PSScriptRoot))
if (-not $CanonicalDirectory) {
    $CanonicalDirectory = Join-Path $repositoryRoot 'shared-input\rime'
}

$dictionaryDirectory = [System.IO.Path]::GetFullPath($RimeDictionaryDirectory)
$canonicalDirectory = [System.IO.Path]::GetFullPath($CanonicalDirectory)
$buildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
$outputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$buildPrefix = $buildRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar
if (-not $outputDirectory.StartsWith(
        $buildPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Production Rime staging must be a child of the explicit BuildRoot.'
}
if (-not (Test-Path -LiteralPath $canonicalDirectory -PathType Container) -or
    -not (Test-Path -LiteralPath $dictionaryDirectory -PathType Container)) {
    throw 'Canonical Rime assets and the locked dictionary directory must exist.'
}

$lockPath = Join-Path $canonicalDirectory 'RIME_ASSET_LOCK.json'
if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
    throw "Rime asset lock is missing: $lockPath"
}
$lock = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 |
    ConvertFrom-Json
if ($lock.format_version -ne 1) {
    throw "Unsupported Rime asset lock version: $($lock.format_version)"
}

function Assert-AssetHash {
    param(
        [Parameter(Mandatory)][string]$Directory,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Expected
    )
    $path = Join-Path $Directory $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Locked Rime asset is missing: $path"
    }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "Rime asset hash mismatch for $Name. Expected $Expected; got $actual."
    }
    return $path
}

$canonicalFiles = @{}
foreach ($property in $lock.canonical_assets.PSObject.Properties) {
    $canonicalFiles[$property.Name] = Assert-AssetHash `
        -Directory $canonicalDirectory -Name $property.Name `
        -Expected ([string]$property.Value)
}
$dictionaryFiles = @{}
foreach ($property in $lock.dictionary_source.assets.PSObject.Properties) {
    $dictionaryFiles[$property.Name] = Assert-AssetHash `
        -Directory $dictionaryDirectory -Name $property.Name `
        -Expected ([string]$property.Value)
}

# The ClipVault schemas intentionally depend only on the locked dictionary and
# ClipVault punctuation preset. An extra upstream schema could silently restore
# rime-prelude/stroke/symbols dependencies, so it is never copied.
$allowedImports = @('clipvault_punctuation', 'pinyin_simp')
foreach ($name in @('clipvault_pinyin.schema.yaml',
                     'clipvault_pinyin_private.schema.yaml')) {
    $content = Get-Content -LiteralPath $canonicalFiles[$name] -Encoding UTF8
    foreach ($line in $content) {
        if ($line -match '^\s*(?:import_preset|dictionary):\s*([^\s#]+)') {
            if ($allowedImports -notcontains $Matches[1]) {
                throw "Unapproved Rime dependency '$($Matches[1])' in $name."
            }
        }
    }
}

if (Test-Path -LiteralPath $outputDirectory) {
    # outputDirectory was resolved and proven to be inside BuildRoot above.
    Remove-Item -LiteralPath $outputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $outputDirectory | Out-Null
foreach ($entry in $canonicalFiles.GetEnumerator()) {
    Copy-Item -LiteralPath $entry.Value -Destination `
        (Join-Path $outputDirectory $entry.Key)
}
foreach ($entry in $dictionaryFiles.GetEnumerator()) {
    Copy-Item -LiteralPath $entry.Value -Destination `
        (Join-Path $outputDirectory $entry.Key)
}

$actualFiles = @(Get-ChildItem -LiteralPath $outputDirectory -File |
    ForEach-Object Name | Sort-Object)
$expectedFiles = @($lock.allowed_staged_files | Sort-Object)
if (($actualFiles -join "`n") -ne ($expectedFiles -join "`n")) {
    throw "Rime staging file-set mismatch. Expected: $($expectedFiles -join ', '); got: $($actualFiles -join ', ')."
}
foreach ($name in $actualFiles) {
    $source = if ($canonicalFiles.ContainsKey($name)) {
        $canonicalDirectory
    } else {
        $dictionaryDirectory
    }
    $expected = if ($canonicalFiles.ContainsKey($name)) {
        [string]$lock.canonical_assets.$name
    } else {
        [string]$lock.dictionary_source.assets.$name
    }
    Assert-AssetHash -Directory $outputDirectory -Name $name `
        -Expected $expected | Out-Null
}

Write-Host "Production Rime data staged at: $outputDirectory"
