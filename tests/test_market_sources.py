"""The remote marketplaces: normalisation, failure, and the install path.

Every test here runs offline. The fetcher is a parameter of every public function
precisely so that this file does not depend on two third-party services being up
— and so that the shapes they return can be pinned, including the two different
registry shapes, instead of being discovered in production.
"""

from __future__ import annotations

import io
import json
import socket
import tarfile
import urllib.error

import pytest

from kairos.extensions import market_sources as ms


@pytest.fixture(autouse=True)
def _clean_cache():
    ms.clear_cache()
    yield
    ms.clear_cache()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly if a test in this file reaches the real network.

    Every source takes its transport as a parameter, and an injected ``fetch`` is
    reused for the repository archive too. This is the check that the injection is
    actually complete: a test that would have gone online fails here, instead of
    passing on a machine that happens to have a route out and failing on one that
    does not.

    Loopback is allowed through because ``TestClient`` builds an asyncio event
    loop, whose Windows self-pipe is a ``socket.socketpair()`` — a loopback
    connection with nothing to do with the marketplaces. Everything else is
    refused, which is where api.github.com, codeload.github.com and jsDelivr
    live.
    """
    real_connect = socket.socket.connect

    def guard(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, (tuple, list)) and address else address
        if str(host) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address, *args, **kwargs)
        raise AssertionError("this test tried to connect to %r" % (address,))

    monkeypatch.setattr(socket.socket, "connect", guard)


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
                if isinstance(payload, (bytes, bytearray)):
                    # A repository tarball is neither JSON nor text: the CDN
                    # transports decode what they fetch, and decoding a gzip would
                    # corrupt it. Bytes go back untouched.
                    return bytes(payload)
                if isinstance(payload, str):
                    return payload
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
    assert ids == {"registry", "smithery", "cline", "cline-plugins",
                   "cline-skills", "anthropic-plugins", "anthropic-skills"}
    for source in ms.list_sources():
        assert source["label"] and source["homepage"]
        assert source["kind"] in ("mcp", "plugin", "skill", "mixed")
        assert isinstance(source["needs_query"], bool)
        # Declared, not measured: `total_count` is what fills this in.
        assert source["total"] is None


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
    # No archive in this fetcher, so the one entry comes from the CDN — one
    # request, and never the directory listing.
    assert [u for u in seen if "airtable" in u] == [
        "https://cdn.jsdelivr.net/gh/cline/marketplace@main/registry/mcps/airtable/entry.json"]
    assert not any("api.github.com" in u for u in seen)


def test_resolving_a_cline_entry_uses_the_archive_and_skips_the_cdn():
    """The road an install really takes, and the reason it takes it.

    jsDelivr answered ``WinError 10054`` (connection reset) for exactly this entry
    during a live install while codeload served the same file. Reading the entry
    out of the archive also means an install right after a page load costs no
    request at all: it is the download the listing already made.
    """
    seen = []
    fetch = archive_fetcher({"codeload.github.com/cline/marketplace": CLINE_TARBALL}, seen)

    entry, error = ms.fetch_entry("cline", "airtable", fetch=fetch)
    assert error is None
    assert entry["name"] == "airtable"
    assert entry["url"] == "https://mcp.airtable.com/mcp"
    assert entry["env_keys"] == ["AIRTABLE_TOKEN"]
    assert seen == [CLINE_TARBALL_URL]

    # An entry the archive does not hold is asked of the CDN rather than reported
    # as missing: "not in this download" is not "not there".
    fallback = make_fetcher({"registry/plugins/agent-browser/entry.json": CLINE_PLUGIN_ENTRY})
    entry, error = ms.fetch_entry("cline-plugins", "agent-browser", fetch=fallback)
    assert error is None and entry["name"] == "agent-browser"


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
    monkeypatch.setattr(ms_mod, "_http_text", make_fetcher({}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ms_mod.clear_cache()

    with TestClient(app_module.app) as client:
        got = client.get("/api/extensions/market/sources")
        assert got.status_code == 200
        assert {s["id"] for s in got.json()["sources"]} == {
            "registry", "smithery", "cline", "cline-plugins", "cline-skills",
            "anthropic-plugins", "anthropic-skills"}

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


# ===========================================================================
# The four later sources.
#
# Every fixture below is *captured* — copied out of a live response from this
# machine — rather than written from memory of the documentation. That is the
# point: the Smithery list row genuinely has no URL in it, the Anthropic
# directory genuinely declares four different source shapes, and the Cline
# plugins/skills directories genuinely hold one entry.json and nothing else.
# A normaliser tested against a convenient shape is a normaliser that fails on
# the real one.
# ===========================================================================

#: ``GET https://registry.smithery.ai/servers?pageSize=3`` — two rows, verbatim,
#: and the pagination block that carries the real ``totalCount`` (17,062 on the
#: day this was captured; the number moves, which is why it is never hard-coded).
SMITHERY_LIST = {
    "servers": [
        {
            "id": "50f26566-7dfe-4842-a5b8-1a9407fb91f4",
            "qualifiedName": "brave",
            "namespace": "brave",
            "slug": "",
            "displayName": "Brave Search",
            "description": ("Search the web with Brave's independent index — web, "
                            "news, images, and videos. Bring your own subscription "
                            "token from the [Brave Search API dashboard]"
                            "(https://api-dashboard.search.brave.com)."),
            "iconUrl": "https://api.smithery.ai/servers/brave/icon",
            "verified": True,
            "useCount": 87579,
            "remote": True,
            "isDeployed": True,
            "unlisted": False,
            "inactive": False,
            "createdAt": "2025-09-05T18:07:47.018Z",
            "homepage": "https://brave.com/search/api/",
            "bySmithery": True,
            "owner": "org_01KPBXJTDN7ASH7MJ3958C05QY",
            "score": None,
        },
        {
            "id": "0cdbc44b-23ed-4725-85a7-8782e87029a2",
            "qualifiedName": "onesignal/onesignal",
            "namespace": "onesignal",
            "slug": "onesignal",
            "displayName": "OneSignal",
            "description": "OneSignal is a customer engagement platform.",
            "iconUrl": "https://api.smithery.ai/servers/onesignal/onesignal/icon",
            "verified": True,
            "useCount": 22491,
            "remote": True,
            "isDeployed": True,
            "unlisted": False,
            "inactive": False,
            "createdAt": "2026-03-10T05:39:45.197Z",
            "homepage": "https://onesignal.com/",
            "bySmithery": False,
            "owner": "org_01KPV6H8KK31A5ER2M8K9PY9HQ",
            "score": None,
        },
    ],
    "pagination": {"currentPage": 1, "pageSize": 3, "totalPages": 167,
                   "totalCount": 17062},
}

#: ``GET https://registry.smithery.ai/servers/brave`` — the detail, which is the
#: only place ``deploymentUrl`` exists. Trimmed to the fields this build reads;
#: the strings are verbatim.
SMITHERY_DETAIL = {
    "qualifiedName": "brave",
    "displayName": "Brave Search",
    "description": ("Search the web with Brave's independent index — web, news, "
                    "images, and videos."),
    "remote": True,
    "deploymentUrl": "https://brave.run.tools",
    "connections": [{
        "type": "http",
        "deploymentUrl": "https://brave.run.tools",
        "configSchema": {
            "type": "object",
            "required": ["braveApiKey"],
            "properties": {
                "braveApiKey": {
                    "type": "string",
                    "title": "Brave API Key",
                    "description": "Your Brave Search subscription token.",
                },
            },
        },
    }],
    "security": None,
}

#: ``GET https://cdn.jsdelivr.net/gh/anthropics/claude-plugins-official@main/
#: .claude-plugin/marketplace.json`` wrapped in its envelope. The four entries
#: are verbatim and are the four shapes the directory actually uses.
ANTHROPIC_MARKETPLACE = {
    "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
    "name": "claude-plugins-official",
    "owner": {"name": "Anthropic", "email": "support@anthropic.com"},
    "plugins": [
        {  # git-subdir with an explicit ref and a sha
            "name": "42crunch-api-security-testing",
            "description": "Automate API security directly in Claude Code with 42Crunch.",
            "author": {"name": "42Crunch"},
            "category": "security",
            "source": {
                "source": "git-subdir",
                "url": "https://github.com/42Crunch-AI/claude-plugins.git",
                "path": "plugins/api-security-testing",
                "ref": "v1.5.5",
                "sha": "faf5305385de8afed9468904e8639be737aff39e",
            },
            "homepage": "https://42crunch.com",
        },
        {  # a whole repository: url + sha, the plugin is the repo root
            "name": "agentforce-adlc",
            "description": "Salesforce Agentforce development lifecycle in Claude Code.",
            "author": {"name": "Salesforce"},
            "category": "development",
            "source": {
                "source": "url",
                "url": "https://github.com/SalesforceAIResearch/agentforce-adlc.git",
                "sha": "09bf1539d41f9ff355ba3eb5d05d4a75813423bb",
            },
            "homepage": "https://github.com/SalesforceAIResearch/agentforce-adlc",
        },
        {  # a subdirectory with no ref: the sha is the only revision there is
            "name": "atomic-agents",
            "description": "Build agents with Atomic Agents.",
            "category": "development",
            "source": {
                "source": "url",
                "url": "https://github.com/BrainBlend-AI/atomic-agents.git",
                "path": "claude-plugin/atomic-agents",
                "sha": "33d2ec94f42d4a65393a336e35edf21ee5700303",
            },
            "homepage": "https://github.com/BrainBlend-AI/atomic-agents",
        },
        {  # a path inside the marketplace repository itself
            "name": "agent-sdk-dev",
            "description": "Development kit for working with the Claude Agent SDK",
            "author": {"name": "Anthropic", "email": "support@anthropic.com"},
            "source": "./plugins/agent-sdk-dev",
            "category": "development",
            "homepage": ("https://github.com/anthropics/claude-plugins-public/tree/"
                         "main/plugins/agent-sdk-dev"),
        },
    ],
}

#: ``GET https://cdn.jsdelivr.net/gh/cline/marketplace@main/registry/plugins/
#: agent-browser/entry.json``, verbatim. Note there is no ``--transport`` and no
#: files: the directory holds this manifest and an icon, and the install
#: arguments name the Cline CLI.
CLINE_PLUGIN_ENTRY = {
    "$schema": "../../../schemas/plugin.schema.json",
    "id": "agent-browser",
    "type": "plugin",
    "name": "Agent Browser",
    "tagline": ("Web and Electron browser automation via the agent-browser CLI skill"),
    "author": {"name": "Cline", "url": "https://cline.bot"},
    "homepage": "https://github.com/cline/plugins/tree/main/plugins/agent-browser",
    "repo": "https://github.com/cline/plugins/tree/main/plugins/agent-browser",
    "tags": ["software"],
    "license": "Apache-2.0",
    "verified": True,
    "install": {"args": ["agent-browser"]},
}

#: ``GET .../registry/skills/amazon-location-service/entry.json``, verbatim.
CLINE_SKILL_ENTRY = {
    "$schema": "../../../schemas/skill.schema.json",
    "id": "amazon-location-service",
    "type": "skill",
    "name": "Amazon Location Service",
    "tagline": "Maps, places, routes, geocoding, geofences, and tracking for AWS apps",
    "description": "Guidance for integrating Amazon Location Service into AWS applications.",
    "author": {"name": "AWS Geospatial"},
    "homepage": "https://github.com/cline/skills/tree/main/skills/amazon-location-service",
    "repo": "https://github.com/cline/skills",
    "tags": ["software", "data"],
    "license": "MIT-0",
    "verified": True,
    "install": {"args": ["cline/skills", "--skill", "amazon-location-service"]},
}

CLINE_PLUGINS_DIR = [
    {"name": "agent-browser", "path": "registry/plugins/agent-browser",
     "sha": "b3535557139348b5342ecde0a5ed04c4828ec0c4", "size": 0,
     "type": "dir", "download_url": None},
    {"name": "agents-squad", "path": "registry/plugins/agents-squad",
     "sha": "ea307bdfc49cdd439a518ecb9e196810931a331b", "size": 0,
     "type": "dir", "download_url": None},
]

CLINE_SKILLS_DIR = [
    {"name": "amazon-location-service", "path": "registry/skills/amazon-location-service",
     "sha": "7c1bead9742c3bcdaf085491b1613d23e891ddd5", "size": 0,
     "type": "dir", "download_url": None},
]

# --------------------------------------------------------------------------- the repository tarball

#: ``registry/mcps/airtable/entry.json`` out of ``cline/marketplace``, captured
#: from the repository tarball (``codeload.github.com/cline/marketplace/tar.gz/main``)
#: with nothing changed but its CRLF line endings. The whole file, not an idea of
#: one: a hand-written "realistic" entry would have left out the ``$schema``, the
#: ``verified``/``featured`` flags, the ``repo`` sitting beside the ``homepage``,
#: and the ``url`` an env var carries when there is a page that mints it.
CLINE_MCP_ENTRY_JSON = r"""{
  "$schema": "../../../schemas/mcp.schema.json",
  "id": "airtable",
  "type": "mcp",
  "name": "Airtable",
  "tagline": "Manage data in Airtable bases",
  "description": "Manage data in Airtable bases. This official Airtable MCP integration is installable through Cline.",
  "author": {
    "name": "Airtable"
  },
  "tags": [
    "data",
    "productivity"
  ],
  "verified": false,
  "featured": false,
  "install": {
    "args": [
      "airtable",
      "--transport",
      "http",
      "https://mcp.airtable.com/mcp"
    ],
    "env": [
      {
        "name": "AIRTABLE_TOKEN",
        "required": true,
        "description": "Airtable personal access token used by local Airtable MCP configuration.",
        "url": "https://airtable.com/create/tokens"
      }
    ]
  },
  "homepage": "https://github.com/Airtable/airtable-mcp-cli",
  "repo": "https://github.com/Airtable/airtable-mcp-cli"
}
"""

#: ``registry/skills/skill-creator/entry.json``, captured the same way and
#: likewise verbatim. The two files together are the two shapes that matter here:
#: an MCP server that installs, and a skill that only the Cline CLI can install.
CLINE_SKILL_CREATOR_ENTRY_JSON = r"""{
  "$schema": "../../../schemas/skill.schema.json",
  "id": "skill-creator",
  "type": "skill",
  "name": "Skill Creator",
  "tagline": "Create, improve, evaluate, benchmark, and tune agent skills",
  "description": "Workflow for creating new skills, modifying existing skills, running evaluations, benchmarking performance, analyzing variance, and optimizing skill descriptions for trigger accuracy.",
  "author": {
    "name": "Cline"
  },
  "homepage": "https://github.com/cline/skills/tree/main/skills/skill-creator",
  "repo": "https://github.com/cline/skills",
  "tags": [
    "productivity",
    "software"
  ],
  "license": "Apache-2.0",
  "verified": true,
  "featured": false,
  "install": {
    "args": [
      "cline/skills",
      "--skill",
      "skill-creator"
    ]
  }
}
"""


def repo_tarball(files, top="cline-marketplace-main"):
    """A real gzipped tar, laid out the way codeload lays one out.

    Every member is named ``<repo>-<ref>/<path>`` — that layout is the one thing
    a reader of the archive has to cope with, so the fixture copies it instead of
    inventing a friendlier one.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for rel, body in files.items():
            payload = body.encode("utf-8") if isinstance(body, str) else bytes(body)
            info = tarfile.TarInfo("%s/%s" % (top, rel))
            info.size = len(payload)
            info.type = tarfile.REGTYPE
            tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def archive_fetcher(tarballs, seen=None):
    """A transport that answers repository tarballs and refuses everything else.

    Keyed by a substring of the URL, and anything it does not recognise is an
    assertion failure rather than a fallback: a URL that reaches the real network
    inside this file is a bug, and the needle is the full
    ``codeload.github.com/<owner>/<repo>`` so an accidental API or CDN call shows
    up as a failure instead of quietly matching.
    """
    def fetch(url, timeout=None):
        if seen is not None:
            seen.append(url)
        for needle, payload in tarballs.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError("unexpected URL: %s" % url)
    return fetch


#: Three real entries across two kinds, which is what a Cline directory looks
#: like: ``registry/mcps`` and ``registry/skills`` are separate directories in the
#: same repository and only the first is an MCP server.
CLINE_ARCHIVE = {
    "registry/mcps/airtable/entry.json": CLINE_MCP_ENTRY_JSON,
    "registry/skills/amazon-location-service/entry.json": json.dumps(CLINE_SKILL_ENTRY),
    "registry/skills/skill-creator/entry.json": CLINE_SKILL_CREATOR_ENTRY_JSON,
}

#: The tarball every archive test starts from, built in memory.
CLINE_TARBALL = repo_tarball(CLINE_ARCHIVE)

CLINE_TARBALL_URL = ms.ARCHIVE_URL % ("cline/marketplace", "main")

#: ``GET https://api.github.com/repos/anthropics/skills/contents/skills`` —
#: three of the 19 directories it returned, with their real shas.
ANTHROPIC_SKILLS_DIR = [
    {"name": "academy-guide", "path": "skills/academy-guide",
     "sha": "baddb91c315ea384f9191615054ba8026eb57ba2", "size": 0, "type": "dir"},
    {"name": "algorithmic-art", "path": "skills/algorithmic-art",
     "sha": "4aef6bcad51d058ec32b1acb9da436851863e56e", "size": 0, "type": "dir"},
    {"name": "brand-guidelines", "path": "skills/brand-guidelines",
     "sha": "1dc8bd3584b80568edae7da16382363e24ecf0f0", "size": 0, "type": "dir"},
]

#: ``skills/academy-guide/SKILL.md`` verbatim (first lines). Note the folded
#: ``description: >``: real skills wrap a whole paragraph this way, and a
#: line-based frontmatter reader answers ">" for it.
ACADEMY_GUIDE_SKILL_MD = """---
name: academy-guide
description: >
  Stop and check this skill before finishing any reply to a question about how
  to use Claude or a Claude product — it recommends matching courses,
  tutorials, and use cases from Claude Academy (academy.claude.com),
  Anthropic's learning hub.
---

# Academy Guide

Teach the user, step by step.
"""


# --------------------------------------------------------------------------- smithery


def test_smithery_turns_a_detail_fetch_into_an_installable_http_entry():
    fetch = make_fetcher({
        "registry.smithery.ai/servers?": SMITHERY_LIST,
        "registry.smithery.ai/servers/brave": SMITHERY_DETAIL,
    })
    result = ms.list_smithery(limit=1, fetch=fetch)

    assert result["ok"] is True
    # The upstream's own count, not the size of the page we asked for.
    assert result["total"] == 17062
    entry = result["entries"][0]
    assert entry["name"] == "brave"
    assert entry["upstream"] == "brave"
    assert entry["url"] == "https://brave.run.tools"
    assert entry["transport"] == "http"
    assert entry["installable"] is True
    assert entry["kind"] == "mcp"
    # Reported, not written as headers: Smithery's hosted endpoints take their
    # config as a connection parameter, and a guessed header would start and
    # then fail at the first call.
    assert entry["config_keys"] == ["braveApiKey"]
    assert entry["env_keys"] == []


def test_smithery_fetches_the_detail_of_the_returned_entries_only():
    seen = []
    fetch = make_fetcher({
        "registry.smithery.ai/servers?": SMITHERY_LIST,
        "registry.smithery.ai/servers/brave": SMITHERY_DETAIL,
    }, seen)
    ms.list_smithery(limit=1, fetch=fetch)

    details = [u for u in seen if "/servers/" in u and "?" not in u]
    assert len(details) == 1  # not one per row in the page, and never all 17,062


def test_a_smithery_entry_whose_detail_fails_is_kept_as_not_installable():
    fetch = make_fetcher({
        "registry.smithery.ai/servers?": SMITHERY_LIST,
        "registry.smithery.ai/servers/brave": SMITHERY_DETAIL,
        "registry.smithery.ai/servers/onesignal/onesignal":
            urllib.error.URLError("connection reset"),
    })
    result = ms.list_smithery(limit=2, fetch=fetch)

    names = [e["name"] for e in result["entries"]]
    # Still listed: "we could not ask" is not "it is gone".
    assert "onesignal" in names
    broken = next(e for e in result["entries"] if e["name"] == "onesignal")
    assert broken["installable"] is False
    assert "connection reset" in broken["note"]
    # ...and installable rows come first, so a broken row never leads the page.
    assert result["entries"][0]["installable"] is True
    assert "could not be resolved" in result["note"]


def test_smithery_sends_the_query_to_the_server():
    seen = []
    fetch = make_fetcher({"registry.smithery.ai/servers?": SMITHERY_LIST}, seen)
    ms.list_smithery(q="airtable", fetch=fetch)
    assert "q=airtable" in seen[0]


def test_smithery_keeps_the_namespace_slash_in_the_detail_url():
    url = ms.smithery_detail_url("onesignal/onesignal")
    assert url.endswith("/servers/onesignal/onesignal")


def test_a_smithery_name_that_cannot_be_a_yaml_key_is_reduced():
    """`onesignal/onesignal` would be refused by the installer as a key."""
    entry = ms.normalize_smithery_entry(SMITHERY_LIST["servers"][1])
    assert entry["name"] == "onesignal"  # the slug, not the namespace path
    assert entry["upstream"] == "onesignal/onesignal"


# --------------------------------------------------------------------------- anthropic plugins


def test_all_four_claude_marketplace_source_shapes_are_walked():
    by_name = {p["name"]: ms.normalize_anthropic_plugin(p)
               for p in ANTHROPIC_MARKETPLACE["plugins"]}

    subdir = by_name["42crunch-api-security-testing"]
    assert (subdir["repo"], subdir["ref"], subdir["path"]) == (
        "42Crunch-AI/claude-plugins", "v1.5.5", "plugins/api-security-testing")

    whole_repo = by_name["agentforce-adlc"]
    assert (whole_repo["repo"], whole_repo["ref"], whole_repo["path"]) == (
        "SalesforceAIResearch/agentforce-adlc",
        "09bf1539d41f9ff355ba3eb5d05d4a75813423bb", "")

    path_without_ref = by_name["atomic-agents"]
    assert (path_without_ref["repo"], path_without_ref["path"]) == (
        "BrainBlend-AI/atomic-agents", "claude-plugin/atomic-agents")
    # No ref declared: the sha is used, because a branch moves and a commit does not.
    assert path_without_ref["ref"] == "33d2ec94f42d4a65393a336e35edf21ee5700303"

    relative = by_name["agent-sdk-dev"]
    assert (relative["repo"], relative["ref"], relative["path"]) == (
        "anthropics/claude-plugins-official", "main", "plugins/agent-sdk-dev")

    for entry in by_name.values():
        assert entry["kind"] == "plugin"
        assert entry["installable"] is True
        assert entry["source"] == "anthropic-plugins"
        assert entry["category"]


def test_a_plugin_source_that_is_not_a_github_repository_is_not_installable():
    entry = ms.normalize_anthropic_plugin({
        "name": "elsewhere",
        "description": "Hosted somewhere this build cannot fetch from.",
        "source": {"source": "url", "url": "https://gitlab.com/someone/plugin.git"},
    })
    assert entry["installable"] is False
    assert "not a GitHub repository" in entry["note"]
    # Returned anyway: the page shows it with the reason rather than hiding it.
    assert entry["name"] == "elsewhere"

    # A payload with no name is nothing at all; one with a name but no source is
    # an entry that cannot be installed, which is information, not silence.
    assert ms.normalize_anthropic_plugin(None) is None
    assert ms.normalize_anthropic_plugin([]) is None
    assert ms.normalize_anthropic_plugin({"description": "no name"}) is None
    nameless_source = ms.normalize_anthropic_plugin({"name": "orphan"})
    assert nameless_source is not None
    assert nameless_source["installable"] is False
    assert "no source" in nameless_source["note"]


def test_the_claude_directory_is_listed_and_filtered_locally():
    seen = []
    fetch = make_fetcher({"marketplace.json": ANTHROPIC_MARKETPLACE}, seen)
    result = ms.list_anthropic_plugins(fetch=fetch)

    assert result["total"] == 4
    assert [e["name"] for e in result["entries"]] == [
        "42crunch-api-security-testing", "agent-sdk-dev", "agentforce-adlc",
        "atomic-agents"]
    # Two requests, in this order: the repository tarball first, then the CDN. This
    # fixture only knows the CDN URL, so the archive path fails and the fallback
    # answers — which is exactly the order the source is built to try.
    assert len(seen) == 2, seen
    assert "codeload.github.com" in seen[0] and "marketplace.json" in seen[1]

    filtered = ms.list_anthropic_plugins(q="security", fetch=fetch)
    assert [e["name"] for e in filtered["entries"]] == ["42crunch-api-security-testing"]


def test_the_claude_directory_still_lists_when_the_cdn_is_unreachable():
    """jsDelivr failed live with SSL errors; the repository tarball did not.

    Three consecutive live loads of ``anthropic-plugins`` reported
    ``unreachable ([SSL: UNEXPECTED_EOF_WHILE_READING])`` while the tarball of that
    same repository measured 3.03 MB / 2.4 s / 460 files — so the archive is read
    first and the CDN is kept as the fallback, not as the only road.
    """
    ms.clear_cache()
    seen = []
    fetch = make_fetcher({
        "codeload.github.com": repo_tarball(
            {".claude-plugin/marketplace.json": json.dumps(ANTHROPIC_MARKETPLACE)},
            top="claude-plugins-official-main"),
        "marketplace.json": OSError("SSL: UNEXPECTED_EOF_WHILE_READING"),
    }, seen)

    result = ms.list_anthropic_plugins(fetch=fetch)

    assert result["total"] == 4
    assert [e["name"] for e in result["entries"]][0] == "42crunch-api-security-testing"
    assert not any("jsdelivr" in url for url in seen), seen


def test_resolving_a_claude_plugin_entry_returns_the_fetchable_location():
    fetch = make_fetcher({"marketplace.json": ANTHROPIC_MARKETPLACE})
    entry, error = ms.fetch_entry("anthropic-plugins", "agent-sdk-dev", fetch=fetch)
    assert error is None
    assert entry["repo"] == "anthropics/claude-plugins-official"
    assert entry["path"] == "plugins/agent-sdk-dev"

    entry, error = ms.fetch_entry("anthropic-plugins", "nope", fetch=fetch)
    assert entry is None and "no plugin named" in error


# --------------------------------------------------------------------------- anthropic skills


def test_the_claude_skills_directory_is_enumerated_with_its_frontmatter():
    fetch = make_fetcher({
        "repos/anthropics/skills/contents/skills": ANTHROPIC_SKILLS_DIR,
        "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD,
    })
    result = ms.list_anthropic_skills(fetch=fetch)

    assert result["total"] == 3
    names = [e["name"] for e in result["entries"]]
    assert names == ["academy-guide", "algorithmic-art", "brand-guidelines"]
    guide = result["entries"][0]
    assert guide["kind"] == "skill"
    # A folded `description: >` block, joined into one line: a line-based reader
    # would have produced ">" here.
    assert guide["description"].startswith("Stop and check this skill")
    assert "matching courses," in guide["description"]
    assert "\n" not in guide["description"]
    assert guide["raw_url"].endswith("/skills/academy-guide/SKILL.md")


def test_a_skill_whose_description_cannot_be_read_is_still_listed():
    fetch = make_fetcher({
        "repos/anthropics/skills/contents/skills": ANTHROPIC_SKILLS_DIR,
        "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD,
        "skills/brand-guidelines/SKILL.md": urllib.error.HTTPError(
            "u", 404, "Not Found", {}, None),
    })
    result = ms.list_anthropic_skills(fetch=fetch)

    assert len(result["entries"]) == 3
    missing = next(e for e in result["entries"] if e["name"] == "brand-guidelines")
    assert missing["installable"] is True  # the file is there; only the read failed
    assert "could not be read" in missing["note"]
    assert "could not be described" in result["note"]


def test_resolving_a_skill_checks_the_directory_rather_than_trusting_the_id():
    fetch = make_fetcher({"repos/anthropics/skills/contents/skills": ANTHROPIC_SKILLS_DIR})
    entry, error = ms.fetch_entry("anthropic-skills", "algorithmic-art", fetch=fetch)
    assert error is None and entry["path"] == "skills/algorithmic-art"

    entry, error = ms.fetch_entry("anthropic-skills", "not-a-skill", fetch=fetch)
    assert entry is None and "no skill named" in error


# --------------------------------------------------------------------------- cline kinds


def test_the_three_cline_kinds_do_not_collapse_into_each_other():
    fetch = make_fetcher({
        "contents/registry/mcps": CLINE_DIR,
        "contents/registry/plugins": CLINE_PLUGINS_DIR,
        "contents/registry/skills": CLINE_SKILLS_DIR,
        "registry/mcps/airtable/entry.json": CLINE_ENTRY,
        "registry/mcps/zeta/entry.json": {"id": "zeta", "install": {}},
        "registry/plugins/agent-browser/entry.json": CLINE_PLUGIN_ENTRY,
        "registry/plugins/agents-squad/entry.json": {
            "id": "agents-squad", "type": "plugin", "name": "Agents Squad",
            "tagline": "A squad of specialist agents",
            "install": {"args": ["agents-squad"]}},
        "registry/skills/amazon-location-service/entry.json": CLINE_SKILL_ENTRY,
    })

    servers = ms.search("cline", fetch=fetch)
    assert servers["total"] == 2
    assert all(e["kind"] == "mcp" for e in servers["entries"])
    assert [e["name"] for e in servers["entries"]] == ["airtable", "zeta"]

    plugins = ms.search("cline-plugins", fetch=fetch)
    assert [e["name"] for e in plugins["entries"]] == ["agent-browser", "agents-squad"]
    plugin = plugins["entries"][0]
    assert plugin["kind"] == "plugin"
    # Not offered as an MCP install, and the reason names the real install path.
    assert plugin["installable"] is False
    assert "Cline CLI" in plugin["note"] and "agent-browser" in plugin["note"]
    assert plugin["kind"] != "mcp"

    skills = ms.search("cline-skills", fetch=fetch)
    assert [e["name"] for e in skills["entries"]] == ["amazon-location-service"]
    skill = skills["entries"][0]
    assert skill["kind"] == "skill"
    assert skill["installable"] is False
    assert skill["name"] != "airtable"


def test_an_unknown_cline_kind_is_refused_with_the_known_ones():
    with pytest.raises(ValueError) as err:
        ms.list_cline("templates")
    assert "mcps" in str(err.value) and "plugins" in str(err.value)


def test_resolving_a_cline_plugin_returns_its_kind():
    fetch = make_fetcher({
        "registry/plugins/agent-browser/entry.json": CLINE_PLUGIN_ENTRY})
    entry, error = ms.fetch_entry("cline-plugins", "agent-browser", fetch=fetch)
    assert error is None
    assert entry["kind"] == "plugin" and entry["installable"] is False


# --------------------------------------------------------------------------- the repository tarball


def test_a_cline_directory_is_read_out_of_the_repository_tarball():
    """One download lists the directory *and* carries every entry file.

    Which is the whole point of the change: the contents API answered ``HTTP 403``
    on a live ``cline-skills`` listing because an anonymous caller gets 60 requests
    an hour, and the identical tarball answered 200.
    """
    seen = []
    fetch = archive_fetcher({"codeload.github.com/cline/marketplace": CLINE_TARBALL}, seen)

    result = ms.list_cline("mcps", fetch=fetch)
    assert result["ok"] is True and result["total"] == 1
    entry = result["entries"][0]
    assert entry["name"] == "airtable"
    assert entry["kind"] == "mcp"
    assert entry["transport"] == "http"
    assert entry["url"] == "https://mcp.airtable.com/mcp"
    assert entry["env_keys"] == ["AIRTABLE_TOKEN"]
    assert entry["installable"] is True
    # Read out of the real captured body: the tagline is what this shape puts in
    # `description`, and the homepage falls back to the `repo` beside it.
    assert entry["description"] == "Manage data in Airtable bases"
    assert entry["homepage"] == "https://github.com/Airtable/airtable-mcp-cli"

    skills = ms.list_cline("skills", fetch=fetch)
    assert skills["ok"] is True and skills["total"] == 2
    assert [e["name"] for e in skills["entries"]] == [
        "amazon-location-service", "skill-creator"]
    assert all(e["kind"] == "skill" for e in skills["entries"])
    # Not MCP servers, and said so rather than offered as an install.
    assert all(e["installable"] is False for e in skills["entries"])
    assert "Cline CLI" in skills["entries"][0]["note"]

    # Two listings, one download, and no per-entry request at all.
    assert seen == [CLINE_TARBALL_URL]
    assert not any("api.github.com" in u or "jsdelivr" in u for u in seen)


def test_the_three_cline_kinds_normalise_from_one_archive_without_collapsing():
    tarball = repo_tarball({
        "registry/mcps/airtable/entry.json": CLINE_MCP_ENTRY_JSON,
        "registry/plugins/agent-browser/entry.json": json.dumps(CLINE_PLUGIN_ENTRY),
        "registry/skills/skill-creator/entry.json": CLINE_SKILL_CREATOR_ENTRY_JSON,
    })
    fetch = archive_fetcher({"codeload.github.com/cline/marketplace": tarball})

    servers = ms.list_cline("mcps", fetch=fetch)
    plugins = ms.list_cline("plugins", fetch=fetch)
    skills = ms.list_cline("skills", fetch=fetch)

    assert [(e["name"], e["kind"]) for e in servers["entries"]] == [("airtable", "mcp")]
    assert [(e["name"], e["kind"]) for e in plugins["entries"]] == [
        ("agent-browser", "plugin")]
    assert [(e["name"], e["kind"]) for e in skills["entries"]] == [
        ("skill-creator", "skill")]
    # A plugin is never offered as an MCP install, whichever road it was read from.
    assert plugins["entries"][0]["installable"] is False
    assert plugins["source"] == "cline-plugins"
    assert skills["source"] == "cline-skills"


def test_a_tarball_that_cannot_be_read_falls_back_to_the_contents_api():
    seen = []
    fetch = make_fetcher({
        # The live failure, replayed: the rate limit is a 403 on the listing.
        "codeload.github.com": urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
        "contents/registry/skills": CLINE_SKILLS_DIR,
        "registry/skills/amazon-location-service/entry.json": CLINE_SKILL_ENTRY,
    }, seen)
    result = ms.list_cline("skills", fetch=fetch)

    assert result["ok"] is True
    assert result["total"] == 1
    assert [e["name"] for e in result["entries"]] == ["amazon-location-service"]
    # The slow road is named, and so is the reason for taking it: a fallback that
    # looks exactly like the primary route is how a rate limit turns into "this
    # marketplace is empty".
    assert "403" in result["note"]
    assert "contents API" in result["note"] and "60 anonymous requests" in result["note"]
    assert any("api.github.com" in u for u in seen)


def test_when_the_archive_and_the_contents_api_both_fail_the_reason_is_reported():
    fetch = make_fetcher({
        "codeload.github.com": urllib.error.URLError("archive is unreachable"),
        "contents/registry/mcps": urllib.error.HTTPError(
            "u", 403, "rate limit exceeded", {}, None),
    })
    result = ms.list_cline("mcps", fetch=fetch)

    assert result["ok"] is False
    assert result["entries"] == []
    # Not 0: zero is a number the upstream could have said, and this is not that.
    assert result["total"] is None
    assert "archive is unreachable" in result["error"]
    assert "403" in result["error"]
    # Both reasons, so the page can say what actually happened.
    assert "no repository archive" in result["error"]
    assert "no contents listing" in result["error"]
    # And the same answer through the facade: "could not ask" never renders as
    # "there is nothing there".
    through_search = ms.search("cline", fetch=fetch)
    assert through_search["ok"] is False and through_search["entries"] == []
    assert through_search["total"] is None and through_search["error"]


def test_an_archive_member_that_would_escape_the_tree_is_skipped_not_smoothed_over():
    tarball = repo_tarball({
        "registry/mcps/airtable/entry.json": CLINE_MCP_ENTRY_JSON,
        "../escape.txt": b"outside",
        "registry/mcps/../../escape2.json": b"outside",
        "/etc/passwd": b"outside",
    })
    files = ms._repo_archive("cline", "marketplace", "main",
                             archive_fetcher({
                                 "codeload.github.com/cline/marketplace": tarball}))

    assert "registry/mcps/airtable/entry.json" in files
    # Dropped, not normalised into a safe-looking path: a member that climbs out
    # of the tree is not a member this build has any use for.
    assert not any(".." in rel.split("/") for rel in files)
    assert not [rel for rel in files if "escape" in rel or "passwd" in rel]
    # Never extracted either — the bytes are read into memory, so there is no
    # directory anything could have been written into.
    assert files["registry/mcps/airtable/entry.json"].decode("utf-8") == CLINE_MCP_ENTRY_JSON


def test_the_tarball_is_downloaded_once_for_the_listing_the_count_and_an_install():
    seen = []
    fetch = archive_fetcher({"codeload.github.com/cline/marketplace": CLINE_TARBALL}, seen)

    assert ms.list_cline("skills", fetch=fetch)["total"] == 2
    assert ms.total_count("cline-skills", fetch=fetch) == 2
    # A different kind out of the same repository: the tarball is cached by URL,
    # so the second listing costs nothing.
    assert ms.total_count("cline", fetch=fetch) == 1
    assert seen == [CLINE_TARBALL_URL]


def test_an_anthropic_skills_listing_comes_from_the_archive_with_its_descriptions():
    tarball = repo_tarball({
        "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD,
        "skills/brand-guidelines/SKILL.md": ("---\nname: brand-guidelines\n"
                                             "description: Colours, type and voice.\n---\n\n# x\n"),
        "README.md": "not a skill, and not named like one\n",
    }, top="skills-main")
    seen = []
    fetch = archive_fetcher({"codeload.github.com/anthropics/skills": tarball}, seen)

    result = ms.list_anthropic_skills(fetch=fetch)

    assert result["ok"] is True and result["total"] == 2
    assert [e["name"] for e in result["entries"]] == ["academy-guide", "brand-guidelines"]
    guide = result["entries"][0]
    assert guide["kind"] == "skill" and guide["installable"] is True
    # The description came out of the same download, folded block and all.
    assert guide["description"].startswith("Stop and check this skill")
    assert "\n" not in guide["description"]
    assert result["note"] is None

    # One request for the directory *and* every SKILL.md, and the contents API —
    # the endpoint with the 60-an-hour limit — is never called.
    assert len(seen) == 1 and "anthropics/skills" in seen[0]
    assert not any("api.github.com" in u or "jsdelivr" in u for u in seen)
    # Resolving one for an install reads the same index, so it agrees with it.
    entry, error = ms.fetch_entry("anthropic-skills", "academy-guide", fetch=fetch)
    assert error is None and entry["path"] == "skills/academy-guide"


# --------------------------------------------------------------------------- totals


def test_total_count_reports_only_what_the_upstream_declared():
    fetch = make_fetcher({
        "registry.smithery.ai/servers?": SMITHERY_LIST,
        "contents/registry/mcps": CLINE_DIR,
        "contents/registry/plugins": CLINE_PLUGINS_DIR,
        "contents/registry/skills": CLINE_SKILLS_DIR,
        "marketplace.json": ANTHROPIC_MARKETPLACE,
        "repos/anthropics/skills/contents/skills": ANTHROPIC_SKILLS_DIR,
    })
    assert ms.total_count("smithery", fetch=fetch) == 17062
    assert ms.total_count("cline", fetch=fetch) == 2
    assert ms.total_count("cline-plugins", fetch=fetch) == 2
    assert ms.total_count("anthropic-plugins", fetch=fetch) == 4
    assert ms.total_count("anthropic-skills", fetch=fetch) == 3
    # The registry publishes a cursor, not a count. Inventing "4,000" here would
    # be a number nobody can check.
    assert ms.total_count("registry", fetch=fetch) is None
    assert ms.total_count("unknown-source", fetch=fetch) is None


def test_total_count_answers_none_rather_than_raising_when_it_cannot_ask():
    def boom(url, timeout=None):
        raise urllib.error.URLError("no route to host")

    assert ms.total_count("smithery", fetch=boom) is None
    assert ms.total_count("cline", fetch=boom) is None
    assert ms.total_count("anthropic-plugins", fetch=boom) is None


def test_an_unreachable_new_source_reports_an_error_rather_than_an_empty_list():
    # One needle per source: the first URL that source fetches.
    first_url = {
        "smithery": "registry.smithery.ai",
        "cline-plugins": "contents/registry/plugins",
        "anthropic-plugins": "marketplace.json",
        "anthropic-skills": "repos/anthropics/skills/contents/skills",
    }
    for source, needle in first_url.items():
        result = ms.search(source, fetch=make_fetcher(
            {needle: urllib.error.URLError("network is unreachable")}))
        assert result["ok"] is False, source
        assert result["entries"] == [], source
        assert "unreachable" in result["error"], source
        # Not 0: zero is a number the upstream could have said.
        assert result["total"] is None, source


# --------------------------------------------------------------------------- the API contract


def _market_fetcher():
    return make_fetcher({
        "registry.modelcontextprotocol.io": REGISTRY_FLAT,
        "registry.smithery.ai/servers?": SMITHERY_LIST,
        "registry.smithery.ai/servers/brave": SMITHERY_DETAIL,
        "contents/registry/mcps": CLINE_DIR,
        "contents/registry/plugins": CLINE_PLUGINS_DIR,
        "registry/mcps/airtable/entry.json": CLINE_ENTRY,
        "registry/plugins/agent-browser/entry.json": CLINE_PLUGIN_ENTRY,
        "marketplace.json": ANTHROPIC_MARKETPLACE,
        "repos/anthropics/skills/contents/skills": ANTHROPIC_SKILLS_DIR,
        "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD,
    })


def _market_archives():
    """The repository tarballs the API tests serve, built from the captured bodies.

    The API reads the tarball *before* it reads anything else, so a test that
    left this transport real would go online from the machine it runs on. An
    offline test that quietly is not one is worse than a test that fails.
    """
    return {
        "codeload.github.com/cline/marketplace": CLINE_TARBALL,
        "codeload.github.com/anthropics/skills": repo_tarball({
            "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD,
            "skills/algorithmic-art/SKILL.md": ACADEMY_GUIDE_SKILL_MD,
            "skills/brand-guidelines/SKILL.md": ACADEMY_GUIDE_SKILL_MD,
        }, top="skills-main"),
    }


def test_the_api_sources_carry_the_fields_the_ui_needs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.app as app_module
    from kairos.extensions import market_sources as ms_mod

    monkeypatch.setattr(ms_mod, "_http_json", _market_fetcher())
    monkeypatch.setattr(ms_mod, "_http_text", make_fetcher({
        "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD}))
    monkeypatch.setattr(ms_mod, "_http_bytes", archive_fetcher(_market_archives()))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ms_mod.clear_cache()

    with TestClient(app_module.app) as client:
        sources = client.get("/api/extensions/market/sources").json()["sources"]

    by_id = {s["id"]: s for s in sources}
    assert set(by_id) == {"registry", "smithery", "cline", "cline-plugins",
                          "cline-skills", "anthropic-plugins", "anthropic-skills"}
    for source in sources:
        assert source["id"] and source["label"]
        assert source["kind"] in ("mcp", "plugin", "skill", "mixed")
        assert isinstance(source["needs_query"], bool)
        assert source["total"] is None or isinstance(source["total"], int)
        assert source["note"] is None or isinstance(source["note"], str)
    # Measured, not declared: a source that answers gets its number.
    assert by_id["smithery"]["total"] == 17062
    # ...and one that has none stays null rather than becoming a zero.
    assert by_id["registry"]["total"] is None
    assert by_id["cline-plugins"]["kind"] == "plugin"
    assert by_id["anthropic-skills"]["kind"] == "skill"
    assert by_id["smithery"]["needs_query"] is True


def test_the_api_search_carries_items_total_note_and_error(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.app as app_module
    from kairos.extensions import market_sources as ms_mod

    monkeypatch.setattr(ms_mod, "_http_json", _market_fetcher())
    monkeypatch.setattr(ms_mod, "_http_text", make_fetcher({
        "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD}))
    monkeypatch.setattr(ms_mod, "_http_bytes", archive_fetcher(_market_archives()))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ms_mod.clear_cache()

    with TestClient(app_module.app) as client:
        got = client.get("/api/extensions/market/search",
                         params={"source": "smithery", "limit": 1})
        assert got.status_code == 200
        body = got.json()
        assert body["ok"] is True and body["error"] is None
        assert body["source"] == "smithery"
        assert body["total"] == 17062
        assert body["items"] == body["entries"] and len(body["items"]) == 1
        assert body["items"][0]["url"] == "https://brave.run.tools"
        assert body["items"][0]["kind"] == "mcp"

        # A source that answers with nothing at all still answers.
        got = client.get("/api/extensions/market/search",
                         params={"source": "cline-plugins", "q": "nothing-matches"})
        empty = got.json()
        assert empty["ok"] is True and empty["error"] is None
        assert empty["items"] == [] and empty["total"] == 0

        # ...and that must not look like a source that could not be reached.
        got = client.get("/api/extensions/market/search", params={"source": "nope"})
        broken = got.json()
        assert broken["ok"] is False and broken["error"] is not None
        assert broken["items"] == [] and broken["total"] is None


def test_the_api_install_answers_with_kind_path_and_warnings(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.app as app_module
    from kairos.extensions import market_sources as ms_mod

    monkeypatch.setattr(ms_mod, "_http_json", _market_fetcher())
    monkeypatch.setattr(ms_mod, "_http_text", make_fetcher({
        "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD}))
    monkeypatch.setattr(ms_mod, "_http_bytes", archive_fetcher(_market_archives()))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ms_mod.clear_cache()

    kairos = tmp_path / ".kairos"
    with TestClient(app_module.app) as client:
        got = client.post("/api/extensions/market/install",
                          json={"source": "smithery", "id": "brave"})
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["ok"] is True and body["changed"] is True
        assert body["kind"] == "mcp"
        assert body["installed_path"] == str(kairos / "mcp.yaml")
        assert body["warnings"] == []
        written = (kairos / "mcp.yaml").read_text(encoding="utf-8")
        assert "url: https://brave.run.tools" in written

        # Idempotent, like every other install in this project.
        again = client.post("/api/extensions/market/install",
                            json={"source": "smithery", "id": "brave"})
        assert again.json()["changed"] is False

        # A listing that is not an MCP server is refused with its own reason.
        got = client.post("/api/extensions/market/install",
                          json={"source": "cline-plugins", "id": "agent-browser"})
        assert got.status_code == 400
        assert "Cline CLI" in got.json()["detail"]


def test_the_api_installs_a_skill_into_the_user_skills_scope(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.app as app_module
    from kairos.extensions import market_sources as ms_mod

    monkeypatch.setattr(ms_mod, "_http_json", _market_fetcher())
    monkeypatch.setattr(ms_mod, "_http_text", make_fetcher({
        "skills/academy-guide/SKILL.md": ACADEMY_GUIDE_SKILL_MD}))
    monkeypatch.setattr(ms_mod, "_http_bytes", archive_fetcher(_market_archives()))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ms_mod.clear_cache()

    with TestClient(app_module.app) as client:
        got = client.post("/api/extensions/market/install",
                          json={"source": "anthropic-skills", "id": "academy-guide"})
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["kind"] == "skill" and body["changed"] is True
        assert body["installed_path"].endswith("SKILL.md")
        assert (tmp_path / ".kairos" / "skills" / "academy-guide"
                / "SKILL.md").exists()
        assert body["warnings"] == []

        assert client.post("/api/extensions/market/install",
                           json={"source": "anthropic-skills",
                                 "id": "academy-guide"}).json()["changed"] is False


def test_a_skill_with_no_frontmatter_is_refused_rather_than_written(tmp_path):
    """The loader skips those, so installing one would look installed and not be."""
    from kairos.extensions_install import install_skill

    entry = ms.normalize_anthropic_skill("no-frontmatter")
    with pytest.raises(ValueError) as err:
        install_skill(entry, user_dir=tmp_path, content="just a body\n")
    assert "frontmatter" in str(err.value)
    assert not (tmp_path / "skills").exists()


# ---------------------------------------------------------------------------


def test_the_three_transports_all_use_the_retrying_open():
    """One place to fix it, so it is one place to check."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1]
            / "kairos" / "extensions" / "market_sources.py").read_text(encoding="utf-8")
    assert text.count("with _open(req, timeout=timeout) as r:") == 3
    assert "ProxyHandler({})" in text


# ---------------------------------------------------------------------------
# The proxy Windows hands to every process
# ---------------------------------------------------------------------------


class _Reply:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _openers(outcomes: dict, calls: list):
    """build_opener(ProxyHandler({})) is the direct path, build_opener() the proxy one."""
    def build(*handlers):
        kind = "direct" if handlers else "proxy"
        calls.append(kind)

        class Opener:
            def open(self, req, timeout=None):
                outcome = outcomes[kind]
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        return Opener()

    return build


def test_a_fetch_goes_direct_first(monkeypatch):
    """Most machines need no proxy, and a stale registry entry must not decide."""
    calls = []
    monkeypatch.setattr(ms.urllib.request, "build_opener",
                        _openers({"direct": _Reply(b'{"ok": true}')}, calls))

    assert ms._http_json("https://example.invalid/x") == {"ok": True}
    assert calls == ["direct"], calls


def test_a_refused_direct_connection_falls_back_to_the_proxy(monkeypatch):
    """A proxy the user actually runs is still how some networks work."""
    calls = []
    monkeypatch.setattr(ms.urllib.request, "build_opener",
                        _openers({"direct": urllib.error.URLError(OSError("refused")),
                                  "proxy": _Reply(b'{"ok": true}')}, calls))

    assert ms._http_json("https://example.invalid/x") == {"ok": True}
    assert calls == ["direct", "proxy"], calls


def test_when_both_routes_fail_the_direct_error_is_raised(monkeypatch):
    """The message a user reads must be about their network, not a last attempt."""
    calls = []
    monkeypatch.setattr(ms.urllib.request, "build_opener",
                        _openers({"direct": urllib.error.URLError(OSError("direct reason")),
                                  "proxy": urllib.error.URLError(OSError("proxy reason"))},
                                 calls))

    with pytest.raises(urllib.error.URLError) as err:
        ms._http_json("https://example.invalid/x")
    assert "direct reason" in str(err.value)
    assert calls == ["direct", "proxy"], calls


def test_the_three_transports_all_use_the_retrying_open():
    """One place to fix it, so it is one place to check."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1]
            / "kairos" / "extensions" / "market_sources.py").read_text(encoding="utf-8")
    assert text.count("with _open(req, timeout=timeout) as r:") == 3
    assert "ProxyHandler({})" in text
