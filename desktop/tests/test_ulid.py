from clipvault.core import ulid

CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_format():
    value = ulid.new()
    assert len(value) == 26
    assert set(value) <= CROCKFORD


def test_zero():
    assert ulid.new(0, b"\x00" * 10) == "0" * 26


def test_time_ordering():
    rnd = b"\xff" * 10
    assert ulid.new(1_000, rnd) < ulid.new(2_000, rnd)
    assert ulid.new(1_717_000_000_000, b"\x00" * 10) < ulid.new(1_717_000_000_001, b"\xff" * 10)
