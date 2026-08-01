from __future__ import annotations

import threading
import uuid

from clipvault.runtime.snapshot import RuntimeSnapshotPublisher
from clipvault.runtime.snapshot_protocol import (
    decode_host_hello,
    decode_snapshot_response,
    encode_client_hello,
    encode_snapshot_request,
)
from clipvault.runtime.snapshot_windows import WindowsRuntimeSnapshotServer
from clipvault.store import db

EPOCH = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"


class _FakeKernel:
    def __init__(self, inbound, *, verified=True):
        self.inbound = list(inbound)
        self.outbound = []
        self.verified = verified
        self.closed = 0
        self.flushed = 0
        self.created = 0

    def pipe_name(self):
        return r"\\.\pipe\ClipVaultRuntimeSnapshotV1-1"

    def create_server(self, expected_host_path, require_signature):
        self.created += 1
        return self.created

    def connect(self, handle, stop_event):
        return True

    def verify_client(self, handle, expected_host_path, require_signature):
        return self.verified

    def read_frame(self, handle, deadline):
        return self.inbound.pop(0)

    def write_frame(self, handle, payload, deadline):
        self.outbound.append(payload)

    def flush_response(self, handle):
        self.flushed += 1

    def close_server(self, handle):
        self.closed += 1


def _publisher():
    conn = db.connect(":memory:")
    db.migrate(conn)
    return RuntimeSnapshotPublisher(conn, publisher_epoch=EPOCH, now_ms=lambda: 1_000)


def test_one_connected_exchange_has_no_query_or_app_context(tmp_path):
    kernel = _FakeKernel(
        [encode_client_hello(CLIENT), encode_snapshot_request(17, 8)]
    )
    server = WindowsRuntimeSnapshotServer(
        _publisher(),
        tmp_path.resolve() / "ClipVaultImeHost.exe",
        kernel=kernel,
        require_signature=True,
    )

    server._serve_connected(1)

    assert decode_host_hello(kernel.outbound[0]) == EPOCH
    response = decode_snapshot_response(kernel.outbound[1], now_ms=1_000)
    assert response.request_id == 17
    assert response.publisher_epoch == EPOCH
    assert response.items == ()


def test_unverified_client_receives_no_snapshot(tmp_path):
    kernel = _FakeKernel([], verified=False)
    stop = threading.Event()
    original_close = kernel.close_server

    def close_once(handle):
        original_close(handle)
        stop.set()

    kernel.close_server = close_once
    server = WindowsRuntimeSnapshotServer(
        _publisher(),
        tmp_path.resolve() / "ClipVaultImeHost.exe",
        kernel=kernel,
    )

    server.run(stop)

    assert kernel.outbound == []
    assert kernel.flushed == 0
    assert kernel.closed == 1


def test_verified_exchange_flushes_response_before_disconnect(tmp_path):
    kernel = _FakeKernel(
        [encode_client_hello(CLIENT), encode_snapshot_request(17, 8)]
    )
    stop = threading.Event()
    original_close = kernel.close_server

    def close_once(handle):
        original_close(handle)
        stop.set()

    kernel.close_server = close_once
    server = WindowsRuntimeSnapshotServer(
        _publisher(),
        tmp_path.resolve() / "ClipVaultImeHost.exe",
        kernel=kernel,
    )

    server.run(stop)

    assert kernel.flushed == 1
    assert kernel.closed == 1


def test_pipe_locator_contains_only_current_session_namespace(tmp_path):
    kernel = _FakeKernel([])
    server = WindowsRuntimeSnapshotServer(
        _publisher(), tmp_path.resolve() / "ClipVaultImeHost.exe", kernel=kernel
    )
    assert server.pipe_name.endswith("-1")
    assert "token" not in server.pipe_name.casefold()
