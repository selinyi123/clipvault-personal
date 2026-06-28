"""SG-1.2: embedded high-entropy credential detection (v1.7 deepening).

The whole-content entropy rule (SG-1, §4.2) only fires when the entire clip is a
single token, so a high-entropy key embedded in surrounding prose was missed.
SG-1.2 adds a per-token scan, gated stricter (token must contain both letters and
digits) so ordinary long words are not flagged. Mirrors the Kotlin
SecretGuardEmbeddedTest so both platforms stay in lockstep.
"""

from clipvault.core import secret_guard as sg

# Random alphanumeric (not any provider format); high Shannon entropy.
_TOKEN = "q7VxT2mKp9LzR4wNb8YcJ3hFs6Dg"


def test_embedded_high_entropy_token_is_caught():
    v = sg.scan(f"deploy key is {_TOKEN} please rotate it")
    assert v.is_secret is True
    assert v.level == "suspect"
    assert v.reasons == ["SG-ENTROPY"]


def test_token_alone_still_caught_by_whole_content_rule():
    # SG-1.2 must not regress the original single-token path.
    v = sg.scan(_TOKEN)
    assert v.is_secret is True and v.reasons == ["SG-ENTROPY"]


def test_prose_long_word_not_flagged():
    # No digit -> fails the credential-shape gate; ordinary prose stays clean.
    assert sg.scan("antidisestablishmentarianism is a very long word").is_secret is False


def test_embedded_hash_and_uuid_excluded():
    assert sg.scan("commit 3f786850e387550fdab836ed7e6dc881de23001b landed").is_secret is False
    assert sg.scan("ticket 550e8400-e29b-41d4-a716-446655440000 closed").is_secret is False


def test_embedded_all_digits_not_flagged():
    # No letter -> fails the credential-shape gate.
    assert sg.scan("order 123456789012345678901234567890 shipped").is_secret is False
