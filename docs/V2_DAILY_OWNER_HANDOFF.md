# ClipVault v2 daily-use Owner handoff

This runbook starts only after one clean CI run produces
`clipvault-v2-daily-unsigned-candidate`. It does not ask Codex or CI to read a
private key, install an IME, approve a license, or publish a release.

The current project-license governance state is `internal_only` with
`license_file: null` and `distribution_allowed: false`. This is an explicit
Owner decision for local/internal daily use. It is not a public license and
must not be represented as permission to distribute the candidate.

## 1. Provision the native/device runner before candidate generation

Complete this section before expecting the candidate workflow to produce a
unified bundle. Register one repository self-hosted Windows x64 runner with
all of these labels:

```text
self-hosted, Windows, X64, clipvault-native, clipvault-android-device
```

On that runner, verify JDK 17, Android SDK/API 36, NDK/CMake, Gradle wrapper
support, and all lock-matched native archives required by
`android/scripts/build-v2-ime.ps1`. Connect exactly one authorized physical
Android device and verify:

```powershell
adb devices -l
```

Using an Owner account that can inspect repository Actions runners, confirm
the runner is online and has the labels above:

```powershell
gh api repos/<owner>/<repo>/actions/runners
```

Only after those checks pass, set the non-secret capability attestation used
by the candidate workflow:

```powershell
gh variable set CLIPVAULT_NATIVE_RUNNER_READY `
  --repo <owner>/<repo> `
  --body true
```

This variable is an Owner attestation, not a substitute for runner labels or
device checks. If it is absent, the Android native lane fails immediately and
the candidate remains blocked; no runner registration token, device data,
keystore, or private key belongs in the repository.

Then trigger the candidate workflow from the integration branch:

```powershell
gh workflow run ci.yml --ref codex/v2-daily-integration
```

## 2. Freeze and verify the source candidate

1. Check out the exact `BUILD_RECEIPT.json.source_commit` with a clean worktree.
2. Extract the unified artifact without renaming or replacing files.
3. Run:

```powershell
python tools/v2_daily_candidate.py --root . verify `
  --artifact-dir <EXTRACTED_CI_BUNDLE> `
  --expected-commit <40_CHAR_COMMIT> `
  --expected-run-id <GITHUB_RUN_ID>

python tools/v2_daily_readiness.py --source-only
python tools/v2_daily_readiness.py --automated-only `
  --candidate-dir <EXTRACTED_CI_BUNDLE>
```

If any command fails, discard the candidate rather than mixing artifacts from
another run. Keep the bundle's `BUILD_RECEIPT.json`, `RELEASE_MANIFEST.json`,
`SHA256SUMS.txt`, and workflow URL with the final evidence.

## 3. Owner-only decisions and signing

- Confirm that `THIRD_PARTY_MANIFEST.yaml` still records the exact
  `internal_only` / `license_file: null` / `distribution_allowed: false`
  triplet. Do not create a root `LICENSE` merely for internal installation.
- For internal daily-use evidence, `license_and_notices_approved: true` means
  that the Owner reviewed the applicable third-party terms/notices for this
  candidate and this internal scope. It does not authorize redistribution.
- If external distribution is considered later, stop this runbook and first
  adopt a real repository license file with `status: approved` and
  `distribution_allowed: true`, then redo the distribution-specific review in
  `docs/V2_LICENSE_RELEASE_GATE.md`.
- Sign both Android APKs with the same approved Android identity.
- Sign the Desktop executable, x64 Host, OTP Broker, and x64/x86 TSF DLLs with
  the same approved Windows identity, then rebuild the Windows IME ZIP from
  those signed members.
- Recompile the Inno installer so it embeds the signed Desktop executable and
  signed Windows IME package, then sign that final installer. Merely signing
  the CI installer would leave its embedded payload unsigned.
- Do not copy keystores, private keys, PINs, tokens, or recovery material into
  the repository or evidence directory.

Capture Android verification for each final APK (not for the unsigned input):

```powershell
$imeReport = @(& java -jar <ANDROID_SDK>\build-tools\<VERSION>\lib\apksigner.jar `
  verify --verbose --print-certs <SIGNED_IME_APK> 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'IME APK verification failed' }
$imeReport | Set-Content -LiteralPath <EVIDENCE_DIR>\ANDROID_IME_APKSIGNER_VERIFY.txt -Encoding utf8

$runtimeReport = @(& java -jar <ANDROID_SDK>\build-tools\<VERSION>\lib\apksigner.jar `
  verify --verbose --print-certs <SIGNED_RUNTIME_APK> 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'Runtime APK verification failed' }
$runtimeReport | Set-Content -LiteralPath <EVIDENCE_DIR>\ANDROID_RUNTIME_APKSIGNER_VERIFY.txt -Encoding utf8
```

Capture Windows evidence without exposing a private key:

```powershell
.\tools\Collect-V2WindowsAuthenticodeEvidence.ps1 `
  -DesktopExecutable <SIGNED_DESKTOP_EXE> `
  -WindowsInstaller <SIGNED_INSTALLER_EXE> `
  -WindowsImePackage <SIGNED_WINDOWS_IME_ZIP> `
  -OutputPath <EVIDENCE_DIR>\WINDOWS_AUTHENTICODE.json
```

The collector fails unless every required Owner binary has Authenticode status
`Valid` and the same certificate thumbprint. Its JSON is then rechecked against
the ZIP member bytes by `tools/v2_daily_readiness.py`.

## 4. Manual daily-use evidence

Copy `docs/V2_DAILY_USE_OWNER_EVIDENCE.example.json`, replace every placeholder,
and execute every row in `docs/V2_DAILY_USE_MANUAL_QA.md` against these same
signed bytes. The schema is v3 and requires Desktop/installer evidence, both
APK signer reports, the Windows Authenticode report, and the original CI receipt.

After the seven-day run, from the clean source checkout run:

```powershell
python tools/v2_daily_readiness.py `
  --candidate-dir <EXTRACTED_CI_BUNDLE> `
  --evidence <OWNER_EVIDENCE_JSON>
```

Only a final `status: ready` plus explicit Owner approval establishes internal
daily-use readiness for this candidate. While the project remains
`internal_only`, that result does not open an external distribution gate and
still does not publish anything automatically.
