# v1.6.0 Windows LGPL relinking contract

The final asset name is:

```text
ClipVault-v1.6.0-LGPL-relink-kit.zip
```

It is the ninth and final member of the exact Release inventory.

## Required contents

1. A `git archive` of the exact ClipVault release target commit.
2. The complete pystray upstream commit archive pinned in
   `source-acquisition-v1.6.0.json`. The pystray wheel is not a substitute.
3. The exact CPython 3.11.9 Windows x64 (`cpython`, `AMD64`) interpreter
   identity and Windows wheelhouse used to build the executable.
4. SHA-256 locks for every archive and wheel.
5. Verbatim license, notice, and embedded SBOM files from those wheels.
6. A machine-readable build-environment record containing Python, pip,
   PyInstaller, target architecture, and the release commit.
7. Commands that create a clean environment from only the locked wheelhouse,
   replace the pystray wheel with an interface-compatible recipient build, run
   the tracked PyInstaller packaging command, and rebuild the installer.
8. A final module/binary inventory proving what entered the released onefile
   executable.
9. The exact Pillow wheel's verbatim comprehensive `LICENSE`, CycloneDX SBOM,
   and frozen feature report proving `libimagequant=False` and `raqm=False`.
10. `build/repack_pystray_wheel.py`, a standard-library-only helper that
    replaces the pure-Python `pystray` package in the locked wheel and
    regenerates its PEP 376 `RECORD` without invoking upstream setup hooks.
11. The verbatim CPython 3.11.9 official Windows binary distribution license
    bundle at
    `licenses/CPython-3.11.9-Windows-LICENSE.txt`, with SHA-256
    `e502c6b880ff58d614901495a9009c136539cd0b1e2a2abb8fc00b934c203419`.

## Executable Windows relink procedure

Run these commands from the extracted kit root in a visible PowerShell window.
Use a clean Windows x64 host with the exact CPython 3.11.9 x64 interpreter,
Git, and Inno Setup 6. Do not put credentials or private user data in this
directory.

The official `python.3.11.9.nupkg` is an acceptable isolated interpreter. It
may be downloaded from the exact `package_url` in
`source-acquisition-v1.6.0.json`, verified against the recorded package
SHA-256, and extracted into a disposable directory. Extraction does not
install Python and does not require changing `PATH`. Set
`CLIPVAULT_PYTHON_3119_X64` to the extracted `tools\python.exe` (or to an
equivalent exact CPython 3.11.9 x64 executable), then establish and verify the
explicit interpreter before any build command:

```powershell
$basePython = (Resolve-Path `
  -LiteralPath $env:CLIPVAULT_PYTHON_3119_X64).Path
$runtimeIdentity = @(
  & $basePython -c `
    "import platform,sys; print(f'{platform.python_version()}|{sys.implementation.name}|{platform.machine()}')"
)
if (
  $LASTEXITCODE -ne 0 -or
  $runtimeIdentity.Count -ne 1 -or
  $runtimeIdentity[0] -cne "3.11.9|cpython|AMD64"
) {
  throw "CPython 3.11.9 x64 runtime identity check failed"
}
```

First verify every supplied wheel and source archive:

```powershell
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$kit = (Get-Location).Path

$lockedPathsBySumFile = @{}
foreach ($sumFile in @(
  "locks\wheelhouse-SHA256SUMS.txt",
  "locks\source-SHA256SUMS.txt"
)) {
  $lockedPaths = @()
  foreach ($line in Get-Content -LiteralPath (Join-Path $kit $sumFile)) {
    if ($line -cnotmatch '^([0-9a-f]{64})  (.+)$') {
      throw "Invalid checksum line in $sumFile"
    }
    $expected = $Matches[1]
    $lockedRelative = $Matches[2]
    $relative = $lockedRelative.Replace(
      "/",
      [IO.Path]::DirectorySeparatorChar
    )
    $actual = (
      Get-FileHash -LiteralPath (Join-Path $kit $relative) -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($actual -cne $expected) {
      throw "Checksum mismatch: $relative"
    }
    $lockedPaths += $lockedRelative
  }
  $lockedPathsBySumFile[$sumFile] = @($lockedPaths | Sort-Object)
}

foreach ($inventory in @(
  @{
    directory = "wheelhouse"
    sum_file = "locks\wheelhouse-SHA256SUMS.txt"
  },
  @{
    directory = "sources"
    sum_file = "locks\source-SHA256SUMS.txt"
  }
)) {
  $actualPaths = @(
    Get-ChildItem -LiteralPath (Join-Path $kit $inventory.directory) -File |
      ForEach-Object { "$($inventory.directory)/$($_.Name)" } |
      Sort-Object
  )
  $inventoryDifference = @(
    Compare-Object -CaseSensitive `
      -ReferenceObject $lockedPathsBySumFile[$inventory.sum_file] `
      -DifferenceObject $actualPaths
  )
  if ($inventoryDifference.Count -ne 0) {
    throw "Unlocked or missing file in $($inventory.directory) inventory"
  }
}
```

Extract the exact application and pystray sources, then create a harmless
recipient modification. The marker changes only a module attribute and does
not change the public tray API:

```powershell
$work = Join-Path $kit "relink-work"
if (Test-Path -LiteralPath $work) {
  throw "relink-work already exists; use a fresh kit extraction or remove only that disposable directory after review"
}
New-Item -ItemType Directory -Path $work | Out-Null

$appArchives = @(Get-ChildItem "$kit\sources" -Filter `
  "clipvault-personal-v1.6.0-*.zip")
$pystrayArchives = @(Get-ChildItem "$kit\sources" -Filter `
  "pystray-1907f8681d6d421517c63d94f425f9cdd74d0034.zip")
if ($appArchives.Count -ne 1 -or $pystrayArchives.Count -ne 1) {
  throw "Expected exactly one application and one pystray source archive"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$applicationRoot = "$work\application"
$pystrayExtractRoot = "$work\pystray"
New-Item -ItemType Directory -Path $applicationRoot | Out-Null
New-Item -ItemType Directory -Path $pystrayExtractRoot | Out-Null
[IO.Compression.ZipFile]::ExtractToDirectory(
  $appArchives[0].FullName,
  $applicationRoot
)
[IO.Compression.ZipFile]::ExtractToDirectory(
  $pystrayArchives[0].FullName,
  $pystrayExtractRoot
)
$pystrayRoot = @(
  Get-ChildItem $pystrayExtractRoot -Directory
)
if ($pystrayRoot.Count -ne 1) {
  throw "Unexpected pystray source archive layout"
}
Add-Content -LiteralPath "$($pystrayRoot[0].FullName)\lib\pystray\__init__.py" `
  -Encoding UTF8 `
  -Value "`nCLIPVAULT_RELINK_EXERCISE_MARKER = 'recipient-modified-pystray'"

$modifiedWheelhouse = "$work\modified-wheelhouse"
New-Item -ItemType Directory -Path $modifiedWheelhouse | Out-Null
& $basePython "$kit\build\repack_pystray_wheel.py" `
  --base-wheel "$kit\wheelhouse\pystray-0.19.5-py2.py3-none-any.whl" `
  --source-dir "$($pystrayRoot[0].FullName)\lib\pystray" `
  --output-wheel "$modifiedWheelhouse\pystray-0.19.5-py2.py3-none-any.whl"
```

The following function builds ClipVault only from local wheel paths. It never
contacts a package index and deliberately excludes the original pystray wheel
when a recipient wheel is supplied:

```powershell
$appRoot = "$work\application"
Copy-Item -LiteralPath "$kit\licenses\runtime-notices" `
  -Destination "$appRoot\desktop\packaging" -Recurse

function Build-ClipVaultRecipient {
  param(
    [Parameter(Mandatory = $true)][string] $Name,
    [Parameter(Mandatory = $true)][string] $PystrayWheel
  )

  $output = "$work\$Name"
  & $basePython -m venv "$output\venv"
  if ($LASTEXITCODE -ne 0) {
    throw "CPython 3.11.9 virtual environment creation failed"
  }
  $python = "$output\venv\Scripts\python.exe"
  $otherWheels = @(
    Get-ChildItem "$kit\wheelhouse" -Filter "*.whl" |
      Where-Object Name -cne "pystray-0.19.5-py2.py3-none-any.whl" |
      Sort-Object Name |
      ForEach-Object FullName
  )
  & $python -m pip install --no-index --no-deps @otherWheels $PystrayWheel
  if ($LASTEXITCODE -ne 0) {
    throw "Offline wheel installation failed"
  }
  & $python -m pip check
  if ($LASTEXITCODE -ne 0) {
    throw "Offline wheel environment is inconsistent"
  }

  Push-Location "$appRoot\desktop"
  try {
    # Windows PowerShell 5.1 promotes native stderr to ErrorRecord objects when
    # ErrorActionPreference is Stop. PyInstaller emits ordinary progress there,
    # so capture the report under Continue and trust the native exit code.
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      $pyinstallerReport = @(
        & $python -m PyInstaller `
          --clean --noconfirm --onefile --name clipvault `
          --hide-console hide-early `
          --icon "$PWD/packaging/clipvault.ico" `
          --hidden-import pystray._win32 `
          --distpath "$output\dist" `
          --workpath "$output\build-pyi" `
          --specpath "$output\spec" `
          --add-data "$PWD\clipvault\store\migrations;clipvault/store/migrations" `
          --add-data "$PWD\clipvault\api\webui;clipvault/api/webui" `
          --add-data "$appRoot\THIRD_PARTY_NOTICES.md;." `
          --add-data "$appRoot\third_party;third_party" `
          --add-data "$PWD\packaging\runtime-notices;third_party/licenses" `
          packaging/run_clipvault.py 2>&1 |
          ForEach-Object { "$_" }
      )
      $pyinstallerExitCode = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $previousErrorActionPreference
    }
    $pyinstallerReport | ForEach-Object { Write-Host $_ }
    if ($pyinstallerExitCode -ne 0) {
      throw "PyInstaller build failed"
    }
  } finally {
    Pop-Location
  }

  $report = @(& "$output\dist\clipvault.exe" --self-test-tray 2>&1)
  if ($LASTEXITCODE -ne 0 -or $report -cnotcontains "tray self-test ok") {
    throw "Frozen tray self-test failed"
  }
  & "$output\venv\Scripts\pyi-archive_viewer.exe" -l -r `
    "$output\dist\clipvault.exe" |
    Set-Content -LiteralPath "$output\onefile-inventory.txt" -Encoding UTF8
  if ($LASTEXITCODE -ne 0) {
    throw "Recursive onefile inventory failed"
  }
}
```

Build the control artifact from the exact unmodified release wheel, then build
the modified Combined Work and verify the marker came from the recipient wheel:

```powershell
Build-ClipVaultRecipient `
  -Name "unmodified" `
  -PystrayWheel "$kit\wheelhouse\pystray-0.19.5-py2.py3-none-any.whl"

$modifiedWheel = (
  Resolve-Path "$modifiedWheelhouse\pystray-0.19.5-py2.py3-none-any.whl"
).Path
Build-ClipVaultRecipient -Name "modified" -PystrayWheel $modifiedWheel
$markerReport = @(
  & "$work\modified\dist\clipvault.exe" `
    --self-test-tray-relink-marker 2>&1
)
if (
  $LASTEXITCODE -ne 0 -or
  $markerReport -cnotcontains "tray relink self-test ok"
) {
  throw "Recipient pystray marker did not enter the frozen executable"
}
```

Finally rebuild the modified installer. The command uses only the disposable
application worktree and does not alter the extracted kit:

```powershell
New-Item -ItemType Directory -Force "$appRoot\desktop\dist" | Out-Null
Copy-Item "$work\modified\dist\clipvault.exe" `
  "$appRoot\desktop\dist\clipvault.exe" -Force
Push-Location "$appRoot\installer"
try {
  $isccCandidate = $env:CLIPVAULT_ISCC
  if ([string]::IsNullOrWhiteSpace($isccCandidate)) {
    $isccCandidate = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
  }
  $iscc = (Resolve-Path -LiteralPath $isccCandidate).Path

  # The official Inno Setup 6.7.3 ISCC.exe has PE ProductVersion 0.0.0.0.
  # Verify the compiler engine banner emitted by the executable instead.
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $isccReport = @(
      & $iscc clipvault.iss 2>&1 | ForEach-Object { "$_" }
    )
    $isccExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }

  $isccReport | ForEach-Object { Write-Host $_ }
  if ($isccExitCode -ne 0) {
    throw "Modified installer build failed"
  }
  $engineEvidence = @(
    $isccReport |
      Where-Object {
        $_ -match '^Compiler engine version: Inno Setup 6(?:\.|$)'
      }
  )
  if ($engineEvidence.Count -ne 1) {
    throw "Inno Setup 6 compiler engine evidence is missing or ambiguous"
  }
} finally {
  Pop-Location
}
```

## Required relink exercise

Before publication, a clean Windows runner must:

1. extract the kit;
2. verify all source and wheel hashes;
3. install with network access disabled and `--no-index --find-links`;
4. rebuild once with the supplied unmodified pystray wheel;
5. rebuild once with an interface-compatible pystray wheel carrying a harmless
   source-level identification change;
6. launch the rebuilt portable executable and prove that the tray launcher is
   functional;
7. record commands and results without signing keys, passwords, local private
   paths, or clipboard contents.

Bit-for-bit reproducibility is desirable but is not the relink acceptance
criterion. The required result is a practical, installable modified Combined
Work built from supplied corresponding/application source and locked inputs.

## Limited permission

To the extent another ClipVault term conflicts, recipients may reverse engineer
the v1.6.0 Windows Combined Work only as needed to debug their modifications to
an LGPL-covered library included in that work. This does not grant a general
right to reverse engineer ClipVault and does not expose or grant rights to any
signing key, secret, service credential, trademark, or private user data.

## Fail-closed conditions

Do not publish when:

- the kit is absent, empty, unattested, or differs between Actions and draft
  Release bytes;
- the application archive does not match the final target commit;
- the pystray archive is not the pinned upstream commit/hash;
- the build interpreter is not exactly CPython 3.11.9 x64 (`AMD64`);
- the wheelhouse can contact an index or contains an unlocked wheel;
- notices, GPL/LGPL license texts, or the exact CPython 3.11.9 Windows license
  bundle are missing or do not match their recorded hashes;
- the relink exercise cannot produce a functioning tray build;
- the exact Pillow wheel feature report does not prove `libimagequant=False`
  and `raqm=False`, or the collected binary inventory contradicts that report.
