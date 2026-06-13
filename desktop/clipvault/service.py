"""Service orchestration: watcher -> ingest -> obsidian.

Logging discipline (GATES G6): clip content never appears in logs — only
id, hash prefix, length and type.
"""

import logging
import sqlite3

from clipvault.config import Config
from clipvault.obsidian import writer
from clipvault.pipeline import ingest as pipeline
from clipvault.store.clips_repo import ClipsRepo

log = logging.getLogger("clipvault.service")


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
