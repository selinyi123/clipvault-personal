"""OTP pair authority, metadata route, HTTP bootstrap, and credential store."""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import shutil
import threading
import uuid
from types import SimpleNamespace

import pytest

from clipvault.api import server as api_server
from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.otp import pairing as otp_pairing
from clipvault.runtime.app import ClipVaultRuntime
from clipvault.service import ClipVaultService
from clipvault.store.peers_repo import PeersRepo
from clipvault.store import db
from clipvault.sync.pairing import hash_token


TOKEN = "otp-pair-bearer"
SYNC_DEVICE = "legacy-phone-id"
SENDER = "device:11111111-1111-4111-8111-111111111111"
SESSION = "22222222-2222-4222-8222-222222222222"
TARGET_UUID = "33333333-3333-4333-8333-333333333333"


class _FakeCredentialStore:
    def __init__(self, *, fail_write=False, fail_delete=False):
        self.values = {}
        self.fail_write = fail_write
        self.fail_delete = fail_delete
        self.closed = 0
        self.write_count = 0

    def write(self, target, blob):
        self.write_count += 1
        if self.fail_write:
            raise RuntimeError("private credential failure")
        self.values[target] = bytearray(blob)

    def read(self, target):
        value = self.values.get(target)
        return None if value is None else bytearray(value)

    def delete(self, target):
        if self.fail_delete:
            raise RuntimeError("private credential failure")
        value = self.values.pop(target, None)
        otp_pairing._wipe(value)
        return value is not None

    def close(self):
        self.closed += 1
        for value in self.values.values():
            otp_pairing._wipe(value)
        self.values.clear()


def _cfg(tmp_path, *, pairing_enabled=False):
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="otp-pair-test",
        db_path=":memory:",
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        host="127.0.0.1",
        port=0,
        otp_pairing_enabled=pairing_enabled,
    )


def _peer(conn):
    PeersRepo(conn).upsert_pair(
        SYNC_DEVICE,
        "phone",
        hash_token(TOKEN),
        "2026-08-01T00:00:00Z",
        peer_cursor=0,
    )


def _uuid_source():
    values = iter((uuid.UUID(SESSION), uuid.UUID(TARGET_UUID)))
    return lambda: next(values)


def _authority(conn, store=None, *, enabled=True):
    return otp_pairing.SqliteOtpPairingAuthority(
        conn,
        store or _FakeCredentialStore(),
        pairing_enabled=enabled,
        uuid4=_uuid_source(),
        token_bytes=lambda count: bytes(range(count)),
    )


def test_credential_codec_is_exact_frozen_96_byte_record():
    verifier = bytearray(range(32))
    blob = otp_pairing.encode_pair_credential(
        session_epoch=SESSION,
        sender_device_id=SENDER,
        target_device_id=f"device:{TARGET_UUID}",
        verifier=verifier,
    )

    assert len(blob) == 96
    assert blob[:8] == b"CVPK\x01\x00\x00\x00"
    assert blob[8:24] == uuid.UUID(SESSION).bytes
    assert blob[24:40] == uuid.UUID(SENDER.removeprefix("device:")).bytes
    assert blob[40:56] == uuid.UUID(TARGET_UUID).bytes
    assert blob[56:88] == verifier
    assert blob[88:96] == bytes(8)

    decoded = otp_pairing.decode_pair_credential(blob)
    assert decoded.session_epoch == SESSION
    assert decoded.sender_device_id == SENDER
    assert decoded.target_device_id == f"device:{TARGET_UUID}"
    assert decoded.verifier == verifier
    assert decoded.high_sequence == 0
    assert "redacted" in repr(decoded)
    decoded.close()
    assert decoded.verifier == bytes(32)


def test_authority_persists_only_route_metadata_and_resolves_identity(conn):
    _peer(conn)
    store = _FakeCredentialStore()
    authority = _authority(conn, store)
    result = authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})

    assert result.session_epoch == SESSION
    assert result.sender_device_id == SENDER
    assert result.target_device_id == f"device:{TARGET_UUID}"
    assert "=" not in result.verifier
    assert base64.urlsafe_b64decode(result.verifier + "=") == bytes(range(32))
    assert "redacted" in repr(result)

    row = conn.execute("SELECT * FROM otp_pair_routes").fetchone()
    assert dict(row) == {
        "sync_device_id": SYNC_DEVICE,
        "session_epoch": SESSION,
        "sender_device_id": SENDER,
        "target_device_id": f"device:{TARGET_UUID}",
        "credential_target": f"ClipVault/OTP/Pair/v1/{SESSION}",
        "revoked": 0,
    }
    assert result.verifier not in {str(value) for value in row}
    assert len(store.values[row["credential_target"]]) == 96

    identity = otp_pairing.SqliteOtpPairIdentityPort(conn)
    route = identity.resolve(SYNC_DEVICE)
    assert route is not None
    assert route.sender_device == SENDER
    assert route.target_device == f"device:{TARGET_UUID}"
    assert identity.resolve("different-peer") is None

    with pytest.raises(otp_pairing.OtpPairingConflict):
        authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})
    assert store.write_count == 1


def test_pair_write_or_database_failure_removes_route_and_orphan(conn):
    _peer(conn)
    failing_store = _FakeCredentialStore(fail_write=True)
    with pytest.raises(otp_pairing.OtpPairingUnavailable) as exc:
        _authority(conn, failing_store).pair(
            SYNC_DEVICE,
            {"sender_device_id": SENDER},
        )
    assert exc.value.security_code == "otp_pairing_unavailable"
    assert conn.execute("SELECT 1 FROM otp_pair_routes").fetchone() is None
    assert failing_store.values == {}

    conn.execute(
        "CREATE TRIGGER reject_otp_pair BEFORE INSERT ON otp_pair_routes "
        "BEGIN SELECT RAISE(ABORT, 'private database detail'); END"
    )
    conn.commit()
    store = _FakeCredentialStore()
    with pytest.raises(otp_pairing.OtpPairingUnavailable) as exc:
        _authority(conn, store).pair(
            SYNC_DEVICE,
            {"sender_device_id": SENDER},
        )
    assert "private database detail" not in str(exc.value)
    assert store.values == {}
    assert conn.execute("SELECT 1 FROM otp_pair_routes").fetchone() is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"sender_device_id": SENDER, "extra": True},
        {"sender_device_id": SENDER.upper()},
        {"sender_device_id": "11111111-1111-4111-8111-111111111111"},
    ],
)
def test_authority_rejects_nonexact_or_noncanonical_request(conn, body):
    _peer(conn)
    store = _FakeCredentialStore()
    with pytest.raises(otp_pairing.OtpPairingRejected):
        _authority(conn, store).pair(SYNC_DEVICE, body)
    assert store.values == {}


def test_revoke_marks_route_inactive_before_credential_delete(conn):
    _peer(conn)
    store = _FakeCredentialStore()
    authority = _authority(conn, store)
    authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})
    identity = otp_pairing.SqliteOtpPairIdentityPort(conn)
    store.fail_delete = True

    with pytest.raises(otp_pairing.OtpPairingUnavailable) as exc:
        authority.revoke(SYNC_DEVICE)
    assert exc.value.security_code == "otp_pair_revoke_failed"
    assert identity.resolve(SYNC_DEVICE) is None
    assert conn.execute(
        "SELECT revoked FROM otp_pair_routes WHERE sync_device_id = ?",
        (SYNC_DEVICE,),
    ).fetchone()[0] == 1

    store.fail_delete = False
    assert authority.revoke(SYNC_DEVICE) is True
    assert conn.execute("SELECT 1 FROM otp_pair_routes").fetchone() is None
    assert store.values == {}


def test_api_unpair_deletes_otp_route_and_credential_before_legacy_peer(conn, tmp_path):
    _peer(conn)
    store = _FakeCredentialStore()
    authority = _authority(conn, store)
    authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})
    api = Api(
        ClipVaultService(conn, _cfg(tmp_path, pairing_enabled=True)),
        otp_pairing_authority=authority,
    )

    assert api.unpair(SYNC_DEVICE) == (
        200,
        {"device_id": SYNC_DEVICE, "unpaired": True},
    )
    assert PeersRepo(conn).get(SYNC_DEVICE) is None
    assert conn.execute("SELECT 1 FROM otp_pair_routes").fetchone() is None
    assert store.values == {}


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        ("127.0.0.1", True),
        ("127.255.10.20", True),
        ("::1", True),
        ("100.64.0.1", True),
        ("100.127.255.254", True),
        ("fd7a:115c:a1e0::1", True),
        ("192.168.1.20", False),
        ("10.0.0.2", False),
        ("100.128.0.1", False),
        ("fd7a:115c:a1e1::1", False),
        ("not-an-address", False),
    ],
)
def test_otp_pair_transport_accepts_only_loopback_or_tailscale(address, allowed):
    assert api_server._otp_pair_source_allowed(address) is allowed


def _serve_one(api, request):
    httpd = api_server.build_server(api, "127.0.0.1", 0, read_timeout_s=1.0)
    result = []
    failures = []

    def client():
        try:
            result.append(request(httpd.server_address[1]))
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=client, daemon=True)
    thread.start()
    try:
        httpd.handle_request()
        thread.join(3)
    finally:
        httpd.server_close()
    assert not thread.is_alive()
    if failures:
        raise failures[0]
    return result[0]


def _post_pair(port, *, token=TOKEN, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(
        "POST",
        otp_pairing.OTP_PAIR_ROUTE,
        body=(
            body
            if isinstance(body, str)
            else json.dumps(
                {"sender_device_id": SENDER} if body is None else body
            )
        ),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    result = response.status, dict(response.headers), payload
    connection.close()
    return result


def test_exact_http_pair_route_authenticates_and_returns_no_store_response(
    conn,
    tmp_path,
    caplog,
):
    _peer(conn)
    store = _FakeCredentialStore()
    authority = _authority(conn, store)
    api = Api(
        ClipVaultService(conn, _cfg(tmp_path, pairing_enabled=True)),
        otp_pairing_authority=authority,
    )

    with caplog.at_level(logging.INFO):
        status, headers, payload = _serve_one(
            api,
            lambda port: _post_pair(port),
        )
    assert status == 201
    assert headers["Cache-Control"] == "no-store"
    assert headers["Pragma"] == "no-cache"
    assert payload == {
        "version": 1,
        "session_epoch": SESSION,
        "sender_device_id": SENDER,
        "target_device_id": f"device:{TARGET_UUID}",
        "verifier": base64.urlsafe_b64encode(bytes(range(32)))
        .rstrip(b"=")
        .decode("ascii"),
    }
    assert payload["verifier"] not in caplog.text


def test_http_pair_rejects_bad_bearer_before_authority(conn, tmp_path):
    _peer(conn)

    class ForbiddenAuthority:
        def pair(self, *_args):
            raise AssertionError("authority must not be called")

        def revoke(self, *_args):
            return False

        def close(self):
            return None

    api = Api(
        ClipVaultService(conn, _cfg(tmp_path, pairing_enabled=True)),
        otp_pairing_authority=ForbiddenAuthority(),
    )
    status, headers, payload = _serve_one(
        api,
        lambda port: _post_pair(port, token="wrong"),
    )
    assert status == 401
    assert headers["Cache-Control"] == "no-store"
    assert payload["error"]["code"] == "unauthorized"


def test_http_pair_rejects_non_tailscale_transport_before_authority(
    conn,
    tmp_path,
    monkeypatch,
):
    _peer(conn)

    class ForbiddenAuthority:
        def pair(self, *_args):
            raise AssertionError("authority must not be called")

        def revoke(self, *_args):
            return False

        def close(self):
            return None

    monkeypatch.setattr(
        api_server,
        "_otp_pair_source_allowed",
        lambda _address: False,
    )
    api = Api(
        ClipVaultService(conn, _cfg(tmp_path, pairing_enabled=True)),
        otp_pairing_authority=ForbiddenAuthority(),
    )
    status, headers, payload = _serve_one(
        api,
        lambda port: _post_pair(port),
    )
    assert status == 403
    assert headers["Cache-Control"] == "no-store"
    assert payload["error"]["code"] == "otp_pair_transport_rejected"


def test_http_pair_rejects_duplicate_sender_member(conn, tmp_path):
    _peer(conn)
    authority = _authority(conn)
    api = Api(
        ClipVaultService(conn, _cfg(tmp_path, pairing_enabled=True)),
        otp_pairing_authority=authority,
    )
    duplicate = (
        '{"sender_device_id":"'
        + SENDER
        + '","sender_device_id":"'
        + SENDER
        + '"}'
    )
    status, _, payload = _serve_one(
        api,
        lambda port: _post_pair(port, body=duplicate),
    )
    assert status == 400
    assert payload["error"]["code"] == "bad_request"


def test_runtime_pairing_config_selects_sqlite_identity_and_windows_authority(
    conn,
    tmp_path,
):
    runtime = ClipVaultRuntime(_cfg(tmp_path, pairing_enabled=True))
    identity = runtime.adapters.otp_pair_identity_port_factory(conn)
    authority = runtime.adapters.otp_pairing_authority_factory(conn)
    assert isinstance(identity, otp_pairing.SqliteOtpPairIdentityPort)
    assert isinstance(authority, otp_pairing.SqliteOtpPairingAuthority)
    identity.close()
    authority.close()


def test_schema_nine_upgrades_to_otp_route_metadata_without_peer_loss(tmp_path):
    v9 = tmp_path / "migrations-v9"
    v9.mkdir()
    for script in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if int(script.name[:4]) <= 9:
            shutil.copy2(script, v9 / script.name)
    connection = db.connect(tmp_path / "legacy.sqlite3")
    try:
        assert db.migrate(connection, v9, expected_latest=9) == 9
        _peer(connection)
        assert db.migrate(connection) == 10
        assert PeersRepo(connection).get(SYNC_DEVICE) is not None
        assert connection.execute(
            "SELECT 1 FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'otp_pair_routes'"
        ).fetchone() is not None
    finally:
        connection.close()


def test_pairing_disabled_and_non_windows_credential_store_fail_closed(
    conn,
    monkeypatch,
):
    _peer(conn)
    store = _FakeCredentialStore()
    with pytest.raises(otp_pairing.OtpPairingUnavailable):
        _authority(conn, store, enabled=False).pair(
            SYNC_DEVICE,
            {"sender_device_id": SENDER},
        )
    assert store.values == {}

    monkeypatch.setattr(
        otp_pairing,
        "os",
        SimpleNamespace(name="posix"),
    )
    real_store = otp_pairing.WindowsCredentialManagerStore(enabled=True)
    blob = otp_pairing.encode_pair_credential(
        session_epoch=SESSION,
        sender_device_id=SENDER,
        target_device_id=f"device:{TARGET_UUID}",
        verifier=bytearray(range(32)),
    )
    with pytest.raises(otp_pairing.OtpPairingUnavailable):
        real_store.write(
            f"ClipVault/OTP/Pair/v1/{SESSION}",
            blob,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager only")
def test_real_windows_credential_manager_round_trip_and_cleanup():
    session = str(uuid.uuid4())
    sender = f"device:{uuid.uuid4()}"
    target = f"device:{uuid.uuid4()}"
    credential_target = f"ClipVault/OTP/Pair/v1/{session}"
    verifier = bytearray(os.urandom(32))
    blob = otp_pairing.encode_pair_credential(
        session_epoch=session,
        sender_device_id=sender,
        target_device_id=target,
        verifier=verifier,
    )
    store = otp_pairing.WindowsCredentialManagerStore(enabled=True)
    read_back = None
    try:
        store.delete(credential_target)
        store.write(credential_target, blob)
        read_back = store.read(credential_target)
        assert read_back == blob
        assert store.delete(credential_target) is True
        assert store.read(credential_target) is None
    finally:
        try:
            store.delete(credential_target)
        finally:
            store.close()
            otp_pairing._wipe(read_back)
            otp_pairing._wipe(blob)
            otp_pairing._wipe(verifier)
