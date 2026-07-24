# Third-party release material

This directory is the tracked input contract for the v1.6.0 Windows legal
delivery. It does not contain generated binaries, downloaded wheels, or release
artifacts.

- `source-acquisition-v1.6.0.json` pins upstream source and candidate wheel
  identities, plus the exact CPython 3.11.9 Windows x64 NuGet runtime package
  and its official binary-distribution license bundle.
- `RELINKING_V1_6_0.md` defines the required ninth Release asset and its
  validation.
- `licenses/CPython-3.11.9-Windows-LICENSE.txt` is the verbatim
  `tools/LICENSE.txt` from the pinned official CPython 3.11.9 NuGet package.
- The release workflow copies verbatim license/SBOM files from the exact locked
  wheelhouse and embeds the tracked CPython Windows license bundle. Do not
  hand-transcribe or silently normalize those files.

Generated wheelhouses, source archives, and relink kits stay out of Git. They
must be produced from the final target commit, attested, checksummed, and
uploaded as the ninth `v1.6.0` Release asset.

This tracked CPython notice closes the known embedded-interpreter notice gap.
It is not a representation that all native dependencies across every
ClipVault artifact have received an independent comprehensive license audit.
