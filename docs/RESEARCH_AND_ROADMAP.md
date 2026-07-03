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
- ⏳ **未做**：Extend IME incognito to the **save** path (no save-clipboard in incognito fields).

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
