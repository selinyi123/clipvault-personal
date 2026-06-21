# ClipVault Personal v1.3 — research, decisions, and next plan

Date: 2026-06-21

This note records the non-duplicative research pass after v1.2.1. It is scoped to the current product goal:

> personal clipboard knowledge capture + two-device sync + Obsidian/Git backup + Android IME panel.

It intentionally excludes broad cloud note apps, generic device-sync suites, and GPL keyboard forks as implementation bases.

## 1. External references reviewed

### 1.1 Clipboard managers: mature feature patterns, not the product boundary

- CopyQ: advanced desktop clipboard manager with search, tags, tabs, editing, and scripting.
  - Source: https://hluk.github.io/CopyQ/
  - Relevance: confirms that search/tags/edit/history are commodity clipboard-manager features.
  - Decision: do not compete as a general clipboard manager. ClipVault's edge is capture-to-knowledge + sync + IME recall.

- Ditto: Windows clipboard manager with searchable history, multi-format support, database backup/restore, hotkeys, and stats.
  - Source: https://ditto-cp.sourceforge.io/
  - Relevance: reinforces that search, backup, hotkeys, and multi-format history are expected baseline capabilities.
  - Decision: keep ClipVault text-first for now; binary/image rich clipboard support is out of v1.3.

- Maccy: lightweight macOS clipboard manager that explicitly positions itself around speed and simplicity.
  - Source: https://github.com/p0deje/Maccy
  - Relevance: supports a minimal UI philosophy.
  - Decision: ClipVault UI should avoid becoming a full database editor on Android; use desktop/Web UI for heavy management.

### 1.2 Text expansion / snippets: overlap with Personal Memory

- Espanso: open-source cross-platform text expander with package ecosystem.
  - Source: https://espanso.org/
  - Relevance: Personal Memory overlaps with snippets/phrases/prompts/commands.
  - Decision: do not rebuild Espanso. ClipVault should expose "memory recall in IME" and optional future import/export, not global abbreviation expansion in v1.x.

### 1.3 Android keyboards: useful UX reference, risky code base

- HeliBoard/OpenBoard: privacy-oriented Android keyboard lineage based on AOSP keyboard implementation; GPL-3.0.
  - Source: https://github.com/Helium314/HeliBoard
  - Relevance: confirms strong privacy patterns: no network dependency, incognito mode, user-visible clipboard controls.
  - Decision: use as UX/privacy reference only. Do not copy GPL code into ClipVault unless the whole relevant module licensing strategy is explicitly changed.

- FlorisBoard: open-source Android keyboard with modern Kotlin architecture.
  - Source: https://github.com/florisboard/florisboard
  - Relevance: useful reference for modular keyboard UI, theming, and extension approach.
  - Decision: reference architecture only; ClipVault's near-term keyboard remains a companion panel/full-keyboard lab.

### 1.4 Android platform security: direct implementation input

- Android Keystore system.
  - Source: https://developer.android.com/privacy-and-security/keystore
  - Relevance: sync bearer token is long-lived authorization material and should not be plaintext SharedPreferences.
  - Decision implemented in v1.3: token is encrypted with an AndroidKeyStore AES-GCM key; host/port/cursor remain normal prefs.

- Android clipboard sensitive flag.
  - Source: https://developer.android.com/develop/ui/views/touch-and-input/copy-paste
  - Relevance: if ClipVault later writes sensitive content to the system clipboard, it must set `ClipDescription.EXTRA_IS_SENSITIVE`.
  - Decision: no current code writes saved clips to the system clipboard; IME uses `InputConnection.commitText`. Keep this as a future guardrail for any "copy to clipboard" action.

- InputConnection.commitText.
  - Source: https://developer.android.com/reference/android/view/inputmethod/InputConnection#commitText(java.lang.CharSequence,%20int)
  - Relevance: confirms the current IME paste path commits text directly into the editor, not through the global clipboard.
  - Decision: preserve commitText for one-tap paste to avoid extra clipboard exposure.

### 1.5 Research cautions

- Stack Overflow security snippets in Android apps can be unsafe when copied blindly.
  - Source: https://arxiv.org/abs/1710.03135
  - Decision: use official Android docs and source-level references for Keystore/IME/security code, not copied Q&A snippets.

- WildKey privacy-aware keyboard toolkit.
  - Source: https://arxiv.org/abs/2105.10223
  - Relevance: keyboard telemetry can be privacy-sensitive even when framed as research/metrics.
  - Decision: CandidateMixer/local learning must store explainable aggregate events only; never persist ordinary typed text.

## 2. v1.3 decisions made

### D-013 Android token storage

Move `Settings.token` from plaintext `clipvault_sync` SharedPreferences into a Keystore-backed encrypted blob.

- AES-GCM key alias: `clipvault_sync_token_v1`
- storage prefs: `clipvault_sync_token`
- failure mode: decrypt failure clears encrypted token blobs and forces re-pairing
- no new dependency added

### D-014 Android ULID contract

Android local clips now use DB-1 compatible ULID shape: 26 Crockford Base32 characters, 48-bit millisecond timestamp prefix, 80-bit randomness.

This removes the previous pseudo-ULID divergence.

### D-015 Backup retry idempotency

Backup JSONL appends are idempotent by line content. If JSONL write succeeds and git commit fails, retry commits the existing line instead of appending a duplicate.

## 3. v1.3 scope status

Done:

- Android sync token encrypted with AndroidKeyStore AES-GCM.
- Android capture id generation changed to real ULID format.
- Backup JSONL retry idempotency present and covered by regression test.
- Research record added.

Still manual/CI-gated:

- `python -m pytest -q`
- `gradle :core:test`
- `gradle :app:assembleDebug`
- real-device pairing + IME panel smoke

## 4. Next version plan

### v1.3.1 — build and device validation

- Run desktop test suite.
- Run Android Gradle tests/build.
- Fix any Kotlin compile errors around Keystore imports or ULID generation.
- Pair real phone with desktop and verify: share capture, tile capture, IME panel paste, release-secret sync.

### v1.4 — Runtime/CandidateMixer safety shell

- Add a `PrivacyAwareFilter` before any candidate source is displayed.
- Detect password/sensitive editor contexts and suppress ClipVault candidates.
- Add candidate provenance labels: recent clip, memory phrase, prompt, command.
- Keep candidate mixing deterministic and testable.

### v2.1 — Chinese input engine spike

- Build a minimal librime JNI proof of concept.
- Evaluate fcitx5-android plugin feasibility as fallback.
- Do not fork GPL keyboard code into the app without explicit licensing decision.

### v2.2 — unified candidate strip

- Merge Rime candidates + ClipVault memory/clip candidates into one ranked strip.
- Keep ordinary typed text out of persistence.

