[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9]+\.[0-9]+\.[0-9]+$")]
    [string] $Version,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{40}$")]
    [string] $Commit,

    [Parameter(Mandatory = $true)]
    [string] $Wheelhouse,

    [Parameter(Mandatory = $true)]
    [string] $RuntimeNotices,

    [Parameter(Mandatory = $true)]
    [string] $Inventory,

    [Parameter(Mandatory = $true)]
    [string] $TraySelfTestReport,

    [Parameter(Mandatory = $true)]
    [string] $PythonExecutable,

    [Parameter(Mandatory = $true)]
    [string] $Output
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$desktopRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $desktopRoot
if ($Version -cne "1.6.0") {
    throw "This relink kit contract supports only v1.6.0"
}
$expectedOutputName = "ClipVault-v$Version-LGPL-relink-kit.zip"
if ((Split-Path -Leaf $Output) -cne $expectedOutputName) {
    throw "Relink kit output must be named $expectedOutputName"
}
if (Test-Path -LiteralPath $Output) {
    throw "Relink kit output already exists: $Output"
}

$head = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -cne $Commit) {
    throw "Release commit does not match the checked-out HEAD"
}
$trackedInputs = @(
    "THIRD_PARTY_NOTICES.md",
    "docs/ADR/0012-windows-tray-dependencies-and-lgpl-delivery.md",
    "third_party/RELINKING_V1_6_0.md",
    "third_party/source-acquisition-v1.6.0.json",
    "desktop/pyproject.toml",
    "desktop/packaging/Build-LgplRelinkKit.ps1",
    "desktop/packaging/Export-WheelNotices.ps1",
    "desktop/packaging/repack_pystray_wheel.py",
    "desktop/packaging/run_clipvault.py",
    "desktop/packaging/windows-release-requirements.txt",
    "installer/clipvault.iss",
    ".github/workflows/release.yml"
)
foreach ($trackedInput in $trackedInputs) {
    & git -C $repoRoot ls-files --error-unmatch -- $trackedInput *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Relink input is not tracked at the release commit: $trackedInput"
    }
}
& git -C $repoRoot diff --quiet $Commit -- $trackedInputs
if ($LASTEXITCODE -ne 0) {
    throw "Tracked relink inputs differ from the release commit"
}

$wheelhousePath = (Resolve-Path -LiteralPath $Wheelhouse).Path
$runtimeNoticesPath = (Resolve-Path -LiteralPath $RuntimeNotices).Path
$inventoryPath = (Resolve-Path -LiteralPath $Inventory).Path
$traySelfTestPath = (Resolve-Path -LiteralPath $TraySelfTestReport).Path
$pythonPath = (Resolve-Path -LiteralPath $PythonExecutable).Path
$requiredNoticeMembers = @(
    "pystray-0.19.5-py2.py3-none-any\pystray-0.19.5.dist-info\COPYING",
    "pystray-0.19.5-py2.py3-none-any\pystray-0.19.5.dist-info\COPYING.LGPL",
    "pillow-12.3.0-cp311-cp311-win_amd64\pillow-12.3.0.dist-info\licenses\LICENSE",
    "pillow-12.3.0-cp311-cp311-win_amd64\pillow-12.3.0.dist-info\sboms\pillow-12.3.0.cdx.json",
    "NOTICE-MANIFEST.json"
)
foreach ($requiredNoticeMember in $requiredNoticeMembers) {
    if (-not (Test-Path -LiteralPath (Join-Path $runtimeNoticesPath $requiredNoticeMember) -PathType Leaf)) {
        throw "Required wheel notice or SBOM is missing: $requiredNoticeMember"
    }
}
$inventoryLines = @(Get-Content -LiteralPath $inventoryPath)
if ($inventoryLines.Count -eq 0) {
    throw "Frozen onefile inventory is empty"
}
$inventoryText = $inventoryLines -join "`n"
foreach ($requiredModule in @("pystray._win32", "PIL.Image")) {
    $requiredToken = "'" + $requiredModule + "'"
    if (-not $inventoryText.Contains($requiredToken)) {
        throw "Frozen onefile inventory is missing required module: $requiredModule"
    }
}
foreach ($disallowedComponent in @("libimagequant", "raqm")) {
    if (
        $inventoryText.IndexOf(
            $disallowedComponent,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    ) {
        throw "Frozen onefile inventory contains disallowed component: $disallowedComponent"
    }
}
$trayReportLines = @(
    Get-Content -LiteralPath $traySelfTestPath |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -ne "" }
)
if ($trayReportLines -cnotcontains "tray self-test ok") {
    throw "Frozen tray report does not contain the exact success marker"
}

$expectedWheels = [ordered]@{
    "altgraph-0.17.5-py2.py3-none-any.whl" = "f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597"
    "packaging-26.2-py3-none-any.whl" = "5fc45236b9446107ff2415ce77c807cee2862cb6fac22b8a73826d0693b0980e"
    "pefile-2024.8.26-py3-none-any.whl" = "76f8b485dcd3b1bb8166f1128d395fa3d87af26360c2358fb75b80019b957c6f"
    "pillow-12.3.0-cp311-cp311-win_amd64.whl" = "8e95e1385e4998ae9694eeaa4730ba5457ff61185b3a55e2e7bea0880aef452a"
    "pyinstaller-6.21.0-py3-none-win_amd64.whl" = "7fae06c494ce0ebfe6bd3055c0e409def884f63af2e3705d06bd431ad9237fc7"
    "pyinstaller_hooks_contrib-2026.6-py3-none-any.whl" = "fd13b8ac126b35361175edacd41a0d97080b75dd5f4b594ecefefff969509dd3"
    "pystray-0.19.5-py2.py3-none-any.whl" = "a0c2229d02cf87207297c22d86ffc57c86c227517b038c0d3c59df79295ac617"
    "pywin32_ctypes-0.2.3-py3-none-any.whl" = "8a1513379d709975552d202d942d9837758905c8d01eb82b8bcc30918929e7b8"
    "setuptools-83.0.0-py3-none-any.whl" = "29b23c360f22f414dc7336bb39178cc7bcbf6021ed2733cde173f09dba19abb3"
    "six-1.17.0-py2.py3-none-any.whl" = "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274"
}
$actualWheelNames = @(
    Get-ChildItem -LiteralPath $wheelhousePath -File |
        Sort-Object Name |
        ForEach-Object { $_.Name }
)
if ($actualWheelNames.Count -ne $expectedWheels.Count) {
    throw "Production wheelhouse must contain exactly $($expectedWheels.Count) files"
}
foreach ($wheelName in $expectedWheels.Keys) {
    $wheelPath = Join-Path $wheelhousePath $wheelName
    if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
        throw "Missing locked wheel: $wheelName"
    }
    $actualHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $expectedWheels[$wheelName]) {
        throw "Locked wheel hash mismatch: $wheelName"
    }
}

$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$stage = Join-Path $tempBase ("clipvault-lgpl-kit-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stage | Out-Null
foreach ($directory in @("build", "inventory", "licenses", "locks", "sources", "wheelhouse")) {
    New-Item -ItemType Directory -Path (Join-Path $stage $directory) | Out-Null
}

Copy-Item -LiteralPath (Join-Path $repoRoot "THIRD_PARTY_NOTICES.md") -Destination $stage
Copy-Item -LiteralPath (Join-Path $repoRoot "third_party\RELINKING_V1_6_0.md") -Destination (Join-Path $stage "README-RELINK.md")
Copy-Item -LiteralPath (Join-Path $repoRoot "docs\ADR\0012-windows-tray-dependencies-and-lgpl-delivery.md") -Destination (Join-Path $stage "build")
Copy-Item -LiteralPath (Join-Path $repoRoot "third_party\source-acquisition-v1.6.0.json") -Destination (Join-Path $stage "locks")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "windows-release-requirements.txt") -Destination (Join-Path $stage "locks")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "run_clipvault.py") -Destination (Join-Path $stage "build")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Export-WheelNotices.ps1") -Destination (Join-Path $stage "build")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Build-LgplRelinkKit.ps1") -Destination (Join-Path $stage "build")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "repack_pystray_wheel.py") -Destination (Join-Path $stage "build")
Copy-Item -LiteralPath (Join-Path $repoRoot "installer\clipvault.iss") -Destination (Join-Path $stage "build")
Copy-Item -LiteralPath (Join-Path $repoRoot ".github\workflows\release.yml") -Destination (Join-Path $stage "build")
Copy-Item -LiteralPath $runtimeNoticesPath -Destination (Join-Path $stage "licenses") -Recurse
Copy-Item `
    -LiteralPath (Join-Path $runtimeNoticesPath "pystray-0.19.5-py2.py3-none-any\pystray-0.19.5.dist-info\COPYING") `
    -Destination (Join-Path $stage "licenses\pystray-COPYING-GPL-3.0.txt")
Copy-Item `
    -LiteralPath (Join-Path $runtimeNoticesPath "pystray-0.19.5-py2.py3-none-any\pystray-0.19.5.dist-info\COPYING.LGPL") `
    -Destination (Join-Path $stage "licenses\pystray-COPYING-LGPL-3.0.txt")
Copy-Item -LiteralPath $inventoryPath -Destination (Join-Path $stage "inventory\clipvault-onefile-inventory.txt")
Copy-Item -LiteralPath $traySelfTestPath -Destination (Join-Path $stage "inventory\tray-self-test.txt")
Copy-Item -Path (Join-Path $wheelhousePath "*.whl") -Destination (Join-Path $stage "wheelhouse")

$wheelSums = foreach ($wheelName in ($expectedWheels.Keys | Sort-Object)) {
    "$($expectedWheels[$wheelName])  wheelhouse/$wheelName"
}
$wheelSums | Set-Content -LiteralPath (Join-Path $stage "locks\wheelhouse-SHA256SUMS.txt") -Encoding ASCII

$applicationSource = Join-Path $stage "sources\clipvault-personal-v$Version-$Commit.zip"
& git -C $repoRoot archive --format=zip --output=$applicationSource $Commit
if ($LASTEXITCODE -ne 0) {
    throw "git archive failed"
}

$sourceArchives = @(
    [ordered]@{
        name = "pystray-1907f8681d6d421517c63d94f425f9cdd74d0034.zip"
        url = "https://github.com/moses-palmer/pystray/archive/1907f8681d6d421517c63d94f425f9cdd74d0034.zip"
        sha256 = "4751562ba90301e054c87606079c1599301d84e7d1e4074b12af4f54a80a4768"
    },
    [ordered]@{
        name = "pyinstaller-6.21.0.tar.gz"
        url = "https://files.pythonhosted.org/packages/d5/4d/ec706c3fcf39e26888c35b39615ff4d5865d184069666c47492cff1fbe50/pyinstaller-6.21.0.tar.gz"
        sha256 = "bb9fab705983e393a2d1cac77d6972513057ad800215fd861dc15ff5272e98fd"
    },
    [ordered]@{
        name = "pyinstaller_hooks_contrib-2026.6.tar.gz"
        url = "https://files.pythonhosted.org/packages/94/5b/c9fe0db5e83ee1c39b2258fa21d23b15e1a60786b6c5990ee5074ead8bb6/pyinstaller_hooks_contrib-2026.6.tar.gz"
        sha256 = "bef5002c32f4f50bd55b005da12cff64eca8783e7eaf86a06a62410164bab725"
    }
)
$sourceSums = New-Object System.Collections.Generic.List[string]
$appHash = (Get-FileHash -LiteralPath $applicationSource -Algorithm SHA256).Hash.ToLowerInvariant()
$sourceSums.Add("$appHash  sources/$(Split-Path -Leaf $applicationSource)")
foreach ($source in $sourceArchives) {
    $destination = Join-Path $stage ("sources\" + $source.name)
    Invoke-WebRequest -Uri $source.url -OutFile $destination
    $actualHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $source.sha256) {
        throw "Source archive hash mismatch: $($source.name)"
    }
    $sourceSums.Add("$actualHash  sources/$($source.name)")
}
$sourceSums | Set-Content -LiteralPath (Join-Path $stage "locks\source-SHA256SUMS.txt") -Encoding ASCII

$pythonVersion = (& $pythonPath -c "import platform,sys; print(platform.python_version()); print(platform.machine()); print(sys.implementation.name)")
if ($LASTEXITCODE -ne 0 -or $pythonVersion.Count -ne 3) {
    throw "Unable to record Python build environment"
}
$pipVersion = @(
    & $pythonPath -c "import importlib.metadata; print(importlib.metadata.version('pip'))"
)
$pipVersionExit = $LASTEXITCODE
$pyInstallerVersion = @(
    & $pythonPath -c "import PyInstaller; print(PyInstaller.__version__)"
)
$pyInstallerVersionExit = $LASTEXITCODE
if (
    $pipVersionExit -ne 0 -or
    $pipVersion.Count -ne 1 -or
    $pyInstallerVersionExit -ne 0 -or
    $pyInstallerVersion.Count -ne 1
) {
    throw "Unable to record packaging versions"
}
[ordered]@{
    schema_version = 1
    release = "v$Version"
    commit = $Commit
    python_version = $pythonVersion[0]
    machine = $pythonVersion[1]
    implementation = $pythonVersion[2]
    pip_version = $pipVersion[0].Trim()
    pyinstaller = $pyInstallerVersion[0].Trim()
    production_wheel_count = $expectedWheels.Count
    pillow_feature_gate = [ordered]@{
        result = "passed"
        disallowed_features = @("libimagequant", "raqm")
        evidence = "inventory/tray-self-test.txt"
        evidence_sha256 = (Get-FileHash -LiteralPath $traySelfTestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $stage "locks\build-environment.json") -Encoding UTF8

$kitInventoryPath = Join-Path $stage "inventory\relink-kit-inventory.json"
$kitInventoryEntries = @(
    Get-ChildItem -LiteralPath $stage -Recurse -File |
        Sort-Object FullName |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($stage.Length)
            while ($relativePath.StartsWith("\") -or $relativePath.StartsWith("/")) {
                $relativePath = $relativePath.Substring(1)
            }
            [ordered]@{
                path = $relativePath.Replace("\", "/")
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                size = [int64] $_.Length
            }
        }
)
[ordered]@{
    schema_version = 1
    hash_scope = "all regular kit files except inventory/relink-kit-inventory.json"
    files = $kitInventoryEntries
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $kitInventoryPath -Encoding UTF8

Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $Output -CompressionLevel Optimal
if (-not (Test-Path -LiteralPath $Output -PathType Leaf) -or (Get-Item -LiteralPath $Output).Length -le 0) {
    throw "Relink kit was not created"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$outputPath = (Resolve-Path -LiteralPath $Output).Path
$expectedArchiveFiles = @(
    Get-ChildItem -LiteralPath $stage -Recurse -File |
        ForEach-Object {
            $relativePath = $_.FullName.Substring($stage.Length)
            while ($relativePath.StartsWith("\") -or $relativePath.StartsWith("/")) {
                $relativePath = $relativePath.Substring(1)
            }
            $relativePath.Replace("\", "/")
        } |
        Sort-Object
)
$archive = [System.IO.Compression.ZipFile]::OpenRead($outputPath)
try {
    $actualArchiveFiles = @(
        $archive.Entries |
            Where-Object { -not [string]::IsNullOrEmpty($_.Name) } |
            ForEach-Object {
                $member = $_.FullName.Replace("\", "/")
                if (
                    $member.StartsWith("/") -or
                    $member -match "(^|/)\.\.(/|$)"
                ) {
                    throw "Unsafe relink kit member: $member"
                }
                $member
            } |
            Sort-Object
    )
}
finally {
    $archive.Dispose()
}
if (
    $actualArchiveFiles.Count -ne $expectedArchiveFiles.Count -or
    (Compare-Object -CaseSensitive $expectedArchiveFiles $actualArchiveFiles)
) {
    throw "Relink kit ZIP inventory does not match the staged payload"
}

$verificationRoot = Join-Path $tempBase ("clipvault-lgpl-kit-verify-" + [guid]::NewGuid().ToString("N"))
try {
    Expand-Archive -LiteralPath $outputPath -DestinationPath $verificationRoot
    $extractedFiles = @(
        Get-ChildItem -LiteralPath $verificationRoot -Recurse -File |
            ForEach-Object {
                $relativePath = $_.FullName.Substring($verificationRoot.Length)
                while ($relativePath.StartsWith("\") -or $relativePath.StartsWith("/")) {
                    $relativePath = $relativePath.Substring(1)
                }
                $relativePath.Replace("\", "/")
            } |
            Sort-Object
    )
    if (
        $extractedFiles.Count -ne $expectedArchiveFiles.Count -or
        (Compare-Object -CaseSensitive $expectedArchiveFiles $extractedFiles)
    ) {
        throw "Extracted relink kit inventory does not match the staged payload"
    }
    foreach ($entry in $kitInventoryEntries) {
        $extractedPath = Join-Path $verificationRoot $entry.path.Replace("/", "\")
        $extractedHash = (Get-FileHash -LiteralPath $extractedPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($extractedHash -cne $entry.sha256) {
            throw "Extracted relink kit hash mismatch: $($entry.path)"
        }
    }
}
finally {
    Remove-Item -LiteralPath $verificationRoot -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Created $expectedOutputName"
