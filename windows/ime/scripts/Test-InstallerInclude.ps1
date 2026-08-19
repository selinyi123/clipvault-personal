[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory,
    [string]$IsccPath = ''
)

$ErrorActionPreference = 'Stop'
$imeRoot = Split-Path -Parent $PSScriptRoot
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
if (-not (Test-Path -LiteralPath $packageDirectory -PathType Container)) {
    throw "IME package does not exist: $packageDirectory"
}
if (-not $IsccPath) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $IsccPath = $command.Source }
}
if (-not $IsccPath) {
    $candidate = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $IsccPath = $candidate
    }
}
if (-not $IsccPath -or
    -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'Inno Setup 6 ISCC.exe is required for the v2 installer gate.'
}

$output = Join-Path ([System.IO.Path]::GetTempPath()) `
    "ClipVaultImeInstaller-$PID-$([Guid]::NewGuid().ToString('N'))"
$priorPackage = $env:CLIPVAULT_IME_TEST_PACKAGE
$priorOutput = $env:CLIPVAULT_IME_TEST_OUTPUT
try {
    New-Item -ItemType Directory -Path $output | Out-Null
    $env:CLIPVAULT_IME_TEST_PACKAGE = $packageDirectory
    $env:CLIPVAULT_IME_TEST_OUTPUT = $output
    & $IsccPath /Qp (Join-Path $imeRoot 'tests\ClipVaultImeV2Syntax.iss')
    if ($LASTEXITCODE -ne 0 -or
        -not (Test-Path -LiteralPath `
            (Join-Path $output 'ClipVaultImeV2Syntax.exe') -PathType Leaf)) {
        throw 'ClipVault v2 installer include did not compile.'
    }
    Write-Host 'CLIPVAULT V2 INSTALLER INCLUDE COMPILE PASSED'
} finally {
    $env:CLIPVAULT_IME_TEST_PACKAGE = $priorPackage
    $env:CLIPVAULT_IME_TEST_OUTPUT = $priorOutput
    $resolvedOutput = [System.IO.Path]::GetFullPath($output)
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if ($resolvedOutput.StartsWith(
            $tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $resolvedOutput)) {
        Remove-Item -LiteralPath $resolvedOutput -Recurse -Force
    }
}
