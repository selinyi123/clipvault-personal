# Third-party release material

This directory is the tracked input contract for the v1.6.0 Windows legal
delivery. It does not contain generated binaries, downloaded wheels, or release
artifacts.

- `source-acquisition-v1.6.0.json` pins upstream source and candidate wheel
  identities.
- `RELINKING_V1_6_0.md` defines the required ninth Release asset and its
  validation.
- The release workflow copies verbatim license/SBOM files from the exact locked
  wheelhouse. Do not hand-transcribe or silently normalize those files.

Generated wheelhouses, source archives, and relink kits stay out of Git. They
must be produced from the final target commit, attested, checksummed, and
uploaded as the ninth `v1.6.0` Release asset.
