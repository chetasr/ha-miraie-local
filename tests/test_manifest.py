import importlib.util
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

MANIFEST_PATH = pathlib.Path("custom_components/miraie_local/manifest.json")
HACS_PATH = pathlib.Path("hacs.json")
CONST_PATH = pathlib.Path("custom_components/miraie_local/const.py")


def _load_const():
    """Load const.py directly; the package is not pip-installed."""
    spec = importlib.util.spec_from_file_location("miraie_local_const", CONST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_has_required_keys():
    m = json.loads(MANIFEST_PATH.read_text())
    for key in REQUIRED_KEYS:
        assert key in m, f"manifest missing {key}"
    assert m["domain"] == "miraie_local"
    assert m["iot_class"] == "local_push"


def test_manifest_keys_are_sorted_as_hassfest_requires():
    """hassfest wants domain, name, then the rest alphabetically."""
    m = json.loads(MANIFEST_PATH.read_text())
    keys = list(m)

    assert keys[0] == "domain"
    assert keys[1] == "name"
    assert keys[2:] == sorted(keys[2:])


def test_manifest_metadata_values():
    m = json.loads(MANIFEST_PATH.read_text())
    assert m["codeowners"] == ["@chetasr"]
    assert m["integration_type"] == "device"
    assert m["config_flow"] is True
    assert m["quality_scale"] == "bronze"
    # hassfest: cryptography is a Home Assistant core dependency, so a custom
    # integration must NOT list it (doing so could force a conflicting version).
    assert "requirements" not in m
    assert m["dependencies"] == ["network"]
    assert m["documentation"] == "https://github.com/chetasr/ha-miraie-local"
    assert m["issue_tracker"] == "https://github.com/chetasr/ha-miraie-local/issues"


def test_const_values():
    const = _load_const()
    assert const.DOMAIN == "miraie_local"
    assert const.DEFAULT_PORT == 5001
    assert const.BROADCAST_PORT == 5002
    assert const.DEFAULT_MULTICAST == "239.255.0.15"
    assert const.MANUFACTURER == "Panasonic"


def test_hacs_json_valid():
    h = json.loads(HACS_PATH.read_text())
    assert h["name"] == "MirAIe Local"
    assert h["homeassistant"] == "2026.3.0"
    assert h["render_readme"] is True
