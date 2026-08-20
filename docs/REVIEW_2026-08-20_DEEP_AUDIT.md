# ClipVault Personal — Deep Review, 2026-08-20

Read-only audit (`AGENT_WORKFLOWS.md` step 1, Repo Auditor role). No product
code, policy doc, or Secret Guard rule was changed by this review. Items in
§3 touch Secret/privacy policy and §1 touches release-boundary docs, both of
which are Owner-gated per `AGENT_WORKFLOWS.md`.

Baseline commit: `95497cf` (`claude/clipvault-deep-review-4dcnab`, identical to
`origin/main`).

## Summary

| # | Finding | Severity | Area |
|---|---|---|---|
| 1 | main CI is red; a docs commit broke the v2.0 boundary gate | Blocking | Build health |
| 2 | Documented Linux verification gate cannot pass | High | Process / verifiability |
| 3 | `SG-ASSIGN` misses snake_case key names (`DB_PASSWORD=…`) | High | Secret Guard |
| 4 | HTTP(S) URLs with embedded credentials are not detected | High | Secret Guard |
| 5 | Azure storage connection strings are not detected | Medium | Secret Guard |
| 6 | `use_memory` succeeds on a soft-deleted item | Low | Local API |
| 7 | `delete_memory` emits a sync event per redundant call | Low | Local API / sync outbox |

Areas audited and found clean are recorded in §5. Nothing was invented to pad
this list.

---

## 1. main CI is red — a docs commit broke the v2.0 boundary gate

**Status: reproducible on Linux and confirmed red in GitHub Actions.**

The last two commits on `main` both fail CI:

- run 552 / `95497cf` — conclusion `failure`
- run 551 / `99735d5` — conclusion `failure`
- run 535 / `9486107` — conclusion `success` (last green main)

The failing job is `Desktop tests` (windows-latest). Its log ends:

```
FAILED tests/test_v2_keyboard_readiness.py::test_current_repo_reports_static_v2_keyboard_evidence_but_keeps_owner_gate_blocked - assert 2 == 1
FAILED tests/test_v2_keyboard_readiness.py::test_cli_json_no_fail_emits_machine_readable_blocked_report - assert 2 == 1
2 failed, 1357 passed in 243.14s (0:04:03)
```

### Cause

`tools/v2_keyboard_readiness.py` `check_docs_release_boundaries()` requires two
exact marker strings in `docs/HANDOFF.md`:

- `v2.0 dual-IME stability planning`
- `v2.0 stays planning/stability-only`

Commit `99735d5` ("docs: add emulator policy and refresh v1.6 handoff baseline")
rewrote the `Current slice` table row and removed both sentences. The gate
therefore flipped from `pass` to `blocked`, taking the report's blocked count
from 1 to 2 and breaking the two tests that pin it at 1.

```
$ python tools/v2_keyboard_readiness.py --json --no-fail
  [blocked] v2.0 docs/release boundary
      problems: [
        "docs/HANDOFF.md is missing marker: v2.0 dual-IME stability planning",
        "docs/HANDOFF.md is missing marker: v2.0 stays planning/stability-only"
      ]
  [blocked] Owner/manual release gate      <- expected, pre-existing
```

Marker presence, before and after:

```
marker: v2.0 dual-IME stability planning     9486107: 1   HEAD: 0
marker: v2.0 stays planning/stability-only   9486107: 1   HEAD: 0
```

The follow-up commit `95497cf` ("test: align handoff assertions with v1.6
baseline") updated `test_release_alignment.py` for the same HANDOFF rewrite but
did not touch `test_v2_keyboard_readiness.py`, so main was left red.

### Why this needs an Owner decision, not a mechanical fix

The gate encodes release-boundary policy: it asserts that HANDOFF still states
v2.0 is planning-only. `99735d5` deliberately reset HANDOFF to a post-v1.6.0
baseline. Two different repairs are possible and they mean different things:

- **Restore the markers** into the new HANDOFF baseline — keeps the v2.0
  planning-only boundary asserted where the gate expects it.
- **Update the gate's required markers** — accepts that the boundary is now
  expressed elsewhere (`AGENTS.md` and `docs/STABILITY_PLAN_V2_0.md` still carry
  their own required markers and still pass).

Restoring the markers is the smaller change and preserves the existing gate
semantics, but choosing between them is a release-boundary call.

---

## 2. The documented Linux verification gate cannot pass

`AGENT_WORKFLOWS.md` step 5 makes this the pre-merge gate:

```
python -m pytest -q --ignore=tests/test_watcher.py --ignore=tests/test_instance_lock.py
（Linux 跳过 4 个 Windows-only）
```

That command does not run on Linux. Two separate problems.

### 2a. The documented ignore list is incomplete

The command aborts during collection:

```
ERROR tests/test_backup_cancellation.py - AttributeError: module 'ctypes' has no attribute 'WinDLL'
ERROR tests/test_main_cli.py           - AttributeError: module 'ctypes' has no attribute 'WinDLL'
ERROR tests/test_release_alignment.py  - AttributeError: module 'ctypes' has no attribute 'WinDLL'
ERROR tests/test_runtime_app.py        - AttributeError: module 'ctypes' has no attribute 'WinDLL'
!!!!!!! Interrupted: 4 errors during collection !!!!!!!
```

Four more modules transitively import `clipvault.instance_lock` or
`clipvault.watcher.win_clipboard`, both of which call `ctypes.WinDLL` at module
import time. Six ignores are needed on Linux, not two. The prose "跳过 4 个
Windows-only" also disagrees with the two `--ignore` flags actually written.

### 2b. One test carries a Windows-only golden constant

With all six ignored, the suite still fails:

```
3 failed, 1215 passed, 9 skipped in 23.41s
FAILED tests/test_release_artifact_evidence_live.py::test_binding_is_stable_across_validation_time
FAILED tests/test_v2_keyboard_readiness.py::... (the two from §1)
```

`test_binding_is_stable_across_validation_time` asserts a hardcoded value:

```python
assert report_a["artifact_binding_sha256"] == (
    "47fee9fc970ac007863c5aac0bd9bbbe96b9afcd7577f51a98e3cecb9a93c383"
)
```

On Linux the computed value is `f0b0fb706678cf6d445e54998452d663a48faf186665d85ae837b580901e7bd8`.

This is not flakiness and not a recent regression:

- The value is stable across repeated `_collect()` calls in one process and
  across separate processes.
- It is byte-identical at `8874b3c` (the commit that introduced the golden),
  `531d177`, and `9486107` — the golden has never matched on Linux.
- The same test **passes** in Windows CI: the job log for run 552 lists only the
  two readiness failures out of 1357 tests.

The other assertions in the test — self-consistency and independence from
`validated_at` — pass on Linux. Only the pinned constant is wrong here.

**Mechanism.** The binding projection covers each fixture artifact's
`size_bytes` and `sha256`. `_build_fixture()` writes its text artifacts with
`Path.write_text(..., encoding="utf-8")` and no `newline=""`, e.g.

```python
(android / "ANDROID_APKSIGNER_VERIFY.txt").write_text(
    f"Signer #1 certificate SHA-256 digest: {OWNER_CERT}\n", encoding="utf-8",
)
```

On Windows Python's text mode translates `\n` to `\r\n`, so the fixture bytes —
and therefore the sizes and digests fed into the binding hash — differ by
platform. On Linux this file is 103 bytes; on Windows it is 104.

(Simulating CRLF translation alone did not reproduce `47fee9…` exactly, so at
least one other fixture writer contributes as well. The platform dependence is
established by the Windows-vs-Linux result; the exact per-file accounting was
not chased further.)

### Impact

`.github/workflows/ci.yml` runs `Desktop tests` **only** on `windows-latest`,
with a comment explaining the `ctypes.WinDLL` collection problem. So the
platform assumption is never exercised in CI, and a contributor working on Linux
— which is what this repository's own agent workflow prescribes for 🟢
locally-verifiable work — cannot produce the green run that step 5 demands. The
gate is currently unsatisfiable rather than merely inconvenient.

Suggested direction (not applied): make the fixture writers newline-explicit
(`newline=""`) so the binding hash is platform-independent, then re-record the
golden; and correct the ignore list and the "4 个" prose in
`AGENT_WORKFLOWS.md`. Both are Owner-visible changes to the verification
contract.

---

## 3. `SG-ASSIGN` misses snake_case key names

`clipvault/core/secret_guard.py:33-39`:

```python
r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
r"|client[_-]?secret|auth)\b\s*[:=]\s*\S{8,}"
```

`_` is a word character, so `\b` never matches between `DATABASE_` and
`PASSWORD`. The rule fires on a bare key name and silently stops firing the
moment the name is prefixed — which is how these values almost always appear in
a developer's clipboard.

Measured, same value in both rows:

```
SECRET  ['SG-ASSIGN']   PASSWORD=Tr0ub4dor&3
** MISS **              DB_PASSWORD=Tr0ub4dor&3
```

Direct regex proof:

```
False  DATABASE_PASSWORD=xxxxxxxxx
False  DATABASEPASSWORD=xxxxxxxxx
True   DATABASE.PASSWORD=xxxxxxxxx
True   DATABASE-PASSWORD=xxxxxxxxx
```

The entropy fallback (SG-ENTROPY) partially masks this, but only when the whole
content is a single token drawn from `[A-Za-z0-9+/=_-]`. Any punctuation in the
value defeats `_TOKEN_CHARS`, so the fallback drops out exactly where the
password is strongest:

```
** MISS **  DATABASE_PASSWORD=sup3rS3cretValue!
** MISS **  DB_PASSWORD=Tr0ub4dor&3
** MISS **  MYSQL_ROOT_PASSWORD=hunter2!@#$
** MISS **  REDIS_PASSWORD=p@ssw0rd-with-symbols!
** MISS **  SMTP_PASSWORD=Xy9$mK2#qL8@wR4
```

A missed clip is not merely un-flagged in the UI: it is public, so it enters
`clips_fts`, the sync outbox to paired phones, the GitHub backup JSONL, and the
Obsidian vault.

`SG-ENV` does not cover this either — it requires **two or more** `KEY=value`
lines, so a single copied `.env` line is unprotected.

---

## 4. HTTP(S) URLs with embedded credentials are not detected

`SG-CONNSTR` covers `postgres(ql)`, `mysql`, `mongodb(+srv)`, `redis`, and
`amqp` only:

```
SECRET      ['SG-CONNSTR']  mongodb://user:pw123456@host:27017/db
** MISS **  []              https://user:token@github.com/org/repo.git
** MISS **  []              https://admin:Str0ngP@ss@git.internal.example/repo.git
```

A git remote carrying a personal access token is a routine clipboard item and a
live credential. Extending the scheme alternation to `https?` would close this;
whether that is acceptable false-positive risk is an Owner/Privacy-gate call.

---

## 5. Azure storage connection strings are not detected

```
** MISS **  DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=U29tZVZlcnlMb25nQmFzZTY0S2V5VmFsdWVIZXJlPT0=;EndpointSuffix=core.windows.net
```

Three rules each miss it for a different reason:

- `SG-ASSIGN` — `AccountKey` matches none of the alternatives (`api[_-]?key`
  and `access[_-]?key` require those exact stems; plain `key` is not listed, and
  `\b` fails inside `AccountKey` regardless).
- Whole-content entropy — the string contains `;`, `:` and `/`, so it fails
  `_TOKEN_CHARS`.
- Per-token entropy — `content.split()` splits on whitespace, and the string has
  none, so the only token is the whole string, which fails `_TOKEN_CHARS` again.

### Related accepted risk (not a defect)

SG-1.1 deliberately excludes pure hex of length 32/40/64 as "provably not
credentials by shape alone". That exclusion also covers real 32-hex API keys —
Twilio auth tokens are the clearest example:

```
** MISS **  8f9a7c6b5d4e3f2a1b0c9d8e7f6a5b4c
```

This is a documented trade-off in the module docstring, not a regression.
Recording it here so the next Secret Guard revision can revisit it with the
false-positive cost in view.

---

## 6. `use_memory` succeeds on a soft-deleted item

`MemoryRepo.list()` filters `deleted=0` and re-scans for legacy secrets;
`MemoryRepo.get()` does neither. `Api.use_memory` gates only on `get(...) is
None`, so a deleted item is still "usable":

```
create      -> 201 use_count 0
delete      -> (200, {'deleted': True})
list after  -> {'memory': []}
USE deleted -> (200, {'used': True})
USE deleted -> (200, {'used': True})
db row      -> deleted=1 use_count=2 last_used_at=2026-08-20T20:44:59Z
```

`use_count` never decreases and `upsert` revives with
`use_count=MAX(use_count, ?)`, so a delete → re-create cycle brings the item
back with the inflated ranking it accumulated while deleted. Loopback-only
surface, so impact is confined to local suggestion ordering.

---

## 7. `delete_memory` emits a sync event per redundant call

`soft_delete` runs `UPDATE memory_items SET deleted=1 WHERE id=?` and reports
`rowcount > 0` whenever the row exists — including when it is already deleted.
`Api.delete_memory` therefore returns 200 every time and calls
`emit_memory_delete` every time:

```
after create:                       [('memory_upsert', 1)]
after 5x DELETE of the same item:   [('memory_delete', 5), ('memory_upsert', 1)]
```

Five no-op deletes produced five outbox rows, each of which is pulled by every
paired peer. A retrying client inflates the outbox without bound.

Adding `AND deleted=0` to the `soft_delete` predicate makes `rowcount` truthful,
which makes both the HTTP response and the sync emission idempotent — that
closes §7 on its own.

§6 needs a separate decision. Filtering `deleted=0` inside `MemoryRepo.get()`
is the obvious move but breaks `upsert()`'s return contract: `upsert` ends with
`return self.get(...)`, so against a tombstoned row with `revive_deleted=False`
it would start returning `None` where its annotation promises a `MemoryItem`.
No current caller trips on that — `_apply_memory_upsert` is the only
`revive_deleted=False` call site and it discards the result — but it is a trap
for the next one. The filter belongs at the two API call sites (`use_memory`,
`delete_memory`) or in a separate `get_active()` accessor, leaving `get()` as
the raw row reader `upsert` depends on.

---

## 8. Areas audited and found clean

Recording these so the audit is honest about where no problem was found.

- **Web UI XSS** — `api/webui/app.js` uses `textContent` exclusively; no
  `innerHTML`, `insertAdjacentHTML`, `outerHTML`, `document.write`, or `eval`.
  Backed by a strict CSP (`default-src 'none'`, `script-src 'self'`,
  `frame-ancestors 'none'`) plus `nosniff`, `X-Frame-Options: DENY`, and
  `Cache-Control: no-store` on every response.
- **Android IME privacy boundary** — no network imports, no `Log.*` calls, and
  no `getTextBeforeCursor` / `getExtractedText` / `onUpdateSelection` read path
  in `ime/`. The only `InputConnection` use is outbound `commitText`. The
  product invariants in `AGENTS.md` hold in the source, independent of the
  static tests that assert them.
- **Obsidian path construction** — `_slug()` strips `\ / : * ? " < > | # ^ [ ]`
  and the slug is embedded between fixed date and id segments, so no traversal
  is reachable from clip content. Writes are atomic (`os.replace`) with a
  collision suffix and never overwrite.
- **Backup secret gating** — independent re-checks at enqueue
  (`BackupQueueRepo.enqueue`/`reenqueue` raise `SecretEnqueueError`), at
  serialization, and again before Git publication, plus unpublished-scrub
  recovery. JSONL writes are inode- and symlink-hardened
  (`O_NOFOLLOW`, `st_nlink != 1` rejection, stat-signature revalidation across
  the whole read/write window).
- **Sync Gate A/B** — every outbox exit path and every peer-apply path re-scans
  content under current rules rather than trusting persisted `is_secret`;
  `apply_push` refuses to advance the ack cursor across a gap, which is the
  property that prevents a peer from discarding an unacknowledged event.
- **Migrations** — `db.migrate` refuses a dirty connection, rejects attached or
  temp schemas, verifies a contiguous manifest, runs `quick_check` +
  `foreign_key_check` before DDL, and holds a cross-process lock.

## 9. Test baseline

Linux, Python 3.11, six Windows-only modules ignored:

```
3 failed, 1215 passed, 9 skipped in 23.41s
```

Windows CI, run 552 (`95497cf`), full suite:

```
2 failed, 1357 passed in 243.14s
```

The 3-vs-2 difference is finding §2b; the 1215-vs-1357 difference is the
Windows-only tests that cannot be collected on Linux.

## 10. Suggested order of work

1. Restore main to green (§1) — Owner picks marker restoration vs. gate update.
2. Make the Linux gate satisfiable (§2) so subsequent 🟢 work is verifiable at
   all: fix the ignore list and prose, make the evidence fixture
   newline-explicit, re-record the golden.
3. Secret Guard revision (§3, §4, §5) as one ADR-backed change with new test
   vectors on both sides of `contracts/`, since it changes privacy semantics and
   is an Owner/Privacy-gate decision.
4. Memory idempotency (§6, §7) as a small independent patch.
