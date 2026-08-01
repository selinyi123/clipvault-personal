# ClipVault Personal third-party notices

This notice applies to the ClipVault Personal v1.6.0 Windows artifacts.
ClipVault remains subject to its own applicable terms. The entries below do not
relicense ClipVault as a whole.

## Runtime components

| Component | Version | Role | License |
|---|---:|---|---|
| CPython | 3.11.9 (Windows x64) | embedded interpreter and Windows runtime | PSF License Version 2, plus the incorporated-software notices in the official Windows binary distribution license bundle |
| pystray | 0.19.5 | Windows notification-area integration | LGPL-3.0-or-later |
| Pillow | 12.3.0 | image object used by the tray icon | MIT-CMU, plus the licenses of native components actually present in the selected wheel/build |
| six | 1.17.0 | pystray compatibility helper | MIT |

The executable embeds the CPython 3.11.9 Windows x64 runtime. The exact
license bundle from `tools/LICENSE.txt` in the official CPython 3.11.9 NuGet
package is tracked verbatim at:

```text
third_party/licenses/CPython-3.11.9-Windows-LICENSE.txt
```

That official Windows binary distribution file contains the PSF License
Version 2, the additional conditions for the Windows binary build, and notices
for incorporated software such as bzip2, libffi, and OpenSSL. Preserving this
distribution-provided bundle does not by itself claim that every native
dependency in the complete ClipVault application has undergone a separate,
independent license audit.

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

## v2 daily-use candidate status

The following sections apply only to the v2 daily-use candidate. They do not
replace, weaken, or retroactively change the v1.6.0 Windows delivery terms and
records above. They are a review inventory, not an approval to distribute a v2
binary.

The Owner has selected an internal-only governance state for ClipVault-owned
code. No license is granted to third parties, and no root `LICENSE` grant is
created for this state. The machine-readable record in
`THIRD_PARTY_MANIFEST.yaml` is:

```text
project_license.status: internal_only
license_file: null
distribution_allowed: false
```

This is a completed decision for local/internal daily use, not a pending
license choice. Public, customer, partner, marketplace, or other external
distribution remains prohibited. A future distribution decision must atomically
adopt a real repository license file, change the status to `approved`, and
complete the candidate-specific notice and signing review.

### v2 Android Runtime candidate

The networked Android Runtime packages
`com.google.android.gms:play-services-auth-api-phone:18.2.0` solely for the
explicit, foreground, one-message SMS User Consent fallback. It does not grant
or require `READ_SMS` or `RECEIVE_SMS`. The exact Google Maven AAR and POM are
frozen in `android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json`:

| Artifact | Size | SHA-256 |
|---|---:|---|
| `play-services-auth-api-phone-18.2.0.aar` | 105177 | `15963fa1cf08ad2778fd54f17ef72cb7597af15f40415885833b1240369230f3` |
| `play-services-auth-api-phone-18.2.0.pom` | 2279 | `1014bbbd9f385e57e1fb3f99d536e60e494b2ef7f4e3959088f748413a89a4b0` |

The official POM identifies the Android Software Development Kit License and
links to `https://developer.android.com/studio/terms.html`. This repository
does not assert that those web terms may be copied or redistributed as a local
license text. Owner review of the Google SDK terms, required notices, and the
final distributed artifact remains a release blocker; the URL and POM record
are provenance evidence, not distribution permission.

### v2 Android IME candidate

The standalone Android IME statically links the following hash-locked native
components and bundles the listed Rime dictionary data:

| Component | Locked version or revision | Candidate use | License record |
|---|---|---|---|
| librime | 1.16.1 / `de4700e9f6b75b109910613df907965e3cbe0567` | statically linked engine | BSD-3-Clause |
| yaml-cpp | 0.9.0 | statically linked native dependency | MIT |
| LevelDB | 1.23.0 | statically linked native dependency | BSD-3-Clause |
| OpenCC | 1.2.0 | statically linked native dependency | Apache-2.0 |
| marisa-trie | 0.3.1 | statically linked native dependency | BSD-2-Clause OR LGPL-2.1-or-later; the candidate selects the BSD-2-Clause option |
| rime-pinyin-simp | `0c6861ef7420ee780270ca6d993d18d4101049d0` | bundled dictionary data | Apache-2.0 |

The yaml-cpp, LevelDB, OpenCC, and marisa-trie archives come from
`fcitx5-android/prebuilt` commit
`86ce2c95d42f1132746fbf60c278193aa1f4b758`, under parent
fcitx5-android commit `048f581c652367567b8ee5c28c5163b805288895`.
Fcitx5 Android runtime code is not linked into or shipped by this IME. The
production build places its NOTICE at `assets/third_party/NOTICE.txt` and the
license texts at `assets/rime/third_party/`; those paths are intentionally
different.

Repository review copies of the current Android license-text inputs are under
`third_party/licenses/`. For the four prebuilt native dependencies, those
copies come from the locked librime source checkout used by the build recipe;
the binary archives come from the separately locked prebuilt repository.
Matching every prebuilt archive to corresponding source and completing the
notice set remain Owner release-gate work and are not implied by the presence
of the review copies.

### v2 Windows IME candidate

The x64 `ClipVaultImeHost.exe` dynamically loads the official librime 1.16.1
`rime.dll` from `rime-de4700e-Windows-msvc-x64.7z`, SHA-256
`e17c1bb4acc9934669e7a62003aef3f8b56d0afa89e5d893ed7dbf34546abb6e`.
The x64 and x86 TSF DLLs do not load librime. The candidate also bundles the
same locked rime-pinyin-simp dictionary revision used by Android.

The current Windows package stages the librime BSD-3-Clause text and the
rime-pinyin-simp Apache-2.0 text. `RIME_SDK_LOCK.json` does not enumerate the
transitive composition of the official `rime.dll`; consequently this notice
does not claim that yaml-cpp, LevelDB, OpenCC, marisa-trie, or any other
transitive component is present or absent from that binary. The exact binary
composition and all applicable transitive notices remain an Owner review item.

### v2 Owner-controlled internal daily-use and distribution gates

An internal daily-use candidate may pass its Owner evidence gate while
`project_license.status: internal_only` remains active, provided the Owner has
reviewed the applicable third-party notices for that internal scope. Such a
pass does not authorize transfer of the binaries or source to another person
or organization.

Public or external v2 distribution remains blocked until the Owner records all
of the following against one immutable candidate:

- the license governing ClipVault-owned code and the matching root license
  file;
- complete Android corresponding-source/provenance and notice review for the
  hash-locked prebuilt archives;
- complete Windows `rime.dll` composition and transitive notice review;
- signed Android IME/Runtime, Windows DLL/EXE, and installer artifacts;
- manual device/application QA, seven-day daily-use evidence, and explicit
  release approval.
