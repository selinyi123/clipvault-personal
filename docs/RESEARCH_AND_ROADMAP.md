# ClipVault — Research Log & Roadmap（加固支线）

> **定位（2026-06-27 厘清）**：本文是 **v1.x 安全/同步/隐私加固支线** 的研究记录与路线，
> **从属于** keyboard 主线 [ROADMAP_V2_KEYBOARD.md](ROADMAP_V2_KEYBOARD.md)。
> 北极星不变：**做一个完整的（中文）输入法**——主线 v2.1 起接 librime/fcitx5 底座。
> 本支线只做"可在桌面/Linux 验证、且不偏离 local-first 与 secrets-never-leave"的加固，
> 为主线提供稳固 Runtime，不替代主线。
>
> **版本标签澄清**：下文 "v1.6 / v1.7 / v1.8" 是**里程碑/PR 标签**，**不是**版本号变更。
> 截至 2026-06-28，源码 `__version__` 已 bump 到 **1.6.0**（Owner 裁定，反映累计加固；未切 Release），最新已发布二进制仍为 **v1.5.10**。
> 各项落地状态见 [docs/HANDOFF.md](HANDOFF.md) 的 *Hardening Support Line Snapshot*（PRs #4–#15）。
> 是否 bump 版本号 + 切新 Release 由 Owner 裁决。

## Core goal (the anchor for all research)

Privacy-first, **local-first** personal clipboard + Personal Memory manager.
Desktop (Windows) captures the clipboard → classifies → **Secret Guard** (secrets
never leave the machine) → SQLite + Obsidian vault + optional Git backup. The
Android app's IME surfaces clips/memory as candidates and syncs with the desktop
over the **LAN** (event-log, last-write-wins). No typed-text collection, no
analytics, secrets never sync.

Research must serve this goal — avoid reinventing existing wheels, and never drift
into cloud storage, typed-text learning, or anything that exfiltrates secrets.

## Research log — round 1 (2026-06-26)

One row per direction. The **decision** column exists so later rounds don't
re-research the same ground.

| # | Direction | Key finding | Decision |
|---|---|---|---|
| R1 | Prior-art clipboard managers (Maccy, CopyQ) | Maccy honours the OS "concealed/transient" clipboard flag that password managers (1Password, Bitwarden) set, so secrets are dropped **at the source** before any scan. CopyQ adds optional encrypted items + file sync. | **Adopt (v1.7):** honour Windows clipboard-exclusion formats in the watcher. |
| R2 | Android IME privacy | `IME_FLAG_NO_PERSONALIZED_LEARNING` (API 26+, we target minSdk 26) marks an "incognito" field; IMEs must not record/personalise typing there. | **Done (this cycle):** `PrivacyAwareFilter` hides candidates when the flag is set. |
| R3 | Secret scanning (gitleaks / trufflehog / detect-secrets) | Best practice = a **regex rule-set + Shannon entropy as a secondary signal** ("regex + entropy beats entropy alone"); gitleaks ships 150+ rules. trufflehog is *verification-first* (calls the provider to confirm). | **Adopt-light (v1.7):** widen Secret Guard rules + tune entropy, gitleaks-inspired, with golden vectors. **Reject** verification-first — a live API call would transmit the secret, violating the core goal. |
| R4 | DNS rebinding / localhost APIs | Loopback bind + source-IP check is **not** enough: a site that rebinds its name to 127.0.0.1 reaches the API from the user's own browser. Defence = validate the `Host` header (must be loopback) and 403 otherwise. Real CVEs exist (e.g. Glances `GHSA-hhcg-r27j-fhv9`). | **Done (this cycle):** `Host`-header guard on the management API. |
| R5 | Local-first sync (cr-sqlite, sqlite-sync, CRDT vs LWW) | Row-level LWW with **delete-wins** matches user expectations; per-column / block-level LWW preserves independent edits. Full CRDT libraries are overkill at our scale. | **Adopt-light (v1.8):** per-field LWW for `clip_meta` (fixes the coarse meta-ts field-masking quirk) without a CRDT dependency. |
| R6 | Pairing / token hardening | Lockout on repeated auth failures is standard; its absence invites code brute-force and single-threaded-server DoS. | **Done (this cycle):** pairing rate-limit (failure lockout). |

Platforms consulted this round: GitHub, Hacker News, Medium, Substack, Microsoft
Learn, Android Developers, and security vendor blogs. (URLs are in the PR that
introduced this file.)

## Research log — round 2 (2026-06-28)

| # | Direction | Key finding | Decision |
|---|---|---|---|
| R7 | CJK full-text search in SQLite FTS5 | 默认 `unicode61` 分词器把整串 CJK 当**单 token**，中文子串/短语**搜不到**（实测："天气"搜不到"今天天气很好"，英文中段子串也搜不到）。社区方案：**trigram**（SQLite 内置，3 字窗，支持 CJK/英文子串，但 <3 字不行）；**ICU 分词器**（需重编 SQLite 链接 ICU，不可移植）；**jieba/cppjieba**（[wangfenjin/simple](https://github.com/wangfenjin/simple)，中文+拼音，质量最好但是 C 扩展依赖）；**bigram 预处理**（纯 SQL，2 字也行，但需改写入路径）。 | **Adopt（已并入）：** clips_fts 改 **trigram**（零新依赖，符合 stdlib-only 原则）+ <2 字/secret 视图 **LIKE 回退**（个人规模可接受）。**Reject** jieba/ICU（依赖/不可移植，违反零依赖原则）。 |
| R8 | librime 在 Android 的嵌入（v2.1 build PoC 前置） | Trime（JNI 直接嵌 librime）与 fcitx5-android（插件式提供 RIME）是两条成熟参考。构建需 **Android NDK 25 + CMake 3.22 + SDK 35**；librime 自身是 CMake/C++ 工程经 JNI 暴露。证实 v2.1 build PoC 必须在 Android 工具链/CI/真机上做，本地（无 NDK）编不了。 | **记录，未动手：** v2.1 开工时按此清单做 build PoC（NDK 编 librime + JNI 喂一次拼音取候选），输出 ADR-0010 终裁。仍是 CI/设备-only。 |

Platforms/sources consulted: GitHub（wangfenjin/simple、streetwriters/sqlite-better-trigram、rime/librime、fcitx5-android、osfans/trime）、DEV.to（FTS5 bigram fix）、SQLite users 邮件列表、F-Droid（fcitx5 RIME plugin）。

## Roadmap

### v1.6 — privacy & security hardening — ✅ 已并入 main
- Pairing rate-limit (R6) · DNS-rebinding `Host` guard (R4) · IME incognito suppression (R2) — **#12**。
- 同期并入：version single-source (**#7**)、candidate source caps (**#8**)、
  sync/peer 状态可见 (**#9**)、device revocation (**#10**)。

### v1.7 — capture-layer privacy + Secret Guard depth
- ✅ **已并入 (#13)**：Widen Secret Guard with a gitleaks-inspired rule set + entropy tuning,
  backed by golden test vectors (R3). *Desktop-testable.*
- ✅ **本分支（SG-1.3）**：Personal Memory 的 `text`/`label` 在 repo 写入、导入、同步出口与两端接收处
  统一过 Secret Guard；历史 secret-shaped 行不进候选，历史 outbox 事件不下发但游标继续推进。
- ✅ **本分支（Windows clipboard exclusion）**：Honour producer-set Windows
  clipboard exclusion formats in the watcher:
  `ExcludeClipboardContentFromMonitorProcessing`, `CanIncludeInClipboardHistory=0`,
  `CanUploadToCloudClipboard=0`, and the de facto `Clipboard Viewer Ignore`
  marker. ClipVault currently has no per-clip "local only, never sync" metadata,
  so cloud-upload opt-out is treated as a capture opt-out. Unit-tested with
  injected watcher/format probes; real clipboard manual QA still needs a Windows
  source app that sets these formats.
- ✅ **已实现，待 Manual QA**：Extend IME incognito to the **save** path.
  `ClipVaultPanelImeService.saveClipboard()` checks the current
  `ImePrivacySession` token before reading the clipboard and again before
  calling `runtime.saveExplicit(...)`. Real-device QA still needs to confirm no
  save action survives password/incognito/no-suggestions editor transitions.

### v1.8 — sync correctness — ✅ 已并入 main
- ✅ **#14**：Per-field LWW for `clip_meta`: track the meta timestamp per field
  (pinned / favorite / deleted) so a newer change to one field can't be masked by
  an older change to another (R5). *Desktop-testable (migration 0004).*
- ✅ **#15**：Optional `Referer` check as a second DNS-rebinding layer.

### 搜索质量（CJK）— ✅ trigram 已并入（本轮，R7）
- ✅ **已并入**：clips_fts 改 trigram 分词器 + 短查询 LIKE 回退 → 中文全文搜索可用（migration 0005，DB-1.1）。
  *Desktop-testable（test_fts_cjk）。*
- ⏳ **候选**：若 2 字中文查询的 LIKE 扫描在大库下变慢，再评估 **bigram 预处理**（纯 SQL，2 字也进 FTS 索引）。
- ⏳ **候选**：Android Room 端中文搜索一致性（Room FTS4/FTS5 的 CJK 同样问题；与桌面对齐，需设备/CI 验证）。

### v2.0 — opt-in transport security — ⏳ 未做（候选）
- Optional self-signed TLS for the LAN sync/pair socket (R4 defence-in-depth), with
  the Android client pinning the desktop certificate at pair time.
  *注：自签证书生成在 stdlib-only 约束下无现成方案（需 openssl 或 cryptography 依赖），开工前需 Architect 定生成方式。*

> 主线优先级高于本支线剩余项：keyboard 主线（ROADMAP_V2_KEYBOARD）的 v2.1 底座 spike（ADR-0010）
> 是北极星路径；本支线 v1.7 残项与 v2.0 为机会性加固，不阻塞主线。

### Explicitly out of scope (keeps us on the core goal)
- Cloud sync / server-side storage — ClipVault is local-first.
- Verification-first secret scanning that calls provider APIs — would exfiltrate
  the secret, violating "secrets never leave".
- Typed-text learning, behavioural profiling, or analytics SDKs in the IME.

## Research supersession (2026-07-02)

- R8's NDK 25 / CMake 3.22 / SDK 35 combination was a historical upstream snapshot, not a current hard requirement.
- R9 in `RESEARCH_V2_1_BUILD_POC_2026_07_02.md` supersedes that toolchain portion. Execute v2.1 with the exact
  versions and 16KB checks frozen in `SLICES/V2-S004-librime-build-poc.md`.

## Research log - round 3 (2026-07-03)

Scope filter: keep the Android IME local-first, no typed-text logging, no analytics
SDKs, no network work inside the IME service, and no cloud storage. This round
focused on avoiding reinvention in privacy keyboards and LAN sync while keeping
ClipVault's explicit-save and desktop-primary architecture.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R10 | Privacy-first Android keyboard boundary | FlorisBoard GitHub (`https://github.com/florisboard/florisboard`); HeliBoard GitHub (`https://github.com/heliborg/heliboard`) | Mature privacy keyboards keep privacy as a product boundary. HeliBoard's public README states it uses no Internet permission and is offline; FlorisBoard positions itself as a privacy-respecting open-source IME with clipboard/history and theming. | **Adopt as gate:** keep ClipVault IME free of network dependencies and audit `ime/` + manifest for accidental Internet-path coupling. Do not copy their full keyboard scope before v2.x engine work. |
| R11 | Android editor privacy signals | Android Developers `InputMethodService`, `EditorInfo`, `InputType` docs (`https://developer.android.com/develop/ui/views/touch-and-input/creating-input-method`, `https://developer.android.com/reference/android/view/inputmethod/EditorInfo`) | Android exposes `EditorInfo.inputType`, password variations, and `IME_FLAG_NO_PERSONALIZED_LEARNING`; the platform docs note the no-personalized-learning flag is a request, not a guarantee, so compliant IMEs need their own enforcement. | **Already aligned, extend tests:** keep suppressing ClipVault personal candidates in password/incognito sessions; future device QA must verify no save/suggestion path survives editor transitions. |
| R12 | LAN REST sync and explicit trust | LocalSend GitHub + protocol (`https://github.com/localsend/localsend`, `https://github.com/localsend/protocol`) | LocalSend avoids external servers and uses REST over local network. Its protocol shows discovery/fingerprint fields, optional HTTPS, PIN/token acceptance, request size/status handling, and that discovery has privacy tradeoffs. | **Adopt selectively:** keep manual pair-code/token flow; add bounded request validation and security headers now. Discovery remains out-of-scope unless it has a privacy design and rotating/non-tracking identifiers. |
| R13 | Cross-device clipboard prior art | KDE Connect GitHub (`https://github.com/kde/kdeconnect-kde`) | KDE Connect demonstrates shared clipboard and TLS-based device communication, but also includes notifications, SMS, remote commands, and broad desktop integration. | **Adopt only the narrow lesson:** TLS/pairing is relevant to v2.0 transport hardening; remote commands, SMS, notifications, and broad device automation are out-of-scope. |
| R14 | Android Rime implementation evidence | Trime GitHub (`https://github.com/osfans/trime`) | Trime is Android Rime via JNI/librime and documents Android SDK/NDK, JDK 17, and OpenCC/Python dictionary generation needs. This confirms v2.1 needs native build evidence, not only API design. | **Use for v2.1 PoC:** treat as a reference implementation and build-risk checklist. Do not vendor or production-wire it until V2-S004 produces reproducible build/license/16KB results. |
| R15 | Local discovery privacy caution | Hacker News LocalSend discussion (`https://news.ycombinator.com/item?id=37938183`); LocalSend advisory GHSA-424h-5f6m-x63f (`https://github.com/localsend/localsend/security/advisories/GHSA-424h-5f6m-x63f`) | Community review called out certificate fingerprints/discovery identifiers as MITM surfaces when not user-verifiable; the later advisory documents spoofed UDP discovery leading to interception. | **Caution:** if ClipVault adds discovery or TLS pinning, surface fingerprints/QR or keep explicit pair-code trust. Avoid persistent broadcast identifiers and unauthenticated discovery. |

### Next-node plan after this round

1. **v1.6.x hardening patch (#32, completed):** bound JSON API bodies to objects, constrain pair `device_id`, add CSP/security headers, render Web UI API data through safe DOM APIs, and reject malformed/oversized sync push batches before they can amplify logs/CPU.
2. **v1.7 capture privacy:** Windows clipboard-exclusion formats are implemented in the current branch; this remains Windows-only and still needs GitHub Actions plus real-source manual evidence.
3. **v2.0 candidate:** optional LAN TLS with pair-time fingerprint or QR verification. Do not start until certificate generation/pinning is designed under the stdlib-only constraint or an ADR approves a dependency/tooling exception.
4. **v2.1 mainline:** execute V2-S004 librime build PoC exactly as frozen in `docs/SLICES/V2-S004-librime-build-poc.md`; no production engine integration until both build/license/native alignment gates pass.

## Research log - round 4 (2026-07-03)

Scope filter: close the remaining v1.7 capture-layer privacy item without
changing Android IME behavior, sync protocol, release metadata, or dependencies.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R16 | Windows clipboard producer privacy formats | Microsoft Clipboard Formats docs (`https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-formats`), `RegisterClipboardFormatW` docs (`https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerclipboardformatw`), CopyQ security docs (`https://copyq.readthedocs.io/en/latest/security.html`) | Windows producers can set registered formats to opt out of clipboard history/monitoring or cross-device clipboard sync. `ExcludeClipboardContentFromMonitorProcessing` blocks history and sync with any data; `CanIncludeInClipboardHistory` uses serialized DWORD 0/1 for history; `CanUploadToCloudClipboard` uses serialized DWORD 0/1 for device sync. CopyQ also documents `Clipboard Viewer Ignore` as a Windows secret marker used by password-manager workflows. | **Adopt now:** the watcher checks these formats before reading text, advances the sequence, and skips capture. This avoids persisting or syncing content that the source app marked as non-history/non-sync. |

### Next-node plan after round 4

1. **PR #11:** leave unmerged; it is conflict/dirty and mostly superseded by main. If needed, close as superseded after owner approval.
2. **Issues #1/#2:** create a current 1.6.0 release-gate issue and migrate only remaining signed release/manual QA items; old version gates are stale.
3. **Residual optional small PR:** decide whether successful pairing should reset recent failed pairing attempts. Treat as UX/security policy, not an automatic bug fix.

## Research log - round 5 (2026-07-03)

Scope filter: keep pairing/auth hardening local to the desktop sync API. Do not
change the pairing protocol, token storage, Android IME behavior, release
metadata, or LAN/Tailscale transport assumptions.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R17 | Pair-code rate-limit recovery semantics | NIST SP 800-63B Rev.4 (`https://pages.nist.gov/800-63-4/sp800-63b.html`), OWASP Authentication Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html`), OWASP WSTG weak lockout testing (`https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/04-Authentication_Testing/03-Testing_for_Weak_Lock_Out_Mechanism`), OWASP Top 10 A07 (`https://owasp.org/Top10/2021/A07_2021-Identification_and_Authentication_Failures/`) | Short one-time pairing codes need rate limiting to resist guessing, but lockout also creates DoS/usability risk. NIST distinguishes generating a new authentication secret from successful authentication: generating a new secret must not reset failures, while successful authentication should disregard/reset previous failures for the authenticator used. OWASP WSTG's lockout test pattern likewise verifies that prior incorrect attempts do not trigger early lockout after a successful login. | **Adopt now:** keep `mint_code()` from clearing failures, but treat a valid one-time code redemption as successful authentication and clear the short consecutive-failure window. Do not add per-IP/per-device buckets in this small patch; that requires API plumbing and separate threat-model review. |

## Research log - round 6 (2026-07-03)

Scope filter: serve Issue #36 release-gate work only. Do not add analytics,
cloud sync, typed-text logging, or network work inside the Android IME.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R18 | Release artifact signing and integrity | GitHub artifact attestations (`https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds`), Android `apksigner` docs (`https://developer.android.com/tools/apksigner`), Inno Setup `SignTool` docs (`https://jrsoftware.org/ishelp/topic_setup_signtool.htm`) | GitHub Actions can attach signed provenance to built binaries when workflows grant `id-token: write` and `attestations: write`; Android release APKs should be signed before publication and verified with `apksigner verify`; Inno Setup supports external signing tools for Setup/Uninstall artifacts. | **Adopt for #36:** keep normal PR CI unsigned/debug-only. For release, build artifacts from a trusted main/tag workflow or local owner machine, sign Android with owner-held keystore, compile/sign the Windows installer, verify signatures, and publish assets plus checksums only after Owner approval. |
| R19 | IME candidate suppression and private input compatibility | Android Developers `InputMethodService` (`https://developer.android.com/develop/ui/views/touch-and-input/creating-input-method`), Android `EditorInfo` / `InputType` references (`https://developer.android.com/reference/android/view/inputmethod/EditorInfo`), AOSP `InputMethodService` source (`https://android.googlesource.com/platform/frameworks/base/+/HEAD/core/java/android/inputmethodservice/InputMethodService.java`), HeliBoard `InputAttributes` (`https://github.com/HeliBorg/HeliBoard/blob/main/app/src/main/java/helium314/keyboard/latin/InputAttributes.java`), HeliBoard README/F-Droid pages (`https://github.com/heliborg/heliboard`, `https://f-droid.org/en/packages/helium314.keyboard/`) | Android IMEs are expected to reset/reinitialize state on `onStartInput` and `onFinishInput`, and to inspect `EditorInfo.inputType` for password-like targets. `TYPE_TEXT_FLAG_NO_SUGGESTIONS` requests no dictionary candidates. `IME_FLAG_NO_PERSONALIZED_LEARNING` marks no-learning/private fields. HeliBoard, a current offline/privacy keyboard, suppresses suggestions for password fields and records no-learning from `EditorInfo.imeOptions`. | **Already aligned, keep as #36 QA gate:** `PrivacyAwareFilter` suppresses ClipVault personal candidates for password, no-suggestions, numeric-password, web-password, and no-personalized-learning fields; `ImePrivacySession` invalidates in-flight candidate loads across editor transitions. Do not add typed-text learning or IME networking. Manual QA remains required because OEM/browser private-field behavior can vary. |
| R20 | Release-candidate dry run | PyInstaller project (`https://github.com/pyinstaller/pyinstaller`), Android command-line build docs (`https://developer.android.com/build/building-cmdline`), Inno Setup command-line compiler docs (`https://jrsoftware.org/ishelp/topic_compilercmdline.htm`) | PyInstaller can package a Python app into a standalone executable; Android Gradle can build from the command line before signing/publishing; Inno Setup exposes `ISCC.exe` for command-line installer compilation. | **Adopt now:** add a manual/PR-safe release-candidate workflow that builds portable exe, installer, Android debug APK, an unsigned release APK, SHA256SUMS, and a machine-readable `RELEASE_MANIFEST.json` as workflow artifacts. This is packaging evidence only; it intentionally records `signed=false` / `published=false` and does not sign or publish release assets. |
| R21 | Release evidence self-verification | SLSA GitHub generator (`https://github.com/slsa-framework/slsa-github-generator`), SLSA provenance blog (`https://slsa.dev/blog/2022/08/slsa-github-workflows-generic-ga`), F-Droid reproducible builds docs (`https://f-droid.org/en/docs/Reproducible_Builds/`), Sigstore CI quickstart (`https://docs.sigstore.dev/quickstart/quickstart-ci/`) | Mature release flows separate artifact bytes, checksums, provenance, and signature verification. F-Droid's APK reproducibility model highlights that signatures and byte-for-byte evidence are distinct; SLSA/Sigstore flows start from stable artifact digests before provenance/signing. | **Adopt now:** verify `RELEASE_MANIFEST.json` and `SHA256SUMS.txt` against staged artifacts in CI before upload. **Adopt later:** add owner-controlled signed release verification (`apksigner`, Windows signer, optional provenance/attestation) only after signing authority and secrets handling are defined. |
| R22 | Android APK reproducibility as a release-quality bar | F-Droid reproducible builds docs (`https://f-droid.org/en/docs/Reproducible_Builds/`), F-Droid signing/reproducible-builds post (`https://f-droid.org/en/2023/09/03/reproducible-builds-signing-keys-and-binary-repos.html`) | F-Droid's model rebuilds from source and compares against the developer-signed APK, treating signature files and APK byte layout as separate evidence. This is stronger than "the build succeeded" but requires fixed toolchains and disciplined metadata handling. | **Adopt later:** for v2.1 native/librime work and any owner-published Android release, keep a reproducibility checklist: fixed JDK/Gradle/AGP/NDK, deterministic generated files, stable ZIP/APK metadata where possible, and an explicit diff allowance for signing blocks. Do not make reproducibility a blocker for the current unsigned dry run. |
| R23 | IME manual QA automation path | Android Developers Espresso basics (`https://developer.android.com/training/testing/espresso/basics`), Android `InputMethodService` reference (`https://developer.android.com/reference/kotlin/android/inputmethodservice/InputMethodService`), Espresso IME testing prototype (`https://github.com/sealor/prototype-Android-Espresso-Keyboard-Testing`) | Espresso encourages user-level interactions rather than direct view references, reducing UI-test flakiness; IME lifecycle still requires device/emulator wiring, and public prototypes show keyboard-switch helpers can exercise an input method in instrumentation tests. | **Adopt later:** convert repeatable parts of #36 IME QA into `androidTest` smoke flows for candidate visibility/commit and sensitive-field suppression. Keep real-device Owner QA as the release gate because OEM/browser private-field behavior can vary. |

## Research log - round 7 (2026-07-03)

Scope filter: serve Issue #36 release-gate work only. Do not change Android IME
behavior, sync behavior, typed-text policy, analytics policy, or artifact
publication semantics.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R24 | Owner-controlled release signing secrets | GitHub environments docs (`https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment`), GitHub deployments/environments reference (`https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments`), GitHub CLI `gh secret set` manual (`https://cli.github.com/manual/gh_secret_set`) | GitHub environment secrets are only available to jobs that reference that environment; if the environment requires approval, jobs cannot access those secrets until a required reviewer approves. The GitHub CLI supports setting an environment secret with `gh secret set --env <environment>`. | **Adopt now:** keep Android signing in the `release` environment and configure signing values as `release` environment secrets instead of repository-level secrets. This keeps the owner approval gate attached to the most sensitive release credentials. |

## Research log - round 8 (2026-07-03)

Scope filter: serve v1.6/v1.7 stability, release-chain hardening, and
local-first privacy only. Do not change Android IME behavior, sync semantics,
artifact publication semantics, runtime dependencies, typed-text policy, or
analytics policy.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R25 | GitHub Actions checkout credential persistence | actions/checkout README (`https://github.com/actions/checkout`), GitHub Docs on `GITHUB_TOKEN` authentication (`https://docs.github.com/actions/reference/authentication-in-a-workflow`), GitHub Actions secure-use reference (`https://docs.github.com/en/actions/reference/security/secure-use`) | `actions/checkout` persists the auth token for later authenticated git commands by default, while GitHub's security guidance recommends granting `GITHUB_TOKEN` only minimum required permissions. ClipVault's CI, dry-run, and release artifact build jobs need repository reads and API-based release creation, not authenticated `git push` after checkout. | **Adopt now:** set `persist-credentials: false` on checkout steps and guard it with a workflow static test. If a future job truly needs authenticated git writes, require a narrow documented exception rather than making persistence the default. |
| R26 | Android app-data backup and device-transfer privacy | Android Auto Backup docs (`https://developer.android.com/identity/data/autobackup`), Android `<application>` docs (`https://developer.android.com/guide/topics/manifest/application-element`), Android Studio `DataExtractionRules` lint docs (`https://googlesamples.github.io/android-custom-lint-rules/checks/DataExtractionRules.md.html`) | Auto Backup includes SharedPreferences and SQLite databases by default. `allowBackup=false` disables cloud backup, but Android 12+ device-to-device transfer behavior can vary by manufacturer; if a `<device-transfer>` section is missing from `data-extraction-rules`, that mode is enabled for app content except no-backup/cache directories. | **Adopt now:** keep `allowBackup=false`, add Android 11-and-lower `fullBackupContent` rules, and add Android 12+ `dataExtractionRules` that exclude the app root for both cloud backup and device transfer. ClipVault's local Room cache, outbox, sync settings, and token ciphertext remain device-local; re-pairing is preferred to migrating authorization material. |

## Research log - round 9 (2026-07-03)

Scope filter: serve v1.7 stability and release-chain hardening only. Do not
change Android IME behavior, artifact publication semantics, signing authority,
runtime dependencies, typed-text policy, analytics policy, or network work
inside the IME service.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R27 | Android IME network-boundary enforcement when the app needs sync | Android permissions overview (`https://developer.android.com/guide/topics/permissions/overview`), Android network operations docs (`https://developer.android.com/develop/connectivity/network-ops/connecting`), Android `<uses-permission>` docs (`https://developer.android.com/guide/topics/manifest/uses-permission-element`), eja Keyboard GitHub (`https://github.com/eja/keyboard`), Fossify Keyboard F-Droid page (`https://f-droid.org/en/packages/org.fossify.keyboard/`) | Android networking is enabled at the app level by declaring `android.permission.INTERNET`, and the permission is install-time/normal. Privacy keyboards often earn trust by requesting no Internet permission at all, but ClipVault's non-IME sync feature legitimately needs LAN HTTP. Because the manifest cannot make Internet available to sync while denying it only to `InputMethodService`, the boundary must be enforced in source structure and tests. | **Adopt now:** keep the app-level Internet permission for LAN sync, but add an Android host-JVM source-boundary test that fails if `ime/` imports project sync, WorkManager, network packages, socket/HTTP APIs, or Android logging. This keeps the IME auditable and local-first without breaking explicit user sync. |
| R28 | GitHub Actions Node.js 24 runtime migration | GitHub Actions Node 20 deprecation changelog (`https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/`), actions/checkout releases (`https://github.com/actions/checkout/releases`), actions/setup-python releases (`https://github.com/actions/setup-python/releases`), actions/setup-java releases (`https://github.com/actions/setup-java/releases`), actions/upload-artifact releases (`https://github.com/actions/upload-artifact/releases`), actions/attest-build-provenance releases (`https://github.com/actions/attest-build-provenance/releases`) | GitHub-hosted CI warns when workflows still target Node 20 action runtimes. Official action manifests for `checkout@v5`, `setup-python@v6`, `setup-java@v5`, and `upload-artifact@v6` use Node 24. `attest-build-provenance@v4` keeps the existing build-provenance inputs while wrapping `actions/attest@v4`. | **Adopt now:** move ClipVault workflows to the minimal Node 24-compatible official action majors and guard the floor in `desktop/tests/test_release_alignment.py`. Keep release semantics unchanged: no new triggers, no signing bypass, no publication without Owner approval. |

## Research log - round 10 (2026-07-03)

Scope filter: serve v1.7 privacy/stability only. Do not change Android IME
behavior, sync semantics, runtime dependencies, typed-text policy, analytics
policy, artifact publication semantics, or Owner-controlled release gates.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R29 | Pull cursor checkpointing and outbox pruning | CouchDB `_changes` docs (`https://docs.couchdb.org/en/stable/api/database/changes.html`), CouchDB replication protocol docs (`https://docs.couchdb.org/en/stable/replication/protocol.html`), PouchDB API replication/checkpoint docs (`https://pouchdb.com/api.html`) | Mature local-first replication systems expose a response cursor/checkpoint that represents the sequence reached by the returned batch, but that cursor is only safe to use as a pruning acknowledgement after the receiving peer has durably stored or explicitly acknowledged it. ClipVault currently returns `next_seq` to Android, but the desktop cannot prove the Android client applied the page before a process kill, DB error, or socket failure. | **Defer:** do not advance desktop `my_acked_seq` to returned `next_seq` inside `/api/sync/pull`. A future improvement needs an explicit peer ack/checkpoint handshake or equivalent durable confirmation before outbox pruning can use delivered cursors safely. |
| R30 | Sensitive local Web UI/API browser caching | OWASP WSTG browser cache weakness (`https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/04-Authentication_Testing/06-Testing_for_Browser_Cache_Weaknesses`), OWASP HTTP Headers Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html`), MDN `Cache-Control` header reference (`https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control`), MDN HTTP caching guide (`https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Caching`), web.dev HTTP cache guide (`https://web.dev/articles/http-cache`) | `no-cache` only forces revalidation and can still store responses. `no-store` is the directive intended for responses that must not be stored in browser or intermediary caches. ClipVault's local Web UI/API returns personal clipboard, memory, and device data, so relying on default browser caching leaves avoidable local privacy residue. | **Adopt now:** send `Cache-Control: no-store` from the shared desktop API/Web UI security-header path, plus `Pragma: no-cache` and `Expires: 0` for conservative compatibility. Apply it globally to local UI/API responses because the UI is small and most routes can expose personal data. |
| R31 | Release artifact upload missing-file hard fail | actions/upload-artifact README (`https://github.com/actions/upload-artifact`), OpenSSF GitHub workflow attack-vector guidance (`https://openssf.org/blog/2024/08/12/mitigating-attack-vectors-in-github-workflows/`) | `actions/upload-artifact` defaults to warning, not failing, when the configured path matches no files. OpenSSF treats workflows that produce sensitive build/release artifacts as part of the supply-chain attack surface, so release evidence should fail closed when an expected artifact path is empty or mistyped. | **Adopt now:** set `if-no-files-found: error` on ClipVault release-candidate and release artifact upload steps and guard it in `desktop/tests/test_release_alignment.py`. This does not sign, publish, or alter release semantics; it only prevents warning-only missing artifact uploads. |

## Research log - round 11 (2026-07-03)

Scope filter: serve v1.7 release-chain and Android sync-credential hardening
only. Do not change Android IME behavior, sync payload semantics, runtime
dependencies, typed-text policy, analytics policy, signing authority, or
artifact publication semantics.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R32 | GitHub Actions `GITHUB_TOKEN` least-privilege regression gate | GitHub Docs on `GITHUB_TOKEN` authentication (`https://docs.github.com/actions/reference/authentication-in-a-workflow`), GitHub Actions secure-use reference (`https://docs.github.com/en/actions/reference/security/secure-use`), OpenSSF workflow attack-vector guidance (`https://openssf.org/blog/2024/08/12/mitigating-attack-vectors-in-github-workflows/`), GitHub build-system security best practices (`https://docs.github.com/enterprise-cloud@latest/code-security/tutorials/implement-supply-chain-best-practices/securing-builds`) | GitHub documents that `GITHUB_TOKEN` can be accessed through the `github.token` context and should be limited to the minimum permissions needed. GitHub's secure-use reference recommends defaulting the token to read-only repository contents and escalating only for individual jobs that need more. OpenSSF treats minimal token permissions as a recurring mitigation for workflow compromise, vulnerable actions, release workflow attacks, and cache-related lateral movement. | **Adopt now:** keep every workflow's top-level default at `contents: read`, forbid write-scoped token permissions in CI and release-candidate dry runs, and add a static release-alignment test that allows formal release write scopes only for provenance attestation (`attestations: write`, `id-token: write`) and optional draft GitHub Release creation (`contents: write`). This is a regression gate around the current secure shape, not a release behavior change. |
| R33 | Release-candidate path-filter coverage for invoked verifier scripts | GitHub Actions events docs (`https://docs.github.com/actions/using-workflows/events-that-trigger-workflows`), GitHub workflow syntax docs (`https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions`) | GitHub Actions `pull_request.paths` filters decide whether the workflow runs for file changes; when combined with branch filters, both must match. A release-candidate workflow that runs `scripts/verify_release_manifest.py` but does not include that script in its PR path filter can skip the dry-run packaging evidence on verifier-only PRs. | **Adopt now:** include `scripts/verify_release_manifest.py` in the release-candidate PR paths and add a static test that every repository `scripts/*.py` invoked by the release-candidate workflow is covered by that workflow's path filter. This changes only trigger coverage for release-verifier edits, not release publication or signing semantics. |
| R34 | Android sync bearer token host-switch leakage | RFC 6750 Bearer Token Usage (`https://datatracker.ietf.org/doc/html/rfc6750`), OWASP REST Security Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html`), OWASP API Security Project (`https://owasp.org/www-project-api-security/`) | Bearer tokens grant access to whoever possesses them, so clients must protect them from disclosure in storage and transport. ClipVault's Android pairing UI wrote a newly entered host before `/api/pair` returned a fresh token; if the device already had an old token and the new pairing failed, a background sync worker could send the old bearer token to the newly entered host. | **Adopt now:** pair against a temporary host and commit host/token only after token redemption succeeds. Commit fail-closed by clearing the token, writing the host, then writing the fresh token, and guard the source shape with a desktop static test. This does not change sync payload semantics, IME behavior, or token format. |
| R35 | Local Web UI global-object clobbering hygiene | OWASP DOM Clobbering Prevention Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/DOM_Clobbering_Prevention_Cheat_Sheet.html`), PortSwigger DOM clobbering guide (`https://portswigger.net/web-security/dom-based/dom-clobbering`), DOMC named window access notes (`https://domclob.xyz/domc_wiki/techniques/windowNamedAccess.html`) | Browsers expose some named DOM elements as properties on `window`/`document`, and DOM clobbering attacks exploit that namespace behavior to steer JavaScript logic when HTML injection is possible. ClipVault's Web UI already avoids parsing API data as HTML, but its search debounce timer still used `window._t`, an unnecessary global property dependency. | **Adopt now:** keep Web UI state module-local instead of writing custom state onto `window`, and guard this with `test_webui_security.py`. This is defense-in-depth only; it does not change API behavior, CSP, sync, or storage semantics. |

## Research log - round 12 (2026-07-03)

Scope filter: serve v1.7 sync credential and release-chain stability only. Do
not change Android IME behavior, sync payload semantics, runtime dependencies,
typed-text policy, analytics policy, signing authority, or artifact publication
semantics.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R36 | Android sync bearer token redirect scope | Java `HttpURLConnection` docs (`https://docs.oracle.com/javase/8/docs/api/java/net/HttpURLConnection.html`), RFC 6750 bearer token usage (`https://datatracker.ietf.org/doc/html/rfc6750`), OWASP REST Security Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html`) | `HttpURLConnection` follows redirects by default, while bearer tokens grant access to whoever possesses them and should stay scoped to the intended resource server. ClipVault's desktop sync API has no redirect semantics, so following 3xx responses is unnecessary for normal LAN/Tailscale sync. | **Adopt now:** set `instanceFollowRedirects = false` before adding the Android sync `Authorization` header and guard the source shape in `test_android_privacy_manifest.py`. Treat any 3xx as a sync failure/retry instead of silently changing the request target. |
| R37 | GitHub release-gate auto-close hygiene | GitHub Docs linked pull request keywords (`https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue`), GitHub Community discussion on linked-issue closure (`https://github.com/orgs/community/discussions/66741`), Stack Overflow closing-keyword syntax discussion (`https://stackoverflow.com/questions/60027222/github-how-can-i-close-the-two-issues-with-commit-message`) | GitHub interprets supported keywords in PR descriptions or commit messages targeting the default branch and auto-closes the referenced issue when merged. That is useful for ordinary bugs but unsafe for release-gate tracker issues where one defense-in-depth PR should not close the gate before signed artifacts and manual QA evidence exist. | **Adopt now:** add PR-template guidance to avoid auto-close keywords directly before release-gate issue references, guard the template with `test_release_alignment.py`, and keep #36 closure manual until all owner-controlled evidence exists. |
| R38 | Artifact action Node 24 runtime floor | GitHub Actions Node 20 deprecation changelog (`https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/`), `actions/upload-artifact` README (`https://github.com/actions/upload-artifact`), `actions/download-artifact` README (`https://github.com/actions/download-artifact`) | GitHub is moving runner JavaScript actions to Node 24 and tells workflow users to update to action versions that run on Node 24. The official artifact action README examples now show `upload-artifact@v7` and `download-artifact@v8`; ClipVault already guarded several official action majors but still allowed stale artifact action majors, especially `download-artifact@v4` in the draft release job. | **Adopt now:** update release-candidate/release artifact uploads to `upload-artifact@v7`, update draft-release artifact downloads to `download-artifact@v8`, and extend `test_release_alignment.py` so future artifact-action downgrades fail locally. This changes only the CI action runtime floor; it does not sign, publish, alter release triggers, or change artifact contents intentionally. |

## Research log - round 13 (2026-07-03)

Scope filter: serve v1.7 local-first sync reliability only. Do not change
Android IME behavior, sync payload semantics, runtime dependencies, typed-text
policy, analytics policy, signing authority, or artifact publication semantics.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R39 | Android sync pull cursor progress guard | JSON:API cursor pagination profile (`https://jsonapi.org/profiles/ethanresnick/cursor-pagination/`), Slack Web API pagination docs (`https://docs.slack.dev/apis/web-api/pagination/`), Relay Cursor Connections specification (`https://relay.dev/graphql/connections.htm`), Zendesk cursor pagination docs (`https://developer.zendesk.com/documentation/api-basics/pagination/paginating-through-lists-using-cursor-pagination/`) | Cursor pagination contracts separate page data from the cursor/has-more metadata that tells the client how to continue and when to stop. ClipVault's Android client already bounds response size, but an abnormal peer response with events or `has_more=true` and a non-advancing `next_seq` could make the worker repeat the same pull page. | **Adopt now:** validate `next_seq` before applying pulled events. If a page has events or claims `has_more=true`, the cursor must advance beyond the requested `since_seq`; regressions or non-advancing pages fail closed and retry. This is a client-side livelock guard only; it does not change desktop pull semantics, event payloads, IME behavior, or release state. |

## Research log - round 14 (2026-07-04)

Scope filter: serve v1.6 release-chain evidence only. Do not change Android IME
behavior, sync semantics, runtime dependencies, typed-text policy, analytics
policy, signing authority, or artifact publication semantics.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R40 | Downloaded release artifact handoff verification | GitHub Docs artifact storage/validation (`https://docs.github.com/actions/tutorials/store-and-share-data`), GitHub artifact digest changelog (`https://github.blog/changelog/2025-03-18-github-actions-now-supports-a-digest-for-validating-your-artifacts-at-runtime/`), GitHub artifact attestations docs (`https://docs.github.com/en/actions/concepts/security/artifact-attestations`), OpenSSF GitHub workflow attack-vector guidance (`https://openssf.org/blog/2024/08/12/mitigating-attack-vectors-in-github-workflows/`) | GitHub artifact download computes and compares the uploaded artifact digest, but digest mismatch is surfaced as a workflow warning, not as ClipVault's release manifest/checksum hard-fail gate. ClipVault already verifies staged artifacts before upload; the remaining handoff is the downloaded artifact directories used by `gh release create`. | **Adopt now:** re-run `scripts/verify_release_manifest.py` in the draft release job after `actions/download-artifact` and before `gh release create`, including `--require-signed` for Android. This is a post-download handoff check only; it does not sign artifacts, publish a non-draft release, change release triggers, or close Issue #36. |
| R41 | Draft release asset-name collision guard | GitHub REST release assets docs (`https://docs.github.com/rest/releases/assets`), GitHub CLI `gh release upload` manual (`https://cli.github.com/manual/gh_release_upload`), GitHub CLI issue #7178 (`https://github.com/cli/cli/issues/7178`) | GitHub Release asset names must be unique, and the CLI `--clobber` path deletes existing assets before uploading replacements. ClipVault's draft-release job flattens downloaded platform artifact directories into `upload-assets/`; future same-basename additions could overwrite locally before GitHub reports a duplicate asset name. | **Adopt now:** fail before copying if `upload-assets/${asset}` already exists. Keep existing platform prefixes for `SHA256SUMS.txt` and `RELEASE_MANIFEST.json`. This is local staging hardening only; it does not sign artifacts, publish a release, change artifact contents, or close Issue #36. |
| R42 | Android signed APK evidence completeness | Android `apksigner` docs (`https://developer.android.com/tools/apksigner`), OWASP MASTG APK signature inspection (`https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0116/`), GitHub artifact attestations docs (`https://docs.github.com/en/actions/concepts/security/artifact-attestations`) | Android documents `apksigner verify` as the supported way to confirm an APK signature will verify on target Android versions, and MASTG uses `apksigner verify --print-certs --verbose` to inspect signer certificate evidence. GitHub artifact attestations prove build provenance, but they do not replace Android APK signature verification output. | **Adopt now:** when `scripts/verify_release_manifest.py` verifies a manifest whose platform is Android with `--require-signed`, require both an APK artifact and a non-empty `ANDROID_APKSIGNER_VERIFY.txt` in the manifest-checked artifact set. This does not create signatures or publish a release; it only hard-fails missing signed-APK evidence. |

## Research log - round 15 (2026-07-04)

Scope filter: serve v1.7 sync credential hardening only. Do not change Android
IME behavior, sync payload semantics, runtime dependencies, typed-text policy,
analytics policy, signing authority, artifact publication semantics, or the
existing fixed-port sync UI.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R43 | Android sync pairing host shape validation | OWASP SSRF Prevention Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html`), OWASP Input Validation Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html`), OWASP Top 10 SSRF page (`https://owasp.org/Top10/2021/A10_2021-Server-Side_Request_Forgery_%28SSRF%29/`), Android security tips (`https://developer.android.com/privacy-and-security/security-tips`) | Even though ClipVault's sync target is user-entered rather than server-controlled, it is still the destination used for bearer-token pairing and later sync. OWASP guidance favors allowlist validation for structured input and URL destinations; the Android pairing UI currently asks for a desktop IP/host, not a full URL or custom port. | **Adopt now:** normalize the pairing host before building sync URLs. Allow plain LAN/DNS hostnames and bracketed IPv6; reject scheme, path, query, fragment, userinfo, whitespace, and `host:port`-style ambiguity. This keeps tokens scoped to an explicit host and does not add discovery, TLS, new dependencies, IME networking, or custom-port UI semantics. |
| R44 | LAN pairing device-name metadata bounds | OWASP Developer Guide input validation checklist (`https://devguide.owasp.org/en/04-design/02-web-app-checklist/05-validate-inputs/`), CWE-20 Improper Input Validation (`https://cwe.mitre.org/data/definitions/20.html`), LocalSend protocol (`https://github.com/localsend/protocol`) | LAN pairing metadata is not the authentication root, but it is still untrusted input that is stored in SQLite and surfaced in the management UI. Local-first sharing tools expose device aliases/model/fingerprint as protocol metadata, while general input-validation guidance still requires type, length, and character-boundary checks before storage. | **Adopt now:** validate ClipVault's `device_name` before redeeming a one-time pair code. Missing/blank names default to `device`; valid names are trimmed and may contain Unicode; non-string, overlong, or control-character values fail before code redemption. This does not change `device_id`, token format, sync payloads, Android IME behavior, or pairing UX for normal device names. |
| R45 | Flat release artifact evidence set | GitHub Actions artifact docs (`https://docs.github.com/actions/tutorials/store-and-share-data`), GitHub Actions artifact digest changelog (`https://github.blog/changelog/2025-03-18-github-actions-now-supports-a-digest-for-validating-your-artifacts-at-runtime/`), SLSA provenance verification docs (`https://slsa.dev/verification_summary`) | GitHub artifact upload/download can preserve files under artifact directories, while ClipVault's manifest/checksum scripts define a flat set of named release files. If a future staging step accidentally leaves nested files, a recursive release upload path could publish bytes that were not represented in `RELEASE_MANIFEST.json` or `SHA256SUMS.txt`. | **Adopt now:** make release manifest generation and verification reject nested artifact directories. Keep v1.6 artifacts as a flat file set; if future releases need subdirectories, extend the manifest schema first instead of silently excluding them from checksums. |

## Research log - round 16 (2026-07-04)

Scope filter: serve v1.6 release-chain evidence only. Do not change Android IME
behavior, sync semantics, runtime dependencies, typed-text policy, analytics
policy, signing authority, or artifact publication semantics.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R46 | Hidden release artifact name mismatch | `actions/upload-artifact` README (`https://github.com/actions/upload-artifact`), GitHub Actions hidden-file breaking-change notice (`https://github.blog/changelog/2024-08-19-notice-of-upcoming-deprecations-and-breaking-changes-in-github-actions-runners/`), Stack Overflow hidden-artifact regression report (`https://stackoverflow.com/questions/79385601/github-actions-does-not-upload-bin-folder-to-artifacts`) | GitHub's official artifact upload action ignores hidden files by default to avoid accidentally uploading sensitive information. ClipVault's release manifest/checksum scripts define the file set that release jobs later upload/download; allowing a dotfile artifact name would make it possible for local manifest evidence to mention a file that the default upload policy can omit. | **Adopt now:** reject artifact names beginning with `.` in both manifest generation and verification. Keep v1.6 release artifacts as visible flat files; if a future release genuinely needs hidden files, redesign the manifest and upload policy together instead of relying on default action behavior. |

## Research log - round 17 (2026-07-04)

Scope filter: serve v1.6/v1.7 local-first sync reliability only. Do not change
Android IME behavior, sync payload shape, runtime dependencies, typed-text
policy, analytics policy, signing authority, artifact publication semantics, or
the explicit-save boundary.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R47 | Duplicate remote event sequence hardening | Matrix Client-Server API transaction IDs (`https://spec.matrix.org/latest/client-server-api/`), Apache Kafka producer idempotence docs (`https://kafka.apache.org/41/configuration/producer-configs/#enable.idempotence`), Automerge repository/sync implementation (`https://github.com/automerge/automerge-repo`) | Mature sync/message systems treat retries and duplicate sends as normal fault cases and use explicit transaction IDs, producer sequence guarantees, or sync-layer deduplication so one logical event cannot be applied twice. ClipVault's desktop sync API already stores a per-peer cursor, but a malformed peer could put two different payloads under the same `seq` in one push batch before the cursor is persisted. | **Adopt now:** make desktop `apply_push()` apply at most one event per `seq` in a single peer batch, log later duplicates, and keep seq-valid malformed SQLite integrity conflicts as acknowledged no-ops. This preserves the existing fail-soft no-wedge contract and does not alter Android payload format, IME behavior, network scope, or normal client sync semantics. |

## Research log - round 18 (2026-07-04)

Scope filter: serve v1.6 release-state truthfulness and v1.7 planning clarity
only. Do not change Android IME behavior, sync semantics, runtime dependencies,
typed-text policy, analytics policy, signing authority, artifact publication
semantics, or GitHub Release creation.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R48 | README and architecture release-state drift guard | GitHub Releases docs (`https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases`), GitHub release management docs (`https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository`), Keep a Changelog (`https://keepachangelog.com/en/1.1.0/`), Release Drafter (`https://github.com/release-drafter/release-drafter`) | GitHub treats Releases as deployable software iterations with downloadable assets, while changelog practice separates unreleased changes from published releases. ClipVault's README still mixed current source-tree progress, stale fixed test counts, and wording that could imply a current signed Android APK existed even though Issue #36 still lacks signed artifacts, manual QA, and a `v1.6.0` Release. The architecture doc also still named the retired FastAPI/WebSocket/syncserver plan, which makes future v1.7 planning start from the wrong implementation map. | **Adopt now:** make README state that source metadata is `1.6.0` but `v1.6.0` binaries are not published, keep `v1.5.10` as the latest published binary, point the remaining gate to Issue #36, and update ARCHITECTURE to the current stdlib HTTPServer + HTTP push/pull + Android HttpURLConnection implementation. Guard both release-state and runtime-topology facts in `test_release_alignment.py`. This is a docs/test truthfulness gate only; it does not sign, publish, create a release, or change runtime behavior. |

## Research log - round 19 (2026-07-04)

Scope filter: serve Android IME explicit-save privacy only. Do not change IME
runtime behavior, sync semantics, runtime dependencies, typed-text policy,
analytics policy, signing authority, artifact publication semantics, or the
existing explicit user action boundary.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R49 | Panel IME explicit-save clipboard privacy source guard | Android IME guide (`https://developer.android.com/develop/ui/views/touch-and-input/creating-input-method`), Android input type guide (`https://developer.android.com/develop/ui/views/touch-and-input/keyboard-input/style`), Android `EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING` docs (`https://developer.android.com/reference/android/view/inputmethod/EditorInfo#IME_FLAG_NO_PERSONALIZED_LEARNING`), Android `InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS` and password variations docs (`https://developer.android.com/reference/android/text/InputType`), FlorisBoard privacy-oriented Android keyboard (`https://github.com/florisboard/florisboard`) | Android treats an IME as the system-wide text-entry surface and exposes editor metadata for passwords, no-suggestion fields, and no-personalized-learning contexts. ClipVault already suppresses candidates and explicit saves through `ImePrivacySession`, but existing tests mostly covered the helper/session rather than the concrete `ClipVaultPanelImeService.saveClipboard()` ordering. A future refactor could accidentally read the clipboard before checking the current editor context, or write after a session change without re-checking. | **Adopt now:** add a host-JVM source-shape test for `ClipVaultPanelImeService.saveClipboard()` requiring the privacy token and `allowsPersonalData(token)` guard before any clipboard read, and a second `allowsPersonalData(token)` guard inside the worker before `runtime.saveExplicit(...)`. This is a regression guard only; it does not change IME runtime behavior, typed-text policy, sync behavior, release state, or user-visible save semantics. |
| R50 | Oversized single sync pull event fail-closed response | IANA HTTP status code registry (`https://www.iana.org/assignments/http-status-codes`), RFC 9110 HTTP semantics via the registry reference (`https://www.rfc-editor.org/rfc/rfc9110.html#name-413-content-too-large`), Android `HttpURLConnection` docs (`https://developer.android.com/reference/java/net/HttpURLConnection`), ClipVault Android bounded response reader (`android/app/src/main/kotlin/com/clipvault/app/sync/Sync.kt`) | ClipVault pages pull responses by event count and byte budget, and Android rejects responses above its bounded reader limit. The desktop pull builder already pages when adding another event would exceed the budget, but if the first available event itself exceeded the budget it could still be returned as an oversized response. That makes Android retry after reading too much data and does not produce a clear desktop-side protocol error. | **Adopt now:** if the first sendable outbox event cannot fit inside the pull response byte budget, return a bounded HTTP 413 `sync_event_too_large` error from `/api/sync/pull` without advancing cursors or deleting/skipping the event. Normal multi-event pagination still returns the preceding page first. This is a fail-closed compatibility guard only; it does not change normal sync payloads, Android IME behavior, typed-text policy, release state, or publication semantics. |

## Research log - round 20 (2026-07-04)

Scope filter: serve v1.7 Secret Guard depth only. Do not add live token
verification, network calls, analytics, typed-text logging, IME behavior
changes, sync payload changes, runtime dependencies, signing authority changes,
or artifact publication semantics.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R51 | Hugging Face access-token Secret Guard parity | Hugging Face user access token docs (`https://huggingface.co/docs/hub/en/security-tokens`), Hugging Face Hub quickstart/authentication docs (`https://huggingface.co/docs/huggingface_hub/en/quick-start`), Hugging Face secrets-scanning docs (`https://huggingface.co/docs/hub/en/security-secrets`), Trivy Hugging Face detector issue with revoked example shape (`https://github.com/aquasecurity/trivy/issues/6823`), GitGuardian Hugging Face detector docs (`https://docs.gitguardian.com/secrets-detection/secrets-detection-engine/detectors/specifics/hugging_face_user_access_token`) | Hugging Face documents user access tokens as credentials for applications, notebooks, git/basic auth, and bearer-token use, with examples using the `hf_` prefix. Hugging Face and GitGuardian both treat Hugging Face access tokens as first-class secret-scanning targets, and Trivy's public detector issue highlights the common `hf_` plus 34-character body shape. ClipVault previously relied on the generic entropy fallback, which can produce a weaker `suspect` verdict or miss lower-entropy provider-shaped tokens. | **Adopt now:** add a high-confidence `SG-HUGGINGFACE` hard rule for `hf_` plus a 34-character alphanumeric body in both Python and Kotlin Secret Guard implementations, with mirrored provider tests and negative cases for ordinary `hf_` notes/short prefixes. This is local deterministic scanning only; it performs no live verification and does not change IME, sync, release, or publication behavior. |

## Research log - round 21 (2026-07-04)

Scope filter: serve v1.6 release evidence and v1.7 reliability/QA
guardrails only. Do not change Android IME behavior, sync payload shape,
runtime sync dependencies, typed-text policy, analytics policy, signing
authority, or artifact publication semantics.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R52 | User-visible sync blocked diagnostics | Syncthing synchronization docs (`https://docs.syncthing.net/users/syncing.html`), Android WorkManager constraints docs (`https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started/define-work`) | Mature local-first/background-sync systems distinguish transient retry/defer states from conditions that require visible user or operator action. Syncthing reports conflicts that cannot be synchronized until the user resolves the underlying file-name state, while WorkManager documents that work can be stopped and retried when constraints become unmet. ClipVault already fail-closes a single oversized pull event with HTTP 413, but the desktop status panel did not explain why a paired device would keep failing to pull. | **Adopt now:** expose a content-safe `sync.blocked_pull` summary through `/api/status` and the local Web UI. Include only code, first sequence, byte budget, actual event size, and affected peer count; do not include clip text, payload fields, bearer tokens, hostnames, or device IDs. |
| R53 | Current-main release-candidate evidence | GitHub Actions push event docs (`https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows`), GitHub workflow syntax path-filter docs (`https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax`) | GitHub Actions can run workflows on `push`, and branch/path filters combine conjunctively when both are defined. ClipVault's release-candidate dry run already covered release-path PRs, but Issue #36 needs evidence tied to the exact main commit, not just a PR head or manually dispatched fallback. | **Adopt now:** add a `push` trigger for `main` to the unsigned release-candidate dry-run workflow, keep PR path filters for cost control, and guard the main-push path with a static test that forbids release environments, secrets, `contents: write`, or `gh release` side effects. |
| R54 | Android residual QA source compile gate | AndroidJUnitRunner docs (`https://developer.android.com/training/testing/instrumented-tests/androidx-test-libraries/runner`) | Android documents `AndroidJUnitRunner` as the runner for instrumented JUnit 4 tests on Android devices and notes it supports Espresso/UI Automator-style instrumented tests. ClipVault has residual IME manual-QA scaffolds in `androidTest`, but if CI never compiles that source set the backlog can silently rot before a future device/emulator cycle. | **Adopt now:** configure the Android test runner and AndroidX Test dependencies, compile `:app:compileDebugAndroidTestKotlin` in CI, and keep the tests `@Ignore` so this compile gate does not pretend to satisfy Owner/manual device QA for Issue #36. |

## Research log - round 22 (2026-07-04)

Scope filter: serve v1.7 Android sync reliability only. Do not change Android
IME behavior, sync payload shape, runtime dependencies, typed-text policy,
analytics policy, signing authority, artifact publication semantics, or the
release gate for Issue #36.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R55 | Android sync auth-failure retry boundary | Android WorkManager getting-started docs (`https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started`), Android WorkManager retry/backoff docs (`https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started/define-work`), RFC 6750 bearer-token usage (`https://datatracker.ietf.org/doc/html/rfc6750`) | WorkManager treats `Result.retry()` as a request to reschedule failed work according to retry/backoff policy, while `Result.success()` finishes the current one-time work. RFC 6750 treats bearer-token failures as auth-specific states: invalid/expired/revoked tokens use HTTP 401 and insufficient scope uses HTTP 403. ClipVault's Android sync client collapsed every non-200 response into retry/null sentinels, so a revoked desktop pairing token could keep scheduling immediate backoff retries instead of requiring re-pair. | **Adopt now:** classify 401/403 from authenticated sync endpoints as permanent local auth failure, clear the Android stored bearer token, and return `Result.success()` to stop the immediate WorkManager retry chain. Keep network errors, 413 oversized-response failures, rate limits, and 5xx responses on the existing retry path. |

## Research log - round 23 (2026-07-04)

Scope filter: serve v1.7 Android IME privacy/source-boundary stability only.
Do not change IME runtime behavior, sync payload semantics, runtime
dependencies, typed-text policy, analytics policy, signing authority, artifact
publication semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R56 | IME frontend source-boundary regression gate | Android Create an input method docs (`https://developer.android.com/develop/ui/views/touch-and-input/creating-input-method`), Android `InputMethodService` API reference (`https://developer.android.com/reference/kotlin/android/inputmethodservice/InputMethodService`), AOSP `InputMethodService.java` lifecycle comments (`https://android.googlesource.com/platform/frameworks/base/+/2963252/core/java/android/inputmethodservice/InputMethodService.java`), Citizen Lab keyboard-app vulnerability research (`https://citizenlab.ca/research/vulnerabilities-across-keyboard-apps-reveal-keystrokes-to-network-eavesdroppers/`), HackTricks IME abuse notes (`https://hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/inputmethodservice-ime-abuse.html`) | Android frames an IME as a system-wide text-entry surface and exposes lifecycle callbacks for each input target. AOSP guidance says IMEs should reset/initialize state when the target editor changes. Public security research and pentest notes show why keyboard apps are a high-risk boundary: once enabled, an IME can observe input across apps, and IME/network combinations have leaked typed content in the wild. ClipVault already keeps current IME source thin, but its source-boundary test only blocked network/sync/logging paths, leaving future direct Room/Capture/SecretGuard/file/preference access unguarded. | **Adopt now:** extend the host-JVM `ImeSourceBoundaryTest` so IME sources must remain thin frontends that call the Runtime facade instead of importing data/capture/core/database/sync/network/logging/persistence APIs directly. This is a regression gate only; it does not change IME runtime behavior or claim device/manual QA completion. |

## Research log - round 24 (2026-07-04)

Scope filter: serve v1.7 Android IME in-flight privacy only. Do not change
normal candidate semantics, sync payloads, runtime dependencies, typed-text
policy, analytics policy, signing authority, artifact publication semantics, or
Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R57 | IME candidate worker pre-read privacy check | AOSP `InputMethodService.java` target-switch lifecycle comments (`https://android.googlesource.com/platform/frameworks/base/+/HEAD/core/java/android/inputmethodservice/InputMethodService.java`), Android `InputMethodService.onStartInput` docs via API reference (`https://developer.android.com/reference/android/inputmethodservice/InputMethodService#onStartInput(android.view.inputmethod.EditorInfo,%20boolean)`), FlorisBoard privacy-focused open-source keyboard (`https://github.com/florisboard/florisboard`), AnySoftKeyboard privacy/open-source keyboard project (`https://anysoftkeyboard.github.io/`) | Android calls `onFinishInput()` / `onStartInput()` when the user switches input targets, and AOSP explicitly recommends clearing or reinitializing input state for the current target. Privacy-focused open-source keyboards treat the keyboard as a sensitive local-first surface. ClipVault already invalidates stale UI application with `ImePrivacySession`, but a worker that was launched in an ordinary editor could still read local Runtime candidates after the field changed to a sensitive editor, even though it would discard the result later. | **Adopt now:** make both Panel IME and Full Keyboard candidate workers re-check the captured privacy token immediately before reading Runtime candidates. This avoids unnecessary local candidate reads after a sensitive-editor transition while preserving existing post-read UI discard checks and normal candidate behavior. |

## Research log - round 25 (2026-07-04)

Scope filter: serve v1.7 Android IME manifest exposure stability only. Do not
change IME runtime behavior, sync payloads, runtime dependencies, typed-text
policy, analytics policy, signing authority, artifact publication semantics, or
Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R58 | IME service manifest exposure regression gate | Android Create an input method docs (`https://developer.android.com/develop/ui/views/touch-and-input/creating-input-method`), Android `<service>` manifest docs (`https://developer.android.com/guide/topics/manifest/service-element`), Android `Manifest.permission.BIND_INPUT_METHOD` reference (`https://developer.android.com/reference/android/Manifest.permission#BIND_INPUT_METHOD`), AOSP `InputMethodManager.java` binding comments (`https://android.googlesource.com/platform/frameworks/base.git/+/refs/heads/master/core/java/android/view/inputmethod/InputMethodManager.java`) | Android IMEs are declared as services with `BIND_INPUT_METHOD`, an `android.view.InputMethod` intent filter, and `android.view.im` metadata. `BIND_INPUT_METHOD` is a signature permission that must be required by `InputMethodService` so only the system can bind; AOSP also documents that the framework refuses to bind to IME services that do not require it. ClipVault's manifest already follows this shape for both IME services, but no test would catch a future manifest edit that drops the permission, adds non-IME actions/data/categories, or registers an unreviewed third IME service. | **Adopt now:** add a desktop static manifest test that locks the two known ClipVault IME services to the Android IME service shape: exported system IME service, `BIND_INPUT_METHOD`, exactly one `android.view.InputMethod` action, no intent categories/data, and `android.view.im` metadata pointing at the expected config XML. This is a regression gate only; it does not change manifest semantics or claim device/manual QA completion. |

## Research log - round 26 (2026-07-04)

Scope filter: serve v1.7 stable-exit planning only. Do not change IME runtime
behavior, sync payloads, runtime dependencies, typed-text policy, analytics
policy, signing authority, artifact publication semantics, or Issue #36 manual
QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R59 | v1.7 stable exit criteria and evidence tiers | Android UI Automator docs (`https://developer.android.com/training/testing/other-components/ui-automator`), GitHub environment deployment docs (`https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment`), GitHub deployment/environment reference (`https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments`), HeliBoard privacy/offline keyboard repo (`https://github.com/HeliBorg/HeliBoard`), HeliBoard F-Droid listing (`https://f-droid.org/en/packages/helium314.keyboard/`) | Android's UI Automator is the right device-test layer for IME interactions because it can drive user apps and system UI, but a compiled `androidTest` scaffold is still weaker evidence than an executed device/emulator run. GitHub environments provide the right owner-controlled approval/secrets boundary for signed release workflows. Privacy-first Android keyboards such as HeliBoard treat offline/no-Internet behavior as a product boundary, which matches ClipVault's IME-local-first rule. | **Adopt now:** convert the v1.7 stability plan from theme bullets into an explicit stable-exit matrix with automated, CI, and Owner/manual evidence columns. Add a static test so future docs cannot confuse compile-only QA scaffolds with executed device QA, unsigned dry-run artifacts with signed releases, or planning labels with a published/stable `v1.7.0`. This is planning/test truthfulness only; it does not publish, sign, run manual QA, or change runtime behavior. |

## Research log - round 27 (2026-07-04)

Scope filter: serve v1.6 release evidence integrity and v1.7 stable-gate
truthfulness only. Do not change runtime behavior, Android IME behavior, sync
payload semantics, version metadata, signing authority, artifact publication
semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R60 | Release manifest state-machine invariants | GitHub artifact attestations docs (`https://docs.github.com/en/actions/concepts/security/artifact-attestations`), SLSA provenance v1 (`https://slsa.dev/provenance/v1`), GitHub release management docs (`https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository`), F-Droid reproducible builds docs (`https://f-droid.org/en/docs/Reproducible_Builds/`), GitHub release asset digest changelog (`https://github.blog/changelog/2025-06-03-releases-now-expose-digests-for-release-assets/`) | Mature release evidence separates artifact bytes, provenance/checksums, signing, draft/published release state, and reproducibility. ClipVault already verifies file hashes and Android apksigner evidence, but the local manifest helpers still allowed contradictory metadata such as a `release-candidate-dry-run` manifest marked `signed=true` or `published=true` if a caller passed the wrong flags. | **Adopt now:** make manifest generation reject dry-run manifests marked signed or published, and make manifest verification reject illegal `kind` values plus signed/published dry-run metadata even when the caller is not explicitly using `--expect-dry-run`. This is release-evidence semantics hardening only; it does not sign, publish, create a GitHub Release, or close Issue #36. |

## Research log - round 28 (2026-07-04)

Scope filter: serve v1.7 Android log privacy and stable-exit evidence only. Do
not change runtime logging behavior, Android IME behavior, sync payloads,
runtime dependencies, typed-text policy, analytics policy, signing authority,
artifact publication semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R61 | Android production log privacy regression gate | Android Log Info Disclosure guidance (`https://developer.android.com/privacy-and-security/risks/log-info-disclosure`), Android security best practices (`https://developer.android.com/privacy-and-security/security-best-practices`), OWASP MASWE-0001 sensitive data in logs (`https://mas.owasp.org/MASWE/MASVS-STORAGE/MASWE-0001/`) | Android advises sanitizing non-debug Logcat output and removing data that may be sensitive. OWASP identifies sensitive data in mobile app/system logs as a confidentiality risk and recommends avoiding, redacting, or removing nonessential production logging. ClipVault already has desktop log-hygiene tests and Android logs currently use constant messages or exception class names, but no Android app source gate would fail if a future change interpolated clip text, memory text, bearer tokens, sync payloads, hosts, or raw stack traces into production logs. | **Adopt now:** add an Android host-JVM source-shape test that allows only constant production `Log.*` messages or exception class-name interpolation and rejects dynamic message interpolation/concatenation plus `printStackTrace()`. Add this evidence to the v1.7 stable exit matrix. This is a regression gate only; it does not change runtime behavior or replace Owner/manual device log QA. |

## Research log - round 29 (2026-07-04)

Scope filter: serve v1.7 Android sync credential-state and explicit-capture
scheduling hardening only. Do not change Android IME behavior, sync payloads,
runtime dependencies, typed-text policy, analytics policy, signing authority,
artifact publication semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R62 | Android re-pair host/token write ordering | Android SharedPreferences guide (`https://developer.android.com/training/data-storage/shared-preferences`), Stack Overflow `commit()`/`apply()` discussion (`https://stackoverflow.com/questions/5960678/whats-the-difference-between-commit-and-apply-in-sharedpreferences`) | Android documents `SharedPreferences` as the framework key-value store for small private settings and saves editor changes with `apply()` or `commit()`. Community guidance consistently distinguishes the return-value/failure-reporting behavior: `apply()` writes asynchronously while `commit()` writes synchronously and returns success. ClipVault's `replacePairing()` intended to clear the old token, write the new host, and only then store the fresh token, but using async `apply()` for the host step left the fail-closed ordering dependent on a background write. | **Adopt now:** use synchronous `commit()` for the new-host preference write in `replacePairing()` and fail closed by keeping the token cleared if that write fails. Add a host-JVM source-shape test so future edits cannot store a fresh token before the host commit succeeds. This is a pairing-state consistency guard only; it does not change sync protocol, token format, IME behavior, or release state. |
| R63 | Android explicit-capture sync push scheduling | Android background tasks overview (`https://developer.android.com/develop/background-work/background-tasks`), Android WorkManager work request docs (`https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started/define-work`) | Android warns that choosing the wrong background-work API or unnecessary background work can hurt performance and resource efficiency. WorkManager requires creating/enqueuing a `WorkRequest`; ClipVault uses that path for local-first sync. Android capture already gates secret clips out of the outbox, but Share/QS/Runtime callers still requested an immediate sync push even for rejected, duplicate, or newly secret captures where no public outbox event exists. | **Adopt now:** make `Capture.Result` expose `shouldRequestSyncPush`, then gate Runtime explicit save, Share target, and QS Tile sync scheduling on a new public outbox event. Sync-now scheduling is best-effort so WorkManager enqueue failure does not turn a completed local capture into a false save failure. Keep duplicate/rejected/secret local behavior unchanged, and keep periodic sync as fallback. This is a WorkManager scheduling/noise guard only; it does not change capture classification, Secret Guard, outbox payload semantics, IME typed-text policy, or release state. |

## Research log - round 30 (2026-07-04)

Scope filter: serve v1.7 Android sync reliability only. Do not change Android
IME behavior, sync payloads, runtime dependencies, typed-text policy, analytics
policy, signing authority, artifact publication semantics, or Issue #36 manual
QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|
| R64 | Android immediate sync unique-work policy | Android WorkManager manage-work docs (`https://developer.android.com/develop/background-work/background-tasks/persistent/how-to/manage-work`), Android `ExistingWorkPolicy` API reference (`https://developer.android.com/reference/androidx/work/ExistingWorkPolicy`), Android update-work guidance (`https://developer.android.com/develop/background-work/background-tasks/persistent/how-to/update-work`) | Android recommends unique work to avoid duplicate background tasks, and its `ExistingWorkPolicy.REPLACE` semantics cancel and delete pending same-name work. Android's update-work guidance also warns that cancel/re-enqueue can make backend transfer work restart. ClipVault's immediate `sync-now` is scheduled after explicit public captures and drains a durable outbox, so cancelling a currently running push/pull during bursty saves is a worse reliability trade-off than allowing a short queued duplicate. | **Adopt now:** change immediate `sync-now` from `REPLACE` to `APPEND_OR_REPLACE` and add a host-JVM source-shape test that forbids returning to cancellation-prone `REPLACE`. This keeps one unique work chain, avoids cancelling in-flight sync, and relies on the existing durable outbox/empty-batch exit for idempotence. It does not change sync payload format, add cloud relay/telemetry, move network work into the IME, or satisfy Owner/manual LAN QA. |

## Research log - round 31 (2026-07-04)

Scope filter: serve v1.6/v1.7 agent-instruction release-state truthfulness
only. Do not change runtime behavior, Android IME behavior, sync payloads,
version metadata, signing authority, artifact publication semantics, or Issue
#36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R65 | Top-level agent instruction drift gate | OpenAI Codex AGENTS.md documentation (`https://github.com/openai/codex/blob/main/docs/advanced.md#memory--project-docs`), GitHub Copilot repository custom instructions docs (`https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions`), Visual Studio Code custom instructions docs (`https://code.visualstudio.com/docs/copilot/copilot-customization`) | Coding agents and AI coding assistants read repository-level instruction files to learn project-specific commands, constraints, and working rules. ClipVault already fixed `docs/AGENT_WORKFLOWS.md`, but the top-level `AGENTS.md` still named stale v1.5 blockers and Issue #3, which can misroute future agents away from the current Issue #36 release gate. | **Adopt now:** update top-level `AGENTS.md` so it says Issue #3/v1.5 is closed, Issue #36 is the current v1.6.0 release gate, and v1.7 stable requires the stability-plan exit criteria plus Owner approval. Add `test_release_alignment.py` coverage so the top-level agent entrypoint cannot regress to stale Issue #3/v1.5 blocker wording. This is documentation-as-release-evidence hardening only; it does not sign artifacts, run manual QA, publish a release, or change product behavior. |

## Research log - round 32 (2026-07-04)

Scope filter: serve local-first sync planning truthfulness only. Do not change
runtime behavior, sync payload semantics, runtime dependencies, Android IME
behavior, typed-text policy, analytics policy, signing authority, artifact
publication semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R66 | Product-spec sync topology drift guard | LocalSend project (`https://localsend.org/`, `https://github.com/localsend/localsend`), KDE Connect Android project (`https://github.com/KDE/kdeconnect-android`), Syncthing documentation (`https://docs.syncthing.net/`) | Local-first peer/device tools keep the product promise at the user boundary—local network, paired/trusted devices, and no required cloud relay—while their implementation protocols vary by product and evolve over time. ClipVault already moved its actual v1 sync implementation and architecture docs to stdlib HTTPServer + HTTP push/pull + Android `HttpURLConnection`, but `PRODUCT_SPEC.md` still used frozen early WebSocket wording. That creates planning drift: future v1.7 work could optimize or test the wrong transport while the real contract is event-log HTTP push/pull with offline outbox. | **Adopt now:** update `PRODUCT_SPEC.md` to describe the current HTTP push-pull implementation topology while preserving the product goal of LAN/Tailscale local-first sync, explicit pairing, and offline outbox behavior. Extend `test_release_alignment.py` so the product-spec entrypoint cannot reintroduce WebSocket/FastAPI sync claims. Clarify the v1.7 stability plan so pre-v1.6 Owner-gate work may include verified safety/reliability defects, but not product semantics, privacy boundaries, payload/schema, dependency, signing, or release-state changes. This is docs/test planning hardening only; it does not alter runtime behavior or satisfy Owner/manual release gates. |

## Research log - round 33 (2026-07-04)

Scope filter: serve v1.7 Android sync auth-failure reliability only. Do not
change Android IME behavior, sync payloads, runtime dependencies, typed-text
policy, analytics policy, signing authority, artifact publication semantics, or
Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R67 | Android authenticated error-body boundary | RFC 9110 HTTP semantics (`https://www.rfc-editor.org/rfc/rfc9110`), Android `HttpURLConnection` reference (`https://developer.android.com/reference/java/net/HttpURLConnection`), OWASP API4:2023 unrestricted resource consumption (`https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/`) | HTTP status code semantics are available before an application consumes an optional response body, Android documents `disconnect()` as the connection cleanup boundary, and OWASP treats unbounded or unnecessary resource consumption as an API risk. ClipVault already bounds sync response bodies, but authenticated sync 401/403 responses were read before the permanent-auth-failure classifier ran; an oversized error body from a bad paired endpoint could therefore turn a known-bad bearer token into a generic retry path. | **Adopt now:** for authenticated sync requests, skip reading 401/403 response bodies and return the status immediately so `SyncAuthException` still clears the token and stops immediate WorkManager retries. Keep `/api/pair` and non-auth responses reading bodies as before, and keep 413/429/5xx on the existing retry path. This is local-first sync reliability hardening only; it does not change payload format, add telemetry/cloud relay, move network work into the IME, or satisfy Owner/manual LAN QA. |

## Research log - round 34 (2026-07-04)

Scope filter: serve v1.6/v1.7 threat-model release-evidence truthfulness only.
Do not change runtime behavior, sync payload semantics, Android IME behavior,
typed-text policy, analytics policy, signing authority, artifact publication
semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R68 | Threat-model sync transport boundary drift guard | Android `android:usesCleartextTraffic` manifest docs (`https://developer.android.com/guide/topics/manifest/application-element#usesCleartextTraffic`), Android Network Security Configuration cleartext docs (`https://developer.android.com/privacy-and-security/security-config`), Tailscale overview docs (`https://tailscale.com/docs/concepts/what-is-tailscale`) | Android documents that apps targeting API 28+ default cleartext traffic to disabled and must explicitly opt in when they need HTTP; Android also warns cleartext lacks confidentiality, authenticity, and tamper protection. Tailscale documents encrypted device-to-device connectivity over WireGuard. ClipVault's manifest and architecture already describe SYNC-2 as HTTP push/pull over LAN/Tailscale with explicit cleartext opt-in, but `THREAT_MODEL.md` still named the retired WS boundary and residual risk. | **Adopt now:** update `THREAT_MODEL.md` to name the current HTTP push/pull network boundary and record pure-LAN HTTP cleartext as the accepted residual risk, mitigated by pairing-token auth, Tailscale recommendation, and future P2 self-signed TLS + pinning. Extend `test_release_alignment.py` so the threat-model entrypoint cannot regress to the retired WS/FastAPI wording. This is docs/test truthfulness only; it does not change sync behavior, widen Android networking, move network work into the IME, sign artifacts, publish a release, or close Issue #36. |

## Research log - round 35 (2026-07-04)

Scope filter: serve v1.7 Android local-first sync reliability only. Do not
change Android IME behavior, sync payload schema, runtime dependencies,
typed-text policy, analytics policy, signing authority, artifact publication
semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R69 | Android outbound sync push request-body budget | PouchDB replication API docs (`https://pouchdb.com/api.html`), Apache CouchDB replicator configuration docs (`https://docs.couchdb.org/en/stable/config/replicator.html`), Android `HttpURLConnection` reference (`https://developer.android.com/reference/kotlin/java/net/HttpURLConnection`), MDN HTTP 413 Content Too Large (`https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/413`), Android WorkManager retry/backoff docs (`https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started/define-work`) | Local-first replication systems expose batch controls because batch size affects memory and request pressure. HTTP 413 means the request body exceeded the server limit, and WorkManager retry keeps rescheduling retryable work. ClipVault's desktop `/api/sync/push` already rejects bodies above 4 MiB, while Android previously batched by event count only; a burst of large but individually valid public outbox events could therefore create a body the desktop rejects, even though smaller sub-batches would sync successfully. | **Adopt now:** bound Android `sync-now` push request bodies before calling `/api/sync/push`. `SyncWorker` builds a JSON batch that fits a conservative local request budget, sends only that prefix, clears only the desktop-acked seqs, and continues with remaining rows in later loop iterations. Preserve at-least-once local-first delivery by including at least one event even when a single row exceeds the budget, instead of silently dropping or rewriting payloads. This keeps payload schema unchanged, adds no telemetry/cloud relay, and does not move network work into the IME. |

## Research log - round 36 (2026-07-04)

Scope filter: serve Android local unit-test reliability only. Do not change
runtime dependencies, production sync behavior, Android IME behavior,
typed-text policy, analytics policy, signing authority, artifact publication
semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R70 | Android host-JVM JSON test runtime | Android local unit-test docs (`https://developer.android.com/training/testing/local-tests`), Stack Overflow `JSONObject.put not mocked` discussion (`https://stackoverflow.com/questions/29402155/android-unit-test-not-mocked`), Maven Central `org.json:json` metadata (`https://repo.maven.apache.org/maven2/org/json/json/`) | Android local unit tests run on the workstation JVM with mockable `android.jar`; framework APIs are present but method bodies are removed, so unmocked Android SDK calls throw "not mocked". ClipVault's host-JVM sync batching tests intentionally exercise `JSONObject`/`JSONArray` serialization logic, and without a real JVM `org.json` implementation they fail before checking the sync budget behavior. | **Adopt now:** add pinned `testImplementation("org.json:json:20260522")` for the Android app module only. Keep it test-only so production still uses Android's platform `org.json` and APK/runtime dependency shape is unchanged. This is test infrastructure hardening only; it does not change sync payload semantics, add cloud relay/telemetry, move network work into the IME, or satisfy Owner/manual LAN QA. |

## Research log - round 37 (2026-07-04)

Scope filter: serve local Android verification reliability only. Do not change
runtime dependencies, production sync behavior, Android IME behavior,
typed-text policy, analytics policy, signing authority, artifact publication
semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R71 | Windows local Android verification path/toolchain hygiene | Android command-line build docs (`https://developer.android.com/build/building-cmdline`), Gradle Wrapper docs (`https://docs.gradle.org/current/userguide/gradle_wrapper.html`), Gradle Java Toolchains docs (`https://docs.gradle.org/current/userguide/toolchains.html`), Gradle build environment docs (`https://docs.gradle.org/current/userguide/build_environment.html`) | Android expects command-line builds to run through the project Gradle wrapper, while Gradle toolchains require a discoverable matching JDK installation. In this workspace, the repository path contains non-ASCII characters and AGP stops before tests unless the path check is explicitly overridden; the machine also defaults to JDK 21 while the project intentionally compiles shared Kotlin with a Java 17 toolchain. | **Adopt now:** add `android.overridePathCheck=true` to project Gradle properties so local agent/Owner verification can run from this Windows workspace. Keep JDK selection out of the repository: local runs should set `JAVA_HOME` or user Gradle properties to a JDK 17 installation, and CI continues to provide its own toolchain. This is verification-environment hygiene only; it does not alter APK runtime behavior, production dependencies, release signing, or manual QA status. |

## Research log - round 38 (2026-07-04)

Scope filter: serve v1.7 manual-QA truthfulness only. Do not run or claim
device/emulator QA, change IME runtime behavior, typed-text policy, analytics
policy, signing authority, artifact publication semantics, or Issue #36 manual
QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R72 | Residual IME instrumented-QA scaffold truth guard | Android command-line testing docs (`https://developer.android.com/studio/test/command-line`), AndroidX Test runner docs (`https://developer.android.com/training/testing/instrumented-tests/androidx-test-libraries/runner`), JUnit 4 `@Ignore` API docs (`https://junit.org/junit4/javadoc/latest/org/junit/Ignore.html`) | Android instrumented tests run on a device or emulator through Android's connected-test tasks and runner, while JUnit `@Ignore` marks tests that are not executed. ClipVault currently uses `androidTest` only as a compile-checked backlog for five residual IME smoke checks; if future edits rename methods, drop `@Ignore`, or add `connectedDebugAndroidTest` to CI without recording a real device run, the release evidence could overstate QA completion. | **Adopt now:** extend the desktop release-alignment gate so the residual IME `androidTest` scaffold must keep exactly the five backlog methods, each with the shared `@Ignore` reason, and CI must compile but not run `connectedDebugAndroidTest`. This is manual-QA evidence hygiene only; it does not execute device QA, alter IME behavior, or satisfy Issue #36 Owner/manual requirements. |

## Research log - round 39 (2026-07-04)

Scope filter: serve Issue #36 Windows clipboard privacy manual-QA
repeatability only. Do not change watcher runtime behavior, clipboard capture
semantics, typed-text policy, analytics policy, signing authority, artifact
publication semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R73 | Windows registered-clipboard privacy QA probe | Microsoft `OpenClipboard` docs (`https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openclipboard`), Microsoft `EmptyClipboard` docs (`https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-emptyclipboard`), Microsoft `SetClipboardData` docs (`https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setclipboarddata`), Microsoft `GlobalAlloc` docs (`https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-globalalloc`) | Owner/manual Windows clipboard privacy QA needs a repeatable source of registered clipboard privacy formats. Win32 clipboard writes require opening and emptying the clipboard, allocating movable global memory for each payload, and transferring ownership to the system with `SetClipboardData`. ClipVault already has unit coverage for the watcher decision logic, but the manual checklist previously required an unspecified source app or harness. | **Adopt now:** add a Windows-only `tools/clipboard_privacy_probe.py` manual QA helper that writes non-sensitive probe text plus one registered privacy format at a time, and document the exact Issue #36 checklist commands. This is manual-QA repeatability only; it overwrites the current clipboard, does not automatically observe ClipVault behavior, and does not by itself satisfy the Owner/manual release gate. |

## Research log - round 40 (2026-07-04)

Scope filter: serve residual IME QA gate routing and release-blocker
truthfulness only. Do not run or claim device/emulator QA, change IME runtime
behavior, typed-text policy, analytics policy, signing authority, artifact
publication semantics, or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R74 | Residual IME backlog gate routing | Android command-line testing docs (`https://developer.android.com/studio/test/command-line`), Android UI Automator docs (`https://developer.android.com/training/testing/other-components/ui-automator-legacy`), Android input-method creation docs (`https://developer.android.com/develop/ui/views/touch-and-input/creating-input-method`), Android testing samples (`https://github.com/android/testing-samples`) | Android connected tests are the command-line path for real device/emulator execution, UI Automator is the Android-supported layer for cross-app/system UI interactions, and IME behavior depends on a system-selected `InputMethodService`. ClipVault's residual IME checks therefore still belong in the current Issue #36 / v1.6.0 manual QA gate until they become real connected tests; routing the backlog through the old v1.5.16 checklist can mislead future agents into updating the wrong evidence source. | **Adopt now:** retarget `docs/INSTRUMENTED_QA_BACKLOG.md` and `docs/VERSION_SYNC.md` to the current Issue #36 / v1.6.0 gate and add static release-alignment checks so final release publication and residual IME evidence cannot drift back to old checklist wording. This is documentation/test truthfulness only; it does not execute `connectedDebugAndroidTest`, satisfy Owner/manual QA, sign artifacts, or publish `v1.6.0`. |

## Research log - round 41 (2026-07-04)

Scope filter: serve release-chain artifact handoff integrity only. Do not change
runtime behavior, signing authority, artifact publication semantics, Android IME
behavior, typed-text policy, analytics policy, or Issue #36 manual QA
requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R75 | Draft release artifact download/staging boundary | GitHub Actions artifact docs (`https://docs.github.com/actions/tutorials/store-and-share-data`), `actions/download-artifact` README (`https://github.com/actions/download-artifact`), GitHub Actions artifacts v4 changelog (`https://github.blog/changelog/2023-12-14-github-actions-artifacts-v4-is-now-generally-available/`) | GitHub's artifact download action can download a single named artifact to a configured path, while omitting `name` downloads all artifacts from the workflow run. Artifact v4+ immutability improves integrity after upload, but it does not decide which workflow artifacts should become release assets. ClipVault's draft release job re-verified the expected Windows and Android signed artifact directories, yet staged every file under `release-artifacts`, so a future unrelated artifact could be swept into a draft GitHub Release outside the manifest verification boundary. | **Adopt now:** download only `clipvault-windows-release-artifacts` and `clipvault-android-signed-release-artifacts` by explicit artifact name, then stage files only from those two verified flat directories. Add static release-alignment tests to forbid returning to download-all/stage-all behavior. This is release-chain hardening only; it does not sign artifacts, create/publish `v1.6.0`, close Issue #36, or satisfy Owner/manual QA. |

## Research log - round 42 (2026-07-04)

Scope filter: serve v1.6/v1.7 release-chain and agent-handoff truthfulness
only. Do not change runtime behavior, Android IME behavior, sync payloads,
typed-text policy, analytics policy, signing authority, artifact publication
semantics, or Issue #36 Owner/manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R76 | Privileged untrusted-code workflow trigger guard | GitHub Actions secure-use reference (`https://docs.github.com/en/actions/reference/security/secure-use`), GitHub `pull_request_target` security guide (`https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target`), GitHub Security Lab "Preventing pwn requests" (`https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/`), GitHub Actions checkout safer `pull_request_target` changelog (`https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/`) | GitHub explicitly warns against using `pull_request_target` and `workflow_run` with untrusted pull requests/code, and says such workflows must not check out untrusted fork/PR code. GitHub's own Security Lab describes the "pwn request" class: privileged PR workflows can expose write tokens or secrets if they run attacker-controlled code. ClipVault's current workflows do not need these privileged triggers because PR checks run untrusted code through ordinary `pull_request` paths and signed release work remains manual/environment-gated. | **Adopt now:** add a static release-alignment guard that forbids `pull_request_target` and `workflow_run` in repository workflows unless a future ADR/Owner-approved design deliberately changes that boundary. While touching the handoff entrypoint, replace stale v2.1 current-slice wording with the current Issue #36 / v1.6.0 release gate and v1.7 stability-planning truth. This is release-chain and agent-routing hardening only; it does not sign artifacts, run manual QA, publish `v1.6.0`, close Issue #36, or alter runtime behavior. |

## Research log - round 43 (2026-07-04)

Scope filter: serve v1.6/v1.7 planning truthfulness and release-evidence
handoff only. Do not change runtime behavior, Android IME behavior, sync
payloads, version metadata, signing authority, artifact publication semantics,
or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R77 | Handoff current-state release gate anchor | GitHub environments/deployment protection docs (`https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments`), GitHub releases overview (`https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases`), SLSA overview (`https://slsa.dev/`) | GitHub environments can gate jobs with required reviewers and keep environment secrets unavailable until approval; GitHub Releases are deployable software iterations tied to tags and assets; SLSA frames release hardening as artifact-integrity evidence rather than optimistic status labels. ClipVault already has the right Issue #36 gate and v1.7 stability plan, but `docs/HANDOFF.md` still anchored the current slice to an older v2.1 build-PoC track and stale fixed test-count release evidence. That can misroute future agents into runtime expansion before the signed-artifact/manual-QA/release-publication gate is resolved. | **Adopt now:** make the top handoff current-state row point at the v1.6.0 release gate and v1.7 stability planning, replace stale fixed release-evidence counts in the current version block, rename the old v1.6 entry gate as an open Issue #36 release gate, and add a static release-alignment test so this memory entrypoint cannot regress. This is planning/evidence hygiene only; it does not sign artifacts, create/publish a release, run manual QA, or change product behavior. |

## Research log - round 44 (2026-07-04)

Scope filter: serve local Web UI privacy/security regression coverage only. Do
not change runtime behavior, Android IME behavior, sync payloads, signing
authority, artifact publication semantics, typed-text policy, analytics policy,
or Issue #36 manual QA requirements.

| # | Direction | Sources | Key finding | Decision |
|---|---|---|---|---|
| R78 | Local Web UI browser API security surface | OWASP DOM Based XSS Prevention Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/DOM_based_XSS_Prevention_Cheat_Sheet.html`), MDN `window.postMessage` security guidance (`https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage`), OWASP HTML5 Security Cheat Sheet (`https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html`), MDN Subresource Integrity overview (`https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Subresource_Integrity`) | ClipVault's local Web UI already avoids parsing clipboard/memory API data as HTML, uses first-party CSP/no-store headers, and keeps debounce state module-local. The remaining browser-side attack surface worth pinning for a local clipboard/memory UI is accidental introduction of cross-window messaging, Web Storage, navigation URL sinks, dynamic script loading, remote static resources, or inline event handlers. OWASP and MDN treat these as high-signal review areas because they can expose sensitive data, bypass origin expectations, or reintroduce XSS/supply-chain risk. | **Adopt now:** extend `test_webui_security.py` so CI fails if the local Web UI starts using `postMessage`, Web Storage, navigation URL sinks, dynamic script loading, remote resources, inline handlers, or non-first-party script/style entrypoints. This is browser-surface regression coverage only; it does not change Web UI runtime semantics, Android IME behavior, release state, or #36 manual QA requirements. |
