[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory,
    [string]$DumpbinPath = ''
)

$ErrorActionPreference = 'Stop'
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)

if (-not $DumpbinPath) {
    $command = Get-Command dumpbin.exe -ErrorAction SilentlyContinue
    if ($command) { $DumpbinPath = $command.Source }
}
if (-not $DumpbinPath) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} `
        'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
        $installation = & $vswhere -latest -products * `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -property installationPath
        if ($installation) {
            $tools = Get-ChildItem -LiteralPath `
                (Join-Path $installation 'VC\Tools\MSVC') -Directory |
                Sort-Object Name -Descending | Select-Object -First 1
            if ($tools) {
                $candidate = Join-Path $tools.FullName `
                    'bin\Hostx64\x64\dumpbin.exe'
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    $DumpbinPath = $candidate
                }
            }
        }
    }
}
if (-not $DumpbinPath -or
    -not (Test-Path -LiteralPath $DumpbinPath -PathType Leaf)) {
    throw 'dumpbin.exe from the Visual Studio C++ workload is required.'
}
$DumpbinPath = [System.IO.Path]::GetFullPath($DumpbinPath)

$artifacts = @(
    @{
        Name = 'TSF x64'
        Relative = 'x64\ClipVaultTextService.dll'
        Machine = 'x64'
        Required = @('ADVAPI32.dll', 'ole32.dll', 'USER32.dll')
        Forbidden = @('bcrypt.dll', 'WINTRUST.dll', 'rime.dll', 'winhttp.dll',
                      'wininet.dll', 'ws2_32.dll')
    },
    @{
        Name = 'TSF x86'
        Relative = 'x86\ClipVaultTextService.dll'
        Machine = 'x86'
        Required = @('ADVAPI32.dll', 'ole32.dll', 'USER32.dll')
        Forbidden = @('bcrypt.dll', 'WINTRUST.dll', 'rime.dll', 'winhttp.dll',
                      'wininet.dll', 'ws2_32.dll')
    },
    @{
        Name = 'IME Host x64'
        Relative = 'host-x64\ClipVaultImeHost.exe'
        Machine = 'x64'
        Required = @('ADVAPI32.dll', 'ole32.dll', 'WINTRUST.dll')
        Forbidden = @('bcrypt.dll', 'rime.dll', 'winhttp.dll', 'wininet.dll',
                      'ws2_32.dll')
    },
    @{
        Name = 'OTP Broker x64'
        Relative = 'otp-broker\ClipVaultOtpBroker.exe'
        Machine = 'x64'
        Required = @('ADVAPI32.dll', 'bcrypt.dll', 'USER32.dll', 'WINTRUST.dll')
        Forbidden = @('rime.dll', 'winhttp.dll', 'wininet.dll', 'ws2_32.dll')
    },
    @{
        Name = 'pinned rime.dll x64'
        Relative = 'host-x64\rime.dll'
        Machine = 'x64'
        Required = @('dbghelp.dll', 'KERNEL32.dll', 'USER32.dll')
        Forbidden = @('bcrypt.dll', 'winhttp.dll', 'wininet.dll', 'ws2_32.dll')
    }
)

$crtPattern = '^(?:msvcp.*|vcruntime.*|concrt.*|msvcr.*|ucrtbased|api-ms-win-crt-.*)\.dll$'
$audits = @{}
foreach ($artifact in $artifacts) {
    $path = Join-Path $packageDirectory $artifact.Relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Production package is missing $($artifact.Name): $path"
    }
    $importsOutput = @(& $DumpbinPath /nologo /imports $path 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "dumpbin /imports failed for $($artifact.Name)."
    }
    $importsText = $importsOutput -join "`n"
    $imports = @([regex]::Matches(
            $importsText, '(?im)^\s{4}([A-Z0-9._-]+\.dll)\s*$') |
        ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
    if (-not $imports) {
        throw "No import table was parsed for $($artifact.Name)."
    }
    $crt = @($imports | Where-Object { $_ -match $crtPattern })
    if ($crt) {
        throw "$($artifact.Name) still needs the VC Runtime: $($crt -join ', ')."
    }
    foreach ($required in $artifact.Required) {
        if ($imports -notcontains $required) {
            throw "$($artifact.Name) is missing required import $required."
        }
    }
    foreach ($forbidden in $artifact.Forbidden) {
        if ($imports -contains $forbidden) {
            throw "$($artifact.Name) crossed a forbidden dependency boundary: $forbidden."
        }
    }

    $headersOutput = @(& $DumpbinPath /nologo /headers $path 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "dumpbin /headers failed for $($artifact.Name)."
    }
    $headersText = $headersOutput -join "`n"
    $machinePattern = if ($artifact.Machine -eq 'x86') {
        '(?im)machine \(x86\)'
    } else {
        '(?im)machine \(x64\)'
    }
    if ($headersText -notmatch $machinePattern) {
        throw "$($artifact.Name) machine type is not $($artifact.Machine)."
    }
    $audits[$artifact.Name] = $importsText
}

foreach ($name in @('TSF x64', 'TSF x86', 'IME Host x64')) {
    if ($audits[$name] -match '(?i)CredReadW|CredWriteW|BCryptDecrypt|BCryptOpenAlgorithmProvider') {
        throw "$name imports OTP authority or decrypt functions."
    }
}
$brokerImports = $audits['OTP Broker x64']
foreach ($function in @('CredReadW', 'CredWriteW', 'BCryptDecrypt',
                         'WinVerifyTrust')) {
    if ($brokerImports -notmatch [regex]::Escape($function)) {
        throw "OTP Broker is missing required production function $function."
    }
}

Write-Host 'WINDOWS IME CLEAN-MACHINE DEPENDENCY AUDIT PASSED'
