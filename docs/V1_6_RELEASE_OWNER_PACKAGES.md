# ClipVault v1.6.0 Release Owner Packages

> Scope: Issue #36 release gate only. This document does not claim that v1.6.0 is released, signed, QA-passed, or stable. It defines the Owner-controlled evidence flow required to close the gate.

## 1. Non-negotiable boundary

Issue #36 can close only after all of the following are true and recorded as evidence:

1. The `release` GitHub environment exists and has the intended approval policy.
2. Android release signing secrets exist in the `release` environment:
   - `ANDROID_RELEASE_KEYSTORE_B64`
   - `ANDROID_RELEASE_KEYSTORE_PASSWORD`
   - `ANDROID_RELEASE_KEY_ALIAS`
   - `ANDROID_RELEASE_KEY_PASSWORD`
3. `Release artifact build` has run on `main` for `version=v1.6.0`.
4. Downloaded release artifacts have been verified from bytes, not only from a green workflow run.
5. Manual Android device QA is recorded.
6. Manual IME privacy QA is recorded.
7. Manual sync QA is recorded.
8. Manual Windows clipboard privacy QA is recorded.
9. If Owner approves publication, GitHub Release `v1.6.0` is created/reviewed/published with the expected assets, checksums, and manifests.

## 2. Parallel agent cluster

| Agent | Lane | Can run in parallel? | Output |
|---|---|---:|---|
| Release Coordinator | Tracks Issue #36 truth table and blocks premature closure | Yes | One consolidated issue-comment draft |
| Environment Agent | Checks environment and required secret names | Partially; Owner-only for secrets | Environment evidence section |
| CI Evidence Agent | Checks current `main` CI and release-candidate dry run | Yes | CI/dry-run URLs and commit SHA |
| Artifact Agent | Runs/verifies signed release artifact package | After env secrets | Artifact evidence JSON + issue comment |
| Windows QA Agent | Runs desktop installer/portable/manual clipboard privacy checks | After Windows artifact exists | Windows QA evidence rows |
| Android QA Agent | Runs pairing/share/QS tile/panel IME checks on device | After Android APK exists | Android QA evidence rows |
| IME Privacy Agent | Tests password/incognito/unknown field suppression and no typed-text logging | After APK exists | IME privacy evidence rows |
| Sync QA Agent | Tests public sync and secret/private isolation desktop <-> Android | After both artifacts exist | Sync QA evidence rows |
| Release Publisher | Creates draft/published release only after all evidence passes | Last only | Release URL + final checklist update |

## 3. Serial dependency graph

```text
main commit frozen
  ├─ CI Evidence Agent
  ├─ Release Candidate Dry Run Evidence Agent
  └─ Environment Agent
       └─ Release artifact build
            ├─ Artifact Agent byte verification
            ├─ Windows QA Agent
            ├─ Android QA Agent
            ├─ IME Privacy Agent
            └─ Sync QA Agent
                 └─ Release Coordinator final review
                      └─ Release Publisher
                           └─ Issue #36 closure candidate
```

## 4. Required local pack

Run:

```powershell
python tools/prepare_v1_6_release_owner_pack.py --out-dir .\release-owner-pack-v1.6.0
```

This generates:

```text
release-owner-pack-v1.6.0/
  OWNER_RELEASE_ACTION_PACK.md
  issue-36-comment-draft.md
  manual-qa-v1.6.0.template.json
  release-artifacts-v1.6.0.template.json
  agent-cluster.md
  pack-summary.json
```

The pack is a coordination artifact only. It does not set GitHub secrets, trigger workflows, download artifacts, run device QA, sign APKs, create releases, or close Issue #36.

## 5. Closure rule

The release coordinator may mark Issue #36 ready to close only when every row has one of these evidence sources:

- GitHub Actions run URL for current `main`;
- downloaded artifact byte verification report;
- manual QA evidence JSON plus rendered issue comment;
- GitHub Release URL and asset list;
- Owner statement approving publication.

A green workflow run alone is not sufficient for artifact evidence. Downloaded bytes must be verified through `RELEASE_MANIFEST.json`, `SHA256SUMS.txt`, and Android signing verification output.
