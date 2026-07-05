# ClipVault v1.6.0 Release Owner Packages

> Scope: Issue #36 release gate only. This document does **not** claim that `v1.6.0` is released, signed, QA-passed, or stable. It defines the Owner-controlled evidence flow required before Issue #36 can be closed.

## 1. Non-negotiable boundary

Issue #36 can close only after all rows below are backed by evidence:

1. The `release` GitHub environment exists and has the intended approval policy.
2. The `release` environment contains these Android release signing secret names:
   - `ANDROID_RELEASE_KEYSTORE_B64`
   - `ANDROID_RELEASE_KEYSTORE_PASSWORD`
   - `ANDROID_RELEASE_KEY_ALIAS`
   - `ANDROID_RELEASE_KEY_PASSWORD`
3. `Release artifact build` has run on `main` for `version=v1.6.0`.
4. Downloaded release artifacts have been verified from bytes, not inferred from a green workflow run.
5. Manual Android device QA is recorded.
6. Manual IME privacy QA is recorded.
7. Manual sync QA is recorded.
8. Manual Windows clipboard privacy QA is recorded.
9. If Owner approves publication, GitHub Release `v1.6.0` is created/reviewed/published with the expected assets, checksums, and manifests.

## 2. Parallel agent cluster

| Agent | Lane | Can run in parallel? | Output |
|---|---|---:|---|
| Release Coordinator | Tracks Issue #36 truth table and blocks premature closure | Yes | Consolidated issue-comment draft |
| Environment Agent | Checks environment and required secret names | Partially; secret values remain Owner-only | Environment evidence section |
| CI Evidence Agent | Checks current `main` CI and release-candidate dry run | Yes | CI/dry-run URLs and commit SHA |
| Artifact Agent | Verifies signed release artifact package | After env secrets | Artifact evidence JSON/comment |
| Windows QA Agent | Runs desktop installer/portable/manual clipboard privacy checks | After Windows artifact exists | Windows QA evidence rows |
| Android QA Agent | Runs pairing/share/QS tile/panel IME checks on device | After Android APK exists | Android QA evidence rows |
| IME Privacy Agent | Tests password/incognito/unknown field suppression and no typed-text logging | After APK exists | IME privacy evidence rows |
| Sync QA Agent | Tests public sync and secret/private isolation desktop <-> Android | After both artifacts exist | Sync QA evidence rows |
| Release Publisher | Creates/reviews/publishes release only after all evidence passes | Last only | Release URL + final checklist update |

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

The Release Coordinator may mark Issue #36 ready to close only when every row has one of these evidence sources:

- GitHub Actions run URL for current `main`;
- downloaded artifact byte verification report;
- manual QA evidence JSON plus rendered issue comment;
- GitHub Release URL and asset list;
- Owner statement approving publication.

A green workflow run alone is not sufficient for artifact evidence. Downloaded bytes must be verified through `RELEASE_MANIFEST.json`, `SHA256SUMS.txt`, and Android signing verification evidence.

## 6. Things this flow must not do

- Do not use auto-close keywords for Issue #36 in commit messages or PR bodies.
- Do not treat unsigned release-candidate artifacts as release artifacts.
- Do not paste secret values, keystores, tokens, private clipboard contents, or unredacted logs into GitHub.
- Do not expand product scope while closing this gate.
- Do not publish `v1.6.0` without Owner approval.
