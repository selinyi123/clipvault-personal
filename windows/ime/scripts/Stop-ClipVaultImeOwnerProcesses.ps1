[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory,
    [Parameter(Mandatory)]
    [string]$RuntimeExecutable,
    [Parameter(Mandatory)]
    [ValidatePattern('^S-1-5-')]
    [string]$OwnerSid,
    [switch]$NoConfirm
)

$ErrorActionPreference = 'Stop'
if ($NoConfirm) { $ConfirmPreference = 'None' }
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$targets = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase)
foreach ($candidate in @(
        [System.IO.Path]::GetFullPath($RuntimeExecutable),
        (Join-Path $packageDirectory 'host-x64\ClipVaultImeHost.exe'),
        (Join-Path $packageDirectory 'otp-broker\ClipVaultOtpBroker.exe'))) {
    [void]$targets.Add([System.IO.Path]::GetFullPath($candidate))
}

Get-CimInstance Win32_Process |
    Where-Object {
        $_.ExecutablePath -and $targets.Contains(
            [System.IO.Path]::GetFullPath($_.ExecutablePath))
    } |
    ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid
        if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne $OwnerSid) {
            return
        }
        if ($PSCmdlet.ShouldProcess($_.ProcessId,
                'Stop the exact packaged ClipVault owner process')) {
            Stop-Process -Id $_.ProcessId -Force
        }
    }
