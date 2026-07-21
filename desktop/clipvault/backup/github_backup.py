"""Backup worker (GHB-1). Gate C lives here: every clip and its origin metadata
are re-scanned with the current Secret Guard rules immediately before
serialization, so a secret that slipped past gates A/B is still dropped here.
"""

import logging
import threading
from dataclasses import replace
from datetime import datetime, timezone

from clipvault.core import origin_metadata, secret_guard
from clipvault.backup import cancellation, git_repo, jsonl_store
from clipvault.backup.repo_lock import RepoLockTimeout, RepoWriteLock
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.unit_of_work import unit_of_work

log = logging.getLogger("clipvault.backup")

_BACKOFF_START_S = 60
_BACKOFF_MAX_S = 1800  # 30 min cap


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _origin_metadata_requires_quarantine(clip) -> bool:
    return not origin_metadata.origin_metadata_is_safe(
        clip.source_device, clip.source_app
    )


def _requires_quarantine(clip) -> bool:
    """Apply Gate C; Owner release exempts content, never origin metadata."""

    return (
        _origin_metadata_requires_quarantine(clip)
        or clip.is_secret
        or (not clip.released and secret_guard.scan(clip.content).is_secret)
    )


class BackupWorker:
    def __init__(self, conn, repo_path: str, *, remote: str = "origin",
                 push_enabled: bool = True, now_fn=_utc_now):
        self.conn = conn
        self.repo_path = repo_path
        self.remote = remote
        self.push_enabled = push_enabled
        self.now_fn = now_fn
        self.clips = ClipsRepo(conn)
        self.queue = BackupQueueRepo(conn)
        self._backoff_s = _BACKOFF_START_S
        self._monotonic_blocked_until = 0.0  # set by caller's clock; 0 = ready
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        """Idempotently cancel in-flight local/remote backup work."""

        self._stop_event.set()

    def run_once(self, monotonic: float = 0.0) -> dict:
        """Serialize pending clips, commit, push. Returns a small stats dict.
        `monotonic` lets the scheduler honour push backoff deterministically."""
        with cancellation.cancellation_scope(self._stop_event):
            try:
                with RepoWriteLock(
                    self.repo_path,
                    cancel_event=self._stop_event,
                ):
                    stats = self._persist_pending()
            except RepoLockTimeout:
                # Another process owns the same backup repository. Nothing was
                # claimed or acknowledged, so a later maintenance pass can retry.
                log.info("backup repository busy; local write deferred")
                return {
                    "written": 0,
                    "dropped": 0,
                    "committed": None,
                    "pushed": False,
                }
            except git_repo.GitWorktreeRecoveryRequired:
                if not self.push_enabled:
                    raise
                # A prior recovery may have scrubbed managed files before its CAS
                # durability point. Do not create a non-append child of the old
                # contaminated ref; let the recovery path resume from Git objects.
                log.info("managed backup worktree requires push recovery")
                stats = {
                    "written": 0,
                    "dropped": 0,
                    "committed": None,
                    "pushed": False,
                }

            pushed = False
            # Retry push even when this run had no new commits; a previous push may
            # have failed after data was safely committed locally.
            if self.push_enabled and git_repo.head_commit(self.repo_path) is not None:
                pushed = self._try_push(monotonic)
            stats["pushed"] = pushed
            return stats

    def _persist_pending(self) -> dict:
        """Persist and acknowledge one queue batch while holding the repo lock."""

        git_repo.assert_managed_worktree_append_only(self.repo_path)
        pending = self.queue.claim_pending()
        written = 0
        dropped = 0
        by_day: dict[str, list[str]] = {}
        mark_after_commit: list[tuple[str, str, str, str]] = []

        for clip_id in pending:
            cancellation.checkpoint(self._stop_event)
            clip = self.clips.get(clip_id)
            if clip is None:
                with unit_of_work(self.conn):
                    if self.clips.get(clip_id) is None:
                        self.queue.mark_dropped(
                            clip_id,
                            "clip_missing",
                            commit=False,
                        )
                continue
            # Gate C: re-scan with current rules.
            verdict = secret_guard.scan(clip.content)
            if (
                _origin_metadata_requires_quarantine(clip)
                or clip.is_secret
                or (verdict.is_secret and not clip.released)
            ):
                transitioned = False
                with unit_of_work(self.conn):
                    # Serialize the drop decision with Owner release/reenqueue.
                    # Whichever transaction commits last observes the other's
                    # state rather than overwriting a newer public intent.
                    current = self.clips.get(clip_id)
                    if current is not None and _requires_quarantine(current):
                        drop_reason = (
                            "gate_c_origin_metadata"
                            if _origin_metadata_requires_quarantine(current)
                            else "gate_c_secret"
                        )
                        transitioned = self.queue.mark_dropped(
                            clip_id,
                            drop_reason,
                            commit=False,
                        )
                        clip = None
                    else:
                        clip = current
                if clip is None:
                    if transitioned:
                        log.error(
                            "gate C dropped suspected secret id=%s reasons=%s",
                            clip_id,
                            ",".join(verdict.reasons) or "stored_flag",
                        )
                        dropped += 1
                    continue
            relpath = jsonl_store.daily_relpath(clip.created_at)
            line = jsonl_store.serialize_clip(clip)
            by_day.setdefault(relpath, []).append(line)
            mark_after_commit.append((clip_id, self.now_fn(), relpath, line))
            written += 1

        committed = None
        if by_day:
            for relpath, lines in by_day.items():
                cancellation.checkpoint(self._stop_event)
                jsonl_store.append_latest_clip_states(
                    self.repo_path,
                    relpath,
                    lines,
                )
            # If commit fails, do not mark queue rows done. The next worker run
            # must retry because there is no durable recovery point yet.
            committed = git_repo.add_commit(
                self.repo_path,
                f"backup: {written} clips {self.now_fn()}",
                paths=sorted(by_day),
            )
            durable_head = committed or git_repo.head_commit(self.repo_path)
            if durable_head is None:
                raise git_repo.GitError("backup durable head verification failed")
            expected_by_path: dict[str, list[str]] = {}
            for clip_id, _done_at, relpath, _expected_line in mark_after_commit:
                expected_by_path.setdefault(relpath, []).append(clip_id)
            durable_lines: dict[tuple[str, str], str] = {}
            for relpath, clip_ids in expected_by_path.items():
                for clip_id, line in git_repo.commit_latest_clip_lines(
                    self.repo_path,
                    durable_head,
                    relpath,
                    clip_ids,
                ).items():
                    durable_lines[(relpath, clip_id)] = line
            with unit_of_work(self.conn):
                for clip_id, done_at, relpath, expected_line in mark_after_commit:
                    if durable_lines.get((relpath, clip_id)) != expected_line:
                        continue
                    current = self.clips.get(clip_id)
                    if current is None or _requires_quarantine(current):
                        continue
                    if jsonl_store.serialize_clip(current) != expected_line:
                        continue
                    if not self.queue.mark_done(clip_id, done_at, commit=False):
                        continue
                    self.clips.set_backed_up_at(
                        clip_id,
                        done_at,
                        commit=False,
                    )

        return {
            "written": written,
            "dropped": dropped,
            "committed": committed,
            "pushed": False,
        }

    def _try_push(self, monotonic: float) -> bool:
        if monotonic and monotonic < self._monotonic_blocked_until:
            log.info("push deferred (backoff)")
            return False
        try:
            with RepoWriteLock(
                self.repo_path,
                cancel_event=self._stop_event,
            ):
                if self.conn.in_transaction:
                    raise git_repo.GitPushError(
                        "backup database transaction already active"
                    )
                # Keep local Git/worktree/ref state single-writer through the
                # exact-SHA push, but never hold SQLite's writer lock across
                # remote commands (each may run until the Git timeout). The
                # supported runtime has no public-to-secret mutator; a future
                # reclassification feature must add a short publication fence,
                # not a database transaction spanning network I/O.
                authorization = self._authorize_with_safe_recovery(
                    repo_lock_held=True
                )
                if authorization is None:
                    self._backoff_s = _BACKOFF_START_S
                    self._monotonic_blocked_until = 0.0
                    log.info("push skipped after safe local recovery")
                    return False
                git_repo.push(
                    authorization,
                    final_validator=self._candidate_still_safe,
                )
        except RepoLockTimeout:
            # Recovery needs the same single-writer boundary as append/commit.
            # Local contention is not a remote failure and must not inflate the
            # network backoff window.
            log.info("backup repository busy; push recovery deferred")
            return False
        except git_repo.OwnerRemediationRequired as exc:
            # Rewriting already-published history is never automatic. Keep the
            # remote and local candidate untouched and make retries infrequent
            # until the Owner rotates the private backup branch/repository.
            self._backoff_s = _BACKOFF_MAX_S
            self._monotonic_blocked_until = (monotonic or 0.0) + _BACKOFF_MAX_S
            log.error(
                "backup push blocked; owner remediation required error=%s",
                exc.__class__.__name__,
            )
            return False
        except git_repo.GitPushError as exc:
            self._backoff_s = min(self._backoff_s * 2, _BACKOFF_MAX_S)
            self._monotonic_blocked_until = (monotonic or 0.0) + self._backoff_s
            log.error(
                "push failed, data committed locally; backoff=%ds error=%s code=%s",
                self._backoff_s,
                exc.__class__.__name__,
                getattr(exc, "returncode", None),
            )
            return False
        self._backoff_s = _BACKOFF_START_S
        self._monotonic_blocked_until = 0.0
        log.info("push ok")
        return True

    def _candidate_still_safe(self, candidate) -> bool:
        """Repeat Gate C after remote preflight, immediately before push."""

        latest: dict[str, tuple[str, str]] = {}

        def inspect(relpath: str, line: str) -> None:
            if not self._validate_unpublished_line(relpath, line):
                raise ValueError("backup line failed final Gate C")
            recorded = self._decode_unpublished_line(relpath, line)
            if recorded is None:
                raise ValueError("backup line failed final Gate C")
            latest[recorded.id] = (relpath, line)

        git_repo.inspect_unpublished_lines(candidate, visitor=inspect)
        return all(
            self._validate_unpublished_line(
                relpath,
                line,
                require_current_state=True,
            )
            for relpath, line in latest.values()
        )

    def _authorize_with_safe_recovery(self, *, repo_lock_held: bool = False):
        """Authorize a candidate, rebuilding at most one contaminated suffix."""

        if not repo_lock_held:
            with RepoWriteLock(
                self.repo_path,
                cancel_event=self._stop_event,
            ):
                return self._authorize_with_safe_recovery(repo_lock_held=True)

        recovery_attempted = False
        while True:
            candidate = git_repo.prepare_push(self.repo_path, self.remote)
            needs_recovery, replacements, drop_ids = self._candidate_recovery_plan(
                candidate
            )
            if needs_recovery:
                if recovery_attempted:
                    raise git_repo.GitPushError(
                        "backup candidate remained unsafe after recovery"
                    )
                # The caller holds the repository writer lock. Re-snapshot so a
                # standalone authorization call and a full push share one path.
                candidate = git_repo.prepare_push(self.repo_path, self.remote)
                needs_recovery, replacements, drop_ids = (
                    self._candidate_recovery_plan(candidate)
                )
                if needs_recovery:
                    still_quarantined = self._invalidate_recovered_secret_acks(
                        drop_ids
                    )
                    if still_quarantined != drop_ids:
                        # Owner release (or another eligibility transition) won
                        # the SQLite write race after the recovery plan. Do not
                        # scrub Git from a stale plan; the next worker pass will
                        # persist/re-plan the new durable state.
                        return None
                    recovered_tip = git_repo._rebuild_unpublished_candidate(
                        candidate,
                        replacements,
                    )
                else:
                    recovered_tip = candidate.candidate_sha
                recovery_attempted = True
                if recovered_tip is None:
                    return None
                continue

            latest_unpublished: dict[str, tuple[str, str]] = {}

            def validate_line(relpath: str, line: str) -> bool:
                if not self._validate_unpublished_line(relpath, line):
                    return False
                try:
                    clip_id = jsonl_store.deserialize_clip(line).id
                except (KeyError, TypeError, ValueError):
                    return False
                latest_unpublished[clip_id] = (relpath, line)
                return True

            authorization = git_repo.authorize_push(
                candidate,
                validator=validate_line,
            )
            if any(
                not self._validate_unpublished_line(
                    relpath,
                    line,
                    require_current_state=True,
                )
                for relpath, line in latest_unpublished.values()
            ):
                raise git_repo.GitPushError(
                    "backup latest state validation failed"
                )
            return authorization

    def _invalidate_recovered_secret_acks(self, clip_ids: set[str]) -> set[str]:
        """Recheck quarantine and remove stale local claims before Git scrub.

        The returned set is the exact recovery eligibility observed under the
        SQLite writer lock. A caller must abandon a stale Git plan when it no
        longer matches the requested IDs.
        """

        still_quarantined: set[str] = set()
        with unit_of_work(self.conn):
            for clip_id in sorted(clip_ids):
                current = self.clips.get(clip_id)
                if current is None:
                    # backup_queue has no FK/cascade, so a deleted/missing clip
                    # can still own a stale pending/done durability claim.
                    # Remove that orphan before scrubbing its only Git copy;
                    # retaining dropped_secret would block a later legitimate
                    # public reconstruction of the same immutable clip ID.
                    still_quarantined.add(clip_id)
                    self.queue.remove_orphan(
                        clip_id,
                        commit=False,
                    )
                    continue
                if not _requires_quarantine(current):
                    continue
                still_quarantined.add(clip_id)
                self.queue.mark_recovered_secret(
                    clip_id,
                    "unpublished_secret_recovery",
                    commit=False,
                )
                self.clips.clear_backed_up_at(clip_id, commit=False)
        return still_quarantined

    def _candidate_recovery_plan(
        self,
        candidate,
    ) -> tuple[bool, dict[str, list[str]], set[str]]:
        records: list[tuple[str, str, str]] = []
        drop_ids: set[str] = set()
        latest: dict[str, tuple[str, str]] = {}
        blocked = False

        def inspect_line(relpath: str, line: str) -> None:
            nonlocal blocked
            recorded = self._decode_unpublished_line(relpath, line)
            if recorded is None:
                # Structurally malformed/manual history must remain fail-closed;
                # it is not safe to infer which record an automatic rewrite owns.
                raise ValueError("unpublished backup record is malformed")
            records.append((recorded.id, relpath, line))
            latest[recorded.id] = (relpath, line)
            current = self.clips.get(recorded.id)
            recorded_secret = (
                _origin_metadata_requires_quarantine(recorded)
                or recorded.is_secret
                or (
                    secret_guard.scan(recorded.content).is_secret
                    and (current is None or not current.released)
                )
            )
            current_secret = current is not None and _requires_quarantine(current)
            if recorded_secret or current_secret:
                # One secret observation taints every unpublished line for this
                # ID. Keeping an earlier public-looking line would defeat a rule
                # upgrade that deliberately quarantined the clip.
                drop_ids.add(recorded.id)
            elif current is None or not self._validate_unpublished_line(relpath, line):
                # Missing rows and ordinary immutable mismatches are data
                # integrity problems, not proof that deletion is safe.
                blocked = True

        git_repo.inspect_unpublished_lines(candidate, visitor=inspect_line)
        for clip_id, (relpath, line) in latest.items():
            if clip_id not in drop_ids and not self._validate_unpublished_line(
                relpath,
                line,
                require_current_state=True,
            ):
                blocked = True
        if blocked:
            raise git_repo.GitPushError(
                "backup unpublished history requires owner inspection"
            )

        replacements: dict[str, list[str]] = {}
        if drop_ids:
            # Inspect every append in the complete published ancestry. Looking
            # only at the base tip's final tree can miss an ID that a later
            # manual rewrite removed even though its blob remains published.
            def inspect_published(relpath: str, line: str) -> None:
                recorded = self._decode_unpublished_line(relpath, line)
                if recorded is None or recorded.id in drop_ids:
                    raise ValueError("published backup requires owner remediation")

            git_repo.inspect_published_lines(
                candidate,
                visitor=inspect_published,
            )
            for clip_id, relpath, line in records:
                if clip_id in drop_ids:
                    continue
                if self._decode_unpublished_line(relpath, line) is None:
                    raise git_repo.GitPushError(
                        "retained backup state failed recovery validation"
                    )
                replacements.setdefault(relpath, []).append(line)
        return bool(drop_ids), replacements, drop_ids

    @staticmethod
    def _decode_unpublished_line(relpath: str, line: str):
        try:
            recorded = jsonl_store.deserialize_clip(line)
            if jsonl_store.serialize_clip(recorded) != line:
                return None
            if jsonl_store.daily_relpath(recorded.created_at) != relpath:
                return None
            jsonl_store.daily_relpath(recorded.last_seen_at)
        except (KeyError, TypeError, ValueError):
            return None
        string_fields = (
            recorded.id,
            recorded.content,
            recorded.content_hash,
            recorded.content_type,
            recorded.source_device,
            recorded.created_at,
            recorded.last_seen_at,
        )
        if any(not isinstance(value, str) or not value for value in string_fields):
            return None
        if recorded.source_app is not None and not isinstance(recorded.source_app, str):
            return None
        if recorded.secret_level is not None and not isinstance(recorded.secret_level, str):
            return None
        if (
            not isinstance(recorded.secret_reasons, list)
            or any(not isinstance(reason, str) for reason in recorded.secret_reasons)
            or not isinstance(recorded.is_secret, bool)
            or not isinstance(recorded.deleted, bool)
            or not isinstance(recorded.times_seen, int)
            or isinstance(recorded.times_seen, bool)
            or recorded.times_seen < 1
            or not isinstance(recorded.pinned, bool)
            or not isinstance(recorded.favorite, bool)
            or not isinstance(recorded.released, bool)
        ):
            return None
        if recorded.released:
            if not isinstance(recorded.released_at, str):
                return None
            try:
                jsonl_store.daily_relpath(recorded.released_at)
            except ValueError:
                return None
        elif recorded.released_at is not None:
            return None
        return recorded

    def _validate_unpublished_line(
        self,
        relpath: str,
        line: str,
        *,
        require_current_state: bool = False,
    ) -> bool:
        """Gate C authorization for one exact, unpublished Git JSONL line."""

        recorded = self._decode_unpublished_line(relpath, line)
        if recorded is None:
            return False
        current = self.clips.get(recorded.id)
        if current is None or current.is_secret or recorded.is_secret:
            return False
        if not origin_metadata.origin_metadata_is_safe(
            current.source_device, current.source_app
        ) or not origin_metadata.origin_metadata_is_safe(
            recorded.source_device, recorded.source_app
        ):
            return False
        if not current.released and (
            secret_guard.scan(current.content).is_secret
            or secret_guard.scan(recorded.content).is_secret
        ):
            return False
        # These recovery-significant fields must still describe the current
        # database row. Only observation/cosmetic fields may legitimately lag
        # because they do not re-enqueue an already completed backup.
        expected = replace(
            current,
            last_seen_at=recorded.last_seen_at,
            times_seen=recorded.times_seen,
            pinned=recorded.pinned,
            favorite=recorded.favorite,
            deleted=(current.deleted if require_current_state else recorded.deleted),
            released=(
                current.released if require_current_state else recorded.released
            ),
            released_at=(
                current.released_at
                if require_current_state
                else recorded.released_at
            ),
        )
        return jsonl_store.serialize_clip(expected) == line
