[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Wheelhouse,

    [Parameter(Mandatory = $true)]
    [string] $Destination
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$wheelhousePath = (Resolve-Path -LiteralPath $Wheelhouse).Path
if (Test-Path -LiteralPath $Destination) {
    throw "Notice destination already exists: $Destination"
}
New-Item -ItemType Directory -Path $Destination | Out-Null
$destinationPath = (Resolve-Path -LiteralPath $Destination).Path

Add-Type -AssemblyName System.IO.Compression.FileSystem

$wheels = @(Get-ChildItem -LiteralPath $wheelhousePath -File -Filter "*.whl" | Sort-Object Name)
if ($wheels.Count -ne 10) {
    throw "Expected exactly 10 production wheels; found $($wheels.Count)"
}

$manifest = New-Object System.Collections.Generic.List[object]
foreach ($wheel in $wheels) {
    $wheelDestination = Join-Path $destinationPath $wheel.BaseName
    New-Item -ItemType Directory -Path $wheelDestination | Out-Null
    $archive = [System.IO.Compression.ZipFile]::OpenRead($wheel.FullName)
    $extracted = 0
    try {
        foreach ($entry in $archive.Entries) {
            $entryName = $entry.FullName.Replace("\", "/")
            if ([string]::IsNullOrWhiteSpace($entry.Name)) {
                continue
            }
            $isNotice = (
                $entryName -match "(?i)\.dist-info/(licenses?/|license[^/]*$|copying[^/]*$|notice[^/]*$|sboms/)" -or
                $entryName -match "(?i)(^|/)(license|copying|notice)(\.[^/]+)?$"
            )
            if (-not $isNotice) {
                continue
            }
            $segments = @($entryName.Split("/") | Where-Object { $_ -ne "" })
            if ($segments.Count -eq 0 -or $segments -contains "..") {
                throw "Unsafe wheel member path in $($wheel.Name)"
            }
            $target = $wheelDestination
            foreach ($segment in $segments) {
                $target = Join-Path $target $segment
            }
            $targetParent = Split-Path -Parent $target
            New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
            $input = $entry.Open()
            $output = [System.IO.File]::Open(
                $target,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            try {
                $input.CopyTo($output)
            }
            finally {
                $output.Dispose()
                $input.Dispose()
            }
            $extracted += 1
            $manifest.Add([ordered]@{
                wheel = $wheel.Name
                member = $entryName
                sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            })
        }
    }
    finally {
        $archive.Dispose()
    }
    if ($extracted -eq 0) {
        throw "No license, notice, or SBOM file found in $($wheel.Name)"
    }
}

$manifestPath = Join-Path $destinationPath "NOTICE-MANIFEST.json"
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Host "Exported notices from $($wheels.Count) locked wheels."
