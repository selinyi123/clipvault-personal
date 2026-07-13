"""Service orchestration: watcher -> ingest -> obsidian.

Logging discipline (GATES G6): clip content never appears in logs — only
id, hash prefix, length and type.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone

from clipvault.config import Config
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


class _ObsidianClaimLost(RuntimeError):
    """The leased queue row is no longer owned by this writer."""


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

    def dispatch_obsidian_work(self, clip) -> bool:
        """Use the async runtime when configured, else preserve sync facade behavior."""

        if self._obsidian_notify is None:
            return self.write_obsidian_or_queue(clip)
        self.notify_obsidian_work()
        return False

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
            self.dispatch_obsidian_work(clip)
        return outcome

    def _try_write_obsidian(self, clip) -> tuple[str | None, str | None]:
        try:
            path = writer.write_clip(clip, self.config.vault_path, self.config.type_dirs)
        except writer.SecretWriteRefused:
            raise
        except Exception as exc:
            # Render validation, filesystem failures, and legacy poison rows are
            # isolated to this queue item.  Never log the exception message: it
            # may contain a private Vault path.
            log.error("obsidian write failed id=%s err=%s", clip.id, exc.__class__.__name__)
            return None, exc.__class__.__name__
        return str(path), None

    def _record_claim_failure(self, claim: ObsidianClaim, error: str, now: str) -> None:
        try:
            self.obsidian_queue.record_failure(claim, error, now)
        except Exception as exc:
            log.error(
                "obsidian retry update failed id=%s err=%s",
                claim.clip_id,
                exc.__class__.__name__,
            )

    def _process_obsidian_claim(self, claim: ObsidianClaim, now: str) -> bool:
        """Perform one filesystem write owned by ``claim`` and finalize safely."""

        clip = self.clips.get(claim.clip_id)
        if clip is None or clip.is_secret or clip.deleted or clip.obsidian_path:
            try:
                self.obsidian_queue.mark_done(claim)
            except Exception as exc:
                log.error(
                    "obsidian stale claim cleanup failed id=%s err=%s",
                    claim.clip_id,
                    exc.__class__.__name__,
                )
            return False

        path, error = self._try_write_obsidian(clip)
        if path is None:
            self._record_claim_failure(claim, error or "obsidian_write_failed", now)
            return False

        try:
            # Recording the durable path and consuming the owned claim are one
            # DB transition.  If it fails after the file write, the writer finds
            # the existing clip-id file on the next leased retry.
            with unit_of_work(self.conn):
                self.clips.set_obsidian_path(clip.id, path, commit=False)
                if not self.obsidian_queue.mark_done(claim, commit=False):
                    # A slow filesystem call may outlive its lease.  Never let
                    # that stale owner commit the path or consume a newer claim.
                    raise _ObsidianClaimLost()
        except Exception as exc:
            log.error(
                "obsidian finalize failed id=%s err=%s",
                clip.id,
                exc.__class__.__name__,
            )
            self._record_claim_failure(claim, exc.__class__.__name__, now)
            return False

        log.info("obsidian written id=%s", clip.id)
        return True

    def _write_obsidian(self, clip) -> bool:
        """Backward-compatible Obsidian entrypoint used by older sync code."""
        return self.write_obsidian_or_queue(clip)

    def write_obsidian_or_queue(self, clip) -> bool:
        """Lease and attempt one queued write without racing the sweep worker."""

        now = _utc_now()
        try:
            self.obsidian_queue.enqueue(clip.id, now)
        except Exception as exc:
            log.error(
                "obsidian retry enqueue failed id=%s err=%s",
                clip.id,
                exc.__class__.__name__,
            )
        try:
            claim = self.obsidian_queue.claim_one(clip.id, now)
        except Exception as exc:
            log.error(
                "obsidian claim failed id=%s err=%s", clip.id, exc.__class__.__name__
            )
            return False
        if claim is None:
            return False
        return self._process_obsidian_claim(claim, now)

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
                BackupQueueRepo(self.conn).enqueue(clip.id, now, commit=False)
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

        deadline = time.monotonic() + max(1, max_runtime_ms) / 1000.0
        repaired = 0
        processed = 0
        now = now_fn()
        self.obsidian_queue.reconcile_missing(now, limit=limit)
        self.obsidian_queue.cleanup_ineligible(limit=max(limit, 1))
        while processed < max(1, min(int(limit), 500)):
            if time.monotonic() >= deadline:
                break
            now = now_fn()
            try:
                claims = self.obsidian_queue.claim_ready(now, limit=1)
            except Exception as exc:
                log.error("obsidian sweep claim failed err=%s", exc.__class__.__name__)
                break
            if not claims:
                break
            claim = claims[0]
            processed += 1
            try:
                if self._process_obsidian_claim(claim, now):
                    repaired += 1
            except Exception as exc:
                # One poison row must never prevent later ready rows in a batch.
                log.error(
                    "obsidian sweep item failed id=%s err=%s",
                    claim.clip_id,
                    exc.__class__.__name__,
                )
                self._record_claim_failure(claim, exc.__class__.__name__, now)
        return repaired

    def obsidian_retry_stats(self) -> dict:
        return self.obsidian_queue.stats(_utc_now())

    def has_ready_obsidian_work(self) -> bool:
        return self.obsidian_queue.has_ready(_utc_now())
