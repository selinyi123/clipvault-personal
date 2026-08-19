# ClipVault Input Engine Protocol V2

> Status: frozen for v2.1 PoCs (2026-08-01). This contract is additive to the v2.0 KBD-1 lab API.
> A production implementation requires the platform gates in `GATES.md` and does not follow merely from
> this document.

## ENG2-1 — Scope and transport independence

The same semantic contract applies to Android in-process/JNI or Binder adapters and to the Windows
TSF-to-Host pipe. Transport framing, generated language bindings and UI rendering may differ, but golden
vectors must observe the same state transitions.

The contract never carries clipboard history, Personal Memory bodies, surrounding editor text, device
keys or network credentials. ClipVault toolbar snapshots use a separate bounded local contract.

## ENG2-2 — Handshake and host epoch

```text
ClientHello(protocol_version=2, client_instance_id, platform, architecture, build_id)
ServerHello(protocol_version=2, host_epoch, engine_build_id, capabilities)
```

- A protocol mismatch fails closed before starting a session.
- `host_epoch` is a new unpredictable value after every Host/engine restart.
- A session, revision or candidate ID from another epoch is invalid.
- Build IDs are diagnostics identifiers only; they must not contain user or machine secrets.

## ENG2-3 — Input context

```text
InputContext
  platform: ANDROID | WINDOWS
  field_kind: TEXT | MULTILINE | EMAIL | URL | NUMBER | PHONE | PASSWORD | OTP | UNKNOWN
  action: NONE | ENTER | DONE | GO | NEXT | SEARCH | SEND
  incognito: bool
  learning_allowed: bool
  clipvault_allowed: bool
  app_scope: optional opaque local identifier
```

`app_scope` may be held for the active session but is not sent over a network or written to content logs.
No selected text, surrounding text, typed body or accessibility tree is part of `InputContext`.

## ENG2-4 — Requests

Every mutating request carries a monotonically increasing `request_seq`. Every state-dependent request
also carries `expected_revision`.

```text
StartSession(host_epoch, session_id, request_seq=1, context) -> EngineState
ProcessKey(session_id, request_seq, expected_revision, key_event) -> EngineState
SelectCandidate(session_id, request_seq, expected_revision, candidate_id) -> EngineState
PageCandidates(session_id, request_seq, expected_revision, PREVIOUS | NEXT) -> EngineState
CommitComposition(session_id, request_seq, expected_revision) -> EngineState
CancelComposition(session_id, request_seq, expected_revision) -> EngineState
SetOption(session_id, request_seq, expected_revision, option, enabled) -> EngineState
EndSession(session_id, request_seq)
```

The client generates a fresh opaque `session_id` after the handshake.
`StartSession` is the first sequenced mutation for that session and therefore
uses `request_seq = 1`; the next accepted request uses `2`. An identical retry
of `StartSession` returns the byte-equivalent cached initial state. Reusing the
same ID with different context, host epoch or request content fails closed.
This rule is transport-independent even when an in-process Android adapter can
allocate a session without IPC.

A sequence is consumed exactly when the Host emits a response carrying that
`ack_request_seq`, including a cached application-error response. An identical
retry returns that cached response and the following request uses the next
sequence. Validation rejected synchronously before any response/ack is emitted
does not consume a sequence. Clients must not reserve an error sequence unless
the error itself carries the authenticated session identity and ack sequence.

`key_event` contains only normalized key identity, action and modifier state required by the engine. It is
not retained after the request finishes.

## ENG2-5 — State

```text
EngineState
  host_epoch: bytes
  session_id: opaque string
  revision: uint64
  handled: bool
  preedit: string
  caret_utf16: uint32
  segments: repeated CompositionSegment
  candidates: repeated EngineCandidate
  page_index: uint32
  has_previous_page: bool
  has_next_page: bool
  commit_text: optional string
  mode: DIRECT | COMPOSING | SELECTING | DISABLED

CompositionSegment
  start_utf16: uint32
  end_utf16: uint32
  kind: RAW | CONVERTED | SELECTED

EngineCandidate
  candidate_id: opaque string
  text: string
  comment: optional string
  source: ENGINE
```

- All offsets are UTF-16 code units. Non-empty segments form a contiguous, ordered partition of the
  complete `preedit`: the first starts at `0`, every next segment starts at the previous end, and the last
  ends at the UTF-16 length.
- `revision` increases whenever visible or selectable state changes.
- Candidate IDs are stable for the same logical candidate while paging within one composition. They are
  valid only inside their declared host epoch/session/composition and must not encode plaintext, page or
  array position.
- `commit_text` is an at-most-once editor effect. A duplicate request may receive the byte-equivalent
  cached response so a lost first response can recover. Each platform client therefore keeps bounded
  applied-response state: either exact keys `(host_epoch, session_id, ack_request_seq)` or, when requests
  and responses are strictly serialized, one monotonic applied-sequence high-water mark per live typed
  `(host_epoch, session_id)`. Duplicate or lower cached responses never write to the editor again. The
  client reserves the sequence before attempting the editor effect; an ambiguous platform result retires
  the session and is never blindly retried. Session end and host-epoch replacement clear the state.
- A Host may retain the latest response briefly for duplicate recovery, but committed/preedit/candidate
  text cannot remain cached for an unbounded idle session. Production transports require an authenticated
  response acknowledgement or a short monotonic retry deadline and must wipe the cached response after
  either condition.
- Empty preedit requires caret `0` and no non-empty segments.

## ENG2-6 — Ordering, stale state and recovery

- A duplicate `request_seq` returns the identical cached result or a defined duplicate response.
- A lower, unknown or conflicting sequence fails with `OUT_OF_ORDER_REQUEST`.
- A mismatched revision fails with `STALE_REVISION`; it never selects by current array position.
- Unknown/expired candidate IDs fail with `INVALID_CANDIDATE` and produce no commit.
- Host restart or transport reconnect returns `STALE_SESSION`; the client clears preedit/candidates and
  starts a new session. It must not replay unconfirmed key streams automatically.
- Timeout clears ClipVault-enhanced UI and falls back to direct/base input without blocking the host app.

## ENG2-7 — Candidate surfaces

Protocol V2 transports engine candidates only. System Inline Autofill suggestions remain system-owned;
ClipVault clipboard/Memory/OTP UI remains a separate surface with its own identity and privacy lifetime.
The UI must not convert these three sources into one array index namespace.

## ENG2-8 — Privacy and observability

- No network calls, Room/SQLite access or Python/HTTP calls occur while processing an engine request.
- Raw key events, preedit, candidate text and commit text are forbidden in ordinary logs, telemetry and
  crash annotations.
- Allowed diagnostics: protocol version, hashed build ID, epoch change, request duration, response size,
  error code and numeric counts.
- Password/incognito context disables ClipVault surfaces and learning. Direct engine behavior follows the
  platform/editor contract without persisting content.

## ENG2-9 — Required golden vectors

The platform-specific fixtures map to the canonical semantic IDs
`ENG2-V001` through `ENG2-V008` in
`contracts/vectors/input_foundation_v2.json`. Android and Windows may use
different framing, but neither adapter may omit or reinterpret one of those
semantic cases.

Stable per-case assertion IDs and their exact text are frozen in
`contracts/vectors/engine_protocol_v2_assertions.tsv`. Platform manifests must
map every assertion ID for all eight semantics; a scenario-name-only mapping is
not sufficient evidence.

At minimum both platform adapters must cover:

1. start → key sequence → preedit/candidate IDs → select by ID → one commit → cleared composition;
2. next/previous page with monotonic revisions;
3. stale revision and stale candidate rejection;
4. duplicate cached response with a client projection proving exactly one editor commit;
5. cancel and explicit end-session cleanup;
6. non-BMP preedit with UTF-16 caret/segment boundaries;
7. host epoch change invalidating every old session and candidate;
8. password/incognito context producing no ClipVault surface request or learning event.

The platform harness must additionally prove that a lost Start response retries
the same client identity, a same-epoch transport session loss takes the
`STALE_SESSION` recovery path, a contiguous malformed response is rejected
before editor projection, live response cache is cleared by acknowledgement or
deadline, and duplicate fingerprints retain no raw key text.
