[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory
)

$ErrorActionPreference = 'Stop'
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$x64Dll = Join-Path $packageDirectory 'x64\ClipVaultTextService.dll'
$x86Dll = Join-Path $packageDirectory 'x86\ClipVaultTextService.dll'
foreach ($required in @($x64Dll, $x86Dll)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing v2 IME package file: $required"
    }
}
$regsvr64 = Join-Path $env:WINDIR 'System32\regsvr32.exe'
$regsvr32 = Join-Path $env:WINDIR 'SysWOW64\regsvr32.exe'

function Invoke-Unregister {
    param([string]$Executable, [string]$Dll)
    $process = Start-Process -FilePath $Executable `
        -ArgumentList @('/s', '/u', ('"' + $Dll + '"')) `
        -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "regsvr32 /u failed for $Dll with exit code $($process.ExitCode)"
    }
}

if ($PSCmdlet.ShouldProcess($packageDirectory,
        'Unregister x86/x64 HKCU COM servers and the system-wide Windows TSF profile')) {
    # Remove the 32-bit registry view before the 64-bit profile owner. Repeating
    # TSF profile removal is idempotent; each DLL removes its own COM view.
    Invoke-Unregister -Executable $regsvr32 -Dll $x86Dll
    Invoke-Unregister -Executable $regsvr64 -Dll $x64Dll
    Write-Host 'ClipVault Input v2 x86/x64 COM servers and TSF profile were unregistered.'
}
