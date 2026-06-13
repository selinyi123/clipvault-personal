"""B6: single-instance named mutex."""

import pytest

from clipvault.core import ulid
from clipvault.instance_lock import AlreadyRunningError, InstanceLock


def test_b6_second_acquire_fails_then_recovers():
    name = f"Local\\ClipVaultTest-{ulid.new()}"
    first = InstanceLock(name)
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            InstanceLock(name).acquire()
    finally:
        first.release()
    # released -> acquirable again
    with InstanceLock(name):
        pass
