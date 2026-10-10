import json
import pathlib

REQUIRED_KEYS = (
    "domain",
    "name",
    "version",
    "documentation",
    "issue_tracker",
    "codeowners",
    "iot_class",
    "integration_type",
)


def test_manifest_has_required_keys():
    m = json.loads(pathlib.Path("custom_components/miraie_local/manifest.json").read_text())
    for key in REQUIRED_KEYS:
        assert key in m, f"manifest missing {key}"
    assert m["domain"] == "miraie_local"
    assert m["iot_class"] == "local_push"


def test_hacs_json_valid():
    h = json.loads(pathlib.Path("hacs.json").read_text())
    assert "name" in h and "homeassistant" in h
