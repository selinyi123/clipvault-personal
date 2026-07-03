import xml.etree.ElementTree as ET
from pathlib import Path

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
