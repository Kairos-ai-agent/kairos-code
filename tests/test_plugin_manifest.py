"""R38.11 — a plugin manifest can say what it contributes, and who it needs.

Before this the manifest carried only ``name``, ``version``, ``description`` and
``enabled``, so two questions had no answer: "what does this plugin add?" (the
file layout told you, the manifest did not) and "will it work with this build?"
(nothing checked). Both now have one, and an unrecognised constraint is reported
rather than silently disabling a plugin.
"""

import pytest

from kairos.plugins import (KNOWN_CAPABILITIES, PluginInfo,
                            check_compatible)


@pytest.mark.parametrize("spec,version,expected", [
    ("", "0.1.4", True),                 # no constraint at all
    (">=0.1", "0.1.4", True),
    (">=0.1,<0.2", "0.1.4", True),
    (">=0.1,<0.2", "0.2.0", False),      # the next minor is out of range
    (">=0.2", "0.1.4", False),
    ("<0.1", "0.1.4", False),
    ("=0.1.4", "0.1.4", True),
    ("=0.1.5", "0.1.4", False),
    ("v0.1.4", "0.1.4", True),           # a stray v is tolerated
    ("garbage", "0.1.4", True),          # unparseable: never silently disable
])
def test_compatibility_ranges(spec, version, expected):
    ok, _why = check_compatible(spec, version)
    assert ok is expected


def test_the_reason_is_useful_when_it_says_no():
    ok, why = check_compatible(">=0.2,<0.3", "0.1.4")
    assert ok is False
    assert "0.2" in why and "0.1.4" in why


def test_the_manifest_surfaces_capabilities_and_compatibility(tmp_path):
    from kairos.plugins import PluginManager

    root = tmp_path / "plugins"
    plugin = root / "demo"
    plugin.mkdir(parents=True)
    (plugin / "kairos-plugin.yaml").write_text(
        "name: demo\n"
        "version: 1.2.3\n"
        "description: a demo plugin\n"
        "capabilities: [skills, hooks, mcp]\n"
        'compatible: ">=0.1,<0.2"\n',
        encoding="utf-8")
    (plugin / "skills").mkdir()
    (plugin / "hooks").mkdir()

    info = PluginManager(root).list_installed()[0]
    assert info.name == "demo"
    assert info.version == "1.2.3"
    assert info.capabilities == ["skills", "hooks", "mcp"]
    assert info.compatible == ">=0.1,<0.2"
    # Serialised for the API, so the UI can show it.
    assert info.to_dict()["capabilities"] == ["skills", "hooks", "mcp"]
    assert info.to_dict()["compatible"] == ">=0.1,<0.2"


def test_an_unknown_capability_is_dropped_not_fatal(tmp_path):
    from kairos.plugins import PluginManager

    root = tmp_path / "plugins"
    plugin = root / "odd"
    plugin.mkdir(parents=True)
    (plugin / "kairos-plugin.yaml").write_text(
        "name: odd\ncapabilities: [skills, telepathy]\n", encoding="utf-8")

    info = PluginManager(root).list_installed()[0]
    assert info.capabilities == ["skills"]          # the known one survives
    assert "telepathy" not in info.capabilities


def test_a_manifest_that_declares_nothing_is_still_valid(tmp_path):
    from kairos.plugins import PluginManager

    root = tmp_path / "plugins"
    plugin = root / "plain"
    plugin.mkdir(parents=True)
    (plugin / "kairos-plugin.yaml").write_text("name: plain\n", encoding="utf-8")

    info = PluginManager(root).list_installed()[0]
    assert info.capabilities == []                  # nothing declared, no lie
    assert info.compatible == ""
    assert check_compatible(info.compatible, "0.1.4")[0] is True
    assert set(KNOWN_CAPABILITIES) >= {"skills", "agents", "hooks", "mcp", "commands"}
    assert PluginInfo(name="x").to_dict()["capabilities"] == []
