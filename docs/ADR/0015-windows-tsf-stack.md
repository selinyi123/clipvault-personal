# ADR-0015: Windows TSF stack for the first implementation PoC

Status: **Accepted for an isolated PoC; production stack remains unselected** (2026-08-01)

## Context

ClipVault Desktop is a Python data/runtime node and currently contains no TSF DLL, COM registration,
native candidate window, named-pipe protocol or librime host. Reimplementing all of TSF before checking
existing open-source boundaries would duplicate work; copying a complete GPL Rime client into ClipVault
would create a different licensing path.

## Decision

1. The first Windows build PoC evaluates the TypeDuck Windows frontend/backend boundary, libIME2 and
   librime. It starts from pinned, unmodified upstream sources and fixed synthetic Rime data.
2. The useful boundary is `TSF DLL -> per-user named pipe -> launcher/external runtime -> librime`.
   TypeDuck-specific cloud, WebDAV, AI and synchronous network paths are out of scope.
3. Protocol V2 freezes one pipe frame as a four-byte unsigned big-endian payload length followed by
   exactly 1–1,048,576 protobuf bytes. Zero-length, truncated, oversized or trailing-byte input closes
   the connection fail closed. Every new connection, including after Host restart, must complete
   `ClientHello -> HostHello` before application frames.
4. The PoC must first prove register/activate/compose/page/select/commit/cancel/unregister without any
   ClipVault Python integration. Only then may Python publish an asynchronous privacy-filtered snapshot.
5. The Microsoft SampleIME is the platform-contract reference. Weasel is a Rime behavior reference only;
   GPL code is not copied into a non-GPL module. Other projects may be used as compatibility references,
   not assumed dependencies.
6. Production selection remains blocked until x86/x64 host coverage, ARM64 design, crash isolation,
   multi-DPI candidate placement, clean install/upgrade/uninstall, signing and full license/submodule/data
   provenance have evidence.

## Consequences

- The first PoC reuses a demonstrated process split while keeping a replacement boundary around it.
- New or insufficiently proven upstreams are treated as PoC inputs, not trusted production dependencies.
- No TSF registration, installer mutation or third-party source vendoring is authorized by this ADR alone.

## Related

- [ADR-0013](0013-cross-platform-input-process-boundary.md)
- [CONTRACTS_INPUT_ENGINE_V2](../CONTRACTS_INPUT_ENGINE_V2.md)
- [THIRD_PARTY_INPUT_RESEARCH_MATRIX](../THIRD_PARTY_INPUT_RESEARCH_MATRIX.md)
