"""Service orchestration: watcher -> ingest -> obsidian.

Logging discipline (GATES G6): clip content never appears in logs — only
id, hash prefix, length and type.
"""

import logging
import sqlite3
import time
from datetime import datetime, timezone

from clipvault.config import Config
from clipvault.obsidian import writer
from clipvault.pipeline import ingest as pipeline
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.memory_repo import MemoryRepo, SecretMemoryError
from clipvault.store.obsidian_queue_repo import ObsidianQueueRepo

log = logging.getLogger("clipvault.service")

# content_type -> memory kind for clip promotion (S007)
_PROMOTE_KIND = {"prompt": "prompt", "command": "command"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ClipVaultService:
    def __init__(self, conn: sqlite3.Connection, config: Config):
        self.conn = conn
        self.config = config
        self.clips = ClipsRepo(conn)
        self.obsidian_queue = ObsidianQueueRepo(conn)

    def handle_clipboard_text(self, text: str, source_app: str | None = None) -> pipeline.IngestOutcome:
        outcome = pipeline.ingest(
            self.conn,
            text,
            source_device=self.config.device_name,
            source_app=source_app,
            max_bytes=self.config.max_clip_bytes,
        )
        if outcome.status == pipeline.STATUS_REJECTED_TOO_LARGE:
            log.warning("rejected oversize clip (limit=%d bytes)", self.config.max_clip_bytes)
            return outcome
        if outcome.clip is None:
            return outcome

        clip = outcome.clip
        if outcome.status == pipeline.STATUS_DUPLICATE:
            log.debug("duplicate id=%s times_seen=%d", clip.id, clip.times_seen)
            return outcome

        log.info(
            "captured id=%s type=%s len=%d hash=%s app=%s",
            clip.id, clip.content_type, len(clip.content),
            clip.content_hash[:8], source_app or "-",
        )
        if clip.is_secret:
            log.warning(
                "quarantined id=%s level=%s reasons=%s",
                clip.id, clip.secret_level, ",".join(clip.secret_reasons),
            )
        if outcome.needs_obsidian:
            self.write_obsidian_or_queue(clip)
        return outcome

    def _try_write_obsidian(self, clip) -> tuple[bool, str | None]:
        try:
            path = writer.write_clip(clip, self.config.vault_path, self.config.type_dirs)
        except writer.SecretWriteRefused:
            raise
        except OSError as exc:
            # No clip content in logs.
            log.error("obsidian write failed id=%s err=%s", clip.id, exc.__class__.__name__)
            return False, exc.__class__.__name__
        self.clips.set_obsidian_path(clip.id, str(path))
        log.info("obsidian written id=%s", clip.id)
        return True, None

    def _write_obsidian(self, clip) -> bool:
        """Backward-compatible Obsidian entrypoint used by older sync code."""
        return self.write_obsidian_or_queue(clip)

    def write_obsidian_or_queue(self, clip) -> bool:
        """Try one Obsidian write and keep a bounded retry row on failure.

        The queue row is created before the external file-system effect. This
        makes the DB the durable source of retry truth without scanning all clips
        every maintenance cycle.
        """

        now = _utc_now()
        try:
            self.obsidian_queue.enqueue(clip.id, now)
        except Exception:
            # Do not prevent the immediate write attempt because queue metadata
            # failed. Also do not log content.
            log.exception("obsidian retry enqueue failed id=%s", clip.id)

        ok, err = self._try_write_obsidian(clip)
        try:
            if ok:
                self.obsidian_queue.mark_done(clip.id)
            else:
                self.obsidian_queue.record_failure(clip.id, err or "obsidian_write_failed", now)
        except Exception:
            log.exception("obsidian retry queue update failed id=%s", clip.id)
        return ok

    def release_clip(self, clip_id: str) -> bool:
        """Release a quarantined clip and re-run the public pipeline
        (FTS already re-indexed by the repo; here we add Obsidian + backup + sync)."""
        clip = self.clips.release_secret(clip_id, _utc_now())
        if clip is None:
            return False
        log.info("released id=%s (was quarantined)", clip.id)
        if not clip.deleted:
            self.write_obsidian_or_queue(clip)
            now = _utc_now()
            try:
                BackupQueueRepo(self.conn).enqueue(clip.id, now)
            except Exception:
                log.exception("enqueue after release failed id=%s", clip.id)
            # Released clips have an explicit user decision and must re-enter the
            # public sync pipeline; otherwise Android never sees them.
            try:
                from clipvault.sync import engine
                engine.emit_clip_new(self.conn, clip, now)
            except Exception:
                log.exception("sync emit after release failed id=%s", clip.id)
        return True

    def promote_clip(self, clip_id: str, kind: str | None = None):
        """Promote a clip into Personal Memory. Secret clips are refused.

        An explicit kind (from a Context Action chip) overrides the default
        content_type mapping; an invalid kind raises ValueError via upsert."""
        clip = self.clips.get(clip_id)
        if clip is None or clip.is_secret:
            return None
        target = kind or _PROMOTE_KIND.get(clip.content_type, "phrase")
        try:
            return MemoryRepo(self.conn).upsert(
                target, clip.content[:200], source="derived"
            )
        except SecretMemoryError:
            # A legacy clip may predate a newer SG-1 rule and still carry
            # is_secret=0. Re-scan at the Memory boundary and fail closed.
            return None

    def retry_obsidian_sweep(
        self,
        *,
        limit: int = 50,
        max_runtime_ms: int = 500,
        now_fn=_utc_now,
    ) -> int:
        """Retry a bounded batch of queued Obsidian writes.

        The old implementation scanned every public clip with obsidian_path NULL
        on every maintenance pass. This version reads only ready queue rows and
        enforces both a row limit and a runtime budget.
        """

        now = now_fn()
        deadline = time.monotonic() + max(1, max_runtime_ms) / 1000.0
        repaired = 0
        self.obsidian_queue.cleanup_ineligible()
        for clip_id in self.obsidian_queue.claim_ready(now, limit=limit):
            if time.monotonic() >= deadline:
                break
            clip = self.clips.get(clip_id)
            if clip is None or clip.is_secret or clip.deleted or clip.obsidian_path:
                self.obsidian_queue.mark_done(clip_id)
                continue
            ok, err = self._try_write_obsidian(clip)
            if ok:
                self.obsidian_queue.mark_done(clip_id)
                repaired += 1
            else:
                self.obsidian_queue.record_failure(clip_id, err or "obsidian_write_failed", now)
        return repaired

    def obsidian_retry_stats(self) -> dict:
        return self.obsidian_queue.stats(_utc_now())
