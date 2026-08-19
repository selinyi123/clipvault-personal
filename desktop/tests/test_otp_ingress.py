"""Opaque, online-only OTP HTTP ingress and runtime lifecycle gates."""

from __future__ import annotations

import ast
import base64
import hashlib
import http.client
import json
import logging
import re
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from clipvault.api import server as api_server
from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.otp import ingress as otp_ingress
from clipvault.otp.ingress import (
    OTP_RELAY_MAX_BODY_BYTES,
    OTP_RELAY_ROUTE,
    DisabledOtpOpaqueIngressPort,
    OtpOpaqueBrokerUnavailable,
    OtpPairRoute,
    canonical_otp_aad,
    parse_opaque_envelope,
)
from clipvault.runtime import app as runtime_app
from clipvault.runtime.app import ClipVaultRuntime, RuntimeAdapters
from clipvault.service import ClipVaultService
from clipvault.store import db
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo
from clipvault.sync.pairing import hash_token


NOW_MS = 2_000_000_000_000
TOKEN = "paired-bearer-token"
SYNC_PEER_ID = "android-legacy-sync-id"
LEGACY_DESKTOP_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
OTP_SENDER = "device:11111111-1111-4111-8111-111111111111"
OTP_TARGET = "device:22222222-2222-4222-8222-222222222222"
SESSION_EPOCH = "33333333-3333-4333-8333-333333333333"
EVENT_ID = "44444444-4444-4444-8444-444444444444"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _envelope(**overrides) -> dict:
    body = {
        "version": 1,
        "algorithm": "A256GCM",
        "session_epoch": SESSION_EPOCH,
        "event_id": EVENT_ID,
        "sender_device_id": OTP_SENDER,
        "target_device_id": OTP_TARGET,
        "sequence": 1,
        "issued_at_ms": NOW_MS - 1_000,
        "expires_at_ms": NOW_MS + 90_000,
        "nonce": _b64(b"n" * 12),
        "ciphertext": _b64(b"482917"),
        "authentication_tag": _b64(b"t" * 16),
    }
    body.update(overrides)
    return body


def _raw(body: dict | None = None) -> bytes:
    return json.dumps(
        body or _envelope(), separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


class _RecordingBroker:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls = 0
        self.closed = 0
        self.envelope = None
        self.snapshot = None

    def forward(self, envelope, *, deadline_monotonic: float) -> None:
        self.calls += 1
        self.envelope = envelope
        self.snapshot = {
            "sender": envelope.sender_device_id,
            "target": envelope.target_device_id,
            "nonce": bytes(envelope.nonce),
            "ciphertext": bytes(envelope.ciphertext),
            "authentication_tag": bytes(envelope.authentication_tag),
            "sequence": envelope.sequence,
            "deadline_monotonic": deadline_monotonic,
        }
        if self.unavailable:
            raise OtpOpaqueBrokerUnavailable()

    def close(self) -> None:
        self.closed += 1


class _PairIdentityMap:
    def __init__(self, route: OtpPairRoute | None = None) -> None:
        self.route = route or OtpPairRoute(OTP_SENDER, OTP_TARGET)
        self.resolved = []
        self.closed = 0

    def resolve(self, authenticated_sync_device_id: str):
        self.resolved.append(authenticated_sync_device_id)
        return self.route

    def close(self) -> None:
        self.closed += 1


def _cfg(tmp_path, *, db_path=":memory:") -> Config:
    return Config(
        device_id=LEGACY_DESKTOP_ID,
        device_name="otp-ingress-test",
        db_path=str(db_path),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        host="127.0.0.1",
        port=0,
    )


def _api(
    conn,
    cfg,
    broker=None,
    pair_map=None,
    *,
    broker_timeout_s=0.25,
) -> tuple[Api, _RecordingBroker, _PairIdentityMap]:
    broker = broker or _RecordingBroker()
    pair_map = pair_map or _PairIdentityMap()
    PeersRepo(conn).upsert_pair(
        SYNC_PEER_ID,
        "legacy phone label",
        hash_token(TOKEN),
        "2026-08-01T00:00:00Z",
        peer_cursor=0,
    )
    api = Api(
        ClipVaultService(conn, cfg),
        otp_ingress_port=broker,
        otp_pair_identity_port=pair_map,
        otp_now_ms=lambda: NOW_MS,
        otp_broker_timeout_s=broker_timeout_s,
    )
    return api, broker, pair_map


def _serve_one(api, client, *, read_timeout_s=1.0):
    httpd = api_server.build_server(
        api,
        "127.0.0.1",
        0,
        read_timeout_s=read_timeout_s,
    )
    results = []
    failures = []

    def run():
        try:
            results.append(client(httpd.server_address[1]))
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        httpd.handle_request()
        thread.join(3)
    finally:
        httpd.server_close()
    if thread.is_alive():
        raise AssertionError("OTP test client did not complete")
    if failures:
        raise failures[0]
    return results[0]


def _post(port: int, body: bytes, token: str = TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(
        "POST",
        OTP_RELAY_ROUTE,
        body=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    response = conn.getresponse()
    payload = response.read()
    status = response.status
    conn.close()
    return status, payload


def test_otp_ingress_api_forwards_only_opaque_buffers_without_persistence(
    conn,
    tmp_path,
    monkeypatch,
):
    api, broker, pair_map = _api(conn, _cfg(tmp_path))
    before_changes = conn.total_changes
    before_outbox = OutboxRepo(conn).max_seq()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("ordinary persistence/clipboard path was called")

    monkeypatch.setattr(OutboxRepo, "append", forbidden)
    monkeypatch.setattr(ClipVaultService, "handle_clipboard_text", forbidden)
    statements = []
    conn.set_trace_callback(statements.append)
    status, response = api.otp_relay(TOKEN, bytearray(_raw()))
    conn.set_trace_callback(None)

    assert status == 202
    assert response == {
        "status": "accepted",
        "event_hash": hashlib.sha256(EVENT_ID.encode("ascii")).hexdigest(),
    }
    assert pair_map.resolved == [SYNC_PEER_ID]
    assert broker.calls == 1
    assert broker.snapshot["sender"] == OTP_SENDER
    assert broker.snapshot["target"] == OTP_TARGET
    assert broker.snapshot["ciphertext"] == b"482917"
    assert broker.snapshot["authentication_tag"] == b"t" * 16
    assert broker.snapshot["sequence"] == 1
    assert set(broker.envelope.nonce) == {0}
    assert set(broker.envelope.ciphertext) == {0}
    assert set(broker.envelope.authentication_tag) == {0}
    assert conn.total_changes == before_changes
    assert OutboxRepo(conn).max_seq() == before_outbox
    assert statements and all(row.lstrip().upper().startswith("SELECT") for row in statements)


@pytest.mark.parametrize(
    ("body", "expected_status", "expected_code"),
    [
        (_envelope(sender_device_id="device:55555555-5555-4555-8555-555555555555"), 403, "otp_sender_mismatch"),
        (_envelope(target_device_id="device:66666666-6666-4666-8666-666666666666"), 403, "otp_target_mismatch"),
        (_envelope(expires_at_ms=NOW_MS), 410, "otp_expired"),
        (_envelope(sender_device_id=SYNC_PEER_ID), 400, "otp_bad_sender"),
        (_envelope(target_device_id=LEGACY_DESKTOP_ID), 400, "otp_bad_target"),
        (_envelope(sender_device_id="device:11111111-1111-1111-8111-111111111111"), 400, "otp_bad_sender"),
        (_envelope(sequence=0), 400, "otp_bad_sequence"),
        (_envelope(sequence=(2**63)), 400, "otp_bad_sequence"),
        (_envelope(sequence=True), 400, "otp_bad_sequence"),
        (_envelope(nonce="bm90LXQxMi1ieXRlcw"), 400, "otp_bad_encoding"),
        (_envelope(ciphertext="***"), 400, "otp_bad_encoding"),
        (_envelope(ciphertext=_b64(b"123")), 400, "otp_bad_encoding"),
        (_envelope(ciphertext=_b64(b"123456789")), 400, "otp_bad_encoding"),
        (_envelope(authentication_tag=_b64(b"short-tag")), 400, "otp_bad_encoding"),
        ({**_envelope(), "unknown": True}, 400, "otp_bad_envelope"),
    ],
)
def test_otp_ingress_rejects_bad_security_metadata_before_broker(
    conn,
    tmp_path,
    body,
    expected_status,
    expected_code,
):
    api, broker, _pair_map = _api(conn, _cfg(tmp_path))
    status, response = api.otp_relay(TOKEN, _raw(body))
    assert status == expected_status
    assert response["error"]["code"] == expected_code
    assert broker.calls == 0


def test_otp_ingress_rejects_duplicate_json_members(conn, tmp_path):
    api, broker, _pair_map = _api(conn, _cfg(tmp_path))
    valid = _raw().decode("ascii")
    duplicate = valid.replace('"version":1', '"version":1,"version":1', 1).encode("ascii")
    status, response = api.otp_relay(TOKEN, duplicate)
    assert status == 400
    assert response["error"]["code"] == "otp_bad_envelope"
    assert broker.calls == 0


def test_otp_v1_canonical_aad_matches_frozen_vector_and_each_field_is_bound(
    vectors_dir,
):
    vector = json.loads((vectors_dir / "otp_aead_v1.json").read_text("utf-8"))
    inputs = vector["inputs"]
    body = _envelope(
        session_epoch=inputs["session_epoch"],
        event_id=inputs["event_id"],
        sender_device_id=inputs["sender_device"],
        target_device_id=inputs["target_device"],
        sequence=inputs["sequence"],
        issued_at_ms=inputs["issued_at_unix_ms"],
        expires_at_ms=inputs["expires_at_unix_ms"],
        nonce=_b64(bytes.fromhex(inputs["nonce_hex"])),
        ciphertext=_b64(bytes.fromhex(vector["derived"]["ciphertext_hex"])),
        authentication_tag=_b64(
            bytes.fromhex(vector["derived"]["authentication_tag_hex"])
        ),
    )
    envelope = parse_opaque_envelope(
        _raw(body),
        authenticated_sender=inputs["sender_device"],
        expected_target=inputs["target_device"],
        now_ms=inputs["issued_at_unix_ms"] + 1,
    )
    try:
        aad = canonical_otp_aad(envelope)
        assert aad.hex() == vector["derived"]["aad_hex"]
        assert hashlib.sha256(aad).hexdigest() == vector["derived"]["aad_sha256_hex"]
        assert len(envelope.ciphertext) == len(inputs["plaintext_ascii"]) == 6
        assert len(envelope.authentication_tag) == 16
        assert "key_id" not in _envelope()
        one_bit_changes = {
            "version": envelope.version ^ 1,
            "session_epoch": envelope.session_epoch[:-1] + "0",
            "event_id": envelope.event_id[:-1] + "3",
            "sender_device_id": envelope.sender_device_id[:-1] + "2",
            "target_device_id": envelope.target_device_id[:-1] + "5",
            "sequence": envelope.sequence ^ 1,
            "issued_at_ms": envelope.issued_at_ms ^ 1,
            "expires_at_ms": envelope.expires_at_ms ^ 1,
        }
        for field_name, changed_value in one_bit_changes.items():
            changed = replace(
                envelope,
                nonce=bytearray(envelope.nonce),
                ciphertext=bytearray(envelope.ciphertext),
                authentication_tag=bytearray(envelope.authentication_tag),
            )
            try:
                setattr(changed, field_name, changed_value)
                assert canonical_otp_aad(changed) != aad, field_name
            finally:
                changed.close()
    finally:
        envelope.close()


def test_otp_wire_parser_fields_and_encoding_match_frozen_json_schema(vectors_dir):
    schema = json.loads(
        (vectors_dir.parent / "otp_relay_wire_v1.schema.json").read_text("utf-8")
    )
    body = _envelope()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(body)
    assert body["version"] == schema["properties"]["version"]["const"]
    assert body["algorithm"] == schema["properties"]["algorithm"]["const"]
    for field_name in (
        "session_epoch",
        "event_id",
        "sender_device_id",
        "target_device_id",
        "nonce",
        "ciphertext",
        "authentication_tag",
    ):
        field_schema = schema["properties"][field_name]
        if "$ref" in field_schema:
            pattern = schema["$defs"][field_schema["$ref"].split("/")[-1]]["pattern"]
        else:
            pattern = field_schema["pattern"]
        assert re.fullmatch(pattern, body[field_name]), field_name
    assert schema["properties"]["sequence"]["maximum"] == (2**63) - 1


def test_otp_ingress_wrong_bearer_does_not_resolve_pair_or_parse_body(conn, tmp_path):
    api, broker, pair_map = _api(conn, _cfg(tmp_path))
    status, response = api.otp_relay("wrong", b"not-json-private-body")
    assert status == 401
    assert response["error"]["code"] == "unauthorized"
    assert pair_map.resolved == []
    assert broker.calls == 0


def test_otp_ingress_offline_broker_returns_503_and_destroys_envelope(conn, tmp_path):
    broker = _RecordingBroker(unavailable=True)
    api, broker, _pair_map = _api(conn, _cfg(tmp_path), broker=broker)
    before = conn.total_changes
    status, response = api.otp_relay(TOKEN, _raw())
    assert status == 503
    assert response["error"]["code"] == "otp_broker_unavailable"
    assert broker.calls == 1
    assert set(broker.envelope.nonce) == {0}
    assert set(broker.envelope.ciphertext) == {0}
    assert set(broker.envelope.authentication_tag) == {0}
    assert conn.total_changes == before
    assert OutboxRepo(conn).max_seq() == 0


def test_otp_ingress_deadline_aware_broker_times_out_and_poison_gate(
    conn,
    tmp_path,
):
    class DeadlineAwareBroker(_RecordingBroker):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.deadline = None

        def forward(self, envelope, *, deadline_monotonic: float) -> None:
            self.calls += 1
            self.envelope = envelope
            self.deadline = deadline_monotonic
            self.started.set()
            while time.monotonic() < deadline_monotonic:
                time.sleep(0.001)
            raise OtpOpaqueBrokerUnavailable("otp_broker_timeout")

        def close(self) -> None:
            self.closed += 1

    broker = DeadlineAwareBroker()
    api, broker, _pair_map = _api(
        conn,
        _cfg(tmp_path),
        broker=broker,
        broker_timeout_s=0.05,
    )
    started = time.monotonic()
    status, response = api.otp_relay(TOKEN, _raw())
    elapsed = time.monotonic() - started
    assert status == 503
    assert response["error"]["code"] == "otp_broker_timeout"
    assert broker.started.is_set()
    assert 0 < elapsed < 0.5
    assert broker.deadline is not None and broker.deadline >= started
    assert set(broker.envelope.nonce) == {0}
    assert set(broker.envelope.ciphertext) == {0}
    assert set(broker.envelope.authentication_tag) == {0}

    # A timed-out adapter is poisoned. A second request is discarded without
    # creating another broker worker or any offline retry state.
    second_status, second_response = api.otp_relay(
        TOKEN,
        _raw(_envelope(sequence=2)),
    )
    assert second_status == 503
    assert second_response["error"]["code"] == "otp_broker_unavailable"
    assert broker.calls == 1
    api.close()
    assert broker.closed == 1


def test_otp_ingress_rejection_logs_are_content_free_and_rate_limited(
    conn,
    tmp_path,
    monkeypatch,
    caplog,
):
    limiter = otp_ingress._SecurityLogLimiter(
        interval_s=60,
        monotonic=lambda: 1.0,
    )
    monkeypatch.setattr(otp_ingress, "_rejection_log_limiter", limiter)
    api, broker, _pair_map = _api(conn, _cfg(tmp_path))
    marker = "never-log-this-envelope-marker"
    bad = _raw(_envelope(sender_device_id=marker))
    with caplog.at_level(logging.WARNING, logger="clipvault.otp.ingress"):
        assert api.otp_relay(TOKEN, bad)[0] == 400
        assert api.otp_relay(TOKEN, bad)[0] == 400
    matching = [
        record
        for record in caplog.records
        if "code=otp_bad_sender" in record.getMessage()
    ]
    assert len(matching) == 1
    assert marker not in caplog.text
    assert broker.calls == 0


def test_otp_server_exact_route_wipes_raw_body_and_never_logs_or_echoes_ciphertext(
    conn,
    tmp_path,
    caplog,
):
    api, broker, _pair_map = _api(conn, _cfg(tmp_path))
    raw_refs = []
    original = api.otp_relay

    def capture_raw(token, raw):
        raw_refs.append(raw)
        return original(token, raw)

    api.otp_relay = capture_raw
    marker = _envelope()["ciphertext"]
    with caplog.at_level(logging.INFO):
        status, payload = _serve_one(api, lambda port: _post(port, _raw()))

    assert status == 202
    response = json.loads(payload)
    assert set(response) == {"status", "event_hash"}
    assert marker.encode("ascii") not in payload
    assert marker not in caplog.text
    assert "POST /api/otp/relay" not in caplog.text
    assert raw_refs and set(raw_refs[0]) == {0}
    assert broker.calls == 1
    assert broker.closed == 1
    assert api_server._remote_allowed(OTP_RELAY_ROUTE) is True
    assert api_server._remote_allowed(f"{OTP_RELAY_ROUTE}/extra") is False


def test_otp_server_auth_rejection_log_is_content_free_and_rate_limited(
    conn,
    tmp_path,
    monkeypatch,
    caplog,
):
    limiter = otp_ingress._SecurityLogLimiter(
        interval_s=60,
        monotonic=lambda: 1.0,
    )
    monkeypatch.setattr(otp_ingress, "_rejection_log_limiter", limiter)
    api, broker, pair_map = _api(conn, _cfg(tmp_path))
    token_marker = "bad-token-private-marker"
    body_marker = "bad-body-private-marker"

    with caplog.at_level(logging.WARNING, logger="clipvault.otp.ingress"):
        for _ in range(2):
            status, payload = _serve_one(
                api,
                lambda port: _post(
                    port,
                    body_marker.encode("ascii"),
                    token=token_marker,
                ),
            )
            assert status == 401
            assert json.loads(payload)["error"]["code"] == "unauthorized"

    matching = [
        record
        for record in caplog.records
        if "code=otp_auth_failed" in record.getMessage()
    ]
    assert len(matching) == 1
    assert token_marker not in caplog.text
    assert body_marker not in caplog.text
    assert "POST /api/otp/relay" not in caplog.text
    assert broker.calls == 0
    assert pair_map.resolved == []


def test_otp_server_rejects_oversized_request_without_broker(conn, tmp_path):
    api, broker, _pair_map = _api(conn, _cfg(tmp_path))
    status, payload = _serve_one(
        api,
        lambda port: _post(port, b"x" * (OTP_RELAY_MAX_BODY_BYTES + 1)),
    )
    assert status == 413
    assert json.loads(payload)["error"]["code"] == "payload_too_large"
    assert broker.calls == 0


def test_otp_server_partial_body_times_out_and_is_discarded(conn, tmp_path):
    api, broker, _pair_map = _api(conn, _cfg(tmp_path))
    body = _raw()
    def partial_client(port):
        with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
            sock.settimeout(2)
            request = (
                f"POST {OTP_RELAY_ROUTE} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Authorization: Bearer {TOKEN}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            sock.sendall(request + body[:1])
            return sock.recv(4096)

    response = _serve_one(api, partial_client, read_timeout_s=0.1)
    assert b" 408 " in response.split(b"\r\n", 1)[0]
    assert broker.calls == 0


def test_otp_ingress_module_imports_no_storage_sync_clipboard_or_network_layer():
    source = Path(runtime_app.__file__).parents[1] / "otp" / "ingress.py"
    source_text = source.read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden_roots = {
        "clipvault.store",
        "clipvault.sync",
        "clipvault.watcher",
        "sqlite3",
        "socket",
        "pathlib",
        "urllib",
        "http",
    }
    assert not any(
        imported == root or imported.startswith(f"{root}.")
        for imported in imports
        for root in forbidden_roots
    )
    assert "threading.Thread(" not in source_text
    assert "daemon=True" not in source_text


class _RuntimeWorker:
    def __init__(self, *_args, **_kwargs):
        pass

    def notify(self):
        return None

    def run(self, stop):
        stop.wait()


class _RuntimeWatcher:
    def __init__(self, *_args, **_kwargs):
        pass

    def run(self, stop):
        stop.wait()


def test_runtime_owns_and_closes_injected_otp_ports_once(tmp_path, monkeypatch):
    broker = _RecordingBroker()
    pair_map = _PairIdentityMap()

    class FakeHttpd:
        server_address = ("127.0.0.1", 0)
        timeout = 0.0

        def handle_request(self):
            time.sleep(0.01)

        def server_close(self):
            return None

    monkeypatch.setattr(runtime_app.api_server, "_prepare_database", lambda _conn: None)
    monkeypatch.setattr(
        runtime_app.api_server,
        "build_server",
        lambda *_args, **_kwargs: FakeHttpd(),
    )
    adapters = RuntimeAdapters(
        connect=db.connect,
        migrate=db.migrate,
        api_serve=runtime_app._RUNTIME_API_SERVE,
        otp_ingress_port_factory=lambda: broker,
        otp_pair_identity_port_factory=lambda: pair_map,
        watcher_factory=_RuntimeWatcher,
        obsidian_worker_factory=_RuntimeWorker,
        thread_factory=threading.Thread,
    )
    runtime = ClipVaultRuntime(
        _cfg(tmp_path, db_path=tmp_path / "runtime.sqlite3"),
        adapters=adapters,
        maintenance_interval_s=60,
    )

    runtime.start()
    runtime.request_stop()
    assert runtime.join(2) == []
    assert broker.closed == 1
    assert pair_map.closed == 1


def test_public_production_composers_remain_unconditionally_closed():
    from clipvault.otp import OtpRelayProducer, OtpRelayReceiver

    with pytest.raises(RuntimeError, match="reviewed platform factory"):
        OtpRelayProducer()
    with pytest.raises(RuntimeError, match="reviewed platform factory"):
        OtpRelayReceiver()
    assert isinstance(DisabledOtpOpaqueIngressPort(), DisabledOtpOpaqueIngressPort)
