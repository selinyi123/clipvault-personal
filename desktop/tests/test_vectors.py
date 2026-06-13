"""VEC-1 conformance: the vectors are the single arbiter for both platforms
(A4, A5). The Kotlin port must pass these same files.
"""

import json

from clipvault.core import classifier, normalize, secret_guard


def _load(vectors_dir, name):
    return json.loads((vectors_dir / name).read_text(encoding="utf-8"))


def test_normalization_vectors(vectors_dir):
    cases = _load(vectors_dir, "normalization.json")
    assert len(cases) >= 20
    for case in cases:
        got = normalize.normalize(case["raw"])
        assert got == case["normalized"], f"normalize mismatch: {case['raw']!r}"
        assert normalize.content_hash(got) == case["hash"], f"hash mismatch: {case['raw']!r}"


def test_classifier_vectors(vectors_dir):
    cases = _load(vectors_dir, "classifier.json")
    per_type: dict[str, int] = {}
    for case in cases:
        got = classifier.classify(case["content"])
        assert got == case["expected_type"], (
            f"classify({case['content']!r}) = {got}, expected {case['expected_type']}"
        )
        per_type[case["expected_type"]] = per_type.get(case["expected_type"], 0) + 1
    for content_type in ("text", "url", "path", "command", "code", "error_log", "prompt"):
        assert per_type.get(content_type, 0) >= 5, f"need >=5 cases for {content_type}"


def test_secret_guard_vectors(vectors_dir):
    cases = _load(vectors_dir, "secret_guard.json")
    negatives = 0
    for case in cases:
        v = secret_guard.scan(case["content"])
        assert v.is_secret == case["is_secret"], f"is_secret mismatch: {case['content']!r}"
        assert v.level == case["level"], f"level mismatch: {case['content']!r}"
        assert sorted(v.reasons) == sorted(case["reasons"]), (
            f"reasons mismatch for {case['content']!r}: {v.reasons} != {case['reasons']}"
        )
        if not case["is_secret"]:
            negatives += 1
    assert negatives >= 10
