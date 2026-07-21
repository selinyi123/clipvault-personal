"""Service orchestration: watcher -> ingest -> obsidian.

Logging discipline (GATES G6): clip content never appears in logs — only
id, hash prefix, length and type.
"""

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from clipvault.application.obsidian_commands import ObsidianCommands
from clipvault.config import Config
from clipvault.core import origin_metadata
from clipvault.obsidian import writer
from clipvault.pipeline import ingest as pipeline
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.memory_repo import MemoryRepo, SecretMemoryError
from clipvault.store.obsidian_queue_repo import ObsidianClaim, ObsidianQueueRepo
from clipvault.store.unit_of_work import unit_of_work

log = logging.getLogger("clipvault.service")

# content_type -> memory kind for clip promotion (S007)
_PROMOTE_KIND = {"prompt": "prompt", "command": "command"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ClipVaultService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        config: Config,
        *,
        obsidian_notify: Callable[[], None] | None = None,
    ):
        self.conn = conn
        self.config = config
        self.clips = ClipsRepo(conn)
        self.obsidian_queue = ObsidianQueueRepo(conn)
        self.obsidian_commands = ObsidianCommands(
            conn,
            clips=self.clips,
            queue=self.obsidian_queue,
            vault_path=config.vault_path,
            type_dirs=config.type_dirs,
            # Resolve the adapter attribute per call so existing test/extension
            # monkeypatches of writer.write_clip remain compatible.
            write_clip=lambda *args, **kwargs: writer.write_clip(*args, **kwargs),
            secret_write_refused=lambda: writer.SecretWriteRefused,
            logger=log,
        )
        self._obsidian_notify = obsidian_notify

    def notify_obsidian_work(self) -> None:
        """Best-effort wake after a durable Obsidian intent has committed."""

        if self._obsidian_notify is None:
            return
        try:
            self._obsidian_notify()
        except Exception as exc:
            # The queue is already durable. A notifier failure must not turn a
            # successful capture/sync/release into a false failure.
            log.error("obsidian worker notify failed err=%s", exc.__class__.__name__)

    def _sync_obsidian_command_context(self) -> None:
        """Keep legacy mutable facade attributes visible to application commands."""

        self.obsidian_commands.clips = self.clips
        self.obsidian_commands.queue = self.obsidian_queue
        self.obsidian_commands.vault_path = self.config.vault_path
        self.obsidian_commands.type_dirs = self.config.type_dirs

    def dispatch_obsidian_work(self, clip) -> bool:
        """Use the async runtime when configured, else preserve sync facade behavior."""

        if self._obsidian_notify is None:
            return self.write_obsidian_or_queue(clip)
        self.notify_obsidian_work()
        return False

    def handle_clipboard_text(self, text: str, source_app: str | None = None) -> pipeline.IngestOutcome:
        # OS/window metadata is best-effort context, not user content. Drop an
        # unsafe optional value without rejecting the actual clipboard capture.
        if not origin_metadata.source_app_is_safe(source_app):
            source_app = None
            log.warning("discarded unsafe clipboard source metadata")
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
            "captured id=%s type=%s len=%d hash=%s",
            clip.id, clip.content_type, len(clip.content),
            clip.content_hash[:8],
        )
        if clip.is_secret:
            log.warning(
                "quarantined id=%s level=%s reasons=%s",
                clip.id, clip.secret_level, ",".join(clip.secret_reasons),
            )
        if outcome.needs_obsidian:
            self.dispatch_obsidian_work(clip)
        return outcome

    def _try_write_obsidian(self, clip) -> tuple[str | None, str | None]:
        self._sync_obsidian_command_context()
        return self.obsidian_commands.try_write(clip)

    def _record_claim_failure(self, claim: ObsidianClaim, error: str, now: str) -> None:
        self._sync_obsidian_command_context()
        self.obsidian_commands.record_claim_failure(claim, error, now)

    def _process_obsidian_claim(self, claim: ObsidianClaim, now: str) -> bool:
        """Perform one filesystem write owned by ``claim`` and finalize safely."""

        self._sync_obsidian_command_context()
        return self.obsidian_commands.process_claim(
            claim,
            now,
            try_write=self._try_write_obsidian,
            record_failure=self._record_claim_failure,
        )

    def _write_obsidian(self, clip) -> bool:
        """Backward-compatible Obsidian entrypoint used by older sync code."""
        return self.write_obsidian_or_queue(clip)

    def write_obsidian_or_queue(self, clip) -> bool:
        """Lease and attempt one queued write without racing the sweep worker."""
        self._sync_obsidian_command_context()
        return self.obsidian_commands.write_or_queue(
            clip,
            process_claim=self._process_obsidian_claim,
        )

    def release_clip(self, clip_id: str) -> bool:
        """Release a quarantined clip and re-run the public pipeline
        (FTS already re-indexed by the repo; here we add Obsidian + backup + sync)."""
        from clipvault.sync import engine

        now = _utc_now()
        with unit_of_work(self.conn):
            clip = self.clips.release_secret(clip_id, now, commit=False)
            if clip is None:
                return False
            if not clip.deleted:
                self.obsidian_queue.enqueue(clip.id, now, commit=False)
                # A legacy forced queue row may already be dropped_secret.
                # Explicit Owner release must reactivate that durable intent.
                BackupQueueRepo(self.conn).reenqueue(clip.id, now, commit=False)
                # Explicit release must re-enter the public sync pipeline.
                engine.emit_clip_new(self.conn, clip, now, commit=False)
        log.info("released id=%s (was quarantined)", clip.id)
        if not clip.deleted:
            self.dispatch_obsidian_work(clip)
        return True

    def promote_clip(self, clip_id: str, kind: str | None = None):
        """Promote a clip into Personal Memory. Secret clips are refused.

        An explicit kind (from a Context Action chip) overrides the default
        content_type mapping; an invalid kind raises ValueError via upsert."""
        from clipvault.sync import engine

        now = _utc_now()
        try:
            with unit_of_work(self.conn):
                clip = self.clips.get(clip_id)
                if clip is None or engine.clip_requires_local_quarantine(clip):
                    return None
                target = kind or _PROMOTE_KIND.get(
                    clip.content_type, "phrase"
                )
                item = MemoryRepo(self.conn).upsert(
                    target,
                    clip.content[:200],
                    source="derived",
                    commit=False,
                )
                engine.emit_memory_upsert(
                    self.conn, item, now, commit=False
                )
                return item
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

        self._sync_obsidian_command_context()
        return self.obsidian_commands.retry_sweep(
            limit=limit,
            max_runtime_ms=max_runtime_ms,
            now_fn=now_fn,
            process_claim=self._process_obsidian_claim,
            record_failure=self._record_claim_failure,
        )

    def obsidian_retry_stats(self) -> dict:
        self._sync_obsidian_command_context()
        return self.obsidian_commands.retry_stats()

    def has_ready_obsidian_work(self) -> bool:
        self._sync_obsidian_command_context()
        return self.obsidian_commands.has_ready()
