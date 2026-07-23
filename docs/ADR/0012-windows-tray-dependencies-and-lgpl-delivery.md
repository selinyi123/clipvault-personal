# ADR-0012: Windows tray dependencies and LGPL delivery

Status: **Accepted with a frozen-wheel feature self-test** (2026-07-23)

## Context

The v1.6.0 Windows executable starts the desktop node through the tray launcher.
Without the tray dependencies, a packaged executable can fall back to a
headless process with no useful user-visible entry point. The Owner approved
these exact direct/build dependencies:

- `pystray==0.19.5`;
- `Pillow==12.3.0`;
- `PyInstaller==6.21.0`;
- `pyinstaller-hooks-contrib==2026.6`.

`pystray` is LGPL-3.0-or-later. Its PyPI release has no source distribution.
The wheel contains the Python modules and the GPL/LGPL license documents, but
not the complete upstream repository and build inputs. A PyInstaller `--onefile`
executable also does not provide a conventional replaceable shared-library
boundary: bundled Python modules are collected into the executable archive.

The official Pillow 12.3.0 Windows wheel embeds a CycloneDX inventory that lists
native components which can participate in Pillow builds, including
FriBiDi/fribidi-shim under LGPL-2.1-or-later and libimagequant 4.4.1 under
GPL-3.0-or-later. That inventory is not proof that an optional feature was
enabled or that its code entered a particular wheel or PyInstaller executable.
Pillow's official Windows wheel build disables imagequant; a checked wheel also
reported `libimagequant=False` and `raqm=False`. Its comprehensive bundled
`LICENSE` file carries the applicable FreeType and other bundled-library
attributions.

The release still tests the exact frozen CPython 3.11 wheel rather than
projecting a result from another interpreter wheel.

## Decision

### 1. Dependency and build lock

The Windows release lane must use the four approved versions above and a
hash-locked Windows CPython 3.11 wheelhouse. The lock must include every
resolved runtime and packaging dependency, including at least `six`,
`altgraph`, `packaging`, `pefile`, `pywin32-ctypes`, and `setuptools` when they
are present in the build environment.

The final relink kit records:

- exact wheel filenames and SHA-256 values;
- the exact Python and pip versions;
- the exact repository commit;
- the exact PyInstaller command/spec and installer input;
- the source-acquisition records in
  `third_party/source-acquisition-v1.6.0.json`.

The build must fail rather than silently resolve a different wheel.

### 2. Ninth GitHub Release asset

The final `v1.6.0` GitHub Release contains exactly nine regular, non-empty
assets. The ninth asset is:

```text
ClipVault-v1.6.0-LGPL-relink-kit.zip
```

The kit is part of the same digest, provenance, draft-to-publication parity, and
Owner approval binding as the other eight assets. It is not an optional
documentation attachment.

At minimum the kit contains:

```text
README-RELINK.md
THIRD_PARTY_NOTICES.md
licenses/
  pystray-COPYING-GPL-3.0.txt
  pystray-COPYING-LGPL-3.0.txt
  ...license and notice files extracted from every locked wheel...
sources/
  clipvault-personal-v1.6.0-<target-commit>.zip
  pystray-1907f8681d6d421517c63d94f425f9cdd74d0034.zip
wheelhouse/
  ...the exact wheels used by the Windows build...
locks/
  wheelhouse-SHA256SUMS.txt
  build-environment.json
  source-acquisition-v1.6.0.json
build/
  repack_pystray_wheel.py
  ...the tracked PyInstaller, installer, and relink instructions from the
     exact application source archive...
```

The application archive is produced with `git archive` from the exact target
commit, not from a mutable working tree. The pystray archive is the exact
upstream commit archive:

```text
commit: 1907f8681d6d421517c63d94f425f9cdd74d0034
sha256: 4751562ba90301e054c87606079c1599301d84e7d1e4074b12af4f54a80a4768
```

The pystray wheel alone is not accepted as complete corresponding source.

### 3. Relinking and reverse-engineering permission

Recipients may modify the LGPL-covered pystray source, replace the locked
pystray wheel with a rebuilt interface-compatible wheel, and use the supplied
application source and build inputs to produce a modified Combined Work.
Because pystray is pure Python, the kit includes a standard-library-only wheel
repacker that replaces the `pystray` package tree and regenerates the wheel
`RECORD`; recipients therefore do not need pystray's historical documentation
build dependencies merely to relink a modified library.

To the extent any ClipVault term would otherwise prohibit it, the distributor
permits reverse engineering of the v1.6.0 Windows Combined Work **solely for
debugging modifications to an LGPL-covered library included in that Combined
Work**. This is a narrow supplemental permission required for LGPL compliance.
It does not relicense ClipVault generally, grant rights to secrets or
trademarks, or authorize reverse engineering for unrelated purposes.

No technical or contractual measure in the release may prevent installation
and execution of a legitimately rebuilt Combined Work on a general-purpose
Windows computer.

### 4. Notice accessibility

Each Windows copy must prominently identify pystray and make the applicable
GPL/LGPL texts and relink-kit location available. The installer installs the
notices/licenses with the application. The portable executable must expose the
same notice without requiring a separate installed copy (for example through a
documented `--third-party-notices` command and a tray-menu entry backed by
embedded notice/license data).

The GitHub Release notes must point directly to the ninth asset and state that
pystray is LGPL-3.0-or-later.

### 5. Pillow frozen-wheel gate

Before a final Windows executable is eligible for Issue #36 evidence, the
release job must inspect the exact Pillow wheel SBOM, preserve the wheel's
verbatim `LICENSE`, and run a frozen feature self-test that proves:

```text
libimagequant=False
raqm=False
```

The test result, exact wheel SHA-256, license/SBOM hashes, and collected
PyInstaller module/binary inventory are included in the relink kit. The SBOM's
disabled optional-component rows must not be presented as code included in the
released executable.

If the exact wheel reports either feature enabled, or if the frozen inventory
contradicts the self-test, the LGPL-only plan in this ADR is insufficient and
the release stops for a new license review. Green unit tests, a valid EXE, and
the presence of a source ZIP do not waive that gate.

## Consequences

- The Windows tray is a supported release feature rather than an optional
  silent fallback.
- The final Release inventory grows from eight to nine assets.
- Final artifact evidence and manual QA must bind the relink kit bytes.
- The onefile artifact remains possible, but only with corresponding source,
  exact application/build inputs, notices, and a practical rebuild path.
- The exact wheel feature result and final binary composition—not an SBOM row in
  isolation or package marketing metadata—decide which third-party obligations
  apply.

## Primary sources

- pystray v0.19.5 source and license:
  https://github.com/moses-palmer/pystray/tree/v0.19.5
- pystray PyPI metadata:
  https://pypi.org/project/pystray/0.19.5/
- GNU LGPL v3, especially section 4:
  https://www.gnu.org/licenses/lgpl-3.0.html
- Pillow 12.3.0 PyPI files and provenance:
  https://pypi.org/project/Pillow/12.3.0/
- Pillow license:
  https://github.com/python-pillow/Pillow/blob/12.3.0/LICENSE
- PyInstaller 6.21.0 licensing and bootloader exception:
  https://github.com/pyinstaller/pyinstaller/blob/v6.21.0/COPYING.txt
- PyInstaller 6.21.0 release metadata:
  https://pypi.org/project/pyinstaller/6.21.0/
- PyInstaller hooks licensing:
  https://github.com/pyinstaller/pyinstaller-hooks-contrib/tree/v2026.6
