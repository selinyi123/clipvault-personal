# GitHub Reference Projects Registry

> Purpose: record external GitHub projects that overlap with ClipVault Personal so the project can reuse proven ideas, avoid duplicate design work, and keep architecture decisions grounded in existing implementations.

ClipVault Personal remains a **local-first personal input-aware clipboard knowledge system**. These projects are not copied wholesale. They are classified as reference, integration candidate, or non-goal boundary.

## 0. Evaluation Rules

Each external project is evaluated by five questions:

1. Does it solve one ClipVault module directly?
2. Can its design be reused without changing ClipVault's local-first / single-user / privacy-first principles?
3. Is the license compatible for reference or reuse?
4. Does it reduce implementation risk?
5. Does it expose a boundary where ClipVault should avoid reinventing a full product?

Adoption levels:

| Level | Meaning |
|---|---|
| `Reference` | Study design/API/UX only; no dependency. |
| `Pattern` | Reuse architecture pattern or test ideas. |
| `Integration Candidate` | Possible optional interop or adapter. |
| `Boundary` | Do not rebuild this whole product; only cover ClipVault-specific subset. |
| `Reject for Core` | Not aligned with ClipVault's scope or privacy model. |

---

## 1. Current Project Anchor

| Project | Role in this registry | Notes |
|---|---|---|
| `selinyi123/clipvault-personal` | Source project | Main implementation and product memory. Do not let external references redefine the core product. |

Current internal modules to compare against external projects:

- Windows clipboard watcher
- Local encrypted-ish store / SQLite store
- deterministic classifier
- Secret Guard
- Obsidian writer
- GitHub backup
- HTTP event sync
- Android capture app
- Android InputMethodService / Keyboard Personal
- Personal Memory
- Suggestion Engine
- Context Action rules

---

## 2. Clipboard Managers

| Project | Area | Adoption | What to learn | What not to copy |
|---|---|---|---|---|
| `hluk/CopyQ` | Advanced clipboard manager | `Reference` | Tabs, tags, scripting, tray workflow, multi-format clipboard handling, ignore rules. | Do not rebuild full CopyQ scripting system in v1. |
| `sabrogden/Ditto` | Windows clipboard history | `Reference` | Local-first UX, hotkey retrieval, database-backed clipboard history, no cloud/no login positioning. | Do not copy Windows-only architecture into Android path. |
| `ffMathy/Shapeshifter` | Windows clipboard replacement | `Boundary` | Hotkey interception idea and multi-format clipboard replacement concept. | Avoid deep Ctrl+V hook replacement in early versions. |
| `clip_share_server` and related `clipboard-sync` topic projects | Cross-device clipboard sync | `Reference` | Simple server/client clipboard sync flows. | ClipVault should keep event-log sync and Secret Guard gates. |

ClipVault decision:

- CopyQ and Ditto show that clipboard history, search, tags, and tray/hotkey access are already solved problems.
- ClipVault should only innovate where they do not: Android IME access, Personal Memory, Obsidian/GitHub gated export, and Secret Guard across sync/export/indexing.

---

## 3. Android Keyboard / IME Projects

| Project | Area | Adoption | What to learn | What not to copy |
|---|---|---|---|---|
| `florisboard/florisboard` | Modern Android keyboard | `Reference` | Kotlin IME structure, privacy-respecting keyboard UX, keyboard settings patterns. | Do not attempt full modern multilingual keyboard parity. |
| `AnySoftKeyboard/AnySoftKeyboard` | Mature Android keyboard | `Reference` | IME architecture, add-ons, incognito mode, suggestions, voice input, language packs. | Do not import its full language engine complexity. |
| `HeliBoard/HeliBoard` | Privacy-conscious AOSP-derived keyboard | `Reference` | Offline keyboard baseline, privacy posture, incognito mode expectations. | Do not depend on GPL code without explicit license review. |
| `fcitx5-android/fcitx5-android` | Android input method framework | `Boundary / Reference` | Plugin architecture, candidate view, clipboard management, RIME/custom schema support. | ClipVault should not become a full Fcitx/RIME replacement. |
| `LiteKite/Android-IME` and other minimal IME examples | Tutorial / minimal IME | `Pattern` | Minimal InputMethodService bootstrapping. | Not enough for production UX/security. |

ClipVault decision:

- Keyboard Personal should remain a **companion IME**: panels, snippets, synced clipboard, key info, prompts, commands.
- Do not implement a full commercial Chinese IME or full predictive typing engine in early versions.
- Input privacy rule remains: never record raw keystrokes; only record explicit saved clips, pinned terms, accepted suggestions, and usage counters.

---

## 4. Cross-device Sync / Transfer

| Project | Area | Adoption | What to learn | What not to copy |
|---|---|---|---|---|
| `localsend/localsend` | Local network transfer | `Reference / Integration Candidate` | LAN discovery, REST API + HTTPS, no server/no account model, cross-platform packaging, firewall troubleshooting docs. | Do not replace ClipVault sync with file-transfer UX. |
| KDE Connect / `kdeconnect-kde` | Device integration | `Reference` | Shared clipboard, notification sync, file sharing, device pairing model. | Do not rebuild a full phone-to-PC suite. |
| Syncthing | File sync | `Boundary` | Device identity, peer approval, encrypted transfer, conflict model. | Do not sync raw SQLite files directly. |
| Tailscale / ZeroTier style overlay network | Network layer | `Integration Candidate` | Use private network transport to avoid public relay. | Not part of ClipVault core domain logic. |

ClipVault decision:

- Keep current HTTP event-log sync.
- Treat file transfer as a later optional module.
- Do not make GitHub, Obsidian, or raw file sync the realtime sync layer.

---

## 5. Obsidian / Markdown Knowledge Capture

| Project | Area | Adoption | What to learn | What not to copy |
|---|---|---|---|---|
| `obsidianmd/obsidian-clipper` | Official web capture | `Reference` | Durable Markdown output, templates, browser capture, sanitization. | ClipVault is clipboard-first, not browser-first. |
| `deathau/markdownload` | Web-to-Markdown clipping | `Reference` | HTML/readability extraction to Markdown, Obsidian integration ideas. | Do not make web clipping the core v1 scope. |
| `Vinzent03/obsidian-git` | Obsidian Git integration | `Integration Candidate / Boundary` | Auto commit/pull/push scheduling, source-control view, mobile limitations. | Avoid relying on mobile Obsidian Git for ClipVault core backup. |
| `ayu5h-raj/clipboard-manager` | Obsidian clipboard plugin | `Reference` | Obsidian-side clipboard history, quick paste modal, export to Markdown. | Do not put ClipVault primary database inside Obsidian plugin storage. |
| `Ar9av/obsidian-wiki` | Obsidian knowledge structure | `Reference` | Wiki-style organization patterns. | Not a clipboard engine. |

ClipVault decision:

- Obsidian remains an export target, not the primary database.
- Obsidian export must stay gated by Secret Guard, triage, and export policy.
- Consider optional compatibility notes for users who also use Obsidian Git.

---

## 6. Secret Detection / Privacy Guard

| Project | Area | Adoption | What to learn | What not to copy |
|---|---|---|---|---|
| `gitleaks/gitleaks` | Secret scanning | `Pattern / Integration Candidate` | Rule format, stdin scanning, pre-commit hook, report formats, redaction. | Do not call it synchronously on every clipboard event if latency is high. |
| `trufflesecurity/trufflehog` | Secret verification/scanning | `Reference / Integration Candidate` | Verified secrets, GitHub/repo/filesystem scanning, CI fail mode. | AGPL license requires caution for embedding. Use as external tool only unless license is reviewed. |
| `Yelp/detect-secrets` | Baseline-based secret prevention | `Pattern` | Baseline workflow, plugin detectors, staged-file hook, Python API. | It is code-repo oriented; adapt only detection concepts. |
| `GitGuardian/ggshield` | CLI/GitHub Action secret detection | `Reference` | Broad provider coverage, pre-commit/CI integration. | Cloud/vendor assumptions may not fit local-first self-use. |

ClipVault decision:

- Keep built-in deterministic Secret Guard for realtime clipping.
- Add optional external scanner mode for backup/export audit, especially before GitHub push.
- Maintain deny-by-default export policy for secret/private classes.

---

## 7. Personal Memory / AI Knowledge / Agent Adjacent Projects

| Project | Area | Adoption | What to learn | What not to copy |
|---|---|---|---|---|
| `MemTensor/MemOS` | Memory OS / AI memory | `Reference` | Memory abstraction, retrieval, lifecycle of remembered facts. | Do not turn ClipVault into a general AI memory OS. |
| `labring/FastGPT` | Knowledge base / agent workflow | `Reference` | RAG/knowledge workflow concepts. | Not core for v1; ClipVault is local-first and personal. |
| `OpenHands/OpenHands` | AI coding agent | `Boundary` | Agent action logs and tool loops. | Do not expand ClipVault into an agent IDE. |
| `alchaincyf/loop-engineering-orange-book` | Loop engineering knowledge | `Reference` | Concepts for prompt/workflow/loop classification. | Not an implementation dependency. |
| `github/spec-kit` | Spec-driven development | `Pattern` | Specifications, gates, implementation slices. | Do not over-bureaucratize small self-use changes. |
| `selinyi123/DPMS-Platform` | Related owner project | `Internal Reference` | Project vocabulary, architecture patterns, prompt/workflow terms. | Keep ClipVault product boundary separate. |
| `selinyi123/prompt-performance-engine` | Related owner project | `Internal Reference` | Prompt quality/evaluation vocabulary. | Do not require it for core clipboard operations. |

ClipVault decision:

- Personal Memory is not general AI memory.
- It is a deterministic, user-controlled memory layer for terms, snippets, prompts, commands, paths, projects, accepted suggestions, and frequency weights.

---

## 8. Web / Content Extraction / Crawling

| Project | Area | Adoption | What to learn | What not to copy |
|---|---|---|---|---|
| `unclecode/crawl4ai` | Web crawling for AI | `Reference` | Structured web extraction and Markdown generation ideas. | Do not make crawling part of clipboard MVP. |
| `D4Vinci/Scrapling` | Scraping / extraction | `Reference` | Robust extraction patterns. | Not part of local clipboard core. |
| `NanmiCoder/MediaCrawler` | Media/social crawling | `Reject for Core` | Useful only for future content ingestion research. | Too broad; not ClipVault scope. |

ClipVault decision:

- Web extraction should be optional future Context Action, not core capture.

---

## 9. Recommended Additions to ClipVault Project Plan

### 9.1 Add a permanent Research Registry

This file is the first version of the registry. Keep it updated when evaluating similar tools.

Suggested process:

1. New external project found.
2. Add it to this file.
3. Classify by module and adoption level.
4. Record what to learn and what not to copy.
5. If it changes architecture, create an ADR.

### 9.2 Add an anti-duplication checklist to future slices

Before implementing a new feature, each slice should answer:

- Which known open-source project already solved this?
- Are we copying a solved commodity feature or building a ClipVault-specific differentiator?
- Is this feature core, adapter, or future plugin?
- Does it violate privacy, local-first, or no-keystroke-recording principles?

### 9.3 Add external audit gates

For major modules, compare against these references:

| ClipVault module | Compare with |
|---|---|
| Clipboard history/search | CopyQ, Ditto |
| Android IME | FlorisBoard, AnySoftKeyboard, HeliBoard, Fcitx5 Android |
| Cross-device sync | LocalSend, KDE Connect, Syncthing concepts |
| Obsidian export | Obsidian Web Clipper, MarkDownload, Obsidian Git |
| Secret Guard | Gitleaks, TruffleHog, detect-secrets, ggshield |
| Personal Memory | MemOS, spec-kit patterns, internal DPMS/prompt projects |

---

## 10. Final Scope Guard

ClipVault should not try to become:

- CopyQ clone
- Ditto clone
- full Chinese IME
- full LocalSend / AirDrop clone
- full KDE Connect clone
- full Obsidian plugin ecosystem
- full AI memory OS
- full agent platform

ClipVault should remain:

> A local-first, single-user, Windows + Android clipboard and input-memory system with gated knowledge export and strict privacy boundaries.
