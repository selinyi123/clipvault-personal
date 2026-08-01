[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory
)

$ErrorActionPreference = 'Stop'
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$broker = [System.IO.Path]::GetFullPath(
    (Join-Path $packageDirectory 'otp-broker\ClipVaultOtpBroker.exe'))
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runValue = 'ClipVaultOtpBrokerV1'
if ($PSCmdlet.ShouldProcess($broker,
        'Disable the current-user OTP Broker and stop its exact packaged process')) {
    Remove-ItemProperty -Path $runKey -Name $runValue -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='ClipVaultOtpBroker.exe'" |
        Where-Object {
            $_.ExecutablePath -and
            [System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $broker
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}
