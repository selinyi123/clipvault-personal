# ClipVault — Research Log & Roadmap

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

## Roadmap

### v1.6 — privacy & security hardening *(this cycle)*
- Pairing rate-limit (R6) · DNS-rebinding `Host` guard (R4) · IME incognito suppression (R2).
- Already merged this cycle: version single-source, candidate source caps,
  release-state display, device revocation.

### v1.7 — capture-layer privacy + Secret Guard depth
- Honour Windows clipboard-exclusion formats
  (`ExcludeClipboardContentFromMonitorProcessing`, `CanIncludeInClipboardHistory=0`)
  in the watcher → never capture what a password manager marked sensitive (R1).
  *Windows-only; needs Windows-CI / manual verification.*
- Widen Secret Guard with a gitleaks-inspired rule set + entropy tuning, backed by
  golden test vectors (R3). *Desktop-testable.*
- Extend IME incognito to the **save** path (no save-clipboard in incognito fields).

### v1.8 — sync correctness
- Per-field LWW for `clip_meta`: track the meta timestamp per field
  (pinned / favorite / deleted) so a newer change to one field can't be masked by
  an older change to another (R5). *Desktop-testable.*
- Optional `Referer` check as a second DNS-rebinding layer.

### v2.0 — opt-in transport security
- Optional self-signed TLS for the LAN sync/pair socket (R4 defence-in-depth), with
  the Android client pinning the desktop certificate at pair time.

### Explicitly out of scope (keeps us on the core goal)
- Cloud sync / server-side storage — ClipVault is local-first.
- Verification-first secret scanning that calls provider APIs — would exfiltrate
  the secret, violating "secrets never leave".
- Typed-text learning, behavioural profiling, or analytics SDKs in the IME.
