[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)]
    [string]$PackageDirectory,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$packageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
$broker = Join-Path $packageDirectory 'otp-broker\ClipVaultOtpBroker.exe'
if (-not (Test-Path -LiteralPath $broker -PathType Leaf)) {
    throw "Missing OTP Broker: $broker"
}
$signature = Get-AuthenticodeSignature -LiteralPath $broker
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw 'Refusing to enable an unsigned or untrusted OTP Broker.'
}
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runValue = 'ClipVaultOtpBrokerV1'
$quotedBroker = '"' + $broker + '"'
if ($PSCmdlet.ShouldProcess($broker,
        'Enable the current-user OTP Broker at logon and start it now')) {
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -Path $runKey -Name $runValue -Value $quotedBroker `
        -PropertyType String -Force | Out-Null
    if (-not $NoStart) {
        Start-Process -FilePath $broker -WindowStyle Hidden
    }
}
