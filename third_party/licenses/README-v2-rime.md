# v2 Rime license review copies

These files are repository copies of the license texts currently staged by the
v2 Android production build. They make the candidate notice inputs reviewable
and hashable without treating a successful build as a legal approval.

The yaml-cpp, LevelDB, OpenCC and marisa-trie texts were copied from the locked
librime `1.16.1` source checkout used by the build recipe. The Android native
archives themselves come from the separately hash-locked
`fcitx5-android/prebuilt` input. Matching every archive to corresponding source
and completing the notice set remain Owner release-gate items.

The rime-pinyin-simp text is copied from the exact dictionary commit recorded
in `shared-input/rime/RIME_ASSET_LOCK.json`.

These copies do not choose a license for ClipVault-owned code and do not grant
permission to distribute a ClipVault binary.
