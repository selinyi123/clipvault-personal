from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_production_workflow_requires_explicit_native_device_runner_and_real_connected_test():
    workflow = read(".github/workflows/v2-ime-production.yml")
    script = read("android/scripts/run-v2-ime-device-tests.ps1")

    assert "native-device-instrumentation:" in workflow
    assert "clipvault-android-device" in workflow
    assert "run-v2-ime-device-tests.ps1" in workflow
    assert ":ime-app:connectedDebugAndroidTest" in script
    assert "NativeRimeDeviceTest" in script
    assert "exactly one authorized device" in script
    assert "$tests -lt 6" in script
    assert "$skipped -ne 0" in script


def test_native_device_suite_covers_daily_rime_state_transitions():
    source = read(
        "android/ime-app/src/androidTest/kotlin/com/clipvault/imeapp/NativeRimeDeviceTest.kt"
    )

    for marker in (
        "jintianxiawuwomenqukaihui",
        "今天下午我们去开会",
        '"xi\'an"',
        "PageDirection.NEXT",
        "PageDirection.PREVIOUS",
        "cancelComposition",
        "session did not recover after cancellation",
    ):
        assert marker in source
