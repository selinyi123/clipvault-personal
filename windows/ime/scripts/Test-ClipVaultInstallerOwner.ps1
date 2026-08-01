[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^S-1-5-')]
    [string]$ExpectedOwnerSid
)

$ErrorActionPreference = 'Stop'
$sessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
$ownerSids = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase)
Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" |
    Where-Object { $_.SessionId -eq $sessionId } |
    ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid
        if ($owner.ReturnValue -eq 0 -and $owner.Sid) {
            [void]$ownerSids.Add([string]$owner.Sid)
        }
    }
if ($ownerSids.Count -ne 1 -or -not $ownerSids.Contains($ExpectedOwnerSid)) {
    throw 'The elevated installer is not bound to the interactive owner session.'
}
