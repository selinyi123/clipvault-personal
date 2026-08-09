"""OTP pair authority, metadata route, HTTP bootstrap, and credential store."""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import shutil
import sqlite3
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


class _FakeBrokerRevocationPort:
    def __init__(self, *, fail=False, before_revoke=None):
        self.fail = fail
        self.before_revoke = before_revoke
        self.sessions = []
        self.closed = 0

    def revoke(self, session_epoch):
        if self.before_revoke is not None:
            self.before_revoke(session_epoch)
        if self.fail:
            raise RuntimeError("private Broker failure")
        self.sessions.append(session_epoch)

    def close(self):
        self.closed += 1


def _cfg(tmp_path, *, pairing_enabled=False, broker_enabled=False):
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
        otp_windows_broker_enabled=broker_enabled,
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


def _authority(conn, store=None, *, enabled=True, revoker=None):
    return otp_pairing.SqliteOtpPairingAuthority(
        conn,
        store or _FakeCredentialStore(),
        pairing_enabled=enabled,
        broker_revocation_port=(
            revoker
            if revoker is not None
            else otp_pairing.DisabledOtpBrokerRevocationPort()
        ),
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


def test_credential_codec_rejects_zero_verifier_and_same_identity():
    with pytest.raises(ValueError, match="invalid OTP pair credential"):
        otp_pairing.encode_pair_credential(
            session_epoch=SESSION,
            sender_device_id=SENDER,
            target_device_id=f"device:{TARGET_UUID}",
            verifier=bytearray(32),
        )
    with pytest.raises(ValueError, match="invalid OTP pair credential"):
        otp_pairing.encode_pair_credential(
            session_epoch=SESSION,
            sender_device_id=SENDER,
            target_device_id=SENDER,
            verifier=bytearray(range(32)),
        )
    malformed = otp_pairing.encode_pair_credential(
        session_epoch=SESSION,
        sender_device_id=SENDER,
        target_device_id=f"device:{TARGET_UUID}",
        verifier=bytearray(range(32)),
    )
    malformed[56:88] = bytes(32)
    with pytest.raises(ValueError, match="invalid OTP pair credential"):
        otp_pairing.decode_pair_credential(malformed)
    malformed[56:88] = bytes(range(32))
    malformed[40:56] = malformed[24:40]
    with pytest.raises(ValueError, match="invalid OTP pair credential"):
        otp_pairing.decode_pair_credential(malformed)
    otp_pairing._wipe(malformed)


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
    revoker = _FakeBrokerRevocationPort()
    authority = _authority(conn, store, revoker=revoker)
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
    assert revoker.sessions == [SESSION]

    store.fail_delete = False
    assert authority.revoke(SYNC_DEVICE) is True
    assert revoker.sessions == [SESSION, SESSION]
    assert conn.execute("SELECT 1 FROM otp_pair_routes").fetchone() is None
    assert store.values == {}


def test_revoke_deletes_credential_before_broker_clear_and_is_recoverable(conn):
    _peer(conn)
    store = _FakeCredentialStore()

    def assert_fail_closed_before_broker(session_epoch):
        row = conn.execute(
            "SELECT credential_target, revoked FROM otp_pair_routes "
            "WHERE sync_device_id = ?",
            (SYNC_DEVICE,),
        ).fetchone()
        assert session_epoch == SESSION
        assert row[1] == 1
        assert row[0] not in store.values

    revoker = _FakeBrokerRevocationPort(
        fail=True,
        before_revoke=assert_fail_closed_before_broker,
    )
    authority = _authority(conn, store, revoker=revoker)
    authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})

    with pytest.raises(otp_pairing.OtpPairingUnavailable) as exc:
        authority.revoke(SYNC_DEVICE)
    assert exc.value.security_code == "otp_pair_revoke_failed"
    assert conn.execute(
        "SELECT revoked FROM otp_pair_routes WHERE sync_device_id = ?",
        (SYNC_DEVICE,),
    ).fetchone()[0] == 1
    assert store.values == {}

    revoker.fail = False
    assert authority.revoke(SYNC_DEVICE) is True
    assert revoker.sessions == [SESSION]
    assert conn.execute("SELECT 1 FROM otp_pair_routes").fetchone() is None


def test_python_credential_mutex_name_matches_native_contract():
    target = f"{otp_pairing.OTP_CREDENTIAL_TARGET_PREFIX}{SESSION}"
    assert otp_pairing._credential_mutex_name(target) == (
        f"Local\\ClipVaultOtpCredentialV1-{SESSION}"
    )


class _FakeRevokeKernel:
    def __init__(self):
        self.handle = object()
        self.request = None
        self.closed = []
        self.verified = []

    def current_session_id(self):
        return 42

    def connect(self, pipe_name, deadline, cancel_requested):
        assert pipe_name == (
            r"\\.\pipe\ClipVaultOtpBrokerV1-42-revoke_test"
        )
        assert deadline == 10.25
        assert cancel_requested.is_set() is False
        return self.handle

    def write_frame(self, handle, request, deadline):
        assert handle is self.handle
        assert deadline == 10.25
        self.request = bytearray(request)

    def verify_server(self, handle, **identity):
        assert handle is self.handle
        self.verified.append(identity)
        return True

    def read_frame(self, handle, deadline):
        assert handle is self.handle
        assert deadline == 10.25
        return bytearray(
            b"CVOB" + bytes((1, 128, 0, 0, 1)) + bytes(17)
        )

    def cancel_and_close(self, handle):
        self.closed.append(handle)


def test_broker_revoke_control_frame_is_bounded_and_epoch_exact(monkeypatch):
    monkeypatch.delenv("CLIPVAULT_INSECURE_TEST_PIPE_TRUST", raising=False)
    monkeypatch.delenv(
        "CLIPVAULT_INSECURE_DEVELOPMENT_PIPE_TRUST",
        raising=False,
    )
    kernel = _FakeRevokeKernel()
    port = otp_pairing.WindowsNamedPipeOtpBrokerRevocationPort(
        enabled=True,
        test_namespace="revoke_test",
        _kernel=kernel,
        _test_trust_paths=("desktop-id", "broker-id"),
        _monotonic=lambda: 10.0,
    )
    port.revoke(SESSION)
    assert kernel.request == bytearray(
        b"CVOB" + bytes((1, 6, 0, 0)) + uuid.UUID(SESSION).bytes
    )
    assert kernel.closed == [kernel.handle]
    assert kernel.verified == [
        {
            "expected_broker_path": "broker-id",
            "expected_desktop_path": "desktop-id",
            "allow_unsigned": False,
        }
    ]


def test_api_unpair_deletes_otp_route_and_credential_before_legacy_peer(conn, tmp_path):
    _peer(conn)
    store = _FakeCredentialStore()
    authority = _authority(conn, store)
    authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})
    api = Api(
        ClipVaultService(
            conn, _cfg(tmp_path, pairing_enabled=True, broker_enabled=True)
        ),
        otp_pairing_authority=authority,
    )

    assert api.unpair(SYNC_DEVICE) == (
        200,
        {"device_id": SYNC_DEVICE, "unpaired": True},
    )
    assert PeersRepo(conn).get(SYNC_DEVICE) is None
    assert conn.execute("SELECT 1 FROM otp_pair_routes").fetchone() is None
    assert store.values == {}


def test_api_unpair_revokes_bearer_before_retryable_otp_cleanup(conn, tmp_path):
    _peer(conn)
    store = _FakeCredentialStore()
    authority = _authority(conn, store)
    authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})
    store.fail_delete = True
    api = Api(
        ClipVaultService(
            conn, _cfg(tmp_path, pairing_enabled=True, broker_enabled=True)
        ),
        otp_pairing_authority=authority,
    )

    status, body = api.unpair(SYNC_DEVICE)

    assert status == 202
    assert body == {
        "device_id": SYNC_DEVICE,
        "unpaired": True,
        "cleanup_pending": True,
        "cleanup_code": "otp_pair_revoke_failed",
    }
    assert api.auth_ok(TOKEN) is False
    assert PeersRepo(conn).get(SYNC_DEVICE) is None
    assert PeersRepo(conn).get_for_cleanup(SYNC_DEVICE)["revoked"] is True
    assert api.list_peers()[1]["peers"][0]["cleanup_pending"] is True
    assert conn.execute(
        "SELECT revoked FROM otp_pair_routes WHERE sync_device_id = ?",
        (SYNC_DEVICE,),
    ).fetchone()[0] == 1

    store.fail_delete = False
    assert api.unpair(SYNC_DEVICE) == (
        200,
        {"device_id": SYNC_DEVICE, "unpaired": True},
    )
    assert PeersRepo(conn).get_for_cleanup(SYNC_DEVICE) is None
    assert conn.execute("SELECT 1 FROM otp_pair_routes").fetchone() is None


def test_pending_unpair_blocks_same_device_repair_and_preserves_code(conn, tmp_path):
    _peer(conn)
    store = _FakeCredentialStore()
    authority = _authority(conn, store)
    authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})
    store.fail_delete = True
    api = Api(
        ClipVaultService(
            conn, _cfg(tmp_path, pairing_enabled=True, broker_enabled=True)
        ),
        otp_pairing_authority=authority,
    )
    assert api.unpair(SYNC_DEVICE)[0] == 202
    code = api.pairing.mint_code()

    status, body = api.pair({
        "code": code,
        "device_id": SYNC_DEVICE,
        "device_name": "replacement phone",
    })

    assert status == 409
    assert body["error"]["code"] == "peer_revocation_pending"
    store.fail_delete = False
    assert api.unpair(SYNC_DEVICE)[0] == 200
    repaired_status, repaired = api.pair({
        "code": code,
        "device_id": SYNC_DEVICE,
        "device_name": "replacement phone",
    })
    assert repaired_status == 200
    assert api.auth_ok(repaired["token"]) is True


def test_peer_finalize_fk_failure_rolls_back_for_cleanup_retry(conn):
    _peer(conn)
    authority = _authority(conn)
    authority.pair(SYNC_DEVICE, {"sender_device_id": SENDER})
    peers = PeersRepo(conn)
    assert peers.revoke(SYNC_DEVICE) is True

    with pytest.raises(sqlite3.IntegrityError):
        peers.finalize_unpair(SYNC_DEVICE)

    assert conn.in_transaction is False
    assert peers.get(SYNC_DEVICE) is None
    assert peers.get_for_cleanup(SYNC_DEVICE)["revoked"] is True
    assert authority.revoke(SYNC_DEVICE) is True
    assert peers.finalize_unpair(SYNC_DEVICE) is True


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        ("127.0.0.1", True),
        ("127.255.10.20", True),
        ("::1", True),
        # A Tailnet-looking remote address alone is not transport proof. The
        # listener bind and accepted socket target are required as well; that
        # full matrix is covered by test_otp_ingress.py.
        ("100.64.0.1", False),
        ("100.127.255.254", False),
        ("fd7a:115c:a1e0::1", False),
        ("192.168.1.20", False),
        ("10.0.0.2", False),
        ("100.128.0.1", False),
        ("fd7a:115c:a1e1::1", False),
        ("not-an-address", False),
    ],
)
def test_otp_pair_remote_address_alone_proves_only_loopback(address, allowed):
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
        ClipVaultService(
            conn, _cfg(tmp_path, pairing_enabled=True, broker_enabled=True)
        ),
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
        ClipVaultService(
            conn, _cfg(tmp_path, pairing_enabled=True, broker_enabled=True)
        ),
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
        lambda _address, **_transport: False,
    )
    api = Api(
        ClipVaultService(
            conn, _cfg(tmp_path, pairing_enabled=True, broker_enabled=True)
        ),
        otp_pairing_authority=ForbiddenAuthority(),
    )
    status, headers, payload = _serve_one(
        api,
        lambda port: _post_pair(port, token="wrong"),
    )
    assert status == 403
    assert headers["Cache-Control"] == "no-store"
    assert payload["error"]["code"] == "otp_pair_transport_rejected"


def test_http_pair_rejects_duplicate_sender_member(conn, tmp_path):
    _peer(conn)
    authority = _authority(conn)
    api = Api(
        ClipVaultService(
            conn, _cfg(tmp_path, pairing_enabled=True, broker_enabled=True)
        ),
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
    runtime = ClipVaultRuntime(
        _cfg(tmp_path, pairing_enabled=True, broker_enabled=True)
    )
    identity = runtime.adapters.otp_pair_identity_port_factory(conn)
    authority = runtime.adapters.otp_pairing_authority_factory(conn)
    assert isinstance(identity, otp_pairing.SqliteOtpPairIdentityPort)
    assert isinstance(authority, otp_pairing.SqliteOtpPairingAuthority)
    assert isinstance(
        authority._broker_revocation_port,
        otp_pairing.WindowsNamedPipeOtpBrokerRevocationPort,
    )
    assert authority._broker_revocation_port._enabled is True
    identity.close()
    authority.close()



def test_schema_nine_upgrades_to_otp_route_and_peer_revocation_metadata(tmp_path):
    v9 = tmp_path / "migrations-v9"
    v9.mkdir()
    for script in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
        if int(script.name[:4]) <= 9:
            shutil.copy2(script, v9 / script.name)
    connection = db.connect(tmp_path / "legacy.sqlite3")
    try:
        assert db.migrate(connection, v9, expected_latest=9) == 9
        # Populate through the schema-nine contract. The current PeersRepo
        # deliberately references the v11 revoked column and cannot be used
        # before the migration under test has added it.
        connection.execute(
            "INSERT INTO sync_peers"
            "(device_id, device_name, token_hash, my_acked_seq, peer_cursor, "
            "paired_at, last_seen_at) VALUES (?,?,?,?,?,?,?)",
            (
                SYNC_DEVICE,
                "phone",
                hash_token(TOKEN),
                0,
                0,
                "2026-08-01T00:00:00Z",
                None,
            ),
        )
        connection.commit()
        assert db.migrate(connection) == 11
        assert PeersRepo(connection).get(SYNC_DEVICE) is not None
        assert connection.execute(
            "SELECT 1 FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'otp_pair_routes'"
        ).fetchone() is not None
        columns = {
            row["name"]: row
            for row in connection.execute(
                "PRAGMA table_info('sync_peers')"
            ).fetchall()
        }
        assert columns["revoked"]["notnull"] == 1
        assert columns["revoked"]["dflt_value"] == "0"
        assert connection.execute(
            "SELECT revoked FROM sync_peers WHERE device_id = ?",
            (SYNC_DEVICE,),
        ).fetchone()[0] == 0
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
