"""Provider-key detectors added in v1.7.

The inputs are assembled by concatenation so no contiguous secret-shaped literal
is committed — GitHub push protection (correctly) blocks those even in test
fixtures, which is also why these cases can't live in the shared JSON vectors.
This file mirrors the Kotlin SecretGuardProviderTest so both platforms stay in
lockstep on the new rules.
"""

from clipvault.core import secret_guard as sg

_A = "A"


def _reasons(content: str) -> list[str]:
    v = sg.scan(content)
    assert v.is_secret and v.level == "hard", content
    return v.reasons


def test_provider_key_patterns():
    assert _reasons("sk_" + "live_" + _A * 20) == ["SG-STRIPE"]
    assert _reasons("rk_" + "test_" + _A * 20) == ["SG-STRIPE"]
    assert _reasons("glpat-" + _A * 20) == ["SG-GITLAB"]
    assert _reasons("SG." + _A * 22 + "." + _A * 43) == ["SG-SENDGRID"]
    assert _reasons("npm_" + _A * 36) == ["SG-NPM"]
    assert _reasons("dop_" + "v1_" + "a" * 64) == ["SG-DIGITALOCEAN"]
    assert _reasons("https://hooks." + "slack.com/services/" + _A * 24) == ["SG-SLACK-URL"]


def test_provider_patterns_do_not_overmatch():
    assert sg.scan("npm install --save lodash").is_secret is False
    assert sg.scan("glpat tutorial notes for the team").is_secret is False
    assert sg.scan("the sky is live and well today").is_secret is False
