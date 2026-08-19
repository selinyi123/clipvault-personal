# ADR-0013: Cross-platform input process and package boundary

Status: **Accepted for v2 foundation PoCs** (2026-08-01)

## Context

The current Android application registers two IME services in one package that also owns network and
database capabilities. The current Windows application is a Python desktop runtime, not a TSF text
service. A full Android and Windows IME must not move networking, persistence, sync or Python into the
keystroke-critical path.

## Decision

1. Android targets two packages: a no-network/no-SMS IME package and a Companion Runtime package.
   Communication is a signature-protected Binder contract with bounded requests, explicit timeouts and
   fail-closed privacy. The existing single-package dual-IME build remains a migration state until those
   gates pass.
2. Windows uses a thin native TSF DLL plus an external IME Runtime. The DLL owns only COM/TSF,
   composition/edit-session handling and candidate presentation. It must not load Python, librime,
   SQLite, HTTP, sync or device keys.
3. The external IME Runtime owns librime sessions and a local, already-filtered ClipVault candidate
   snapshot. The existing Python Desktop Runtime remains the data/sync node and publishes snapshots
   asynchronously; it is never called synchronously for each key.
4. A Runtime crash, timeout or protocol mismatch invalidates its sessions and ClipVault candidates but
   must not crash the host application or prevent direct/basic input.
5. No production package split, TSF registration or installer change is authorized by this ADR alone.
   Those mutations require their version gates and Owner approval.

## Consequences

- Android gains a real package-level permission boundary instead of relying only on source discipline.
- Windows host applications do not inherit the risk surface of Python, librime, databases or networking.
- Clipboard, Personal Memory and OTP can fail independently of the base input engine.
- Package/install complexity increases and needs explicit upgrade, signing and uninstall tests.

## Related

- [NEXT_PHASE_V2_INPUT_FOUNDATION](../NEXT_PHASE_V2_INPUT_FOUNDATION.md)
- [ADR-0010](0010-keyboard-base-selection.md)
- [ADR-0011](0011-input-context-privacy.md)
- [CONTRACTS_INPUT_ENGINE_V2](../CONTRACTS_INPUT_ENGINE_V2.md)
