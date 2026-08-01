from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from clipvault.core.models import Clip
from clipvault.runtime.snapshot import RuntimeSnapshotPublisher, SnapshotItem
from clipvault.runtime.snapshot_protocol import (
    SnapshotProtocolError,
    decode_client_hello,
    decode_snapshot_request,
    decode_snapshot_response,
    encode_client_hello,
    encode_snapshot_request,
    encode_snapshot_response,
    frame,
    parse_frame,
)
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.memory_repo import MemoryRepo

ROOT = Path(__file__).resolve().parents[2]
EPOCH = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    return conn


def _clip(item_id: str, content: str, *, times_seen: int = 3) -> Clip:
    return Clip(
        id=item_id,
        content=content,
        content_hash=(item_id * 64)[:64],
        content_type="text",
        source_device="desktop",
        created_at="2026-08-01T00:00:00Z",
        last_seen_at="2026-08-01T00:00:00Z",
        times_seen=times_seen,
    )


def test_golden_assertions_are_all_exercised_by_snapshot_suite():
    vector = json.loads(
        (ROOT / "contracts/vectors/runtime_snapshot_v1.json").read_text(encoding="utf-8")
    )
    assert {row["id"] for row in vector["assertions"]} == {
        f"SNAP-V00{i}" for i in range(1, 9)
    }


def test_publisher_returns_bounded_safe_full_values_and_scoped_ids():
    conn = _conn()
    MemoryRepo(conn).upsert("phrase", "daily safe phrase", label="Greeting", pinned=True)
    ClipsRepo(conn).insert(_clip("A", "recent safe clipboard"))
    ids = iter(["snapshot-id-1", "snapshot-id-2", "snapshot-id-3"])
    publisher = RuntimeSnapshotPublisher(
        conn,
        publisher_epoch=EPOCH,
        now_ms=lambda: 1_775_174_400_000,
        candidate_id_factory=lambda: next(ids),
    )

    first = publisher.publish(request_id=7, limit=2)
    second = publisher.publish(request_id=8, limit=1)

    assert first.publisher_epoch == EPOCH
    assert first.generation == 1
    assert first.expires_at_ms == 1_775_174_430_000
    assert {item.source for item in first.items} == {1, 2}
    assert {item.text for item in first.items} == {
        "daily safe phrase",
        "recent safe clipboard",
    }
    assert second.generation == 2
    assert {item.candidate_id for item in first.items}.isdisjoint(
        {item.candidate_id for item in second.items}
    )
    assert len(encode_snapshot_response(first)) <= 65_536


def test_publisher_rechecks_complete_values_and_never_truncates():
    conn = _conn()
    clips = ClipsRepo(conn)
    clips.insert(_clip("B", "safe prefix " + "sk-proj-" + "A1" * 20))
    clips.insert(_clip("C", "x" * 16_385))
    publisher = RuntimeSnapshotPublisher(
        conn,
        publisher_epoch=EPOCH,
        now_ms=lambda: 1_775_174_400_000,
        candidate_id_factory=lambda: str(uuid.uuid4()),
    )

    snapshot = publisher.publish(request_id=1, limit=8)

    assert snapshot.items == ()
    assert clips.get("B").is_secret is True
    assert clips.get("C").content == "x" * 16_385


def test_protocol_round_trip_and_framing():
    assert decode_client_hello(encode_client_hello(CLIENT)).client_instance == CLIENT
    assert decode_snapshot_request(encode_snapshot_request(9, 8)).limit == 8
    snapshot = RuntimeSnapshotPublisher(
        _conn(), publisher_epoch=EPOCH, now_ms=lambda: 1_000
    ).publish(request_id=9, limit=8)
    payload = encode_snapshot_response(snapshot)
    assert decode_snapshot_response(payload, now_ms=1_000) == snapshot
    assert parse_frame(frame(payload)) == payload


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p + b"\x30\x01",  # unknown field 6
        lambda p: p + b"\x08\x09",  # duplicate request_id
        lambda p: p[:-1],
    ],
)
def test_protocol_rejects_unknown_duplicate_and_truncated_fields(mutate):
    payload = encode_snapshot_request(1, 1)
    with pytest.raises(SnapshotProtocolError):
        decode_snapshot_request(mutate(payload))


def test_protocol_rejects_duplicate_item_id_invalid_utf8_and_generation_rollback_policy_input():
    from clipvault.runtime.snapshot import RuntimeSnapshot

    duplicate = RuntimeSnapshot(
        request_id=1,
        publisher_epoch=EPOCH,
        generation=1,
        expires_at_ms=31_000,
        items=(
            SnapshotItem("same", 1, "Memory", "one"),
            SnapshotItem("same", 2, "Clipboard", "two"),
        ),
    )
    with pytest.raises(SnapshotProtocolError, match="duplicate"):
        encode_snapshot_response(duplicate)

    # Item text field with one invalid UTF-8 byte.
    item = b"\x0a\x02id\x10\x01\x1a\x00\x22\x01\xff"
    raw = (
        b"\x08\x01"
        + b"\x12\x24"
        + EPOCH.encode()
        + b"\x18\x01\x20\x88\xf2\x01"
        + b"\x2a"
        + bytes([len(item)])
        + item
    )
    with pytest.raises(SnapshotProtocolError, match="UTF-8"):
        decode_snapshot_response(raw)


def test_protocol_rejects_noncanonical_uuid_and_bad_frame_bounds():
    with pytest.raises(SnapshotProtocolError):
        encode_client_hello("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")
    with pytest.raises(SnapshotProtocolError):
        parse_frame(b"\x00\x00\x00\x02x")
