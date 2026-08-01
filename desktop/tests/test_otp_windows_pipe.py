"""Windows opaque OTP broker wire and bounded port behavior."""

from __future__ import annotations

import struct
import threading
from types import SimpleNamespace
import uuid

import pytest

from clipvault.config import Config
from clipvault.otp.ingress import (
    DisabledOtpOpaqueIngressPort,
    OtpOpaqueBrokerUnavailable,
    OtpOpaqueEnvelope,
)
from clipvault.otp import windows_pipe
from clipvault.runtime.app import ClipVaultRuntime, RuntimeAdapters


SESSION_EPOCH = "b6a0dff2-5362-41ec-ae7e-aa84178d79c8"
EVENT_ID = "64ecf704-f35b-4be0-9cb3-1dc98c17ecf4"
SENDER_ID = "device:ca8b0e77-60fc-43a4-8957-85c6d6c85212"
TARGET_ID = "device:f61d395d-e3b2-47cd-8e7b-33ac11dd6627"


def _envelope() -> OtpOpaqueEnvelope:
    return OtpOpaqueEnvelope(
        version=1,
        algorithm="A256GCM",
        session_epoch=SESSION_EPOCH,
        event_id=EVENT_ID,
        sender_device_id=SENDER_ID,
        target_device_id=TARGET_ID,
        sequence=7,
        issued_at_ms=1_700_000_000_000,
        expires_at_ms=1_700_000_090_000,
        nonce=bytearray(range(12)),
        ciphertext=bytearray(b"123456"),
        authentication_tag=bytearray(range(16, 32)),
        event_hash="opaque-test-hash",
    )


def _response(status: int = 1, *, secret: bytes = b"") -> bytearray:
    return bytearray(
        b"CVOB"
        + bytes((1, 128, 0, 0, status))
        + bytes(16)
        + bytes((len(secret),))
        + secret
    )


def test_offer_encoding_matches_frozen_cpp_wire_exactly():
    envelope = _envelope()
    actual = windows_pipe._encode_offer(envelope)
    expected = bytearray(b"CVOB" + bytes((1, 1, 0, 0, 1, 1)))
    expected.extend(uuid.UUID(SESSION_EPOCH).bytes)
    expected.extend(uuid.UUID(EVENT_ID).bytes)
    expected.extend(uuid.UUID(SENDER_ID.removeprefix("device:")).bytes)
    expected.extend(uuid.UUID(TARGET_ID.removeprefix("device:")).bytes)
    expected.extend(struct.pack(">QQQ", 7, 1_700_000_000_000, 1_700_000_090_000))
    expected.extend(range(12))
    expected.extend((6,))
    expected.extend(b"123456")
    expected.extend(range(16, 32))

    assert actual == expected


def test_offer_encoder_rejects_noncanonical_uuid():
    envelope = _envelope()
    envelope.session_epoch = SESSION_EPOCH.upper()
    with pytest.raises(windows_pipe._PipeProtocol):
        windows_pipe._encode_offer(envelope)


@pytest.mark.parametrize(
    "frame",
    [
        bytearray(),
        bytearray(b"FAIL" + bytes((1, 128, 0, 0, 1)) + bytes(17)),
        bytearray(b"CVOB" + bytes((2, 128, 0, 0, 1)) + bytes(17)),
        bytearray(b"CVOB" + bytes((1, 1, 0, 0, 1)) + bytes(17)),
        bytearray(b"CVOB" + bytes((1, 128, 1, 0, 1)) + bytes(17)),
        _response(0),
        _response(9),
        _response(1) + b"x",
        _response(1, secret=b"1234"),
        _response(7, secret=b"123"),
    ],
)
def test_response_decoder_rejects_malformed_frames(frame):
    with pytest.raises(windows_pipe._PipeProtocol):
        windows_pipe._decode_response(frame)


def test_response_decoder_accepts_all_strict_status_shapes():
    assert windows_pipe._decode_response(_response(1)) == 1
    assert windows_pipe._decode_response(_response(2)) == 2
    assert windows_pipe._decode_response(_response(7, secret=b"1234")) == 7


@pytest.mark.parametrize("size", [0, 513, -1, True])
def test_frame_length_rejects_out_of_range_values(size):
    with pytest.raises(windows_pipe._PipeProtocol):
        windows_pipe._frame_length_prefix(size)


def test_frame_length_is_unsigned_big_endian():
    prefix = windows_pipe._frame_length_prefix(512)
    assert prefix == bytearray(b"\x00\x00\x02\x00")
    assert windows_pipe._parse_frame_length(prefix) == 512
    with pytest.raises(windows_pipe._PipeProtocol):
        windows_pipe._parse_frame_length(bytearray(4))


class _FakeKernel:
    def __init__(self, response=None, *, read_error=None):
        self.response = response if response is not None else _response()
        self.read_error = read_error
        self.handle = object()
        self.calls = []
        self.closed = []
        self.request = None

    def current_session_id(self):
        return 42

    def connect(self, pipe_name, deadline, cancel_requested):
        self.calls.append(("connect", deadline))
        assert cancel_requested.is_set() is False
        assert pipe_name == r"\\.\pipe\ClipVaultOtpBrokerV1-42-test_1"
        return self.handle

    def write_frame(self, handle, request, deadline):
        assert handle is self.handle
        self.calls.append(("write", deadline))
        self.request = bytearray(request)

    def read_frame(self, handle, deadline):
        assert handle is self.handle
        self.calls.append(("read", deadline))
        if self.read_error is not None:
            raise self.read_error
        return bytearray(self.response)

    def cancel_and_close(self, handle):
        self.closed.append(handle)


def _port(kernel, *, enabled=True):
    return windows_pipe.WindowsNamedPipeOtpOpaqueIngressPort(
        enabled=enabled,
        test_namespace="test_1",
        _kernel=kernel,
        _monotonic=lambda: 10.0,
    )


def test_port_uses_one_capped_absolute_deadline_and_accepts_only_status_one():
    kernel = _FakeKernel()
    port = _port(kernel)
    port.forward(_envelope(), deadline_monotonic=99.0)

    assert [call[0] for call in kernel.calls] == ["connect", "write", "read"]
    assert {call[1] for call in kernel.calls} == {10.25}
    assert kernel.request is not None
    assert kernel.closed == [kernel.handle]


def test_port_maps_valid_nonaccepted_status_to_content_free_rejection():
    kernel = _FakeKernel(_response(2))
    with pytest.raises(OtpOpaqueBrokerUnavailable) as exc:
        _port(kernel).forward(_envelope(), deadline_monotonic=10.2)
    assert exc.value.security_code == "otp_broker_rejected"
    assert kernel.closed == [kernel.handle]


def test_port_timeout_cancels_and_closes_handle():
    kernel = _FakeKernel(read_error=windows_pipe._PipeTimeout("private detail"))
    with pytest.raises(OtpOpaqueBrokerUnavailable) as exc:
        _port(kernel).forward(_envelope(), deadline_monotonic=10.2)
    assert exc.value.security_code == "otp_broker_timeout"
    assert "private detail" not in str(exc.value)
    assert kernel.closed == [kernel.handle]


def test_port_rejects_malformed_response_and_closes_handle():
    kernel = _FakeKernel(bytearray(b"bad"))
    with pytest.raises(OtpOpaqueBrokerUnavailable) as exc:
        _port(kernel).forward(_envelope(), deadline_monotonic=10.2)
    assert exc.value.security_code == "otp_broker_protocol"
    assert kernel.closed == [kernel.handle]


def test_disabled_port_fails_closed_without_touching_kernel():
    kernel = _FakeKernel()
    with pytest.raises(OtpOpaqueBrokerUnavailable) as exc:
        _port(kernel, enabled=False).forward(
            _envelope(),
            deadline_monotonic=10.2,
        )
    assert exc.value.security_code == "otp_broker_unavailable"
    assert kernel.calls == []
    assert kernel.closed == []


def test_enabled_port_fails_closed_off_windows(monkeypatch):
    monkeypatch.setattr(
        windows_pipe,
        "os",
        SimpleNamespace(name="posix", environ={}),
    )
    port = windows_pipe.WindowsNamedPipeOtpOpaqueIngressPort(
        enabled=True,
        _monotonic=lambda: 10.0,
    )
    with pytest.raises(OtpOpaqueBrokerUnavailable) as exc:
        port.forward(_envelope(), deadline_monotonic=10.2)
    assert exc.value.security_code == "otp_broker_unavailable"


class _BlockingKernel(_FakeKernel):
    def __init__(self):
        super().__init__()
        self.read_started = threading.Event()
        self.cancelled = threading.Event()

    def read_frame(self, handle, deadline):
        self.calls.append(("read", deadline))
        self.read_started.set()
        assert self.cancelled.wait(2)
        raise windows_pipe._PipeUnavailable("cancelled")

    def cancel_and_close(self, handle):
        super().cancel_and_close(handle)
        self.cancelled.set()


def test_concurrent_close_cancels_active_handle_once():
    kernel = _BlockingKernel()
    port = _port(kernel)
    failures = []

    def run_forward():
        try:
            port.forward(_envelope(), deadline_monotonic=10.2)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run_forward, daemon=True)
    worker.start()
    assert kernel.read_started.wait(2)
    port.close()
    worker.join(2)

    assert worker.is_alive() is False
    assert len(failures) == 1
    assert isinstance(failures[0], OtpOpaqueBrokerUnavailable)
    assert kernel.closed == [kernel.handle]
    port.close()
    assert kernel.closed == [kernel.handle]


def _config(tmp_path, *, enabled=False):
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="otp-pipe-test",
        db_path=str(tmp_path / "clipvault.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        otp_windows_broker_enabled=enabled,
    )


def test_runtime_config_defaults_disabled_and_explicit_adapters_still_win(tmp_path):
    disabled_runtime = ClipVaultRuntime(_config(tmp_path))
    disabled_port = disabled_runtime.adapters.otp_ingress_port_factory()
    assert isinstance(disabled_port, DisabledOtpOpaqueIngressPort)

    enabled_runtime = ClipVaultRuntime(_config(tmp_path, enabled=True))
    enabled_port = enabled_runtime.adapters.otp_ingress_port_factory()
    assert isinstance(
        enabled_port,
        windows_pipe.WindowsNamedPipeOtpOpaqueIngressPort,
    )
    assert (
        enabled_runtime.adapters.otp_pair_identity_port_factory.__name__
        == "disabled_pair_identity_factory"
    )
    enabled_port.close()

    injected = RuntimeAdapters()
    assert ClipVaultRuntime(
        _config(tmp_path, enabled=True),
        adapters=injected,
    ).adapters is injected


def test_adapter_source_has_required_win32_lifecycle_and_no_data_layer_imports():
    source = windows_pipe.__file__
    assert source is not None
    with open(source, encoding="utf-8") as source_file:
        text = source_file.read()
    assert "FILE_FLAG_OVERLAPPED" in text
    assert "CancelIoEx" in text
    assert "CloseHandle" in text
    for forbidden in (
        "clipvault.store",
        "clipvault.sync",
        "clipboard",
        "import logging",
    ):
        assert forbidden not in text
