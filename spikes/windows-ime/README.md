# Windows IME research evidence

The executable Windows input method is no longer a spike. Its maintained
source, build scripts, tests, locks, and operational documentation live under
[`windows/ime/`](../../windows/ime/README.md).

This directory retains only transport-neutral research fixtures used to derive
the production contract:

- `protocol/` and `vectors/` freeze the protocol-v2 framing/state evidence;
- `tools/` contains the standard-library conformance model and bootstrap checks;
- `UPSTREAM_LOCK.json` records reviewed upstream/reference boundaries.

Passing these fixtures is architecture research evidence. It is not a signed
installer, system registration, interactive application compatibility, or
daily-use stability evidence.
