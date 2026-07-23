# ClipVault Personal third-party notices

This notice applies to the ClipVault Personal v1.6.0 Windows artifacts.
ClipVault remains subject to its own applicable terms. The entries below do not
relicense ClipVault as a whole.

## Runtime components

| Component | Version | Role | License |
|---|---:|---|---|
| pystray | 0.19.5 | Windows notification-area integration | LGPL-3.0-or-later |
| Pillow | 12.3.0 | image object used by the tray icon | MIT-CMU, plus the licenses of native components actually present in the selected wheel/build |
| six | 1.17.0 | pystray compatibility helper | MIT |

pystray copyright notice: Copyright (C) 2016-2022 Moses Palmér.

pystray is free software under the GNU Lesser General Public License, version 3
or (at the recipient's option) any later version. The Windows executable is a
Combined Work for delivery purposes. The full GNU GPL v3 and GNU LGPL v3 texts,
the exact pystray corresponding source, the exact ClipVault application source
and build inputs, the locked wheelhouse, and relink instructions are in:

```text
ClipVault-v1.6.0-LGPL-relink-kit.zip
```

To the extent any ClipVault term would otherwise prohibit it, the distributor
permits reverse engineering of the v1.6.0 Windows Combined Work solely for
debugging modifications to an LGPL-covered library included in that Combined
Work. This limited permission does not grant a general reverse-engineering
right or relicense ClipVault.

Pillow is Copyright (C) 1997-2011 by Secret Labs AB, Copyright (C) 1995-2011
by Fredrik Lundh and contributors, and Copyright (C) 2010 by Jeffrey 'Alex'
Clark and contributors. Its MIT-CMU license text and its wheel SBOM are
included in the relink kit.

The official Pillow wheel SBOM can list optional native components that are
disabled in the selected build. The v1.6.0 relink kit preserves the exact
wheel's comprehensive bundled `LICENSE`, SBOM, feature self-test, and final
binary composition report. The release self-test must report
`libimagequant=False` and `raqm=False`; an SBOM row alone is not a claim that
the component entered the executable.

## Build and packaging components

These tools or their runtime hooks participate in the Windows build. Their
presence here does not imply that their license applies to ClipVault source as
a whole.

| Component | Version | License |
|---|---:|---|
| PyInstaller | 6.21.0 | GPL-2.0-or-later with the upstream bootloader exception |
| pyinstaller-hooks-contrib | 2026.6 | GPL-2.0-or-later for standard hooks; Apache-2.0 for runtime hooks |
| altgraph | 0.17.5 | MIT |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pefile | 2024.8.26 | MIT |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| setuptools | 83.0.0 | MIT, with bundled-component notices in its wheel |

Verbatim license and notice files are extracted from the exact hash-locked
wheels into the relink kit. The source-acquisition record is
`third_party/source-acquisition-v1.6.0.json`.
