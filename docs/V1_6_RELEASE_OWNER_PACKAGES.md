# ClipVault v1.6.0 Release Owner Packages

> Scope: Issue #36 only. This document does not claim that v1.6.0 is released,
> signed by the expected Owner identity, QA-passed, or stable.

## 1. Non-negotiable boundary

Issue #36 can close only after all of the following are true and recorded for
one exact current-main commit:

1. Current-main CI and release-candidate dry run both pass on that commit.
2. The `release` GitHub environment exists with the Owner-approved policy.
3. The required Android signing secrets exist in that environment:
   - `ANDROID_RELEASE_KEYSTORE_B64`
   - `ANDROID_RELEASE_KEYSTORE_PASSWORD`
   - `ANDROID_RELEASE_KEY_ALIAS`
   - `ANDROID_RELEASE_KEY_PASSWORD`
4. The workflow enforces an independently confirmed 64-hex Owner certificate
   SHA-256 before attestation or upload. Missing,
   malformed, multi-signer, and mismatched certificates fail closed; a generic
   valid APK signature is not sufficient.
5. The final `draft=true` run produces the draft Release asset set.
6. Downloaded bytes, manifests, attestations, APK signer identity, and SHA-256
   values are verified from that same run and draft.
7. API 26 and API 27 execute the named non-skipped CursorWindow regression with
   SDK, JUnit, debug app APK, and instrumentation APK evidence.
8. Physical Android/IME/sync and Windows QA use the exact final draft assets.
9. Owner approval binds the target commit, draft URL, and final digest set.
10. The existing draft is published without rebuilding, and the resulting
    non-prerelease Release targets the same commit with the exact assets.

## 2. Dependency graph

```text
freeze one clean current-main SHA
  -> current-main CI + RC dry run
  -> release environment policy + secret names
  -> Owner Android certificate identity enforcement
  -> draft=false preflight
  -> draft=true final draft asset build
  -> exact-run provenance, signer, byte, and digest verification
  -> API 26 + API 27 compatibility QA
  -> physical signed-APK / IME / sync / Windows QA
  -> Owner publication approval
  -> publish the existing draft without rebuilding
  -> readiness review and Issue #36 closure candidate
```

Parallel agents may collect read-only state and prepare commands, but no agent
may infer secret values, physical-device observations, or Owner approval.

## 3. Generate the ignored local pack

From a clean checkout of current `main`:

```powershell
python tools/prepare_v1_6_release_owner_pack.py
```

The default output is already ignored by Git:

```text
.field-test-artifacts/v1.6.0-owner-pack/
  OWNER_RELEASE_ACTION_PACK.md
  issue-36-comment-draft.md
  manual-qa-v1.6.0.template.json
  release-artifacts-v1.6.0.template.json
  agent-cluster.md
  pack-summary.json
```

The generator refuses a non-empty output directory by default. `--force` may
replace only these six known regular files; unknown files are preserved.
Symlinked, junction, and reparse-point paths, directories at known file paths,
and hard-linked known files are rejected. All conflicts are checked before any
pack file is replaced, and a failed replacement attempts to restore every
previous known file before returning an error.

The manual template is generated directly from
`tools/manual_qa_evidence.py`, so it uses schema v2 and the exact 18-item gate.
The release-artifact JSON is explicitly a coordination worksheet and is not
accepted as validator evidence.

## 4. Evidence handling

Follow `OWNER_RELEASE_ACTION_PACK.md` and `docs/MANUAL_QA_V1_6_0.md`. In
particular:

- do not manually change the generated Issue draft from BLOCKED to PASS;
- render manual evidence through `tools/manual_qa_evidence.py`;
- do not treat `ANDROID_APKSIGNER_VERIFY.txt` shape as Owner identity proof;
- do not post absolute local paths, private clipboard content, device serials,
  keystore material, passwords, or unredacted logs;
- do not use QA from the `draft=false` preflight to approve assets rebuilt by
  the later `draft=true` run.
- set `GIT_EXE_PATH`, `GH_CLI_PATH`, and `PYTHON_EXE_PATH` to absolute trusted
  `.exe` files outside the workspace, `APKSIGNER_JAR_PATH` to Android SDK
  `lib/apksigner.jar`, and `JAVA_EXE_PATH` to an absolute trusted `java.exe`;
  batch launchers, UNC/device namespace paths, non-fixed drives, tool paths with
  any reparse-point ancestor, and workspace-local tools are rejected;
- run every generated PowerShell block from the exact repository root and clean
  frozen target; subdirectory execution is rejected before a verifier runs, Git
  and GitHub CLI injection variables are sanitized, and critical validator source
  bytes are matched to the frozen commit before and after execution;
- run `tools/release_artifact_evidence.py --require-live-final-draft`; its JSON
  and path-free comment are evidence inputs, not self-authenticating proof, so
  readiness must rerun or live-cross-check every security-relevant claim;
- retain the generated artifact binding SHA-256 so manual QA and publication
  approval can be tied to the same exact eight files; copy that binding from the
  posted Owner approval into Step H, which recomputes it and consumes only the
  verifier's in-memory publication projection;
- keep both `main` and the exact `refs/tags/v1.6.0` frozen during Step H; the
  generated block resolves direct or annotated tags before and after publication;
- after publication, re-download with the trusted GitHub CLI, then run the same
  tracked verifier against that fresh directory to revalidate the exact
  published Release, tag object, eight asset identities and bytes,
  attestations, run attempt, current `main`, and Owner signer. Retain its separate
  publication-closure binding and path-free comment draft for final gate review.

## 5. Closure rule

A green workflow alone is not artifact evidence. A filled coordination template
alone is not QA evidence. Issue #36 remains open until verified artifact and
manual-QA reports, an Owner publication statement, and the final published
Release all bind the same current-main commit and exact asset digest set. The
publication-closure binding is evidence of that linkage; it is not permission to
complete the gate by itself.
