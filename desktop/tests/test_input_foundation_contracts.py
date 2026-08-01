"""Machine-readable gates for the isolated v2 input-foundation PoCs."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import struct
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VECTOR_PATH = ROOT / "contracts" / "vectors" / "input_foundation_v2.json"
ENGINE_ASSERTION_PATH = (
    ROOT / "contracts" / "vectors" / "engine_protocol_v2_assertions.tsv"
)
OTP_AEAD_PATH = ROOT / "contracts" / "vectors" / "otp_aead_v1.json"
OTP_WIRE_SCHEMA_PATH = ROOT / "contracts" / "otp_relay_wire_v1.schema.json"


def _load_vectors() -> dict:
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


def _case_ids(suite: dict) -> set[str]:
    return {case["id"] for case in suite["cases"]}


def test_input_foundation_vectors_are_synthetic_offline_and_non_persistent():
    vectors = _load_vectors()

    assert vectors["format_version"] == 1
    assert vectors["source"] == "project-authored synthetic semantics"
    assert vectors["contains_personal_data"] is False
    assert vectors["typed_text_persistence_allowed"] is False
    assert vectors["network_allowed"] is False


def test_engine_protocol_v2_semantic_coverage_is_complete_and_cross_platform():
    suite = _load_vectors()["suites"]["engine_protocol_v2"]

    assert suite["protocol_version"] == 2
    assert suite["required_platforms"] == ["android", "windows"]
    assert _case_ids(suite) == {f"ENG2-V{index:03d}" for index in range(1, 9)}
    assert len(suite["cases"]) == len(_case_ids(suite))

    for case in suite["cases"]:
        assert case["platforms"] == ["android", "windows"]
        assert case["name"].strip()
        assert len(case["assertions"]) >= 3
        assert len(case["assertions"]) == len(set(case["assertions"]))


def test_engine_assertion_ids_are_stable_and_match_the_canonical_json():
    rows = []
    for raw in ENGINE_ASSERTION_PATH.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#") or raw == "semantic_id\tassertion_id\tassertion":
            continue
        rows.append(raw.split("\t"))

    cases = {
        case["id"]: case["assertions"]
        for case in _load_vectors()["suites"]["engine_protocol_v2"]["cases"]
    }
    observed: dict[str, list[tuple[str, str]]] = {}
    for semantic_id, assertion_id, assertion in rows:
        observed.setdefault(semantic_id, []).append((assertion_id, assertion))

    assert list(observed) == [f"ENG2-V{index:03d}" for index in range(1, 9)]
    for semantic_id, assertions in cases.items():
        assert [text for _, text in observed[semantic_id]] == assertions
        assert [assertion_id for assertion_id, _ in observed[semantic_id]] == [
            f"{semantic_id}-A{index:02d}"
            for index in range(1, len(assertions) + 1)
        ]


def test_otp_relay_semantic_coverage_is_complete_and_local_only():
    suite = _load_vectors()["suites"]["otp_relay_v1"]

    assert suite["protocol_version"] == 1
    assert suite["required_platforms"] == ["core"]
    assert _case_ids(suite) == {f"OTP-V{index:03d}" for index in range(1, 11)}
    assert len(suite["cases"]) == len(_case_ids(suite))

    for case in suite["cases"]:
        assert case["platforms"] == ["core"]
        assert case["name"].strip()
        assert len(case["assertions"]) >= 3
        assert len(case["assertions"]) == len(set(case["assertions"]))

    cases = {case["id"]: case for case in suite["cases"]}
    v004 = " ".join(cases["OTP-V004"]["assertions"])
    assert "revalidated at secret use" in v004
    assert "before lease creation" in v004
    v009 = " ".join(cases["OTP-V009"]["assertions"])
    assert "canonical content-free identifiers" in v009


def test_contract_docs_bind_the_machine_readable_vector_ranges():
    engine_contract = (ROOT / "docs" / "CONTRACTS_INPUT_ENGINE_V2.md").read_text(
        encoding="utf-8"
    )
    otp_contract = (ROOT / "docs" / "CONTRACTS_OTP_RELAY.md").read_text(
        encoding="utf-8"
    )
    gates = (ROOT / "docs" / "GATES.md").read_text(encoding="utf-8")
    phase = (ROOT / "docs" / "NEXT_PHASE_V2_INPUT_FOUNDATION.md").read_text(
        encoding="utf-8"
    )
    windows_adr = (ROOT / "docs" / "ADR" / "0015-windows-tsf-stack.md").read_text(
        encoding="utf-8"
    )

    assert "contracts/vectors/input_foundation_v2.json" in engine_contract
    assert "contracts/vectors/engine_protocol_v2_assertions.tsv" in engine_contract
    assert "ENG2-V001" in engine_contract and "ENG2-V008" in engine_contract
    assert (
        "StartSession(host_epoch, session_id, request_seq=1, context) -> EngineState"
        in engine_contract
    )
    assert "EndSession(session_id, request_seq)" in engine_contract
    assert "EndSession(session_id, request_seq, expected_revision)" not in engine_contract
    assert "four-byte unsigned big-endian" in windows_adr
    assert "1–1,048,576 protobuf bytes" in windows_adr
    assert "ClientHello -> HostHello" in windows_adr
    assert "contracts/vectors/input_foundation_v2.json" in otp_contract
    assert "contracts/vectors/otp_aead_v1.json" in otp_contract
    assert "OTP-V001" in otp_contract and "OTP-V010" in otp_contract
    for document in (gates, phase):
        assert "contracts/vectors/input_foundation_v2.json" in document
        assert "ENG2-V001..ENG2-V008" in document
        assert "OTP-V001..OTP-V010" in document


def test_semantic_assertions_do_not_define_a_plaintext_transport_or_storage_path():
    vectors = _load_vectors()
    serialized = json.dumps(vectors, ensure_ascii=False).casefold()

    forbidden_patterns = (
        r"write otp to (?:a )?database",
        r"copy otp to (?:the )?clipboard",
        r"plaintext cross-device transport",
        r"persist typed text",
        r"log typed text",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, serialized) is None


def test_otp_aead_v1_vector_has_canonical_cross_platform_kdf_and_aad_bytes():
    vector = json.loads(OTP_AEAD_PATH.read_text(encoding="utf-8"))
    inputs = vector["inputs"]
    expected = vector["derived"]

    assert vector["contract"] == "OTP-3A"
    assert vector["algorithm"] == "AES-256-GCM"
    assert len(bytes.fromhex(inputs["nonce_hex"])) == 12
    assert len(bytes.fromhex(expected["authentication_tag_hex"])) == 16

    session = uuid.UUID(inputs["session_epoch"])
    event = uuid.UUID(inputs["event_id"])
    sender = uuid.UUID(inputs["sender_device"].removeprefix("device:"))
    target = uuid.UUID(inputs["target_device"].removeprefix("device:"))
    pair_verifier = hashlib.sha256(inputs["pair_secret_utf8"].encode("utf-8")).digest()
    salt = hashlib.sha256(
        b"ClipVault OTP Relay KDF v1\0" + session.bytes
    ).digest()
    prk = hmac.new(salt, pair_verifier, hashlib.sha256).digest()
    info = b"ClipVault OTP Relay key v1\0" + sender.bytes + target.bytes
    key = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
    aad = (
        b"ClipVault OTP Relay AEAD v1\0"
        + bytes([1])
        + session.bytes
        + event.bytes
        + sender.bytes
        + target.bytes
        + struct.pack(
            ">QQQ",
            inputs["sequence"],
            inputs["issued_at_unix_ms"],
            inputs["expires_at_unix_ms"],
        )
    )

    assert pair_verifier.hex() == expected["pair_verifier_sha256_hex"]
    assert salt.hex() == expected["salt_hex"]
    assert prk.hex() == expected["prk_hex"]
    assert info.hex() == expected["info_hex"]
    assert key.hex() == expected["key_hex"]
    assert aad.hex() == expected["aad_hex"]
    assert hashlib.sha256(aad).hexdigest() == expected["aad_sha256_hex"]
    assert inputs["plaintext_ascii"].encode("ascii").hex() == expected["plaintext_hex"]


def test_otp_wire_v1_schema_freezes_the_online_only_envelope_shape():
    schema = json.loads(OTP_WIRE_SCHEMA_PATH.read_text(encoding="utf-8"))
    expected_fields = {
        "version",
        "algorithm",
        "session_epoch",
        "event_id",
        "sender_device_id",
        "target_device_id",
        "sequence",
        "issued_at_ms",
        "expires_at_ms",
        "nonce",
        "ciphertext",
        "authentication_tag",
    }

    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == expected_fields
    assert set(schema["properties"]) == expected_fields
    assert schema["properties"]["version"]["const"] == 1
    assert schema["properties"]["algorithm"]["const"] == "A256GCM"
    assert schema["properties"]["sequence"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": (2**63) - 1,
    }
    assert schema["properties"]["nonce"]["pattern"].endswith("{16}$")
    assert "{6,8}" in schema["properties"]["ciphertext"]["pattern"]
    assert "{10,11}" in schema["properties"]["ciphertext"]["pattern"]
    assert schema["properties"]["authentication_tag"]["pattern"].endswith(
        "{22}$"
    )


def test_daily_use_contract_keeps_current_platform_and_production_path_gates():
    acceptance = (ROOT / "docs" / "V2_DAILY_USE_ACCEPTANCE.md").read_text(
        encoding="utf-8"
    )
    manual_qa_path = ROOT / "docs" / "V2_DAILY_USE_MANUAL_QA.md"
    manual_qa = manual_qa_path.read_text(encoding="utf-8")

    for gate_id in (
        "V2D-A01",
        "V2D-A03",
        "V2D-A09",
        "V2D-A10",
        "V2D-A11",
        "V2D-W01",
        "V2D-W04",
        "V2D-W08",
        "V2D-W09",
        "V2D-O01",
        "V2D-O07",
        "V2D-O08",
    ):
        assert f"`{gate_id}`" in acceptance

    assert "API 36" in acceptance
    assert "30ms" in acceptance
    assert (
        "拥有网络权限的 Runtime APK 物理不含旧 Panel/FullKeyboard manifest、服务类、"
        "输入法 XML 或 Rime payload"
    ) in acceptance
    assert "正式构建入口仍从 `spikes/` 加载生产源码" in acceptance
    assert "V2_DAILY_USE_MANUAL_QA.md" in acceptance
    assert "CONTRACTS_RUNTIME_SNAPSHOT_V1.md" in acceptance
    assert "SNAP-V001..SNAP-V008" in acceptance

    gates = (ROOT / "docs" / "GATES.md").read_text(encoding="utf-8")
    assert "tools/v2_daily_readiness.py --no-fail" in gates
    assert "--automated-only" in gates
    assert "V2_DAILY_USE_MANUAL_QA.md" in gates

    for marker in (
        "candidate_id:",
        "android_ime_sha256:",
        "windows_package_sha256:",
        "Android 15 / 16 KiB",
        "Android 16 / API 36",
        "Android 17",
        "Office 或其他 Win32 x86",
        "7 天日用稳定性",
        "owner_decision: approve | reject",
    ):
        assert marker in manual_qa

    threat_model = (ROOT / "docs" / "THREAT_MODEL_OTP_RELAY.md").read_text(
        encoding="utf-8"
    )
    assert "Android 17 with target API 37" in threat_model
    assert "cross-device SMS transfer" in threat_model
    assert "answer/10208820" in threat_model
    assert "PLAY_SMS_PERMISSION.md" in threat_model

    play_gate = (ROOT / "docs" / "PLAY_SMS_PERMISSION.md").read_text(
        encoding="utf-8"
    )
    assert "IME APK must" in play_gate
    assert "runtime otpSmsRelay" in play_gate
    assert "RECEIVE_SMS only after Owner enables the approved release lane" in play_gate
    assert "Passing repository tests cannot turn this gate" in play_gate


def test_runtime_snapshot_v1_is_bounded_local_and_context_free():
    contract = (ROOT / "docs" / "CONTRACTS_RUNTIME_SNAPSHOT_V1.md").read_text(
        encoding="utf-8"
    )
    vectors = json.loads(
        (ROOT / "contracts" / "vectors" / "runtime_snapshot_v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert vectors["protocol_version"] == 1
    assert vectors["limits"] == {
        "max_items": 8,
        "max_candidate_id_utf8_bytes": 128,
        "max_label_utf8_bytes": 64,
        "max_text_utf8_bytes": 16384,
        "max_response_bytes": 65536,
        "request_deadline_ms": 250,
        "max_snapshot_lifetime_ms": 30000,
    }
    assert [item["id"] for item in vectors["assertions"]] == [
        f"SNAP-V{number:03d}" for number in range(1, 9)
    ]
    for forbidden in (
        "query prefixes",
        "surrounding text",
        "selected text",
        "window titles",
    ):
        assert forbidden in contract
    assert "PIPE_REJECT_REMOTE_CLIENTS" in contract
    assert "Oversized items are excluded, not silently truncated" in contract
