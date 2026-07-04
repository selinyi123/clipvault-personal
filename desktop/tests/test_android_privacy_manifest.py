import xml.etree.ElementTree as ET
from pathlib import Path
import re

_ROOT = Path(__file__).resolve().parents[2]
_ANDROID_NS = "http://schemas.android.com/apk/res/android"


def _android_attr(name: str) -> str:
    return f"{{{_ANDROID_NS}}}{name}"


def _read_xml(rel: str) -> ET.Element:
    return ET.parse(_ROOT / rel).getroot()


def test_android_app_data_backup_and_transfer_are_disabled():
    manifest = _read_xml("android/app/src/main/AndroidManifest.xml")
    app = manifest.find("application")
    assert app is not None

    assert app.attrib[_android_attr("allowBackup")] == "false"
    assert app.attrib[_android_attr("fullBackupContent")] == "@xml/backup_rules"
    assert app.attrib[_android_attr("dataExtractionRules")] == "@xml/data_extraction_rules"


def test_android_backup_rules_exclude_all_app_private_data():
    rules = _read_xml("android/app/src/main/res/xml/backup_rules.xml")
    assert rules.tag == "full-backup-content"

    excludes = {(node.attrib.get("domain"), node.attrib.get("path")) for node in rules.findall("exclude")}
    assert ("root", ".") in excludes


def test_android_data_extraction_rules_exclude_cloud_and_device_transfer():
    rules = _read_xml("android/app/src/main/res/xml/data_extraction_rules.xml")
    assert rules.tag == "data-extraction-rules"

    for section_name in ("cloud-backup", "device-transfer"):
        section = rules.find(section_name)
        assert section is not None
        excludes = {
            (node.attrib.get("domain"), node.attrib.get("path"))
            for node in section.findall("exclude")
        }
        assert ("root", ".") in excludes


def _manifest_services() -> dict[str, ET.Element]:
    manifest = _read_xml("android/app/src/main/AndroidManifest.xml")
    app = manifest.find("application")
    assert app is not None

    services = app.findall("service")
    return {service.attrib[_android_attr("name")]: service for service in services}


def _intent_actions(service: ET.Element) -> list[str]:
    return [
        action.attrib[_android_attr("name")]
        for intent_filter in service.findall("intent-filter")
        for action in intent_filter.findall("action")
    ]


def _metadata_by_name(service: ET.Element) -> dict[str, ET.Element]:
    return {
        metadata.attrib[_android_attr("name")]: metadata
        for metadata in service.findall("meta-data")
    }


def test_android_ime_services_are_bound_only_as_system_input_methods():
    expected_ime_services = {
        ".ime.ClipVaultPanelImeService": "@xml/ime_panel_config",
        ".ime.ClipVaultFullKeyboardService": "@xml/ime_full_config",
    }
    services = _manifest_services()

    bind_input_services = {
        name
        for name, service in services.items()
        if service.attrib.get(_android_attr("permission"))
        == "android.permission.BIND_INPUT_METHOD"
    }
    assert bind_input_services == set(expected_ime_services)

    for name, config_resource in expected_ime_services.items():
        service = services[name]

        assert service.attrib[_android_attr("exported")] == "true"
        assert service.attrib[_android_attr("permission")] == "android.permission.BIND_INPUT_METHOD"

        intent_filters = service.findall("intent-filter")
        assert len(intent_filters) == 1
        assert _intent_actions(service) == ["android.view.InputMethod"]
        assert intent_filters[0].findall("category") == []
        assert intent_filters[0].findall("data") == []

        metadata = _metadata_by_name(service)
        assert set(metadata) == {"android.view.im"}
        assert metadata["android.view.im"].attrib[_android_attr("resource")] == config_resource


def _read_text(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_android_pairing_does_not_commit_host_before_token_redeem():
    main = _read_text("android/app/src/main/kotlin/com/clipvault/app/ui/MainActivity.kt")
    sync = _read_text("android/app/src/main/kotlin/com/clipvault/app/sync/Sync.kt")

    assert "apply { this.host = h }" not in main
    assert "SyncClient(s).pairWithHost(h, c)" in main

    assert "fun pairWithHost(host: String, code: String): Boolean" in sync
    assert "val token = SyncClient(s, h).requestPairToken(code) ?: return false" in sync
    assert "s.replacePairing(h, token)" in sync

    commit = re.search(
        r"(?s)fun replacePairing\(host: String, token: String\) \{(?P<body>.*?)\n    \}",
        sync,
    )
    assert commit, "Settings.replacePairing must commit pairing state fail-closed"
    body = commit.group("body")
    clear = body.index("tokenStore.set(null)")
    host = body.index('putString("host", host)')
    token = body.index("tokenStore.set(token)")
    assert clear < host < token


def test_android_sync_client_does_not_follow_redirects_with_bearer_tokens():
    sync = _read_text("android/app/src/main/kotlin/com/clipvault/app/sync/Sync.kt")

    assert "HttpURLConnection" in sync
    assert "instanceFollowRedirects = false" in sync
    assert "instanceFollowRedirects = true" not in sync

    redirect_guard = sync.index("instanceFollowRedirects = false")
    auth_header = sync.index('setRequestProperty("Authorization", "Bearer $it")')
    assert redirect_guard < auth_header
