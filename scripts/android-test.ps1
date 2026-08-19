[CmdletBinding()]
param(
    [string]$AvdName,

    [ValidatePattern('^emulator-\d+$')]
    [string]$EmulatorSerial,

    [ValidateRange(30, 900)]
    [int]$BootTimeoutSeconds = 300,

    [switch]$ShowEmulator,
    [switch]$SkipUnitTests,
    [switch]$SkipInstall,
    [switch]$SkipLaunch,
    [switch]$RunInstrumentationTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$androidRoot = Join-Path $repoRoot 'android'

function Get-AndroidSdkRoots {
    $roots = @()

    foreach ($variableName in @('ANDROID_SDK_ROOT', 'ANDROID_HOME')) {
        $value = [Environment]::GetEnvironmentVariable($variableName, 'Process')
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $roots += $value
        }
    }

    $localProperties = Join-Path $androidRoot 'local.properties'
    if (Test-Path -LiteralPath $localProperties -PathType Leaf) {
        $sdkLine = Get-Content -LiteralPath $localProperties |
            Where-Object { $_ -match '^sdk\.dir=' } |
            Select-Object -First 1
        if ($sdkLine) {
            $sdkDir = $sdkLine.Substring('sdk.dir='.Length)
            $sdkDir = $sdkDir.Replace('\:', ':').Replace('\\', '\')
            if (-not [string]::IsNullOrWhiteSpace($sdkDir)) {
                $roots += $sdkDir
            }
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $roots += (Join-Path $env:LOCALAPPDATA 'Android\Sdk')
    }

    return @($roots | Select-Object -Unique)
}

function Resolve-AndroidTool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CommandName,

        [Parameter(Mandatory = $true)]
        [string]$SdkRelativePath
    )

    $command = Get-Command $CommandName -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Source
    }

    foreach ($sdkRoot in Get-AndroidSdkRoots) {
        $candidate = Join-Path $sdkRoot $SdkRelativePath
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Cannot find $CommandName. Configure ANDROID_SDK_ROOT/ANDROID_HOME or android/local.properties."
}

$script:AdbPath = Resolve-AndroidTool -CommandName 'adb.exe' -SdkRelativePath 'platform-tools\adb.exe'

function Get-AdbDevices {
    $output = & $script:AdbPath devices
    if ($LASTEXITCODE -ne 0) {
        throw "adb devices failed with exit code $LASTEXITCODE."
    }

    $devices = @()
    foreach ($line in $output) {
        $deviceLine = "$line".Trim()
        if (
            [string]::IsNullOrWhiteSpace($deviceLine) -or
            $deviceLine -eq 'List of devices attached'
        ) {
            continue
        }

        if ($deviceLine -match '^(?<serial>\S+)\s+(?<state>\S+)(?:\s|$)') {
            $devices += [pscustomobject]@{
                Serial = $Matches.serial
                State  = $Matches.state
            }
        }
    }
    return $devices
}

function Get-RunningAvdName {
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^emulator-\d+$')]
        [string]$Serial
    )

    $output = & $script:AdbPath -s $Serial emu avd name 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }

    return @($output | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_) -and $_.Trim() -ne 'OK'
    } | Select-Object -First 1)
}

function Find-RunningEmulator {
    $devices = @(Get-AdbDevices)
    $physicalCount = @($devices | Where-Object {
        $_.Serial -notmatch '^emulator-\d+$'
    }).Count
    if ($physicalCount -gt 0) {
        Write-Warning "$physicalCount physical Android device(s) detected and ignored. Physical-device fallback is prohibited."
    }

    $emulators = @($devices | Where-Object {
        $_.Serial -match '^emulator-\d+$'
    })

    if ($EmulatorSerial) {
        $selected = @($emulators | Where-Object {
            $_.Serial -eq $EmulatorSerial
        })
        if ($selected.Count -ne 1) {
            throw "Requested emulator $EmulatorSerial is not visible to adb."
        }
        return $selected[0]
    }

    if ($AvdName) {
        foreach ($emulator in $emulators) {
            if ((Get-RunningAvdName -Serial $emulator.Serial) -eq $AvdName) {
                return $emulator
            }
        }
        return $null
    }

    if ($emulators.Count -gt 1) {
        throw 'Multiple emulators are running. Pass -EmulatorSerial or -AvdName explicitly.'
    }
    if ($emulators.Count -eq 1) {
        return $emulators[0]
    }
    return $null
}

function Start-SafeEmulator {
    $emulatorPath = Resolve-AndroidTool -CommandName 'emulator.exe' -SdkRelativePath 'emulator\emulator.exe'
    $availableAvds = @(& $emulatorPath -list-avds | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    })
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list Android Virtual Devices (exit code $LASTEXITCODE)."
    }

    $selectedAvd = $AvdName
    if ($selectedAvd) {
        if ($availableAvds -notcontains $selectedAvd) {
            throw "AVD '$selectedAvd' was not found. Available AVDs: $($availableAvds -join ', ')"
        }
    }
    elseif ($availableAvds.Count -eq 1) {
        $selectedAvd = $availableAvds[0]
    }
    elseif ($availableAvds.Count -eq 0) {
        throw 'No Android emulator is running and no AVD is installed. Physical-device fallback is prohibited.'
    }
    else {
        throw "No emulator is running and multiple AVDs are installed. Pass -AvdName. Available AVDs: $($availableAvds -join ', ')"
    }

    $arguments = @('-avd', $selectedAvd, '-no-snapshot-save', '-no-boot-anim')
    if (-not $ShowEmulator) {
        $arguments += @('-no-window', '-no-audio')
        $process = Start-Process -FilePath $emulatorPath -ArgumentList $arguments -WindowStyle Hidden -PassThru
    }
    else {
        $process = Start-Process -FilePath $emulatorPath -ArgumentList $arguments -PassThru
    }
    Write-Host "Started AVD '$selectedAvd'. Waiting for an emulator target..."

    $deadline = [DateTime]::UtcNow.AddSeconds($BootTimeoutSeconds)
    do {
        if ($process.HasExited) {
            throw "AVD '$selectedAvd' exited before becoming available (exit code $($process.ExitCode))."
        }

        Start-Sleep -Seconds 2
        $candidate = Find-RunningEmulator
        if ($candidate) {
            return $candidate
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "Timed out after $BootTimeoutSeconds seconds waiting for AVD '$selectedAvd'."
}

function Wait-ForAndroidBoot {
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^emulator-\d+$')]
        [string]$Serial
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($BootTimeoutSeconds)
    do {
        $device = @(Get-AdbDevices | Where-Object {
            $_.Serial -eq $Serial -and $_.State -eq 'device'
        })
        if ($device.Count -eq 1) {
            $bootCompleted = & $script:AdbPath -s $Serial shell getprop sys.boot_completed 2>$null
            if ($LASTEXITCODE -eq 0 -and "$bootCompleted".Trim() -eq '1') {
                return
            }
        }

        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "Timed out after $BootTimeoutSeconds seconds waiting for $Serial to finish booting."
}

function Invoke-Adb {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $script:AdbPath -s $script:TargetSerial @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "adb -s $script:TargetSerial $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Invoke-Gradle {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Tasks
    )

    $gradleWrapper = Join-Path $androidRoot 'gradlew.bat'
    if (-not (Test-Path -LiteralPath $gradleWrapper -PathType Leaf)) {
        throw "Gradle wrapper not found: $gradleWrapper"
    }

    Push-Location -LiteralPath $androidRoot
    try {
        & $gradleWrapper @Tasks --no-daemon
        $gradleExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($gradleExitCode -ne 0) {
        throw "Gradle task(s) '$($Tasks -join ' ')' failed with exit code $gradleExitCode."
    }
}

if ($EmulatorSerial -and $AvdName) {
    throw 'Pass either -EmulatorSerial or -AvdName, not both.'
}
if ($RunInstrumentationTests -and $SkipInstall) {
    throw '-RunInstrumentationTests cannot be combined with -SkipInstall.'
}

$selectedEmulator = Find-RunningEmulator
if (-not $selectedEmulator) {
    if ($EmulatorSerial) {
        throw "Requested emulator $EmulatorSerial is not running. Physical-device fallback is prohibited."
    }
    $selectedEmulator = Start-SafeEmulator
}

$script:TargetSerial = $selectedEmulator.Serial
if ($script:TargetSerial -notmatch '^emulator-\d+$') {
    throw "Refusing non-emulator target '$script:TargetSerial'."
}

Wait-ForAndroidBoot -Serial $script:TargetSerial
Write-Host "Android runtime target: $script:TargetSerial (emulator only)"

$originalSerial = [Environment]::GetEnvironmentVariable('ANDROID_SERIAL', 'Process')
$hadOriginalSerial = $null -ne $originalSerial

try {
    # Child processes inherit this process-local target. No user- or
    # machine-level environment is changed.
    $env:ANDROID_SERIAL = $script:TargetSerial

    if (-not $SkipUnitTests) {
        Invoke-Gradle -Tasks @(':core:test', ':app:testDebugUnitTest')
    }
    Invoke-Gradle -Tasks @(':app:assembleDebug')

    $apkPath = Join-Path $androidRoot 'app\build\outputs\apk\debug\app-debug.apk'
    if (-not (Test-Path -LiteralPath $apkPath -PathType Leaf)) {
        throw "Debug APK was not produced at $apkPath."
    }

    if (-not $SkipInstall) {
        Invoke-Adb -Arguments @('install', '-r', $apkPath)
    }

    if (-not $SkipLaunch) {
        Invoke-Adb -Arguments @('logcat', '-c')
        Invoke-Adb -Arguments @(
            'shell', 'am', 'start', '-W', '-n',
            'com.clipvault.app/.ui.MainActivity'
        )
        Start-Sleep -Seconds 2

        $pidOutput = & $script:AdbPath -s $script:TargetSerial shell pidof com.clipvault.app 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace("$pidOutput")) {
            throw 'ClipVault did not remain running after launch. Inspect emulator logcat for the crash.'
        }

        $androidRuntimeErrors = & $script:AdbPath -s $script:TargetSerial logcat -d -v brief 'AndroidRuntime:E' '*:S'
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect emulator logcat (exit code $LASTEXITCODE)."
        }
        if ("$androidRuntimeErrors" -match 'FATAL EXCEPTION') {
            $androidRuntimeErrors | Write-Output
            throw 'A fatal Android runtime exception was recorded after launch.'
        }
    }

    if ($RunInstrumentationTests) {
        # Build locally, then install and run through the already validated
        # emulator serial. Gradle's connected* task may enumerate every ADB
        # device, so it is intentionally not used here.
        Invoke-Gradle -Tasks @(':app:assembleDebugAndroidTest')
        $testApkPath = Join-Path $androidRoot 'app\build\outputs\apk\androidTest\debug\app-debug-androidTest.apk'
        if (-not (Test-Path -LiteralPath $testApkPath -PathType Leaf)) {
            throw "Instrumentation APK was not produced at $testApkPath."
        }

        Invoke-Adb -Arguments @('install', '-r', '-t', $testApkPath)
        $instrumentationOutput = & $script:AdbPath -s $script:TargetSerial shell am instrument -w `
            'com.clipvault.app.test/androidx.test.runner.AndroidJUnitRunner'
        $instrumentationExitCode = $LASTEXITCODE
        $instrumentationOutput | Write-Output
        $instrumentationText = "$($instrumentationOutput -join "`n")"
        if (
            $instrumentationExitCode -ne 0 -or
            $instrumentationText -match 'FAILURES!!!|INSTRUMENTATION_(?:FAILED|ABORTED)|Process crashed' -or
            $instrumentationText -notmatch '(?m)^OK \(\d+ tests?\)'
        ) {
            throw "Instrumentation tests failed on $script:TargetSerial."
        }
    }

    Write-Host 'ClipVault Android emulator validation completed successfully.'
}
finally {
    if ($hadOriginalSerial) {
        $env:ANDROID_SERIAL = $originalSerial
    }
    else {
        Remove-Item Env:ANDROID_SERIAL -ErrorAction SilentlyContinue
    }
}
