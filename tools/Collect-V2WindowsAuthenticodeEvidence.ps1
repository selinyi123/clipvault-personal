[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$DesktopExecutable,
    [Parameter(Mandatory)]
    [string]$WindowsInstaller,
    [Parameter(Mandatory)]
    [string]$WindowsImePackage,
    [Parameter(Mandatory)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Resolve-InputFile([string]$Path, [string]$Label) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Label must be one regular file: $Path"
    }
    return $item.FullName
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ValidSignature([string]$Path, [string]$Label) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        $null -eq $signature.SignerCertificate -or
        [string]::IsNullOrWhiteSpace($signature.SignerCertificate.Thumbprint)) {
        throw "$Label Authenticode verification is not Valid: $($signature.Status)"
    }
    return $signature.SignerCertificate.Thumbprint.ToLowerInvariant()
}

function New-TopLevelEvidence(
    [string]$Role,
    [string]$Path,
    [string]$ExpectedThumbprint
) {
    $thumbprint = Get-ValidSignature $Path $Role
    if ($thumbprint -ne $ExpectedThumbprint) {
        throw "$Role signer does not match the first trusted artifact"
    }
    return [ordered]@{
        role = $Role
        path = $Path
        sha256 = Get-Sha256 $Path
        status = 'Valid'
        signing_thumbprint = $thumbprint
    }
}

$desktop = Resolve-InputFile $DesktopExecutable 'Desktop executable'
$installer = Resolve-InputFile $WindowsInstaller 'Windows installer'
$package = Resolve-InputFile $WindowsImePackage 'Windows IME package'
$trustedThumbprint = Get-ValidSignature $desktop 'Desktop executable'

$requiredMembers = @(
    'host-x64/ClipVaultImeHost.exe',
    'otp-broker/ClipVaultOtpBroker.exe',
    'x64/ClipVaultTextService.dll',
    'x86/ClipVaultTextService.dll'
)
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempDirectory = Join-Path $tempRoot ("clipvault-v2-auth-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempDirectory -ErrorAction Stop | Out-Null

try {
    $archive = [IO.Compression.ZipFile]::OpenRead($package)
    try {
        $memberEvidence = @()
        foreach ($required in $requiredMembers) {
            $matches = @($archive.Entries | Where-Object {
                $_.FullName.Replace('\', '/') -ceq $required
            })
            if ($matches.Count -ne 1) {
                throw "Expected exactly one signed package member '$required', found $($matches.Count)"
            }
            $entry = $matches[0]
            if ($entry.Length -gt 67108864) {
                throw "Signed package member is unexpectedly large: $required"
            }
            $extension = [IO.Path]::GetExtension($required)
            $temporary = Join-Path $tempDirectory ([guid]::NewGuid().ToString('N') + $extension)
            $inputStream = $entry.Open()
            $outputStream = [IO.File]::Open(
                $temporary,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            try {
                $inputStream.CopyTo($outputStream)
            }
            finally {
                $outputStream.Dispose()
                $inputStream.Dispose()
            }
            $thumbprint = Get-ValidSignature $temporary $required
            if ($thumbprint -ne $trustedThumbprint) {
                throw "Package member signer does not match the Desktop signer: $required"
            }
            $memberEvidence += [ordered]@{
                archive_path = $required
                sha256 = Get-Sha256 $temporary
                status = 'Valid'
                signing_thumbprint = $thumbprint
            }
        }
    }
    finally {
        $archive.Dispose()
    }

    $report = [ordered]@{
        schema_version = 1
        signing_thumbprint = $trustedThumbprint
        top_level = @(
            (New-TopLevelEvidence 'desktop_executable' $desktop $trustedThumbprint),
            (New-TopLevelEvidence 'windows_installer' $installer $trustedThumbprint)
        )
        package = [ordered]@{
            path = $package
            sha256 = Get-Sha256 $package
            members = $memberEvidence
        }
    }

    $output = [IO.Path]::GetFullPath($OutputPath)
    $outputParent = Split-Path -Parent $output
    if ([string]::IsNullOrWhiteSpace($outputParent)) {
        throw 'OutputPath must have a parent directory'
    }
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    $json = $report | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText(
        $output,
        $json + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    Write-Output "wrote redacted Authenticode evidence: $output"
}
finally {
    $resolvedTemp = [IO.Path]::GetFullPath($tempDirectory)
    if (-not $resolvedTemp.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a temporary path outside the system temp directory: $resolvedTemp"
    }
    if (Test-Path -LiteralPath $resolvedTemp) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
