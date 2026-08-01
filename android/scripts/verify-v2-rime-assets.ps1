[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Apk,
    [Parameter(Mandatory = $true)]
    [string]$AssetLock
)

$ErrorActionPreference = 'Stop'
$apkPath = (Resolve-Path -LiteralPath $Apk).Path
$lockPath = (Resolve-Path -LiteralPath $AssetLock).Path
$lock = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 | ConvertFrom-Json

$allowed = @($lock.allowed_staged_files | ForEach-Object { [string]$_ })
if ($allowed.Count -eq 0 -or @($allowed | Sort-Object -Unique).Count -ne $allowed.Count) {
    throw 'Rime asset lock contains an empty or duplicate allowed_staged_files list.'
}

$lockedHashes = @{}
foreach ($entry in $lock.canonical_assets.PSObject.Properties) {
    $lockedHashes[$entry.Name] = ([string]$entry.Value).ToLowerInvariant()
}
foreach ($entry in $lock.dictionary_source.assets.PSObject.Properties) {
    $lockedHashes[$entry.Name] = ([string]$entry.Value).ToLowerInvariant()
}
if (@(Compare-Object ($allowed | Sort-Object) ($lockedHashes.Keys | Sort-Object)).Count -ne 0) {
    throw 'Rime asset lock hashes do not exactly cover allowed_staged_files.'
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($apkPath)
try {
    $prefix = 'assets/rime/'
    $topLevelEntries = @(
        $archive.Entries | Where-Object {
            $_.FullName.StartsWith($prefix, [System.StringComparison]::Ordinal) -and
            -not $_.FullName.EndsWith('/') -and
            -not $_.FullName.Substring($prefix.Length).Contains('/')
        }
    )
    $actual = @($topLevelEntries | ForEach-Object { $_.FullName.Substring($prefix.Length) })
    $difference = @(Compare-Object ($allowed | Sort-Object) ($actual | Sort-Object))
    if ($difference.Count -ne 0) {
        throw "APK Rime assets do not match allowed_staged_files. Difference: $($difference | Out-String)"
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($entry in $topLevelEntries) {
            $name = $entry.FullName.Substring($prefix.Length)
            $stream = $entry.Open()
            try {
                $hashBytes = $sha256.ComputeHash($stream)
            } finally {
                $stream.Dispose()
            }
            $actualHash = ([System.BitConverter]::ToString($hashBytes)).Replace('-', '').ToLowerInvariant()
            if ($actualHash -cne $lockedHashes[$name]) {
                throw "APK Rime asset SHA-256 mismatch for $name. Expected $($lockedHashes[$name]), got $actualHash"
            }
        }
    } finally {
        $sha256.Dispose()
    }
} finally {
    $archive.Dispose()
}

Write-Host "APK Rime assets match lock: $apkPath"
