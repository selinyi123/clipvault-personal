# Engine Session Contract PoC

Status: **isolated, synthetic, not a production API**.

This JVM-only contract exercises the Engine Protocol V2 shape needed between an
Android IME shell and a future native engine host. It does not replace the
frozen `InputEngineAdapter` in `docs/CONTRACTS_KEYBOARD.md`; production adoption
or a change to that contract still requires the existing ADR/Owner gate.

## Invariants

- Every host process/boot owns a random, non-reusable `HostEpoch`. The client
  creates a fresh opaque `SessionId`; `StartSession(epoch, session, 1, context)`
  is the first sequenced mutation, and an identical immediate retry returns the
  cached initial response. The client retains that identity across a lost Start
  response and retries the exact request; every later request starts at sequence 2.
- Every successful revision-checked composition mutation advances a monotonic
  `revision` exactly once. Terminal session/host invalidation is not revisioned.
- Every mutation carries a per-session `requestSeq`. The next sequence is
  accepted atomically, an identical duplicate returns its cached transition,
  and gaps, lower sequences, or conflicting duplicates fail closed. Rejected
  requests do not consume a sequence.
- A state-dependent mutation carries the current revision. Stale requests fail
  without changing state. Start and terminal End are sequenced but not
  revision-checked.
- `SetOption` accepts only the content-free `EngineOption` enum. The current PoC
  whitelist contains `FULL_SHAPE`; arbitrary option names or content cannot
  cross this boundary.
- Candidate selection uses an opaque stable candidate ID, never a list index.
  The same logical candidate keeps its ID while paging within one composition;
  IDs contain no preedit, candidate text, page, or array position.
- A commit is an at-most-once mutation. Its cached duplicate response carries
  the original request sequence, while a later snapshot cannot replay it. The
  IME client keeps one monotonic applied-response high-water mark per live,
  strongly typed `(HostEpoch, SessionId)` key. It accepts only `previous + 1`;
  a response gap retires the session, while a cached/lower response is ignored.
  Memory is bounded by the number of live sessions. The client reserves the
  sequence before attempting the editor effect; an ambiguous or rejected
  platform result invalidates the session and is never blindly retried. Ending
  a session or replacing the host epoch erases its mark.
- `AndroidImeSessionClient.EditorConnection` models composing, clearing, and
  commit effects as `APPLIED`, `REJECTED`, or `AMBIGUOUS`. It is an abstraction
  boundary only; no Android framework class is linked by this JVM project.
- A host epoch change clears the old ledger and attempts to clear the composing
  projection. A confirmed clear opens a new client-created session and returns
  `HOST_RESTART_RECOVERED_NO_REPLAY`; a rejected/ambiguous clear leaves no live
  session. The interrupted key is never replayed automatically.
- A same-epoch `STALE_SESSION` (for example, transport reconnect with lost Host
  session state) follows the same fail-closed recovery path: clear the old
  projection, create a fresh session, and never replay the interrupted key.
- Commit, candidate selection, and cancel clear composing text and candidates.
- Ending a session invalidates it. Restarting the host invalidates every active
  session from the previous host epoch. Both paths erase context, composing,
  candidate and cached response content before retaining a bounded tombstone.
- The trusted in-process seam acknowledges every successfully projected Start or
  transition response and the fake Host immediately erases its cached response
  and duplicate fingerprint. Duplicate fingerprints use a per-Host HMAC and
  never retain the raw key. The Host also exposes a scheduled monotonic retry
  deadline cleanup, exercised with a deterministic clock. A production Binder
  transport must authenticate acknowledgements and schedule the same deadline
  fallback when acknowledgement delivery can fail.
- Preedit caret and segment offsets are UTF-16 code units. Locked vectors include
  a non-BMP value; caret/segment boundaries cannot split a surrogate pair, and
  candidate IDs are unique within every visible state.
- `InputContext` carries only bounded editor metadata; `appScope` is null or a
  bounded ASCII opaque token. Password/incognito contexts force learning and
  ClipVault surfaces off. `CandidateSurfaces` keeps engine candidates and the
  ClipVault toolbar in different typed lists/ID namespaces; sensitive contexts
  return an empty ClipVault list without invoking its local supplier.
- The adapter is memory-only. It has no Room, outbox, network, Android framework,
  clipboard, logging, or user-data dependency.

## Foundation semantic mapping

| Semantic ID | Android JVM evidence |
|---|---|
| `ENG2-V001` | selection commits once and clears composition |
| `ENG2-V002` | paging preserves opaque IDs; stale revision/candidate has no editor effect |
| `ENG2-V003` | strict applied-response ledger ignores duplicates, rejects gaps, and retires malformed contiguous responses before projection |
| `ENG2-V004` | ambiguous/rejected editor effects retire the session without retry |
| `ENG2-V005` | host restart or same-epoch session loss creates a fresh session without replaying interrupted input |
| `ENG2-V006` | non-BMP caret and segments use surrogate-safe UTF-16 offsets |
| `ENG2-V007` | password/incognito contexts never query or expose ClipVault candidates |
| `ENG2-V008` | acknowledged live responses are evicted; end/restart wipe content state; ledgers and tombstones remain bounded |

`AndroidImeSliceRunner` loads the separately locked
`android-ime-slice-vectors.tsv`, requires the exact `ENG2-V001..ENG2-V008`
mapping and every stable assertion ID from the byte-identical locked Foundation
assertion manifest, then executes every mapped scenario. The session TSV
additionally checks idempotent sequence-1 Start and the `FULL_SHAPE` SetOption
request.

`FakeEngineHost` is deliberately deterministic and uses only project-authored
synthetic fixtures. It is a protocol test double, not a Chinese decoder and not
evidence that librime or either Android route builds.

The locked vectors and Foundation assertion mirror live under `../vectors/`;
their SHA-256 values are pinned in `../POC_LOCK.json` and checked by
`../tools/validate_poc.py`.
The normal Gradle `check` lifecycle depends on `verifySessionContract` and
`verifyAndroidImeSlice`, so neither the golden host vectors nor the client
vertical slice can be skipped by the standard project verification command.
