# V2 daily-use automated candidate workflow

`V2 daily-use automated candidate` is a manual, fail-closed automation gate. It
coordinates the existing production entrypoints without publishing or changing
the test machine:

1. Run the complete Desktop test suite, build the locked PyInstaller portable
   executable, and upload an unsigned internal Desktop candidate.
2. Call the reusable Android native production workflow. Its labelled
   self-hosted runner must provide the lock-file-matched librime, Fcitx prebuilt,
   dictionary, Android SDK, and native archives adjacent to the checkout.
3. Call the reusable Windows production workflow. It fetches only the pinned
   librime archive and exact dictionary commit, verifies them, builds x64 Host
   and x64/x86 TSF clients, runs CTest, and uploads a transient unsigned package.
4. Run `python tools/v2_daily_readiness.py --source-only` only after all three
   upstream gates pass. This proves repository-local source gates, not a
   downloadable candidate.
5. Download those three internal artifacts into a clean aggregation job,
   compile the v2 Inno Setup candidate without running it, generate a unified
   build receipt and SHA-256 manifest, then run
   `python tools/v2_daily_readiness.py --automated-only --candidate-dir <bundle>`
   before uploading one downloadable bundle.

Start it from GitHub Actions with **Run workflow** on
`.github/workflows/v2-daily-candidate.yml`, selecting
`codex/v2-daily-integration`. Direct dispatches from another ref are rejected
by the workflow policy guard; the `ci.yml` reusable caller remains limited to
the same integration branch. A missing self-hosted runner, missing
Android native input, unavailable pinned Windows input, lock/hash mismatch,
build failure, test failure, or packaging failure leaves the candidate
**BLOCKED**. There is no Direct-only/native-missing fallback.

Candidate runs are immutable and latest-run-wins. The workflow concurrency
group is scoped to the branch and uses `cancel-in-progress: true`, so an older
queued native/device lane cannot indefinitely block a newer candidate. This
does not cancel a promoted artifact: a candidate is eligible only after the
new run produces its own receipt-bound bundle and passes the Owner gates.

Before the Android reusable workflow is scheduled, a read-only runner
preflight checks the repository's online runner labels. Missing
`self-hosted, Windows, X64, clipvault-native, clipvault-android-device` fails
the Android lane immediately with an explicit error instead of leaving a
queued job for hours. Desktop and Windows cloud-hosted gates still run so their
evidence remains available; the aggregate candidate remains fail-closed.

## Android native runner preflight

The Owner-controlled repository runner must be a Windows x64 host registered
with all of these labels:

```text
self-hosted, Windows, X64, clipvault-native, clipvault-android-device
```

Before rerunning the candidate, verify that the runner has the JDK 17, Android
SDK/API 36, NDK/CMake, Gradle wrapper support, and every lock-file-matched
native archive required by `android/scripts/build-v2-ime.ps1` in the expected
paths. The device job additionally requires exactly one authorized Android
device visible to `adb devices -l`; an emulator or a missing device is not a
substitute for the device gate. The read-only GitHub check is:

```powershell
gh api repos/<owner>/<repo>/actions/runners
adb devices -l
```

Do not put runner registration tokens, keystores, signing keys, or other
credentials in the repository or candidate evidence.

This workflow has read-only repository permission. It uploads only explicitly
named, unsigned internal candidate artifacts with a 14-day retention period.
It does not sign binaries, register an IME, run the compiled installer, publish
a release, access secrets, or read/write Owner evidence. The restricted Android
SMS lane is compiled, linted, and unit-tested only; it never emits an APK or AAB
in this workflow. The default Android Runtime and the standalone networkless IME
are separate APKs, and both are checked against final permission, 16 KiB native
alignment, and locked Rime asset gates before upload.

The unified artifact is named `clipvault-v2-daily-unsigned-candidate` and
contains the two Android APKs, the Windows IME package, the Desktop executable,
the compiled installer candidate, `CANDIDATE-NOT-A-RELEASE.txt`,
`BUILD_RECEIPT.json`, `RELEASE_MANIFEST.json`, and `SHA256SUMS.txt`. The receipt
binds the GitHub run, successful component jobs, candidate version, Git commit,
and production lock digests. The manifest binds the exact flat artifact set and
is verified together with the receipt before upload. Compiling the installer is
a packaging operation only; it does
not install or register the TSF service on the runner. Signing, installation,
manual Android/Windows/OTP QA, seven-day daily-use evidence, license approval,
and the Owner decision remain separate release gates described in
`docs/V2_DAILY_USE_ACCEPTANCE.md`. Owner evidence uses schema v3 and must retain
both APK `apksigner` reports plus the structured Windows Authenticode report;
boolean "signed" assertions are not accepted as signature evidence.
