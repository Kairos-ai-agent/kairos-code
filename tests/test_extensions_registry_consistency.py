"""The extension registries must describe themselves accurately.

``installed.json`` carries counters next to the records they summarise, and a
status per record. Both drift silently, and both are read by the API layer — so
they are pinned here. The specific lesson: a source that no longer exists is not
a "download-failed" (nothing is retryable), and calling it one buries a fact the
user can act on under a network error they cannot.
"""

import json
from pathlib import Path

REG = Path(__file__).resolve().parent.parent / "kairos" / "extensions"

KNOWN_STATUSES = {
    "installed", "already-installed", "source-missing", "download-failed",
    "dry-run", "pending",
}


def _installed():
    return json.loads((REG / "installed.json").read_text(encoding="utf-8"))


def test_the_skill_counters_match_the_records():
    sk = _installed()["skills"]
    recs = sk["records"]
    assert sk["total"] == len(recs)
    assert sk["installed"] == sum(
        1 for r in recs if r["status"] in ("installed", "already-installed"))
    assert sk["failed"] == sum(
        1 for r in recs if r["status"] == "download-failed")
    assert sk.get("sourceMissing", 0) == sum(
        1 for r in recs if r["status"] == "source-missing")


def test_no_vanished_source_is_recorded_as_a_download_failure():
    sk = _installed()["skills"]
    assert sk["failed"] == 0, (
        "every remaining failure should be either retryable (network) or "
        "reclassified as source-missing")
    for r in sk["records"]:
        if r["status"] == "source-missing":
            assert r.get("note"), f"{r.get('name')} says 'missing' without why"


def test_every_record_has_a_known_status():
    recs = _installed()["skills"]["records"]
    unknown = [r.get("name") for r in recs if r["status"] not in KNOWN_STATUSES]
    assert not unknown, unknown


def test_the_mcp_registry_keeps_its_shape():
    d = json.loads((REG / "mcps.json").read_text(encoding="utf-8"))
    assert isinstance(d["servers"], list) and len(d["servers"]) >= 20
    for s in d["servers"]:
        assert s["name"] and s["command"] and isinstance(s["args"], list)
        # A bundled entry must name something we can actually launch locally.
        if s.get("bundled"):
            assert s["name"] in ("filesystem", "git", "sqlite", "time", "fetch")
            assert s.get("needsNetwork") in (True, False)
