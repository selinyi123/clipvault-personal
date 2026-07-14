"""Cooperative cancellation shared by backup locks and Git subprocesses."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol


class CancellationEvent(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class BackupCancelled(BaseException):
    """Normal control flow when process shutdown interrupts backup work.

    This intentionally derives from ``BaseException`` so broad operational
    ``except Exception`` blocks cannot turn shutdown into a retry/backoff error.
    Runtime owns the one boundary that catches it and exits the worker quietly.
    """


class BackupProcessTerminationError(BaseException):
    """Fail closed when an interrupted Git leader cannot be proven reaped."""


class BackupLockCleanupError(BaseException):
    """Fail closed when repository lock release cannot be proven complete."""


_CURRENT_EVENT: ContextVar[CancellationEvent | None] = ContextVar(
    "clipvault_backup_cancellation_event",
    default=None,
)


@contextmanager
def cancellation_scope(event: CancellationEvent | None) -> Iterator[None]:
    """Bind one worker's cancellation event to the current execution context."""

    token = _CURRENT_EVENT.set(event)
    try:
        checkpoint(event)
        yield
    finally:
        _CURRENT_EVENT.reset(token)


def current_event() -> CancellationEvent | None:
    return _CURRENT_EVENT.get()


def checkpoint(event: CancellationEvent | None = None) -> None:
    """Raise a content-free cancellation signal when shutdown was requested."""

    selected = event if event is not None else _CURRENT_EVENT.get()
    if selected is not None and selected.is_set():
        raise BackupCancelled("backup shutdown requested")
