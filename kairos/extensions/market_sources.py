"""Remote extension marketplaces, normalised into this project's entry shape.

Kairos ships a curated registry (``kairos/extensions/mcps.json``, 26 servers) and
that is deliberate — a list someone has actually run is worth more than a list of
everything. But "we picked 26" is not an answer to "is there anything else?", and
before this module the only way to see more was to open another tab and
hand-write YAML.

Two remote sources are wired up, and only two, because these are the ones that
survive being checked rather than read about:

``registry``
    The official MCP registry (``registry.modelcontextprotocol.io``). Public
    OpenAPI, no authentication, and — the part that matters — entries that carry
    *install information*: an npm package plus ``runtimeHint: npx`` for a local
    stdio server, or a ``remotes[].url`` for a hosted one. Reached through a real
    ``search`` parameter, so the query goes to the server rather than being
    filtered here.

``cline``
    ``github.com/cline/marketplace``, whose entries are one ``entry.json`` per
    id with an explicit ``install.args[]`` / ``install.env[]``. It is a manifest
    repository, so enumerating it means reading a directory — and that directory
    is read out of the repository *tarball* (``codeload.github.com``), which
    returns the listing and every entry file in one unthrottled request, rather
    than out of the GitHub contents API, which answers 60 anonymous requests an
    hour and answered ``HTTP 403`` on a live ``cline-skills`` listing. The
    contents API is kept as the fallback, and the reason it was needed is put in
    the response. The entry files themselves also come out of the tarball; only
    a per-entry read that the tarball cannot serve falls back to jsDelivr, which
    is used because ``raw.githubusercontent.com`` is unreliable from mainland
    China while jsDelivr answers 200.

Everything here is read-only and best-effort: a source that cannot be reached
produces ``{"ok": False, "error": ...}`` and the page says so. A marketplace that
silently shows an empty list when the network is down is the same defect as a
server that is listed but cannot start.

Five more sources were added later, and each one earned its place by being
*checked* rather than read about:

``smithery``
    ``registry.smithery.ai``. 17,000 hosted servers, and the list endpoint
    carries no endpoint URL — only ``remote: true``. The connect URL lives on
    ``/servers/<qualifiedName>``, so it is fetched per entry, and only for the
    entries a page is about to show. An entry whose detail cannot be read is
    still returned, marked not installable and told why: dropping it would make
    a network hiccup look like a server that does not exist.

``cline-plugins`` / ``cline-skills``
    The same manifest repository as ``cline``, two more directories. These are
    **not MCP servers** — a plugin is a Cline CLI bundle and a skill is a
    SKILL.md pack — so they carry their own kind and are never offered as an MCP
    install.

``anthropic-plugins``
    ``anthropics/claude-plugins-official``: 311 plugins whose components are
    found by *directory convention* (``commands/``, ``agents/``, ``skills/``,
    ``hooks/hooks.json``, ``.mcp.json``) rather than declared in the manifest —
    exactly the convention :mod:`kairos.plugins` already loads. Installing one
    means converting it; that work lives in
    :mod:`kairos.extensions.claude_plugin`.

``anthropic-skills``
    ``anthropics/skills``, the SKILL.md-format collection this project already
    adapted 59 skills from, so the format is proven portable here.

``total_count`` reports only numbers an upstream actually declared (or that a
listing it served actually contained). The registry publishes a page cursor and
no total, so it returns ``None`` rather than a guess.

The normalised shape is exactly what ``kairos.extensions_install.install_entry``
consumes, so a remote entry installs through the same path as a curated one:

    {name, category, transport, command, args, url, env_keys, description, source}
"""

from __future__ import annotations

import io
import json
import re
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

USER_AGENT = "kairos-code-marketplace/1.0 (+https://github.com/Kairos-ai-agent/kairos-code)"

REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
CLINE_REPO = "cline/marketplace"
#: The *fallback* listing for the Cline directories, kept because the archive can
#: fail — not because it is the primary road. An anonymous caller gets 60 requests
#: an hour and the live bug was an ``HTTP 403`` from exactly this URL.
CLINE_API = "https://api.github.com/repos/%s/contents" % CLINE_REPO
CLINE_CDN = "https://cdn.jsdelivr.net/gh/%s@main" % CLINE_REPO

#: The branch both Cline and ``anthropics/skills`` are read from. The archive is
#: cached by its full URL, so a different ref cannot be served out of this one's
#: cache entry.
CLINE_REF = "main"

#: A repository's tarball. One request returns the listing *and* the contents of
#: every file in it, and — the part that matters — codeload is not the API:
#: anonymous GitHub API calls are capped at 60 an hour, which is how a live
#: ``list_cline("skills")`` came back ``HTTP 403`` while the repository itself was
#: perfectly readable. Measured against ``anthropics/claude-plugins-official``:
#: 3.03 MB, 2.4 s, 460 files — the same file set the (rate-limited) git-trees API
#: reports, where the jsDelivr equivalent listing answered 337.
ARCHIVE_URL = "https://codeload.github.com/%s/tar.gz/%s"

SMITHERY_BASE = "https://registry.smithery.ai"
SMITHERY_SERVERS = SMITHERY_BASE + "/servers"

#: The Cline repository holds three listings side by side, all written in the
#: same ``entry.json`` shape. Only ``mcps`` is an MCP server: a plugin is a Cline
#: CLI bundle and a skill is a SKILL.md pack, which is why the kind travels with
#: the entry instead of being assumed from the source.
CLINE_KINDS: Dict[str, str] = {"mcps": "mcp", "plugins": "plugin", "skills": "skill"}

#: Anthropic's official plugin marketplace. Fetched through jsDelivr for the same
#: reason Cline is: ``raw.githubusercontent.com`` does not answer from this
#: network while jsDelivr does.
ANTHROPIC_MARKETPLACE_REPO = "anthropics/claude-plugins-official"
ANTHROPIC_MARKETPLACE_REF = "main"
ANTHROPIC_MARKETPLACE_URL = (
    "https://cdn.jsdelivr.net/gh/%s@%s/.claude-plugin/marketplace.json"
    % (ANTHROPIC_MARKETPLACE_REPO, ANTHROPIC_MARKETPLACE_REF))

#: ``anthropics/skills`` — the SKILL.md collection this project already adapted
#: skills from, so the format is known to load here.
ANTHROPIC_SKILLS_REPO = "anthropics/skills"
ANTHROPIC_SKILLS_REF = "main"
ANTHROPIC_SKILLS_DIR = "skills"
#: The *fallback* listing for ``anthropics/skills``, kept for the same reason as
#: ``CLINE_API``: the repository tarball is the primary road.
ANTHROPIC_SKILLS_API = "https://api.github.com/repos/%s/contents/%s" % (
    ANTHROPIC_SKILLS_REPO, ANTHROPIC_SKILLS_DIR)


def jsdelivr(repo: str, ref: Optional[str], path: str) -> str:
    """A jsDelivr URL for one file in a GitHub repository.

    ``ref`` may be a branch, a tag or a commit sha — the sha is the stable
    choice when the upstream gives one, because a branch moves under the user.
    With no ref the URL omits the version, which jsDelivr resolves to the
    repository's default branch.
    """
    base = "https://cdn.jsdelivr.net/gh/%s" % repo
    if ref:
        base += "@%s" % ref
    return "%s/%s" % (base, str(path).lstrip("/"))


def _anthropic_marketplace(fetch: Optional[Callable[..., Any]] = None,
                           *, timeout: float = 60.0) -> Any:
    """The Claude plugin marketplace, from the repository tarball when it can be read.

    jsDelivr used to be the only road to this file, until it stopped answering
    from this network: three consecutive live loads of ``anthropic-plugins``
    returned ``unreachable ([SSL: UNEXPECTED_EOF_WHILE_READING])`` while the
    tarball for that same repository measured 3.03 MB / 2.4 s / 460 files. The CDN
    stays as the fallback — it is not removed, it is no longer the only way in.

    Both reasons are reported when both roads fail, for the rule this file repeats
    everywhere: a source that cannot be reached must never look like a source with
    nothing in it.
    """
    def read() -> Any:
        owner, repo = ANTHROPIC_MARKETPLACE_REPO.split("/", 1)
        archive_problem = "the archive was not readable"
        try:
            files = _repo_archive(owner, repo, ANTHROPIC_MARKETPLACE_REF, fetch,
                                  timeout=timeout)
            body = files.get(".claude-plugin/marketplace.json")
        except Exception as exc:  # noqa: BLE001 - the CDN below is the fallback
            archive_problem = "%s: %s" % (type(exc).__name__, exc)
        else:
            if body is not None:
                return json.loads(body.decode("utf-8", "replace"))
            archive_problem = "the archive holds no .claude-plugin/marketplace.json"

        getter = _resolve_fetcher(fetch)
        try:
            return getter(ANTHROPIC_MARKETPLACE_URL, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - both roads are closed; say both
            raise ValueError(
                "the Claude plugin marketplace could not be read: no archive (%s) "
                "and no CDN (%s)" % (archive_problem, exc)) from exc

    return _cached("anthropic:marketplace", read)


#: How long a fetched page may be reused. The marketplaces change slowly and
#: every fetch is a request the user did not ask for.
CACHE_TTL_S = 300.0

#: Bounds. A page the user is looking at does not need to be 30,000 entries.
MAX_LIMIT = 100
DEFAULT_LIMIT = 30
#: Detail fetches for the Cline source (one request per id). Capped so a page
#: load cannot turn into 149 requests.
DETAIL_WORKERS = 8

SOURCES: Dict[str, Dict[str, Any]] = {
    "registry": {
        "label": "Official MCP Registry",
        "homepage": "https://registry.modelcontextprotocol.io",
        "kind": "mcp",
        "description": (
            "The reference registry. Public API, no sign-in, and entries carry "
            "the package or URL needed to actually start the server."
        ),
        "installable": True,
        # 4,000+ servers behind a server-side ``search``: a bare page is an
        # arbitrary slice, so the page asks for a query first.
        "needs_query": True,
        "note": "the API reports a page cursor, not a total",
    },
    "smithery": {
        "label": "Smithery",
        "homepage": "https://smithery.ai",
        "kind": "mcp",
        "description": (
            "The largest hosted MCP directory. The list endpoint carries no "
            "endpoint URL, so the detail of each entry on the page is fetched "
            "one at a time."
        ),
        "installable": True,
        "needs_query": True,
        "note": "hosted servers; the connect URL comes from a per-entry detail fetch",
    },
    "cline": {
        "label": "Cline marketplace",
        "homepage": "https://github.com/cline/marketplace",
        "kind": "mcp",
        "description": (
            "A manifest repository with explicit install arguments. The listing "
            "and the entry files come out of the repository archive in one "
            "request, so it cannot be rate limited."
        ),
        "installable": True,
        "needs_query": False,
        "note": None,
    },
    "cline-plugins": {
        "label": "Cline plugins",
        "homepage": "https://github.com/cline/marketplace/tree/main/registry/plugins",
        "kind": "plugin",
        "description": (
            "Cline's plugin listings. They are Cline CLI bundles, not MCP "
            "servers, so they are listed for discovery and not offered as an "
            "MCP install."
        ),
        "installable": False,
        "needs_query": False,
        "note": "not installable here: the Cline CLI installs these",
    },
    "cline-skills": {
        "label": "Cline skills",
        "homepage": "https://github.com/cline/marketplace/tree/main/registry/skills",
        "kind": "skill",
        "description": (
            "Cline's SKILL.md packs. Listed for discovery; the manifest points "
            "at the Cline CLI rather than at a file this build can fetch."
        ),
        "installable": False,
        "needs_query": False,
        "note": "not installable here: the Cline CLI installs these",
    },
    "anthropic-plugins": {
        "label": "Claude plugins (official)",
        "homepage": "https://github.com/anthropics/claude-plugins-official",
        "kind": "plugin",
        "description": (
            "Anthropic's official plugin directory. Installing one converts it "
            "for Kairos: commands, agents, skills and hooks are copied, the "
            "plugin-root variable is rewritten, and files that cannot be "
            "rewritten are skipped and named."
        ),
        "installable": True,
        "needs_query": False,
        "note": "install converts the plugin; a plugin with no licence we can verify is refused",
    },
    "anthropic-skills": {
        "label": "Claude skills (anthropics/skills)",
        "homepage": "https://github.com/anthropics/skills",
        "kind": "skill",
        "description": (
            "Anthropic's SKILL.md collection. Installing one writes it into the "
            "user's skills scope, the same layout the loader already reads."
        ),
        "installable": True,
        "needs_query": False,
        "note": "SKILL.md packs; installed into the user skills scope",
    },
}


# --------------------------------------------------------------------------- fetching


def _open(req: "urllib.request.Request", timeout: float):
    """Open ``req``: direct first, and only then through the machine's proxy.

    Windows hands every process the proxy in the registry, and a leftover entry
    (``127.0.0.1:7897`` after the tool that owned it stopped, ``ProxyEnable=1``)
    refuses every connection with ``WinError 10061``. That made a marketplace
    source report itself unreachable and a user saw empty plugins and skills
    tabs, with the reason sitting in a registry key they had no reason to know
    about.

    So the direct connection is tried first: for most machines it is simply the
    right one, and a proxy the user actually runs answers on the second attempt
    (a dead one costs one refused connection instead of every request). The
    failure that matters is reported as a warning, because a person reading the
    console needs to know which of the two it was.
    """
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        return direct.open(req, timeout=timeout)
    except Exception as first:  # noqa: BLE001
        proxies = urllib.request.getproxies()
        if not proxies:
            raise
        logger.warning(
            "market fetch to %s failed directly (%s); retrying through the "
            "configured proxy %s", req.full_url, getattr(first, "reason", first),
            proxies)
        try:
            return urllib.request.build_opener().open(req, timeout=timeout)
        except Exception:  # noqa: BLE001
            raise first


def _http_json(url: str, timeout: float = 20.0) -> Any:
    """GET ``url`` and parse JSON. Raises on any failure — callers decide what a
    failure means for them, because the answer differs per source."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with _open(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body)


def _http_text(url: str, timeout: float = 20.0) -> str:
    """GET ``url`` and return the body as text.

    Needed because half of what is fetched here is not JSON: a plugin's
    ``SKILL.md``, a ``LICENSE``, a shell script inside a ``hooks/`` directory.
    Kept next to :func:`_http_json` so a test that replaces one transport
    replaces the other the same way.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/plain, text/markdown, */*",
    })
    with _open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _http_bytes(url: str, timeout: float = 30.0) -> bytes:
    """GET ``url`` and return the body as bytes, undecoded.

    Needed for an archive: a ``.tar.gz`` is neither JSON nor text, and decoding it
    to replace the undecodable bytes would corrupt it. Lives here for the same
    reason as :func:`_http_text` — one place a test replaces to take the network
    out of a run.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/octet-stream, */*",
    })
    with _open(req, timeout=timeout) as r:
        return r.read()


def _resolve_fetcher(fetch: Optional[Callable[..., Any]],
                     default: Optional[Callable[..., Any]] = None) -> Callable[..., Any]:
    """The getter to use for this call.

    Deliberately resolved at call time rather than bound as a default argument:
    a default is captured when the module is imported, which makes the transport
    impossible to replace — by a test, a proxy, or a future cache — without
    editing the code that uses it. ``default`` is only consulted when the caller
    passed nothing, so a JSON endpoint and a text endpoint can each have their
    own transport while both stay replaceable.
    """
    if fetch is not None:
        return fetch
    return default if default is not None else _http_json


_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, produce: Callable[[], Any]) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    value = produce()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    """Drop every cached page. Used by tests and by an explicit refresh."""
    with _cache_lock:
        _cache.clear()


# --------------------------------------------------------------------------- archives


def _archive_members(blob: bytes, url: str) -> Dict[str, bytes]:
    """``{path within the repository: bytes}`` out of a GitHub tarball.

    A codeload tarball names every member ``<repo>-<ref>/<path>``, so the
    top-level directory is dropped and what is left is relative to the
    repository root — the same spelling a path elsewhere in this module uses.

    Defensive on purpose, because this is an untrusted download that is never
    written to disk:

    * only regular files are read, so a symlink or a device node in the archive
      cannot point anywhere and cannot be followed;
    * a member whose relative path has a ``..`` component is skipped outright
      rather than normalised into something that looks safe;
    * members are read into memory and never extracted, so there is no
      extraction step for a hostile path to escape through.
    """
    files: Dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            name = member.name
            while name.startswith("./"):
                name = name[2:]
            # "<repo>-<ref>/<path>": drop the top-level directory only, so a
            # path that starts with "/" cannot survive as an absolute one.
            _top, sep, inside = name.partition("/")
            if not sep or not inside:
                continue
            parts = inside.split("/")
            if any(part in ("", "..") for part in parts):
                continue
            handle = tf.extractfile(member)
            if handle is not None:
                files["/".join(parts)] = handle.read()
    if not files:
        raise ValueError("the archive at %s holds no readable file" % url)
    return files


def _repo_archive(owner: str, repo: str, ref: Optional[str] = None,
                  fetch: Optional[Callable[..., Any]] = None, *,
                  timeout: float = 60.0) -> Dict[str, bytes]:
    """A GitHub repository's files as ``{relative path: bytes}``.

    Read from the repository tarball, which is the only listing measured to be
    both *complete* and *unthrottled*:

    * the contents API is authoritative but anonymous callers get 60 requests an
      hour — a live ``cline-skills`` enumeration answered ``HTTP 403``;
    * jsDelivr's file listing is not a listing at all: whole directories of the
      Claude plugin marketplace were missing from it (``claude-security`` 42
      files, 0 through the CDN), so a build that trusted it would install an
      empty plugin and report success.

    One download returns the listing *and* every file's contents, so a page load
    costs one request instead of one per entry, and it cannot be rate limited.

    Cached by URL: the same tarball is reused by the listing, the per-entry read
    and the count, so ``list_cline`` followed by an install makes no second
    request. ``fetch`` is resolved when the call is made, never bound as a
    default, so one injected transport takes the network out of every path here.
    """
    slug = "%s/%s" % (str(owner or "").strip("/ "), str(repo or "").strip("/ "))
    if not slug.strip("/"):
        raise ValueError("no repository given")
    url = ARCHIVE_URL % (slug.strip("/ "), urllib.parse.quote(str(ref or "HEAD"), safe=""))
    getter = _resolve_fetcher(fetch, _http_bytes)
    blob = _cached("repo-archive:%s" % url, lambda: getter(url, timeout=timeout))
    if not isinstance(blob, (bytes, bytearray)):
        raise ValueError("expected a tarball from %s, got %s"
                         % (url, type(blob).__name__))
    return _archive_members(bytes(blob), url)


def _resolve_fetchers(fetch: Optional[Callable[..., Any]],
                      archive_fetch: Optional[Callable[..., Any]] = None
                      ) -> Tuple[Callable[..., Any], Callable[..., Any]]:
    """The ``(file, archive)`` transports to use for this call.

    Resolved at call time like every other transport here, and an injected
    ``fetch`` is reused for the archive when no explicit one was passed — the
    same rule :mod:`kairos.extensions.claude_plugin` follows, and for the same
    reason: a test that takes the network out of a run has to take it out of
    *both* paths, or the archive quietly goes online instead.
    """
    if archive_fetch is not None:
        return _resolve_fetcher(fetch), archive_fetch
    if fetch is not None:
        return _resolve_fetcher(fetch), fetch
    return _resolve_fetcher(None), _http_bytes


# --------------------------------------------------------------------------- registry


def normalize_registry_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """One entry from the official registry → this project's entry shape.

    The registry serves entries in two shapes: a bare object, and one wrapped in
    ``{"server": {...}}`` (both are live — the wrapper appeared with a schema
    version bump). Both are accepted here, because depending on which one the
    server happens to send today is not a property worth having.
    """
    if not isinstance(raw, dict):
        return None
    entry = raw.get("server") if isinstance(raw.get("server"), dict) else raw

    name = str(entry.get("name") or "").strip()
    if not name:
        return None

    # The registry's `name` is reverse-DNS and namespaced ("io.github.x/y"),
    # which is correct and unreadable as a key in a YAML file a person may edit.
    # Keep the tail, which is what a user would have called it anyway.
    short = name.rsplit("/", 1)[-1] or name

    packages = entry.get("packages") or []
    remotes = entry.get("remotes") or []
    out: Dict[str, Any] = {
        "name": short,
        "description": str(entry.get("description") or entry.get("title") or ""),
        "version": str(entry.get("version") or "") or None,
        "source": "registry",
        "upstream": name,
        "homepage": ((entry.get("repository") or {}).get("url")
                     if isinstance(entry.get("repository"), dict) else None),
        "env_keys": [],
        "args": [],
        "command": None,
        "url": None,
        "transport": None,
        "category": "registry",
    }

    # A hosted server is preferred only when there is no local package: a stdio
    # package runs on the user's machine and needs no account.
    if packages:
        pkg = packages[0] if isinstance(packages[0], dict) else {}
        runtime = str(pkg.get("runtimeHint") or "").strip().lower()
        identifier = str(pkg.get("identifier") or "").strip()
        registry_type = str(pkg.get("registryType") or "").strip().lower()
        version = str(pkg.get("version") or "").strip()
        pinned = "%s@%s" % (identifier, version) if version else identifier

        if registry_type == "npm" or runtime in ("npx", "node"):
            out["command"] = "npx"
            out["args"] = ["-y", pinned] if pinned else ["-y", identifier]
        elif registry_type == "pypi" or runtime in ("uvx", "python"):
            out["command"] = "uvx"
            out["args"] = [pinned] if pinned else [identifier]
        elif identifier:
            out["command"] = identifier
            out["args"] = []
        out["transport"] = "stdio"

        for arg in (pkg.get("runtimeArguments") or []):
            if isinstance(arg, dict) and arg.get("type") == "positional" and arg.get("value"):
                out["args"].insert(1, str(arg["value"]))

        for var in (pkg.get("environmentVariables") or []):
            if isinstance(var, dict) and var.get("name"):
                out["env_keys"].append(str(var["name"]))

    if not out["command"] and remotes:
        remote = remotes[0] if isinstance(remotes[0], dict) else {}
        url = str(remote.get("url") or "").strip()
        if url:
            out["url"] = url
            out["transport"] = "http"
            for hdr in (remote.get("headers") or []):
                if isinstance(hdr, dict) and hdr.get("name"):
                    out["env_keys"].append(str(hdr["name"]))

    # Without a launcher or a URL there is nothing to install; the row is still
    # returned so the page can show it as a link rather than pretend it works.
    out["installable"] = bool(out["command"] or out["url"])
    return out


def _newest_first(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The registry paginates one row per published version, so the same server
    can appear repeatedly. Keep the highest version, which is what "the server"
    means to anyone reading the list."""
    best: Dict[str, Dict[str, Any]] = {}

    def key(version: Optional[str]) -> Tuple[int, ...]:
        parts = []
        for chunk in str(version or "").split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            parts.append(int(digits) if digits else -1)
        return tuple(parts or [-1])

    for e in entries:
        ident = str(e.get("upstream") or e.get("name") or "")
        current = best.get(ident)
        if current is None or key(e.get("version")) > key(current.get("version")):
            best[ident] = e
    return list(best.values())


def fetch_registry(*, query: Optional[str] = None, limit: int = DEFAULT_LIMIT,
                   timeout: float = 20.0, fetch: Optional[Callable[..., Any]] = None,
                   ) -> Dict[str, Any]:
    """Search the official MCP registry. ``search`` is sent to the server."""
    fetch = _resolve_fetcher(fetch)
    want = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    params = {"limit": "100"}
    if query:
        params["search"] = str(query)
    url = "%s/v0/servers?%s" % (REGISTRY_BASE, urllib.parse.urlencode(params))

    raw = _cached("registry:%s" % query, lambda: fetch(url, timeout=timeout))
    rows = raw.get("servers") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("unexpected registry response shape")

    entries = [e for e in (normalize_registry_entry(r) for r in rows) if e]
    entries = _newest_first(entries)
    entries.sort(key=lambda e: (not e.get("installable"), e.get("name") or ""))
    return {"ok": True, "source": "registry", "kind": "mcp", "total": len(entries),
            "entries": entries[:want],
            # The registry's own page size, not its size: it publishes a cursor.
            "note": "showing one page of the registry; it reports a cursor, not a total"}


# --------------------------------------------------------------------------- cline


def normalize_cline_entry(raw: Any, kind: str = "mcp") -> Optional[Dict[str, Any]]:
    """One Cline ``entry.json`` → this project's entry shape.

    ``install.args`` is the post-install argument list for the Cline CLI, so it is
    not a command: the first element is the server id and the rest are flags. An
    ``http`` transport is the only one recognisable from here without guessing, so
    anything else is left without a launcher and marked as not installable rather
    than guessed into a command that would fail at start time.

    ``kind`` is ``mcp`` under ``registry/mcps`` and ``plugin``/``skill`` under the
    other two directories — all three are written in the same ``entry.json``
    shape, and only the first is an MCP server. A plugin or a skill is still
    returned, so the page can show it, but it is never offered as an MCP install:
    its manifest only names arguments for the Cline CLI, and the directory it
    lives in holds nothing but the manifest itself.
    """
    if not isinstance(raw, dict):
        return None
    ident = str(raw.get("id") or "").strip()
    if not ident:
        return None

    install = raw.get("install") if isinstance(raw.get("install"), dict) else {}
    args = [str(a) for a in (install.get("args") or []) if a is not None]

    out: Dict[str, Any] = {
        "name": ident,
        "description": str(raw.get("tagline") or raw.get("description") or ""),
        "version": str(raw.get("version") or "") or None,
        "source": "cline",
        "upstream": ident,
        "homepage": raw.get("homepage") or (
            (raw.get("repository") or {}).get("url")
            if isinstance(raw.get("repository"), dict) else None),
        "env_keys": [str(v["name"]) for v in (install.get("env") or [])
                     if isinstance(v, dict) and v.get("name")],
        "args": [],
        "command": None,
        "url": None,
        "transport": None,
        "category": "cline",
        "kind": kind,
        "note": None,
    }

    if kind != "mcp":
        # Nothing under registry/plugins or registry/skills can be fetched into a
        # plugin directory: the listing holds the manifest and nothing else. The
        # row stays so the page can say what is there, and says why it cannot be
        # installed rather than offering a button that would fail.
        out["installable"] = False
        out["note"] = (
            "%r is a Cline %s, not an MCP server; its manifest installs through "
            "the Cline CLI (%s)"
            % (ident, kind, " ".join(args) if args else "no install arguments"))
        return out

    if "--transport" in args:
        i = args.index("--transport")
        kind = str(args[i + 1]).lower() if i + 1 < len(args) else ""
        url = str(args[i + 2]) if i + 2 < len(args) else ""
        if kind in ("http", "sse") and url.startswith(("http://", "https://")):
            out["transport"] = kind
            out["url"] = url
    elif len(args) >= 3 and str(args[1]) == "--transport":
        kind = str(args[2]).lower()
        url = str(args[3]) if len(args) > 3 else ""
        if kind in ("http", "sse") and url.startswith(("http://", "https://")):
            out["transport"] = kind
            out["url"] = url

    out["installable"] = bool(out["url"])
    return out


def _cline_entry_files(files: Dict[str, bytes], where: str) -> Dict[str, bytes]:
    """``{id: entry.json bytes}`` for one Cline directory, out of a file listing.

    The ids are the directory names that actually hold an ``entry.json``: a
    ``README.md`` beside them is not an entry, and neither is a directory that
    holds only an icon.
    """
    prefix = "registry/%s/" % where
    out: Dict[str, bytes] = {}
    for rel, body in files.items():
        if not rel.startswith(prefix) or not rel.endswith("/entry.json"):
            continue
        ident = rel[len(prefix):-len("/entry.json")]
        if not ident or "/" in ident:
            continue
        out[ident] = body
    if not out:
        raise ValueError("the archive holds no %s<id>/entry.json" % prefix)
    return out


def _cline_archive(where: str, *, timeout: float,
                   archive_fetch: Callable[..., Any]) -> Dict[str, bytes]:
    """One Cline directory as ``{id: entry.json bytes}``, from the repository tarball.

    Cached by kind on top of the tarball's own cache-by-URL, so a page load, the
    source's own count and an install all share one download and one parse.
    """
    owner, _sep, repo = CLINE_REPO.partition("/")
    return _cached(
        "cline:archive:%s" % where,
        lambda: _cline_entry_files(
            _repo_archive(owner, repo, CLINE_REF, archive_fetch, timeout=timeout),
            where))


def list_cline(kind: str = "mcps", q: Optional[str] = None, limit: int = 50, *,
               timeout: float = 20.0, fetch: Optional[Callable[..., Any]] = None,
               ) -> Dict[str, Any]:
    """List one of the Cline marketplace's three directories.

    ``kind`` is ``mcps`` (MCP servers), ``plugins`` or ``skills``. The directory
    is read out of the repository *tarball*: one request that cannot be rate
    limited, and — because the tarball carries every file, not just the listing —
    the entries come with it, so no per-entry request is needed at all.

    ``codeload.github.com`` is not the API, which is the whole point: the GitHub
    contents API allows 60 anonymous requests an hour, and a live listing came
    back ``HTTP 403`` for exactly that reason. When the archive cannot be read
    the contents API is still tried, and the reason the archive failed is put in
    ``note`` — a fallback that silently looks the same as the primary route is
    how a rate limit turns into "this marketplace is empty".

    The three kinds are kept apart on purpose: a caller that asked for plugins
    must not silently receive servers.
    """
    file_fetch, archive_fetch = _resolve_fetchers(fetch)
    where = str(kind or "mcps").strip().lower()
    if where not in CLINE_KINDS:
        raise ValueError("unknown Cline kind %r (known: %s)"
                         % (kind, ", ".join(sorted(CLINE_KINDS))))
    entry_kind = CLINE_KINDS[where]
    want = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    source_id = "cline" if where == "mcps" else "cline-%s" % where

    archived: Optional[Dict[str, bytes]] = None
    archive_problem: Optional[str] = None
    try:
        archived = _cline_archive(where, timeout=timeout, archive_fetch=archive_fetch)
    except Exception as exc:  # noqa: BLE001 - the contents API below is the fallback
        archive_problem = "%s: %s" % (type(exc).__name__, exc)

    if archived is not None:
        ids = list(archived)
    else:
        try:
            listing = _cached("cline:index:%s" % where, lambda: file_fetch(
                "%s/registry/%s" % (CLINE_API, where), timeout=timeout))
            if not isinstance(listing, list):
                raise ValueError("unexpected Cline listing shape")
        except Exception as exc:  # noqa: BLE001 - both roads are closed; say both
            return {"ok": False, "source": source_id, "kind": where,
                    "entry_kind": entry_kind, "total": None, "entries": [],
                    "note": None,
                    "error": ("Cline's %s could not be listed: no repository "
                              "archive (%s) and no contents listing (%s)"
                              % (where, archive_problem, exc))}
        ids = []
        for item in listing:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in (None, "dir"):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                ids.append(name)

    if q:
        needle = str(q).lower()
        ids = [i for i in ids if needle in i.lower()]
    ids.sort()
    chosen = ids[:want]

    def one(ident: str) -> Optional[Dict[str, Any]]:
        raw: Any = None
        blob = archived.get(ident) if archived else None
        if blob is not None:
            try:
                raw = json.loads(blob.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raw = None  # one unreadable file falls back; it does not win
        if raw is None:
            try:
                raw = _cached("cline:%s:%s" % (where, ident), lambda: file_fetch(
                    "%s/registry/%s/%s/entry.json" % (CLINE_CDN, where, ident),
                    timeout=timeout))
            except Exception:
                # One unreachable entry must not cost the whole page.
                return None
        return normalize_cline_entry(raw, entry_kind)

    with ThreadPoolExecutor(max_workers=min(DETAIL_WORKERS, max(1, len(chosen)))) as pool:
        entries = [e for e in pool.map(one, chosen) if e]

    entries.sort(key=lambda e: (not e.get("installable"), e.get("name") or ""))
    notes: List[str] = []
    if where != "mcps":
        notes.append("%d %s listed; the %s are not MCP servers"
                     % (len(ids), where, where))
    if archive_problem is not None:
        notes.append(
            "the repository archive could not be read (%s), so this listing came "
            "from GitHub's contents API, which allows 60 anonymous requests an hour"
            % archive_problem)
    return {"ok": True, "source": source_id, "kind": where,
            "entry_kind": entry_kind, "total": len(ids), "entries": entries,
            "note": "; ".join(notes) or None}


def fetch_cline(*, query: Optional[str] = None, limit: int = DEFAULT_LIMIT,
                timeout: float = 20.0, fetch: Optional[Callable[..., Any]] = None,
                ) -> Dict[str, Any]:
    """The MCP half of the Cline marketplace — :func:`list_cline` for ``mcps``."""
    return list_cline("mcps", q=query, limit=limit, timeout=timeout, fetch=fetch)


# --------------------------------------------------------------------------- smithery


def smithery_detail_url(qualified_name: str) -> str:
    """The per-server endpoint.

    Discovered rather than documented: the list endpoint hands back no endpoint
    URL at all — only ``remote: true`` — so the connect URL exists solely on
    ``/servers/<qualifiedName>``. The namespace slash is part of the path
    (``namespace/slug``), so it is quoted as a path separator on purpose.
    """
    return "%s/%s" % (SMITHERY_SERVERS, urllib.parse.quote(str(qualified_name), safe="/"))


def _entry_key(text: str) -> str:
    """A name usable as a YAML key.

    Smithery ids are ``namespace/slug`` and a slash cannot be a key in
    ``mcp.yaml`` — the installer refuses it — so the tail is kept, the same
    reduction the registry source already applies to its reverse-DNS names.
    """
    tail = str(text or "").strip().rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", tail).strip("-")
    return cleaned or "server"


def normalize_smithery_entry(raw: Any, detail: Any = None,
                             note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One Smithery row, plus its detail when we have it → entry shape.

    The row alone cannot be installed from: it says ``remote: true`` and nothing
    else. The detail carries ``deploymentUrl`` (and ``connections[].deploymentUrl``
    per transport) which is the URL an MCP client connects to, so ``detail`` is
    what turns a listing into something installable. ``note`` is the reason it is
    not — an entry whose detail could not be read is still returned, because a
    network hiccup and a server that does not exist must not look the same.

    The detail's ``connections[].configSchema.required`` names what the server
    needs configured. Those names are reported in ``config_keys`` and are *not*
    written into ``mcp.yaml`` as headers: Smithery's hosted endpoints take their
    config as a connection parameter, and guessing header names would produce a
    config that starts and then fails at the first call.
    """
    if not isinstance(raw, dict):
        return None
    qualified = str(raw.get("qualifiedName") or raw.get("id") or "").strip()
    if not qualified:
        return None

    out: Dict[str, Any] = {
        "name": _entry_key(str(raw.get("slug") or "").strip() or qualified),
        "description": str(raw.get("description") or ""),
        "version": None,
        "source": "smithery",
        "upstream": qualified,
        "homepage": raw.get("homepage") or None,
        "env_keys": [],
        "args": [],
        "command": None,
        "url": None,
        "transport": None,
        "category": "smithery",
        "kind": "mcp",
        "config_keys": [],
        "use_count": raw.get("useCount"),
        "verified": bool(raw.get("verified")),
        "note": None,
    }

    url = ""
    keys: List[str] = []
    if isinstance(detail, dict):
        url = str(detail.get("deploymentUrl") or "").strip()
        for conn in (detail.get("connections") or []):
            if not isinstance(conn, dict):
                continue
            if not url:
                url = str(conn.get("deploymentUrl") or "").strip()
            schema = conn.get("configSchema")
            if isinstance(schema, dict):
                for key in (schema.get("required") or []):
                    key = str(key or "").strip()
                    if key and key not in keys:
                        keys.append(key)
        if not out["description"]:
            out["description"] = str(detail.get("description") or "")

    out["config_keys"] = keys
    if url.startswith(("http://", "https://")):
        out["url"] = url
        out["transport"] = "http"
    out["installable"] = bool(out["url"])
    if not out["installable"]:
        out["note"] = note or (
            "Smithery lists this server but no endpoint URL could be read for it, "
            "so there is nothing here to connect an MCP client to")
    elif keys:
        out["note"] = ("hosted at %s; it declares required settings: %s"
                       % (out["url"], ", ".join(keys)))
    return out


def list_smithery(q: Optional[str] = None, limit: int = 20, *,
                  timeout: float = 20.0, fetch: Optional[Callable[..., Any]] = None,
                  ) -> Dict[str, Any]:
    """List Smithery's hosted servers, fetching the detail of the ones returned.

    The list endpoint is asked for one page (100 rows) and ``q`` goes to the
    server, which filters: measured here, ``q=airtable`` dropped ``totalCount``
    from 17,062 to 105, so this is a real server-side search and not a local
    filter pretending to be one.

    The detail fetch is per entry and bounded to what is about to be returned —
    never to all 17,000. ``limit`` is deliberately small (20) for that reason:
    each visible row costs one request.
    """
    fetch = _resolve_fetcher(fetch)
    want = max(1, min(int(limit or 20), MAX_LIMIT))
    params = {"pageSize": "100"}
    if q:
        params["q"] = str(q)
    url = "%s?%s" % (SMITHERY_SERVERS, urllib.parse.urlencode(params))

    raw = _cached("smithery:%s" % q, lambda: fetch(url, timeout=timeout))
    rows = raw.get("servers") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected Smithery response shape")

    total: Optional[int] = None
    pagination = raw.get("pagination") if isinstance(raw, dict) else None
    if isinstance(pagination, dict) and isinstance(pagination.get("totalCount"), int):
        total = int(pagination["totalCount"])

    chosen = [r for r in rows[:want] if isinstance(r, dict)]

    def one(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        qualified = str(row.get("qualifiedName") or "").strip()
        if not qualified:
            return normalize_smithery_entry(row)
        try:
            detail = _cached("smithery:detail:%s" % qualified, lambda: fetch(
                smithery_detail_url(qualified), timeout=timeout))
        except Exception as exc:
            # The entry is not dropped: "we could not ask" is not "it is gone".
            return normalize_smithery_entry(
                row, note=("its detail could not be read (%s), so this build "
                           "cannot tell where to reach it" % exc))
        return normalize_smithery_entry(row, detail=detail)

    with ThreadPoolExecutor(max_workers=min(DETAIL_WORKERS, max(1, len(chosen)))) as pool:
        entries = [e for e in pool.map(one, chosen) if e]

    entries.sort(key=lambda e: (not e.get("installable"),
                                -int(e.get("use_count") or 0),
                                e.get("name") or ""))
    unreachable = sum(1 for e in entries if not e.get("installable"))
    return {"ok": True, "source": "smithery", "kind": "mcp",
            "total": total if total is not None else len(rows),
            "entries": entries,
            "note": (None if not unreachable else
                     "%d of %d entries could not be resolved to an endpoint"
                     % (unreachable, len(entries)))}


# --------------------------------------------------------------------------- anthropic


def _github_repo(url: Any) -> Optional[str]:
    """``https://github.com/OWNER/REPO.git`` → ``OWNER/REPO``; else ``None``.

    Only GitHub is accepted, because the fetch path (jsDelivr, or the GitHub
    trees API for a listing) is a GitHub one. A source that points anywhere else
    is reported as not installable instead of being fetched half-way.
    """
    text = str(url or "").strip()
    if not text:
        return None
    if text.startswith("git@github.com:"):
        text = text.split(":", 1)[1]
    elif "github.com/" in text:
        text = text.split("github.com/", 1)[1]
    else:
        return None
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        return None
    return "%s/%s" % (owner, name)


def _frontmatter_field(text: str, field: str) -> str:
    """One scalar out of a ``---``-delimited frontmatter block; "" when absent.

    Parsed as YAML, not by reading the line: real skills write their description
    as a folded block (``description: >``), and a line-based reader answers ">"
    for those — which is what this did until a live run of ``anthropics/skills``
    showed it. Whitespace is collapsed because a description is one line
    wherever it is shown.
    """
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(\n|\Z)", str(text or ""), re.DOTALL)
    if not match:
        return ""
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get(field)
    if value is None:
        return ""
    return " ".join(str(value).split())


def normalize_anthropic_plugin(raw: Any, *, marketplace_repo: str = ANTHROPIC_MARKETPLACE_REPO,
                               marketplace_ref: str = ANTHROPIC_MARKETPLACE_REF,
                               ) -> Optional[Dict[str, Any]]:
    """One ``marketplace.json`` plugin → entry shape, with a fetchable location.

    The directory declares four different ``source`` shapes and all four are
    live, so all four are walked:

    ``"./plugins/name"``         a path inside the marketplace repository itself
    ``{url, sha}``               a whole repository (the plugin is its root)
    ``{url, path, ref, sha}``    a subdirectory of another repository, at a tag
    ``{url, path, sha}``         the same, with no ref — the sha is used instead

    ``ref`` is preferred when it is declared (that is the revision the
    marketplace validated), and the sha is the fallback because it is immutable:
    a branch can move between the listing and the install, a commit cannot.
    ``installable`` is false only when no GitHub repository can be read out of
    the shape at all — that is a property of the entry, and the page shows the
    reason rather than a button that would fail.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None

    author = raw.get("author")
    author_name = (str((author or {}).get("name") or "") if isinstance(author, dict)
                   else str(author or ""))

    source = raw.get("source")
    repo: Optional[str] = None
    ref: Optional[str] = None
    path = ""
    why = ""

    if isinstance(source, str):
        rel = source.strip()
        if rel.startswith("./"):
            rel = rel[2:]
        if rel and not rel.startswith(("http://", "https://", "/")):
            repo, ref, path = marketplace_repo, marketplace_ref, rel.strip("/")
        else:
            why = "its source %r is not a path inside the marketplace repository" % source
    elif isinstance(source, dict):
        repo = _github_repo(source.get("url"))
        ref = (str(source.get("ref") or "").strip()
               or str(source.get("sha") or "").strip() or None)
        path = str(source.get("path") or "").strip().strip("/")
        if repo is None:
            why = ("its source points at %r, which is not a GitHub repository"
                   % source.get("url"))
    else:
        why = "it declares no source"

    out: Dict[str, Any] = {
        "name": name,
        "description": str(raw.get("description") or ""),
        "version": str(raw.get("version") or "") or None,
        "source": "anthropic-plugins",
        "upstream": name,
        "homepage": raw.get("homepage") or None,
        "author": author_name,
        "category": str(raw.get("category") or "") or None,
        "kind": "plugin",
        "repo": repo,
        "ref": ref,
        "path": path,
        "env_keys": [],
        "args": [],
        "command": None,
        "url": None,
        "transport": None,
        "installable": bool(repo),
        "note": None if repo else ("this build cannot fetch it: %s" % why),
    }
    return out


def list_anthropic_plugins(q: Optional[str] = None, limit: int = 50, *,
                           timeout: float = 20.0,
                           fetch: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """List Anthropic's official plugin directory (one 186 KB JSON, 311 plugins).

    The whole directory is one request, so the query is filtered here rather
    than server-side — the marketplace has no search API. Installing one of
    these is a conversion, not a download: :mod:`kairos.extensions.claude_plugin`
    does that part.
    """
    fetch = _resolve_fetcher(fetch)
    want = max(1, min(int(limit or 50), MAX_LIMIT))

    raw = _anthropic_marketplace(fetch, timeout=timeout)
    rows = raw.get("plugins") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected Claude marketplace shape")

    entries = [e for e in (normalize_anthropic_plugin(r) for r in rows) if e]
    if q:
        needle = str(q).lower()
        entries = [e for e in entries
                   if needle in (e.get("name") or "").lower()
                   or needle in (e.get("description") or "").lower()
                   or needle in str(e.get("category") or "").lower()]
    entries.sort(key=lambda e: (not e.get("installable"), e.get("name") or ""))
    unfetchable = sum(1 for e in entries if not e.get("installable"))
    return {"ok": True, "source": "anthropic-plugins", "kind": "plugin",
            "total": len(entries), "entries": entries[:want],
            "note": (None if not unfetchable else
                     "%d of %d plugins declare a source this build cannot fetch"
                     % (unfetchable, len(entries)))}


def normalize_anthropic_skill(name: str, repo: str = ANTHROPIC_SKILLS_REPO,
                              ref: Optional[str] = ANTHROPIC_SKILLS_REF,
                              description: str = "") -> Optional[Dict[str, Any]]:
    """One skill directory → entry shape.

    A skill is one ``SKILL.md``; ``raw_url`` is where it is read from at install
    time, so the installer never has to know which repository layout it came out
    of.
    """
    name = str(name or "").strip()
    if not name:
        return None
    path = "%s/%s" % (ANTHROPIC_SKILLS_DIR, name)
    return {
        "name": name,
        "description": description,
        "version": None,
        "source": "anthropic-skills",
        "upstream": name,
        "homepage": "https://github.com/%s/tree/%s/%s" % (repo, ref or "main", path),
        "kind": "skill",
        "category": "skill",
        "repo": repo,
        "ref": ref,
        "path": path,
        "raw_url": jsdelivr(repo, ref, "%s/SKILL.md" % path),
        "env_keys": [],
        "args": [],
        "command": None,
        "url": None,
        "transport": None,
        "installable": True,
        "note": None,
    }


def _skill_dirs(listing: Any) -> List[str]:
    """Directory names out of a GitHub contents listing; files are skipped."""
    names: List[str] = []
    for item in (listing if isinstance(listing, list) else []):
        if not isinstance(item, dict):
            continue
        if item.get("type") not in (None, "dir"):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _anthropic_skill_bodies(*, timeout: float,
                            archive_fetch: Callable[..., Any]) -> Dict[str, str]:
    """``{skill name: SKILL.md text}`` out of the ``anthropics/skills`` tarball.

    One request for the directory *and* every ``SKILL.md`` in it, which is also
    what supplies each description — so a listing costs one request instead of
    one per skill, and it cannot run into the contents API's 60-an-hour limit.
    """
    owner, _sep, repo = ANTHROPIC_SKILLS_REPO.partition("/")
    files = _repo_archive(owner, repo, ANTHROPIC_SKILLS_REF, archive_fetch,
                          timeout=timeout)
    prefix = ANTHROPIC_SKILLS_DIR + "/"
    out: Dict[str, str] = {}
    for rel, body in files.items():
        if not rel.startswith(prefix) or not rel.endswith("/SKILL.md"):
            continue
        name = rel[len(prefix):-len("/SKILL.md")]
        if not name or "/" in name:
            continue
        out[name] = body.decode("utf-8", "replace")
    if not out:
        raise ValueError("the archive holds no %s<name>/SKILL.md" % prefix)
    return out


def _anthropic_skills_index(*, timeout: float, file_fetch: Callable[..., Any],
                            archive_fetch: Callable[..., Any]
                            ) -> Tuple[List[str], Dict[str, str], Optional[str]]:
    """``(names, bodies, archive_problem)`` — the skills directory, tarball first.

    ``bodies`` is empty when the archive could not be read: the listing still
    happens (through the contents API) and every ``SKILL.md`` is then fetched one
    at a time through jsDelivr, which is what this did before the archive
    existed. ``archive_problem`` carries the reason, so a caller can say *why*
    the slower road was taken rather than leaving the two indistinguishable.
    """
    try:
        bodies = _cached("anthropic:skills:archive",
                         lambda: _anthropic_skill_bodies(
                             timeout=timeout, archive_fetch=archive_fetch))
    except Exception as exc:  # noqa: BLE001 - the contents API below is the fallback
        problem = "%s: %s" % (type(exc).__name__, exc)
    else:
        return sorted(bodies), bodies, None

    listing = _cached("anthropic:skills", lambda: file_fetch(
        ANTHROPIC_SKILLS_API, timeout=timeout))
    if not isinstance(listing, list):
        raise ValueError("unexpected anthropics/skills listing shape")
    return sorted(_skill_dirs(listing)), {}, problem


def list_anthropic_skills(q: Optional[str] = None, limit: int = 50, *,
                          timeout: float = 20.0,
                          fetch: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """Enumerate ``anthropics/skills``' ``skills/`` directory.

    Enumerated out of the repository tarball — which carries every ``SKILL.md``
    as well, so the descriptions cost nothing extra. When the archive cannot be
    read, the contents API lists the directory and each ``SKILL.md`` is fetched
    through jsDelivr; the reason is put in ``note``.

    A skill whose description cannot be read is still listed: the file is there,
    and making it vanish because a read timed out would be the same lie as an
    empty marketplace.
    """
    file_fetch, archive_fetch = _resolve_fetchers(fetch)
    text_fetch = _resolve_fetcher(fetch, _http_text)
    want = max(1, min(int(limit or 50), MAX_LIMIT))

    names, bodies, archive_problem = _anthropic_skills_index(
        timeout=timeout, file_fetch=file_fetch, archive_fetch=archive_fetch)

    if q:
        needle = str(q).lower()
        names = [n for n in names if needle in n.lower()]
    names.sort()
    chosen = names[:want]

    def one(name: str) -> Optional[Dict[str, Any]]:
        entry = normalize_anthropic_skill(name)
        if entry is None:
            return None
        body = bodies.get(name)
        if body is None:
            try:
                body = _cached("anthropic:skill:%s" % name, lambda: text_fetch(
                    entry["raw_url"], timeout=timeout))
            except Exception as exc:
                entry["note"] = ("its SKILL.md could not be read (%s); the skill is "
                                 "listed without a description" % exc)
                return entry
        entry["description"] = _frontmatter_field(body, "description")
        return entry

    with ThreadPoolExecutor(max_workers=min(DETAIL_WORKERS, max(1, len(chosen)))) as pool:
        entries = [e for e in pool.map(one, chosen) if e]

    undescribed = sum(1 for e in entries if e.get("note"))
    notes: List[str] = []
    if undescribed:
        notes.append("%d of %d skills could not be described"
                     % (undescribed, len(entries)))
    if archive_problem is not None:
        notes.append(
            "the repository archive could not be read (%s), so this listing came "
            "from GitHub's contents API, which allows 60 anonymous requests an hour"
            % archive_problem)
    return {"ok": True, "source": "anthropic-skills", "kind": "skill",
            "total": len(names), "entries": entries,
            "note": "; ".join(notes) or None}


# --------------------------------------------------------------------------- facade


# Every source is reached through one of these, and all of them take the same
# keyword arguments — ``search`` never has to know which source it is calling.
# The fetcher stays a parameter all the way down, so the transport is still
# resolved at call time inside the source that uses it.


def _cline_page(kind: str) -> Callable[..., Dict[str, Any]]:
    def page(*, query: Optional[str] = None, limit: int = DEFAULT_LIMIT,
             timeout: float = 20.0, fetch: Optional[Callable[..., Any]] = None,
             ) -> Dict[str, Any]:
        return list_cline(kind, q=query, limit=limit, timeout=timeout, fetch=fetch)
    return page


def _smithery_page(*, query: Optional[str] = None, limit: int = 20,
                   timeout: float = 20.0, fetch: Optional[Callable[..., Any]] = None,
                   ) -> Dict[str, Any]:
    return list_smithery(q=query, limit=limit, timeout=timeout, fetch=fetch)


def _anthropic_plugins_page(*, query: Optional[str] = None, limit: int = 50,
                            timeout: float = 20.0,
                            fetch: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    return list_anthropic_plugins(q=query, limit=limit, timeout=timeout, fetch=fetch)


def _anthropic_skills_page(*, query: Optional[str] = None, limit: int = 50,
                           timeout: float = 20.0,
                           fetch: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    return list_anthropic_skills(q=query, limit=limit, timeout=timeout, fetch=fetch)


_FETCHERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "registry": fetch_registry,
    "smithery": _smithery_page,
    "cline": _cline_page("mcps"),
    "cline-plugins": _cline_page("plugins"),
    "cline-skills": _cline_page("skills"),
    "anthropic-plugins": _anthropic_plugins_page,
    "anthropic-skills": _anthropic_skills_page,
}


def list_sources() -> List[Dict[str, Any]]:
    """The sources this build knows how to reach.

    ``total`` is ``None`` here on purpose: the number belongs to the upstream
    and is filled in by :func:`total_count`, which will not invent one. The
    caller that shows a total shows what it got, not what it hoped for.
    """
    out = []
    for sid, meta in SOURCES.items():
        item = dict(meta)
        item.setdefault("needs_query", False)
        item.setdefault("note", None)
        item["id"] = sid
        item["total"] = None
        out.append(item)
    return out


def total_count(source_id: str, *, timeout: float = 20.0,
                fetch: Optional[Callable[..., Any]] = None) -> Optional[int]:
    """How many entries the upstream itself says it has, or ``None``.

    Only numbers an upstream actually declared are returned: Smithery's
    ``pagination.totalCount``, or the length of a directory it served. The
    official registry publishes a page cursor and no total, so it answers
    ``None`` — writing "4,000" there would be a number nobody can check.

    Never raises. A count is not worth a failed request, and "we could not ask"
    is not a number.
    """
    file_fetch, archive_fetch = _resolve_fetchers(fetch)
    sid = str(source_id or "").strip().lower()
    try:
        if sid == "smithery":
            raw = _cached("smithery:", lambda: file_fetch(
                "%s?pageSize=1" % SMITHERY_SERVERS, timeout=timeout))
            pagination = raw.get("pagination") if isinstance(raw, dict) else None
            value = pagination.get("totalCount") if isinstance(pagination, dict) else None
            return int(value) if isinstance(value, int) else None
        if sid in ("cline", "cline-plugins", "cline-skills"):
            where = {"cline": "mcps", "cline-plugins": "plugins",
                     "cline-skills": "skills"}[sid]
            try:
                return len(_cline_archive(where, timeout=timeout,
                                          archive_fetch=archive_fetch))
            except Exception:
                pass  # the contents API below is the fallback
            listing = _cached("cline:index:%s" % where, lambda: file_fetch(
                "%s/registry/%s" % (CLINE_API, where), timeout=timeout))
            if not isinstance(listing, list):
                return None
            return len([i for i in listing
                        if isinstance(i, dict) and i.get("name")
                        and i.get("type") in (None, "dir")])
        if sid == "anthropic-plugins":
            raw = _anthropic_marketplace(file_fetch, timeout=timeout)
            rows = raw.get("plugins") if isinstance(raw, dict) else None
            return len(rows) if isinstance(rows, list) else None
        if sid == "anthropic-skills":
            return len(_anthropic_skills_index(
                timeout=timeout, file_fetch=file_fetch,
                archive_fetch=archive_fetch)[0])
        return None  # the registry: a cursor, not a count
    except Exception:
        return None


def fetch_entry(source: str, ident: str, *, timeout: float = 20.0,
                fetch: Optional[Callable[..., Any]] = None,
                ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve one specific entry, for the moment between "click Install" and
    "it is in the file". Returns ``(entry, error)`` — never raises.

    A Cline entry is read out of the repository tarball first, which is the same
    download the listing already made — so an install right after a page load
    costs no request at all — and jsDelivr is the fallback rather than the road.
    That order was measured, not guessed: during a live install the CDN answered
    ``WinError 10054`` (connection reset) for the same entry codeload served.
    """
    file_fetch, archive_fetch = _resolve_fetchers(fetch)
    sid = str(source or "").strip().lower()
    want = str(ident or "").strip()
    if not want:
        return None, "no id given"
    try:
        if sid in ("cline", "cline-plugins", "cline-skills"):
            where = {"cline": "mcps", "cline-plugins": "plugins",
                     "cline-skills": "skills"}[sid]
            raw: Any = None
            try:
                blob = _cline_archive(where, timeout=timeout,
                                      archive_fetch=archive_fetch).get(want)
                raw = json.loads(blob.decode("utf-8")) if blob is not None else None
            except Exception:  # noqa: BLE001 - the CDN below is the fallback
                # No archive, an entry that is not in it, or a file that will not
                # parse: all three mean "ask the CDN", not "this entry is gone".
                raw = None
            if raw is None:
                raw = _cached("cline:%s:%s" % (where, want), lambda: file_fetch(
                    "%s/registry/%s/%s/entry.json" % (CLINE_CDN, where, want),
                    timeout=timeout))
            entry = normalize_cline_entry(raw, CLINE_KINDS[where])
            if entry is None:
                return None, "Cline has no entry named %r under %s" % (want, where)
            return entry, None
        if sid == "smithery":
            detail = _cached("smithery:detail:%s" % want, lambda: file_fetch(
                smithery_detail_url(want), timeout=timeout))
            raw = dict(detail) if isinstance(detail, dict) else {}
            raw.setdefault("qualifiedName", want)
            entry = normalize_smithery_entry(raw, detail=detail)
            if entry is None:
                return None, "Smithery has no server named %r" % want
            return entry, None
        if sid == "anthropic-plugins":
            page = list_anthropic_plugins(q=want, limit=MAX_LIMIT, timeout=timeout,
                                          fetch=file_fetch)
            for entry in page.get("entries", []):
                if want in (entry.get("name"), entry.get("upstream")):
                    return entry, None
            return None, "the Claude plugin directory has no plugin named %r" % want
        if sid == "anthropic-skills":
            names, _bodies, _problem = _anthropic_skills_index(
                timeout=timeout, file_fetch=file_fetch,
                archive_fetch=archive_fetch)
            entry = normalize_anthropic_skill(want)
            # Checked against the directory rather than assumed: an id a client
            # typed by hand must not produce a skill that 404s at install time.
            if entry is None or want not in names:
                return None, "anthropics/skills has no skill named %r" % want
            return entry, None
        if sid == "registry":
            page = fetch_registry(query=want, limit=MAX_LIMIT, timeout=timeout,
                                  fetch=file_fetch)
            for entry in page.get("entries", []):
                if entry.get("upstream") == want or entry.get("name") == want:
                    return entry, None
            # The registry's search is substring-based, so a short name can pull
            # in neighbours; prefer an exact match on either spelling.
            for entry in page.get("entries", []):
                if want.lower() in str(entry.get("upstream") or "").lower():
                    return entry, None
            return None, "the registry has no server named %r" % want
        return None, "unknown source %r" % source
    except urllib.error.HTTPError as e:
        return None, "%s answered HTTP %s" % (sid, e.code)
    except urllib.error.URLError as e:
        return None, "%s is unreachable (%s)" % (sid, e.reason)
    except Exception as e:
        return None, "%s: %s" % (sid, e)


def search(source: str, *, query: Optional[str] = None, limit: int = DEFAULT_LIMIT,
           timeout: float = 20.0, fetch: Optional[Callable[..., Any]] = None,
           ) -> Dict[str, Any]:
    """Search one source. Never raises: a marketplace that cannot be reached is
    reported, not hidden.

    A failure answers with ``total: None``, not ``0``: zero is a number the
    upstream could have said, and using it for "we could not ask" is exactly the
    confusion this module exists to avoid.
    """
    fetch = _resolve_fetcher(fetch)
    sid = str(source or "").strip().lower()
    fn = _FETCHERS.get(sid)
    if fn is None:
        return {"ok": False, "source": sid, "entries": [], "total": None,
                "kind": None, "note": None,
                "error": "unknown source %r (known: %s)"
                         % (source, ", ".join(sorted(_FETCHERS)))}
    try:
        return fn(query=query, limit=limit, timeout=timeout, fetch=fetch)
    except urllib.error.HTTPError as e:
        return {"ok": False, "source": sid, "entries": [], "total": None,
                "kind": None, "note": None,
                "error": "%s answered HTTP %s" % (sid, e.code)}
    except urllib.error.URLError as e:
        return {"ok": False, "source": sid, "entries": [], "total": None,
                "kind": None, "note": None,
                "error": "%s is unreachable (%s)" % (sid, e.reason)}
    except Exception as e:  # shape changes, timeouts, bad JSON
        return {"ok": False, "source": sid, "entries": [], "total": None,
                "kind": None, "note": None,
                "error": "%s: %s" % (sid, e)}
