"""The remote marketplaces: normalisation, failure, and the install path.

Every test here runs offline. The fetcher is a parameter of every public function
precisely so that this file does not depend on two third-party services being up
— and so that the shapes they return can be pinned, including the two different
registry shapes, instead of being discovered in production.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from kairos.extensions import market_sources as ms


@pytest.fixture(autouse=True)
def _clean_cache():
    ms.clear_cache()
    yield
    ms.clear_cache()


# --------------------------------------------------------------------------- fixtures

REGISTRY_FLAT = {
    "servers": [
        {  # npm package → a local stdio server
            "name": "io.github.example/filesystem",
            "description": "Read and write files under a root.",
            "version": "1.2.0",
            "repository": {"url": "https://github.com/example/filesystem"},
            "packages": [{
                "registryType": "npm",
                "identifier": "@example/filesystem",
                "version": "1.2.0",
                "runtimeHint": "npx",
                "transport": {"type": "stdio"},
                "environmentVariables": [
                    {"name": "EXAMPLE_ROOT", "isRequired": True},
                    {"name": "EXAMPLE_TOKEN", "isSecret": True},
                ],
            }],
        },
        {  # no package, only a hosted URL
            "name": "io.github.example/hosted",
            "description": "A hosted server.",
            "version": "0.2.0",
            "remotes": [{"type": "streamable-http", "url": "https://api.example.com/mcp"}],
        },
        {  # a pypi package
            "name": "io.github.example/py",
            "description": "A Python server.",
            "version": "3.0.0",
            "packages": [{
                "registryType": "pypi",
                "identifier": "example-mcp",
                "version": "3.0.0",
                "runtimeHint": "uvx",
            }],
        },
        {  # an older version of the first one: must not produce a second row
            "name": "io.github.example/filesystem",
            "description": "Read and write files under a root.",
            "version": "1.0.0",
            "packages": [{
                "registryType": "npm", "identifier": "@example/filesystem",
                "version": "1.0.0", "runtimeHint": "npx",
            }],
        },
        {  # nothing to install: shown, but not offered
            "name": "io.github.example/hollow",
            "description": "No package and no URL.",
            "version": "1.0.0",
        },
    ],
    "metadata": {"nextCursor": "cursor-1"},
}

#: The same registry also serves entries wrapped in {"server": {...}} — both are
#: live, so both have to normalise identically.
REGISTRY_WRAPPED = {"servers": [{"server": {
    "name": "io.github.example/wrapped",
    "description": "The wrapped shape.",
    "version": "9.9.9",
    "packages": [{"registryType": "npm", "identifier": "wrapped-mcp",
                  "version": "9.9.9", "runtimeHint": "npx"}],
}}]}

CLINE_DIR = [
    {"name": "airtable", "type": "dir", "path": "registry/mcps/airtable"},
    {"name": "zeta", "type": "dir", "path": "registry/mcps/zeta"},
    {"name": "README.md", "type": "file", "path": "registry/mcps/README.md"},
]

CLINE_ENTRY = {
    "id": "airtable",
    "type": "mcp",
    "name": "Airtable",
    "tagline": "Manage data in Airtable bases",
    "install": {
        "args": ["airtable", "--transport", "http", "https://mcp.airtable.com/mcp"],
        "env": [{"name": "AIRTABLE_TOKEN", "required": True,
                 "description": "A personal access token."}],
    },
}


def make_fetcher(payloads, seen=None):
    """A fetcher that answers from ``payloads``, keyed by a substring of the URL."""
    def fetch(url, timeout=None):
        if seen is not None:
            seen.append(url)
        for needle, payload in payloads.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return json.loads(json.dumps(payload))  # a fresh copy each call
        raise AssertionError("unexpected URL: %s" % url)
    return fetch


# --------------------------------------------------------------------------- normalising


def test_an_npm_package_becomes_an_npx_stdio_command():
    entry = ms.normalize_registry_entry(REGISTRY_FLAT["servers"][0])

    assert entry["name"] == "filesystem"
    assert entry["upstream"] == "io.github.example/filesystem"
    assert entry["command"] == "npx"
    # Pinned to the published version: "latest" would drift under the user.
    assert entry["args"] == ["-y", "@example/filesystem@1.2.0"]
    assert entry["transport"] == "stdio"
    assert entry["env_keys"] == ["EXAMPLE_ROOT", "EXAMPLE_TOKEN"]
    assert entry["installable"] is True


def test_a_pypi_package_becomes_a_uvx_command():
    entry = ms.normalize_registry_entry(REGISTRY_FLAT["servers"][2])
    assert entry["command"] == "uvx"
    assert entry["args"] == ["example-mcp@3.0.0"]
    assert entry["transport"] == "stdio"


def test_a_url_only_entry_becomes_an_http_transport():
    entry = ms.normalize_registry_entry(REGISTRY_FLAT["servers"][1])
    assert entry["url"] == "https://api.example.com/mcp"
    assert entry["transport"] == "http"
    assert entry["command"] is None
    assert entry["installable"] is True


def test_an_entry_with_neither_package_nor_url_is_not_installable():
    entry = ms.normalize_registry_entry(REGISTRY_FLAT["servers"][4])
    assert entry["installable"] is False
    # ...but it is still returned, because "we cannot install this" is
    # information the page should show rather than hide.
    assert entry["name"] == "hollow"


def test_the_wrapped_registry_shape_normalises_the_same_way():
    wrapped = ms.normalize_registry_entry(REGISTRY_WRAPPED["servers"][0])
    flat = ms.normalize_registry_entry(REGISTRY_WRAPPED["servers"][0]["server"])
    assert wrapped == flat
    assert wrapped["name"] == "wrapped"


def test_a_nonsense_payload_normalises_to_nothing():
    for junk in (None, [], "x", {}, {"name": ""}, {"server": "nope"}):
        assert ms.normalize_registry_entry(junk) is None


def test_a_cline_entry_keeps_its_http_transport_and_env():
    entry = ms.normalize_cline_entry(CLINE_ENTRY)
    assert entry["name"] == "airtable"
    assert entry["transport"] == "http"
    assert entry["url"] == "https://mcp.airtable.com/mcp"
    assert entry["env_keys"] == ["AIRTABLE_TOKEN"]
    assert entry["installable"] is True
    # `install.args` is a Cline CLI argument list, not a command; it must not be
    # mistaken for one.
    assert entry["command"] is None


def test_a_cline_entry_without_a_url_is_not_installable():
    entry = ms.normalize_cline_entry({"id": "weird", "install": {"args": ["weird"]}})
    assert entry["installable"] is False
    assert entry["url"] is None


# --------------------------------------------------------------------------- listing


def test_the_search_keeps_one_row_per_server_and_the_newest_version():
    fetch = make_fetcher({"registry.modelcontextprotocol.io": REGISTRY_FLAT})
    result = ms.search("registry", fetch=fetch)

    names = [e["name"] for e in result["entries"]]
    assert names.count("filesystem") == 1
    filesystem = next(e for e in result["entries"] if e["name"] == "filesystem")
    assert filesystem["version"] == "1.2.0"
    # Installable rows come first: a list whose first three entries cannot be
    # installed teaches the user the page is broken.
    assert result["entries"][0]["installable"] is True


def test_the_search_sends_the_query_to_the_registry():
    seen = []
    fetch = make_fetcher({"registry.modelcontextprotocol.io": REGISTRY_FLAT}, seen)
    ms.search("registry", query="filesystem", fetch=fetch)
    assert "search=filesystem" in seen[0]


def test_cline_enumerates_the_directory_and_skips_files():
    seen = []
    fetch = make_fetcher({
        "api.github.com": CLINE_DIR,
        "registry/mcps/airtable/entry.json": CLINE_ENTRY,
        "registry/mcps/zeta/entry.json": {"id": "zeta", "install": {}},
    }, seen)
    result = ms.search("cline", fetch=fetch)

    assert result["ok"] is True
    assert result["total"] == 2  # README.md is a file, not an entry
    assert sorted(e["name"] for e in result["entries"]) == ["airtable", "zeta"]
    # One request for the directory, one per entry — not one for the README.
    assert sum("api.github.com" in u for u in seen) == 1


def test_a_query_filters_cline_locally_because_there_is_no_search_api():
    fetch = make_fetcher({
        "api.github.com": CLINE_DIR,
        "registry/mcps/airtable/entry.json": CLINE_ENTRY,
    })
    result = ms.search("cline", query="air", fetch=fetch)
    assert [e["name"] for e in result["entries"]] == ["airtable"]


def test_one_unreachable_cline_entry_does_not_cost_the_page():
    fetch = make_fetcher({
        "api.github.com": CLINE_DIR,
        "registry/mcps/airtable/entry.json": CLINE_ENTRY,
        "registry/mcps/zeta/entry.json": urllib.error.URLError("boom"),
    })
    result = ms.search("cline", fetch=fetch)
    # The reachable one is still listed; the broken one is simply absent.
    assert [e["name"] for e in result["entries"]] == ["airtable"]


def test_the_page_is_cached_so_a_second_look_costs_nothing():
    seen = []
    fetch = make_fetcher({"registry.modelcontextprotocol.io": REGISTRY_FLAT}, seen)
    ms.search("registry", query="x", fetch=fetch)
    ms.search("registry", query="x", fetch=fetch)
    assert len(seen) == 1


def test_a_limit_caps_what_is_returned():
    fetch = make_fetcher({"registry.modelcontextprotocol.io": REGISTRY_FLAT})
    result = ms.search("registry", limit=2, fetch=fetch)
    assert len(result["entries"]) == 2


# --------------------------------------------------------------------------- failure


def test_an_unreachable_source_reports_a_reason_instead_of_an_empty_list():
    fetch = make_fetcher({"registry": urllib.error.URLError("network is unreachable")})
    result = ms.search("registry", fetch=fetch)

    assert result["ok"] is False
    assert result["entries"] == []
    # "no results" and "we could not ask" must not look the same.
    assert "unreachable" in result["error"]


def test_an_http_error_reports_the_status():
    fetch = make_fetcher({"registry": urllib.error.HTTPError(
        "u", 503, "Service Unavailable", {}, None)})
    result = ms.search("registry", fetch=fetch)
    assert result["ok"] is False
    assert "503" in result["error"]


def test_a_changed_response_shape_is_reported_not_crashed():
    fetch = make_fetcher({"registry": {"unexpected": True}})
    result = ms.search("registry", fetch=fetch)
    assert result["ok"] is False
    assert "shape" in result["error"]


def test_an_unknown_source_is_refused_with_the_known_names():
    result = ms.search("nope")
    assert result["ok"] is False
    assert "registry" in result["error"] and "cline" in result["error"]


def test_the_declared_sources_are_the_ones_that_can_be_searched():
    ids = {s["id"] for s in ms.list_sources()}
    assert ids == {"registry", "cline"}
    for source in ms.list_sources():
        assert source["label"] and source["homepage"]


# --------------------------------------------------------------------------- resolving


def test_resolving_a_registry_entry_finds_it_by_its_full_name():
    fetch = make_fetcher({"registry.modelcontextprotocol.io": REGISTRY_FLAT})
    entry, error = ms.fetch_entry("registry", "io.github.example/filesystem", fetch=fetch)
    assert error is None
    assert entry["name"] == "filesystem"


def test_resolving_a_registry_entry_that_is_not_there_says_so():
    fetch = make_fetcher({"registry.modelcontextprotocol.io": REGISTRY_FLAT})
    entry, error = ms.fetch_entry("registry", "io.github.example/nope", fetch=fetch)
    assert entry is None
    assert "no server named" in error


def test_resolving_a_cline_entry_fetches_just_that_one():
    seen = []
    fetch = make_fetcher({
        "registry/mcps/airtable/entry.json": CLINE_ENTRY,
    }, seen)
    entry, error = ms.fetch_entry("cline", "airtable", fetch=fetch)
    assert error is None and entry["name"] == "airtable"
    assert len(seen) == 1 and "airtable" in seen[0]


def test_resolving_reports_an_unreachable_source():
    fetch = make_fetcher({"registry": urllib.error.URLError("down")})
    entry, error = ms.fetch_entry("registry", "anything", fetch=fetch)
    assert entry is None and "unreachable" in error


# --------------------------------------------------------------------------- installing

def test_a_remote_entry_installs_through_the_same_path_as_a_curated_one(tmp_path):
    """The point of `install_entry`: a server from a marketplace lands in the file
    exactly as a curated one does, with credentials as ${VAR} references."""
    from kairos.extensions_install import install_entry

    entry, _ = ms.normalize_registry_entry(REGISTRY_FLAT["servers"][0]), None
    result = install_entry(entry, user_dir=tmp_path)

    assert result["ok"] is True and result["changed"] is True
    text = (tmp_path / "mcp.yaml").read_text(encoding="utf-8")
    assert "filesystem:" in text
    assert "@example/filesystem@1.2.0" in text
    assert "EXAMPLE_ROOT: ${EXAMPLE_ROOT}" in text

    # Idempotent, like every other install.
    again = install_entry(entry, user_dir=tmp_path)
    assert again["changed"] is False


def test_a_hosted_entry_installs_as_a_url_not_a_command(tmp_path):
    from kairos.extensions_install import install_entry

    entry = ms.normalize_registry_entry(REGISTRY_FLAT["servers"][1])
    install_entry(entry, user_dir=tmp_path)
    text = (tmp_path / "mcp.yaml").read_text(encoding="utf-8")
    assert "transport: http" in text
    assert "url: https://api.example.com/mcp" in text


# --------------------------------------------------------------------------- the API


def test_the_endpoints_serve_sources_search_and_install(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.app as app_module
    from kairos.extensions import market_sources as ms_mod

    monkeypatch.setattr(ms_mod, "_http_json", make_fetcher({
        "registry.modelcontextprotocol.io": REGISTRY_FLAT,
        "api.github.com": CLINE_DIR,
        "registry/mcps/airtable/entry.json": CLINE_ENTRY,
    }))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ms_mod.clear_cache()

    with TestClient(app_module.app) as client:
        got = client.get("/api/extensions/market/sources")
        assert got.status_code == 200
        assert {s["id"] for s in got.json()["sources"]} == {"registry", "cline"}

        got = client.get("/api/extensions/market/search",
                         params={"source": "registry", "q": "filesystem"})
        assert got.status_code == 200
        body = got.json()
        assert body["ok"] is True and body["total"] >= 4
        assert any(e["name"] == "filesystem" for e in body["entries"])

        name = "io.github.example/filesystem"
        got = client.post("/api/extensions/market/install",
                          json={"source": "registry", "id": name})
        assert got.status_code == 200, got.text
        assert got.json()["changed"] is True
        assert (tmp_path / ".kairos" / "mcp.yaml").exists()

        # The device under test: an entry with nothing to launch is refused with
        # an explanation rather than installed as a broken key.
        got = client.post("/api/extensions/market/install",
                          json={"source": "registry", "id": "io.github.example/hollow"})
        assert got.status_code == 400
        assert "nothing to install" in got.json()["detail"]

        got = client.post("/api/extensions/market/install",
                          json={"source": "registry", "id": "io.github.example/absent"})
        assert got.status_code == 400


def test_an_unreachable_source_is_reported_through_the_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.app as app_module
    from kairos.extensions import market_sources as ms_mod

    monkeypatch.setattr(ms_mod, "_http_json", make_fetcher(
        {"registry": urllib.error.URLError("no route to host")}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ms_mod.clear_cache()

    with TestClient(app_module.app) as client:
        got = client.get("/api/extensions/market/search", params={"source": "registry"})
        assert got.status_code == 200
        body = got.json()
        assert body["ok"] is False and body["entries"] == []
        assert "unreachable" in body["error"]
