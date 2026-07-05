# v1.6.0 Evidence Workspace

This directory is for release evidence templates and local Owner notes. It is not release evidence by itself.

## Expected evidence classes

| Evidence | Source |
|---|---|
| Current-main CI | GitHub Actions run URL |
| Release-candidate dry run | GitHub Actions run URL |
| Release environment | Owner-confirmed environment and required secret names |
| Signed release artifacts | Release artifact workflow + downloaded byte verification |
| Android device QA | Manual test evidence on physical/emulated Android device |
| IME privacy QA | Manual password/incognito/unknown-field privacy evidence |
| Sync QA | Manual desktop <-> Android sync evidence |
| Windows clipboard privacy QA | Manual Windows source-app/exclusion-format evidence |
| Final GitHub Release | Release URL, asset names, checksums, manifests |

## Recommended local files

```text
manual-qa-v1.6.0.json
release-artifacts-v1.6.0.json
issue-36-comment-draft.md
screenshots/
logs-redacted/
```

Do not commit secrets, tokens, private clipboard contents, raw signed keystores, APK signing passwords, or unredacted logs.
