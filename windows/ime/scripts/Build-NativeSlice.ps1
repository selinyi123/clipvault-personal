[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Debug',
    [ValidateSet('x64', 'x86', 'ARM64')]
    [string]$Architecture = 'x64',
    [string]$BuildDirectory = '',
    [string]$RimeSdkDirectory = '',
    [string]$RimeDataDirectory = '',
    [switch]$SkipTests,
    [switch]$RequireRime,
    [switch]$EnableArm64Experimental
)

$ErrorActionPreference = 'Stop'
$nativeRoot = Split-Path -Parent $PSScriptRoot
if (-not $BuildDirectory) {
    $BuildDirectory = Join-Path $nativeRoot "out\$($Architecture.ToLowerInvariant())-$($Configuration.ToLowerInvariant())"
}
$BuildDirectory = [System.IO.Path]::GetFullPath($BuildDirectory)

$cmakeCommand = Get-Command cmake.exe -ErrorAction SilentlyContinue
$cmakePath = if ($cmakeCommand) { $cmakeCommand.Source } else { '' }
if (-not $cmakePath) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $installation = & $vswhere -latest -products * `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -property installationPath
        if ($installation) {
            $candidate = Join-Path $installation 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
            if (Test-Path -LiteralPath $candidate) {
                $cmakePath = $candidate
            }
        }
    }
}
if (-not $cmakePath) {
    throw 'CMake with the Visual Studio C++ x86/x64 workload is required.'
}

if ($Architecture -eq 'ARM64' -and -not $EnableArm64Experimental) {
    throw 'ARM64 remains a release gate. Pass -EnableArm64Experimental for development only.'
}
if ($Architecture -ne 'x64' -and ($RimeSdkDirectory -or $RimeDataDirectory)) {
    throw 'librime and ClipVaultImeHost are built only in the x64 configuration.'
}
if ($RequireRime -and $Architecture -ne 'x64') {
    throw '-RequireRime is valid only for the x64 Host build.'
}
if ($RequireRime -and (-not $RimeSdkDirectory -or -not $RimeDataDirectory)) {
    throw '-RequireRime needs both -RimeSdkDirectory and -RimeDataDirectory.'
}

$buildTesting = if ($SkipTests) { 'OFF' } else { 'ON' }
$configureArguments = @(
    '-S', $nativeRoot,
    '-B', $BuildDirectory,
    '-A', $(if ($Architecture -eq 'x86') { 'Win32' } else { $Architecture }),
    "-DBUILD_TESTING=$buildTesting",
    "-DCLIPVAULT_BUILD_HOST=$(if ($Architecture -eq 'x64') { 'ON' } else { 'OFF' })",
    "-DCLIPVAULT_REQUIRE_RIME_RUNTIME=$(if ($RequireRime) { 'ON' } else { 'OFF' })",
    "-DCLIPVAULT_ENABLE_ARM64_EXPERIMENTAL=$(if ($EnableArm64Experimental) { 'ON' } else { 'OFF' })"
)
if ($RimeSdkDirectory) {
    $RimeSdkDirectory = [System.IO.Path]::GetFullPath($RimeSdkDirectory)
    $configureArguments += "-DCLIPVAULT_RIME_SDK_DIR=$RimeSdkDirectory"
}
if ($RimeDataDirectory) {
    $RimeDataDirectory = [System.IO.Path]::GetFullPath($RimeDataDirectory)
    $configureArguments += "-DCLIPVAULT_RIME_DATA_DIR=$RimeDataDirectory"
}
& $cmakePath @configureArguments
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed with exit code $LASTEXITCODE" }
& $cmakePath --build $BuildDirectory --config $Configuration --parallel
if ($LASTEXITCODE -ne 0) { throw "Native build failed with exit code $LASTEXITCODE" }

if (-not $SkipTests) {
    $ctestPath = Join-Path (Split-Path -Parent $cmakePath) 'ctest.exe'
    if (-not (Test-Path -LiteralPath $ctestPath -PathType Leaf)) {
        throw "CTest was not found beside CMake: $ctestPath"
    }
    & $ctestPath --test-dir $BuildDirectory -C $Configuration --output-on-failure
    if ($LASTEXITCODE -ne 0) { throw "Native tests failed with exit code $LASTEXITCODE" }
}

$output = Join-Path $BuildDirectory 'bin'
if ($RequireRime) {
    $hostExe = Join-Path $output 'ClipVaultImeHost.exe'
    $rimeDll = Join-Path $output 'rime.dll'
    $rimeData = Join-Path $output 'rime-data'
    if (-not (Test-Path -LiteralPath $hostExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $rimeDll -PathType Leaf) -or
        -not (Test-Path -LiteralPath $rimeData -PathType Container) -or
        -not (Get-ChildItem -LiteralPath $rimeData -Filter '*.schema.yaml' -File)) {
        throw 'Production Rime artifact validation failed: Host, rime.dll, or schema data is missing.'
    }

    $deployUser = Join-Path ([System.IO.Path]::GetTempPath()) `
        "ClipVaultImeDeploy-$PID-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $deployUser | Out-Null
    $priorData = $env:CLIPVAULT_RIME_DATA_DIR
    $priorUser = $env:CLIPVAULT_RIME_USER_DIR
    try {
        $env:CLIPVAULT_RIME_DATA_DIR = $rimeData
        $env:CLIPVAULT_RIME_USER_DIR = $deployUser
        $process = Start-Process -FilePath $hostExe -ArgumentList '--deploy-rime' `
            -WorkingDirectory $output -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw "Rime deployment validation failed with exit code $($process.ExitCode)"
        }
    } finally {
        $env:CLIPVAULT_RIME_DATA_DIR = $priorData
        $env:CLIPVAULT_RIME_USER_DIR = $priorUser
        if (Test-Path -LiteralPath $deployUser) {
            Remove-Item -LiteralPath $deployUser -Recurse -Force
        }
    }
}
Write-Host "Native slice output: $output"
