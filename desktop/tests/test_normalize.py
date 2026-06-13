"""A9: rejection behaviour (the transform itself is covered by vectors)."""

from clipvault.core import normalize


def test_empty_rejected():
    assert normalize.reject_reason("") == normalize.REJECT_EMPTY


def test_whitespace_only_rejected():
    assert normalize.normalize("  \n\t  ") == ""
    assert normalize.reject_reason(normalize.normalize("  \n\t  ")) == normalize.REJECT_EMPTY


def test_too_large_rejected():
    assert normalize.reject_reason("x" * 11, max_bytes=10) == normalize.REJECT_TOO_LARGE


def test_too_large_counts_utf8_bytes():
    # 4 Chinese chars = 12 UTF-8 bytes
    assert normalize.reject_reason("你好世界", max_bytes=10) == normalize.REJECT_TOO_LARGE
    assert normalize.reject_reason("你好世界", max_bytes=12) is None


def test_known_hash():
    assert (
        normalize.content_hash("hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
