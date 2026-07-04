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
