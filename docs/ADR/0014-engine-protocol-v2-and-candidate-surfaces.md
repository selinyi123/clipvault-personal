# ADR-0014: Engine Protocol V2 and separated candidate surfaces

Status: **Accepted for v2.1 contracts and PoCs** (2026-08-01)

## Context

KBD-1 selects candidates by array index and has no session, paging, revision, caret, segment or host
restart semantics. KBD-2 assumes that engine and ClipVault candidates immediately share one mixed list.
That model cannot safely represent an asynchronous Android Binder or Windows out-of-process engine:
an index can refer to a different candidate after paging, refresh or reconnect.

## Decision

1. Keep the frozen KBD-1 API only as the v2.0 lab compatibility surface. New integrations use the
   versioned contract in `CONTRACTS_INPUT_ENGINE_V2.md`.
2. After the host handshake, the client creates an opaque session ID.
   `StartSession(host_epoch, session_id, request_seq=1, context)` is the first
   idempotent sequenced mutation; an identical retry returns its cached initial
   state and conflicting reuse fails closed. Every later mutating request uses
   the next session sequence; state-dependent mutations also carry the expected
   revision. `EndSession` is sequenced but does not require a revision. Every
   candidate has a stable opaque ID scoped to one host
   epoch/session/composition.
3. Engine state explicitly carries preedit, UTF-16 caret, segments, paging, optional commit text and mode.
   Host restart invalidates all previous sessions, revisions and candidate IDs.
   A platform client applies commit text at most once per host/session/request sequence, even when a lost
   response is recovered by replaying the Host's byte-equivalent cached response.
4. The first production UI keeps system Inline Autofill, engine candidates and ClipVault toolbar
   candidates as separate surfaces. ClipVault ranking remains deterministic inside its own surface.
5. Unified interleaving is a later experiment, not a v2.2 prerequisite. It requires a new evidence-backed
   decision proving stable identity, learning ownership, accessibility and privacy behavior.

## Consequences

- Candidate selection cannot silently target stale content after asynchronous updates.
- Rime learning and paging remain owned by the engine instead of being reimplemented by ClipVault.
- Runtime failure cannot erase or reorder the engine's base candidates.
- Existing KBD-1/KBD-2 tests remain historical compatibility evidence; V2 tests are additive.

## Related

- [CONTRACTS_KEYBOARD](../CONTRACTS_KEYBOARD.md)
- [CONTRACTS_INPUT_ENGINE_V2](../CONTRACTS_INPUT_ENGINE_V2.md)
- [KEYBOARD_PRIVACY](../KEYBOARD_PRIVACY.md)
