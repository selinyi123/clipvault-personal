"""Strict protobuf-wire subset for Runtime Snapshot V1.

Message kinds are determined by the connection state: ClientHello,
HostHello, SnapshotRequest, then SnapshotResponse.  Unknown fields and duplicate
singleton fields are rejected so a parser differential cannot widen the local
IME trust boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from clipvault.runtime.snapshot import (
    MAX_CANDIDATE_ID_BYTES,
    MAX_ITEMS,
    MAX_LABEL_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_SNAPSHOT_LIFETIME_MS,
    MAX_TEXT_BYTES,
    PROTOCOL_VERSION,
    RuntimeSnapshot,
    SnapshotItem,
)


class SnapshotProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ClientHello:
    client_instance: str


@dataclass(frozen=True)
class SnapshotRequest:
    request_id: int
    limit: int


def encode_client_hello(client_instance: str) -> bytes:
    canonical = _canonical_uuid(client_instance)
    return _uint_field(1, PROTOCOL_VERSION) + _bytes_field(2, canonical.encode())


def decode_client_hello(payload: bytes) -> ClientHello:
    fields = _decode_fields(payload, allowed={1: 0, 2: 2})
    _require_exact(fields, {1, 2})
    if fields[1][0] != PROTOCOL_VERSION:
        raise SnapshotProtocolError("unsupported protocol version")
    return ClientHello(_decode_uuid(fields[2][0]))


def encode_host_hello(publisher_epoch: str) -> bytes:
    canonical = _canonical_uuid(publisher_epoch)
    return _uint_field(1, PROTOCOL_VERSION) + _bytes_field(2, canonical.encode())


def decode_host_hello(payload: bytes) -> str:
    fields = _decode_fields(payload, allowed={1: 0, 2: 2})
    _require_exact(fields, {1, 2})
    if fields[1][0] != PROTOCOL_VERSION:
        raise SnapshotProtocolError("unsupported protocol version")
    return _decode_uuid(fields[2][0])


def encode_snapshot_request(request_id: int, limit: int) -> bytes:
    if not 1 <= request_id <= 2**63 - 1 or not 1 <= limit <= MAX_ITEMS:
        raise SnapshotProtocolError("request bounds")
    return _uint_field(1, request_id) + _uint_field(2, limit)


def decode_snapshot_request(payload: bytes) -> SnapshotRequest:
    fields = _decode_fields(payload, allowed={1: 0, 2: 0})
    _require_exact(fields, {1, 2})
    request_id = fields[1][0]
    limit = fields[2][0]
    if not 1 <= request_id <= 2**63 - 1 or not 1 <= limit <= MAX_ITEMS:
        raise SnapshotProtocolError("request bounds")
    return SnapshotRequest(request_id, limit)


def encode_snapshot_response(snapshot: RuntimeSnapshot) -> bytes:
    epoch = _canonical_uuid(snapshot.publisher_epoch).encode()
    if not 1 <= snapshot.request_id <= 2**63 - 1:
        raise SnapshotProtocolError("response request id")
    if not 1 <= snapshot.generation <= 2**63 - 1:
        raise SnapshotProtocolError("response generation")
    if snapshot.expires_at_ms <= 0:
        raise SnapshotProtocolError("response expiry")
    if len(snapshot.items) > MAX_ITEMS:
        raise SnapshotProtocolError("too many items")
    seen: set[str] = set()
    payload = bytearray()
    payload += _uint_field(1, snapshot.request_id)
    payload += _bytes_field(2, epoch)
    payload += _uint_field(3, snapshot.generation)
    payload += _uint_field(4, snapshot.expires_at_ms)
    for item in snapshot.items:
        _validate_item(item, seen)
        encoded = (
            _bytes_field(1, item.candidate_id.encode("utf-8", "strict"))
            + _uint_field(2, item.source)
            + _bytes_field(3, item.label.encode("utf-8", "strict"))
            + _bytes_field(4, item.text.encode("utf-8", "strict"))
        )
        payload += _bytes_field(5, encoded)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise SnapshotProtocolError("response too large")
    return bytes(payload)


def decode_snapshot_response(
    payload: bytes,
    *,
    now_ms: int | None = None,
) -> RuntimeSnapshot:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise SnapshotProtocolError("response too large")
    fields = _decode_fields(payload, allowed={1: 0, 2: 2, 3: 0, 4: 0, 5: 2}, repeated={5})
    _require_exact(fields, {1, 2, 3, 4})
    request_id = fields[1][0]
    epoch = _decode_uuid(fields[2][0])
    generation = fields[3][0]
    expires_at_ms = fields[4][0]
    if not 1 <= request_id <= 2**63 - 1 or not 1 <= generation <= 2**63 - 1:
        raise SnapshotProtocolError("response integer bounds")
    if expires_at_ms <= 0:
        raise SnapshotProtocolError("response expiry")
    if now_ms is not None and not now_ms < expires_at_ms <= now_ms + MAX_SNAPSHOT_LIFETIME_MS:
        raise SnapshotProtocolError("response lifetime")
    raw_items = fields.get(5, [])
    if len(raw_items) > MAX_ITEMS:
        raise SnapshotProtocolError("too many items")
    items: list[SnapshotItem] = []
    seen: set[str] = set()
    for raw in raw_items:
        item_fields = _decode_fields(raw, allowed={1: 2, 2: 0, 3: 2, 4: 2})
        _require_exact(item_fields, {1, 2, 3, 4})
        try:
            item = SnapshotItem(
                candidate_id=item_fields[1][0].decode("utf-8", "strict"),
                source=item_fields[2][0],
                label=item_fields[3][0].decode("utf-8", "strict"),
                text=item_fields[4][0].decode("utf-8", "strict"),
            )
        except UnicodeError as exc:
            raise SnapshotProtocolError("invalid UTF-8") from exc
        _validate_item(item, seen)
        items.append(item)
    return RuntimeSnapshot(request_id, epoch, generation, expires_at_ms, tuple(items))


def frame(payload: bytes) -> bytes:
    if not 1 <= len(payload) <= MAX_RESPONSE_BYTES:
        raise SnapshotProtocolError("frame bounds")
    return len(payload).to_bytes(4, "big") + payload


def parse_frame(frame_bytes: bytes) -> bytes:
    if len(frame_bytes) < 4:
        raise SnapshotProtocolError("short frame")
    length = int.from_bytes(frame_bytes[:4], "big")
    if not 1 <= length <= MAX_RESPONSE_BYTES or len(frame_bytes) != length + 4:
        raise SnapshotProtocolError("frame length")
    return frame_bytes[4:]


def _validate_item(item: SnapshotItem, seen: set[str]) -> None:
    candidate_id = _encode_bounded(item.candidate_id, 1, MAX_CANDIDATE_ID_BYTES)
    _encode_bounded(item.label, 0, MAX_LABEL_BYTES)
    _encode_bounded(item.text, 1, MAX_TEXT_BYTES)
    if item.source not in (1, 2):
        raise SnapshotProtocolError("invalid item source")
    if item.candidate_id in seen:
        raise SnapshotProtocolError("duplicate candidate id")
    if not candidate_id:
        raise SnapshotProtocolError("empty candidate id")
    seen.add(item.candidate_id)


def _encode_bounded(value: str, minimum: int, maximum: int) -> bytes:
    if not isinstance(value, str):
        raise SnapshotProtocolError("field is not text")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise SnapshotProtocolError("invalid UTF-8") from exc
    if not minimum <= len(encoded) <= maximum:
        raise SnapshotProtocolError("text bounds")
    return encoded


def _canonical_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise SnapshotProtocolError("invalid UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise SnapshotProtocolError("non-canonical UUID")
    return canonical


def _decode_uuid(value: bytes) -> str:
    try:
        decoded = value.decode("ascii", "strict")
    except UnicodeError as exc:
        raise SnapshotProtocolError("invalid UUID encoding") from exc
    return _canonical_uuid(decoded)


def _require_exact(fields: dict[int, list], required: set[int]) -> None:
    if not required.issubset(fields):
        raise SnapshotProtocolError("missing field")


def _uint_field(number: int, value: int) -> bytes:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SnapshotProtocolError("invalid uint")
    return _varint((number << 3) | 0) + _varint(value)


def _bytes_field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _read_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(payload):
            raise SnapshotProtocolError("truncated varint")
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if _varint(value) != payload[offset - ((shift // 7) + 1) : offset]:
                raise SnapshotProtocolError("non-canonical varint")
            return value, offset
    raise SnapshotProtocolError("varint overflow")


def _decode_fields(
    payload: bytes,
    *,
    allowed: dict[int, int],
    repeated: set[int] | None = None,
) -> dict[int, list]:
    if not isinstance(payload, bytes):
        raise SnapshotProtocolError("payload type")
    repeated = repeated or set()
    fields: dict[int, list] = {}
    offset = 0
    while offset < len(payload):
        key, offset = _read_varint(payload, offset)
        number, wire = key >> 3, key & 7
        if number not in allowed or allowed[number] != wire:
            raise SnapshotProtocolError("unknown field or wire type")
        if number in fields and number not in repeated:
            raise SnapshotProtocolError("duplicate singleton field")
        if wire == 0:
            value, offset = _read_varint(payload, offset)
        elif wire == 2:
            length, offset = _read_varint(payload, offset)
            if length > len(payload) - offset:
                raise SnapshotProtocolError("truncated bytes")
            value = payload[offset : offset + length]
            offset += length
        else:  # guarded by allowed, retained as a fail-closed assertion
            raise SnapshotProtocolError("unsupported wire type")
        fields.setdefault(number, []).append(value)
    return fields
