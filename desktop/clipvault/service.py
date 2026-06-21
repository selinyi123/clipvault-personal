"""Service orchestration: watcher -> ingest -> obsidian.

Logging discipline (GATES G6): clip content never appears in logs — only
id, hash prefix, length and type.
"""

import logging
import sqlite3
from datetime import datetime, timezone

from clipvault.config import Config
from clipvault.obsidian import writer
from clipvault.pipeline import ingest as pipeline
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.memory_repo import MemoryRepo

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
            self._write_obsidian(clip)
        return outcome

    def _write_obsidian(self, clip) -> bool:
        try:
            path = writer.write_clip(clip, self.config.vault_path, self.config.type_dirs)
        except writer.SecretWriteRefused:
            raise
        except OSError as exc:
            log.error("obsidian write failed id=%s err=%s", clip.id, exc)
            return False
        self.clips.set_obsidian_path(clip.id, str(path))
        log.info("obsidian written id=%s", clip.id)
        return True

    def release_clip(self, clip_id: str) -> bool:
        """Release a quarantined clip and re-run the public pipeline
        (FTS already re-indexed by the repo; here we add Obsidian + backup + sync)."""
        clip = self.clips.release_secret(clip_id, _utc_now())
        if clip is None:
            return False
        log.info("released id=%s (was quarantined)", clip.id)
        if not clip.deleted:
            self._write_obsidian(clip)
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
        return MemoryRepo(self.conn).upsert(
            target, clip.content[:200], source="derived"
        )

    def retry_obsidian_sweep(self) -> int:
        """The DB is the retry queue: any public clip without obsidian_path
        is pending. Returns the number of clips repaired."""
        rows = self.conn.execute(
            "SELECT id FROM clips "
            "WHERE obsidian_path IS NULL AND is_secret = 0 AND deleted = 0"
        ).fetchall()
        repaired = 0
        for (clip_id,) in rows:
            clip = self.clips.get(clip_id)
            if clip and self._write_obsidian(clip):
                repaired += 1
        return repaired
