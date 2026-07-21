"""Obsidian queue application commands.

This layer owns claim/write/finalize/retry orchestration.  It depends on store
ports and injected filesystem behavior, never on the service facade, API,
runtime, sync engine, pipeline, or the concrete Obsidian writer module.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone

from clipvault.core import origin_metadata
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.obsidian_queue_repo import ObsidianClaim, ObsidianQueueRepo
from clipvault.store.unit_of_work import unit_of_work


class _ObsidianClaimLost(RuntimeError):
    """The leased queue row is no longer owned by this writer."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ObsidianCommands:
    """Coordinate durable Obsidian claims with an injected file writer."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clips: ClipsRepo,
        queue: ObsidianQueueRepo,
        vault_path: str,
        type_dirs: dict[str, str],
        write_clip: Callable,
        secret_write_refused: type[BaseException]
        | Callable[[], type[BaseException]],
        logger: logging.Logger | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.conn = conn
        self.clips = clips
        self.queue = queue
        self.vault_path = vault_path
        self.type_dirs = type_dirs
        self._write_clip = write_clip
        if isinstance(secret_write_refused, type):
            self._secret_write_refused = lambda: secret_write_refused
        else:
            self._secret_write_refused = secret_write_refused
        self.log = logger or logging.getLogger("clipvault.application.obsidian")
        self._monotonic = monotonic

    def try_write(self, clip) -> tuple[str | None, str | None]:
        refusal_type = self._secret_write_refused()
        if (
            hasattr(clip, "source_device")
            and not origin_metadata.origin_metadata_is_safe(
                clip.source_device, getattr(clip, "source_app", None)
            )
        ):
            raise refusal_type(clip.id)
        try:
            path = self._write_clip(clip, self.vault_path, self.type_dirs)
        except refusal_type:
            raise
        except Exception as exc:
            # Never include exception messages: render/filesystem errors may
            # contain a private Vault path.
            self.log.error(
                "obsidian write failed id=%s err=%s",
                clip.id,
                exc.__class__.__name__,
            )
            return None, exc.__class__.__name__
        return str(path), None

    def record_claim_failure(self, claim: ObsidianClaim, error: str, now: str) -> None:
        try:
            self.queue.record_failure(claim, error, now)
        except Exception as exc:
            self.log.error(
                "obsidian retry update failed id=%s err=%s",
                claim.clip_id,
                exc.__class__.__name__,
            )

    def process_claim(
        self,
        claim: ObsidianClaim,
        now: str,
        *,
        try_write: Callable | None = None,
        record_failure: Callable[[ObsidianClaim, str, str], None] | None = None,
    ) -> bool:
        """Perform one filesystem write owned by ``claim`` and finalize safely."""

        try_write = try_write or self.try_write
        record_failure = record_failure or self.record_claim_failure

        clip = None
        skip_write = False
        try:
            # Re-read under a short writer transaction.  A clip created under
            # an older Secret Guard may still be persisted as public, so Gate B
            # must use today's detector before any filesystem call.  Persisting
            # that quarantine, removing its FTS row, and consuming this exact
            # leased claim are one atomic transition.
            with unit_of_work(self.conn):
                clip = self.clips.get(claim.clip_id)
                if clip is None or clip.is_secret:
                    if not self.queue.mark_done(claim, commit=False):
                        raise _ObsidianClaimLost()
                    skip_write = True
                elif self.clips.quarantine_current_secret(
                    clip.id, commit=False
                ) is not None:
                    if not self.queue.mark_done(claim, commit=False):
                        raise _ObsidianClaimLost()
                    skip_write = True
                elif clip.deleted or clip.obsidian_path:
                    if not self.queue.mark_done(claim, commit=False):
                        raise _ObsidianClaimLost()
                    skip_write = True
        except Exception as exc:
            self.log.error(
                "obsidian preflight failed id=%s err=%s",
                claim.clip_id,
                exc.__class__.__name__,
            )
            return False

        if skip_write:
            return False
        if clip is None:  # Kept explicit for injected repository implementations.
            return False

        # Older extension/test facades may provide a clip-shaped object without
        # origin fields. Real persisted Clips always have source_device; retain
        # that compatibility while enforcing the gate for every durable row.
        has_origin = hasattr(clip, "source_device")
        if has_origin and not origin_metadata.origin_metadata_is_safe(
            clip.source_device, getattr(clip, "source_app", None)
        ):
            try:
                blocked = self.queue.block_origin_metadata(claim, now)
            except Exception as exc:
                self.log.error(
                    "obsidian unsafe-origin block failed id=%s err=%s",
                    claim.clip_id,
                    exc.__class__.__name__,
                )
            else:
                self.log.error(
                    "obsidian write blocked for unsafe origin metadata "
                    "id=%s owned=%s",
                    claim.clip_id,
                    blocked,
                )
            return False

        path, error = try_write(clip)
        if path is None:
            record_failure(claim, error or "obsidian_write_failed", now)
            return False

        try:
            # The durable path and owned-claim consumption are one DB transition.
            # If it fails after the file write, the idempotent writer finds the
            # existing clip-id file on the next leased retry.
            with unit_of_work(self.conn):
                self.clips.set_obsidian_path(clip.id, path, commit=False)
                if not self.queue.mark_done(claim, commit=False):
                    raise _ObsidianClaimLost()
        except Exception as exc:
            self.log.error(
                "obsidian finalize failed id=%s err=%s",
                clip.id,
                exc.__class__.__name__,
            )
            record_failure(claim, exc.__class__.__name__, now)
            return False

        self.log.info("obsidian written id=%s", clip.id)
        return True

    def write_or_queue(
        self,
        clip,
        *,
        now_fn: Callable[[], str] = _utc_now,
        process_claim: Callable[[ObsidianClaim, str], bool] | None = None,
    ) -> bool:
        """Lease and attempt one specific queued write."""

        process_claim = process_claim or self.process_claim

        now = now_fn()
        try:
            self.queue.enqueue(clip.id, now)
        except Exception as exc:
            self.log.error(
                "obsidian retry enqueue failed id=%s err=%s",
                clip.id,
                exc.__class__.__name__,
            )
        try:
            claim = self.queue.claim_one(clip.id, now)
        except Exception as exc:
            self.log.error(
                "obsidian claim failed id=%s err=%s",
                clip.id,
                exc.__class__.__name__,
            )
            return False
        if claim is None:
            return False
        return process_claim(claim, now)

    def retry_sweep(
        self,
        *,
        limit: int = 50,
        max_runtime_ms: int = 500,
        now_fn: Callable[[], str] = _utc_now,
        process_claim: Callable[[ObsidianClaim, str], bool] | None = None,
        record_failure: Callable[[ObsidianClaim, str, str], None] | None = None,
    ) -> int:
        """Retry a bounded batch of queued writes within a soft time budget."""

        process_claim = process_claim or self.process_claim
        record_failure = record_failure or self.record_claim_failure

        deadline = self._monotonic() + max(1, max_runtime_ms) / 1000.0
        repaired = 0
        processed = 0
        now = now_fn()
        self.queue.reconcile_missing(now, limit=limit)
        self.queue.cleanup_ineligible(limit=max(limit, 1))
        while processed < max(1, min(int(limit), 500)):
            if self._monotonic() >= deadline:
                break
            now = now_fn()
            try:
                claims = self.queue.claim_ready(now, limit=1)
            except Exception as exc:
                self.log.error(
                    "obsidian sweep claim failed err=%s",
                    exc.__class__.__name__,
                )
                break
            if not claims:
                break
            claim = claims[0]
            processed += 1
            try:
                if process_claim(claim, now):
                    repaired += 1
            except Exception as exc:
                # One poison row must not prevent later ready rows in the batch.
                self.log.error(
                    "obsidian sweep item failed id=%s err=%s",
                    claim.clip_id,
                    exc.__class__.__name__,
                )
                record_failure(claim, exc.__class__.__name__, now)
        return repaired

    def retry_stats(self, *, now_fn: Callable[[], str] = _utc_now) -> dict:
        return self.queue.stats(now_fn())

    def has_ready(self, *, now_fn: Callable[[], str] = _utc_now) -> bool:
        return self.queue.has_ready(now_fn())
