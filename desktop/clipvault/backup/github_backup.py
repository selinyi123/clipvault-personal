"""Backup worker (GHB-1). Gate C lives here: every clip is re-scanned with
the current Secret Guard rules immediately before serialization, so a secret
that slipped past gates A/B (older rules) is still dropped here.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from clipvault.core import secret_guard
from clipvault.backup import git_repo, jsonl_store
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo

log = logging.getLogger("clipvault.backup")

_BACKOFF_START_S = 60
_BACKOFF_MAX_S = 1800  # 30 min cap


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_missing_lines(repo_path: str, relpath: str, lines: list[str]) -> None:
    """Append JSONL lines idempotently.

    If a previous worker run wrote the file but crashed/failed before the git
    commit, the queue is still pending. Retrying must not duplicate those lines;
    it should commit the already-written working-tree change instead.
    """
    target = Path(repo_path) / relpath
    existing = set(target.read_text(encoding="utf-8").splitlines()) if target.exists() else set()
    missing = [line for line in lines if line not in existing]
    if missing:
        jsonl_store.append_lines(repo_path, relpath, missing)


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

    def run_once(self, monotonic: float = 0.0) -> dict:
        """Serialize pending clips, commit, push. Returns a small stats dict.
        `monotonic` lets the scheduler honour push backoff deterministically."""
        pending = self.queue.claim_pending()
        written = 0
        dropped = 0
        by_day: dict[str, list[str]] = {}
        mark_after_commit: list[tuple[str, str]] = []

        for clip_id in pending:
            clip = self.clips.get(clip_id)
            if clip is None:
                self.queue.mark_dropped(clip_id, "clip_missing")
                continue
            # Gate C: re-scan with current rules.
            verdict = secret_guard.scan(clip.content)
            if verdict.is_secret or clip.is_secret:
                self.queue.mark_dropped(clip_id, "gate_c_secret")
                log.error("gate C dropped suspected secret id=%s reasons=%s",
                          clip_id, ",".join(verdict.reasons) or "stored_flag")
                dropped += 1
                continue
            relpath = jsonl_store.daily_relpath(clip.created_at)
            by_day.setdefault(relpath, []).append(jsonl_store.serialize_clip(clip))
            mark_after_commit.append((clip_id, self.now_fn()))
            written += 1

        committed = None
        if by_day:
            for relpath, lines in by_day.items():
                _append_missing_lines(self.repo_path, relpath, lines)
            # If commit fails, do not mark queue rows done. The next worker run
            # must retry because there is no durable recovery point yet.
            committed = git_repo.add_commit(
                self.repo_path, f"backup: {written} clips {self.now_fn()}"
            )
            if committed is not None:
                for clip_id, done_at in mark_after_commit:
                    self.clips.set_backed_up_at(clip_id, done_at)
                    self.queue.mark_done(clip_id, done_at)

        pushed = False
        # Retry push even when this run had no new commits; a previous push may
        # have failed after data was safely committed locally.
        if self.push_enabled and git_repo.head_commit(self.repo_path) is not None:
            pushed = self._try_push(monotonic)

        return {"written": written, "dropped": dropped,
                "committed": committed, "pushed": pushed}

    def _try_push(self, monotonic: float) -> bool:
        if monotonic and monotonic < self._monotonic_blocked_until:
            log.info("push deferred (backoff)")
            return False
        try:
            git_repo.push(self.repo_path, self.remote)
        except git_repo.GitPushError as exc:
            self._backoff_s = min(self._backoff_s * 2, _BACKOFF_MAX_S)
            self._monotonic_blocked_until = (monotonic or 0.0) + self._backoff_s
            log.error("push failed, data committed locally; backoff=%ds err=%s",
                      self._backoff_s, exc)
            return False
        self._backoff_s = _BACKOFF_START_S
        self._monotonic_blocked_until = 0.0
        log.info("push ok")
        return True
