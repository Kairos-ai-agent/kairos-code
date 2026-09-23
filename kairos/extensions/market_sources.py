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
    repository, so enumerating it means reading a directory — the GitHub contents
    API does that, and the entry files themselves are fetched through jsDelivr,
    because ``raw.githubusercontent.com`` is unreliable from mainland China while
    jsDelivr answers 200.

Everything here is read-only and best-effort: a source that cannot be reached
produces ``{"ok": False, "error": ...}`` and the page says so. A marketplace that
silently shows an empty list when the network is down is the same defect as a
server that is listed but cannot start.

The normalised shape is exactly what ``kairos.extensions_install.install_entry``
consumes, so a remote entry installs through the same path as a curated one:

    {name, category, transport, command, args, url, env_keys, description, source}
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

USER_AGENT = "kairos-code-marketplace/1.0 (+https://github.com/Kairos-ai-agent/kairos-code)"

REGISTRY_BASE = "https://registry.modelcontextprotocol.io"
CLINE_REPO = "cline/marketplace"
CLINE_API = "https://api.github.com/repos/%s/contents" % CLINE_REPO
CLINE_CDN = "https://cdn.jsdelivr.net/gh/%s@main" % CLINE_REPO

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
    },
    "cline": {
        "label": "Cline marketplace",
        "homepage": "https://github.com/cline/marketplace",
        "kind": "mcp",
        "description": (
            "A manifest repository with explicit install arguments. Fetched "
            "through jsDelivr because raw.githubusercontent.com is unreliable here."
        ),
        "installable": True,
    },
}


# --------------------------------------------------------------------------- fetching


def _http_json(url: str, timeout: float = 20.0) -> Any:
    """GET ``url`` and parse JSON. Raises on any failure — callers decide what a
    failure means for them, because the answer differs per source."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body)


def _resolve_fetcher(fetch: Optional[Callable[..., Any]]) -> Callable[..., Any]:
    """The HTTP getter to use for this call.

    Deliberately resolved at call time rather than bound as a default argument:
    a default is captured when the module is imported, which makes the transport
    impossible to replace — by a test, a proxy, or a future cache — without
    editing the code that uses it.
    """
    return fetch if fetch is not None else _http_json


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
    return {"ok": True, "source": "registry", "total": len(entries),
            "entries": entries[:want]}


# --------------------------------------------------------------------------- cline


def normalize_cline_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """One Cline ``entry.json`` → this project's entry shape.

    ``install.args`` is the post-install argument list for the Cline CLI, so it is
    not a command: the first element is the server id and the rest are flags. An
    ``http`` transport is the only one recognisable from here without guessing, so
    anything else is left without a launcher and marked as not installable rather
    than guessed into a command that would fail at start time.
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
    }

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


def fetch_cline(*, query: Optional[str] = None, limit: int = DEFAULT_LIMIT,
                timeout: float = 20.0, fetch: Optional[Callable[..., Any]] = None,
                ) -> Dict[str, Any]:
    """List the Cline marketplace. The directory is one request; the entries are
    one request each, so only ``limit`` of them are fetched."""
    fetch = _resolve_fetcher(fetch)
    want = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    listing = _cached("cline:index", lambda: fetch(
        "%s/registry/mcps" % CLINE_API, timeout=timeout))
    if not isinstance(listing, list):
        raise ValueError("unexpected Cline listing shape")

    ids: List[str] = []
    for item in listing:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in (None, "dir"):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            ids.append(name)

    if query:
        needle = str(query).lower()
        ids = [i for i in ids if needle in i.lower()]
    ids.sort()
    chosen = ids[:want]

    def one(ident: str) -> Optional[Dict[str, Any]]:
        try:
            raw = _cached("cline:%s" % ident, lambda: fetch(
                "%s/registry/mcps/%s/entry.json" % (CLINE_CDN, ident), timeout=timeout))
        except Exception:
            # One unreachable entry must not cost the whole page.
            return None
        return normalize_cline_entry(raw)

    with ThreadPoolExecutor(max_workers=min(DETAIL_WORKERS, max(1, len(chosen)))) as pool:
        entries = [e for e in pool.map(one, chosen) if e]

    entries.sort(key=lambda e: (not e.get("installable"), e.get("name") or ""))
    return {"ok": True, "source": "cline", "total": len(ids), "entries": entries}


# --------------------------------------------------------------------------- facade


_FETCHERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "registry": fetch_registry,
    "cline": fetch_cline,
}


def list_sources() -> List[Dict[str, Any]]:
    """The sources this build knows how to reach."""
    return [dict(id=sid, **meta) for sid, meta in SOURCES.items()]


def fetch_entry(source: str, ident: str, *, timeout: float = 20.0,
                fetch: Optional[Callable[..., Any]] = None,
                ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve one specific entry, for the moment between "click Install" and
    "it is in the file". Returns ``(entry, error)`` — never raises."""
    fetch = _resolve_fetcher(fetch)
    sid = str(source or "").strip().lower()
    want = str(ident or "").strip()
    if not want:
        return None, "no id given"
    try:
        if sid == "cline":
            raw = _cached("cline:%s" % want, lambda: fetch(
                "%s/registry/mcps/%s/entry.json" % (CLINE_CDN, want), timeout=timeout))
            entry = normalize_cline_entry(raw)
            if entry is None:
                return None, "Cline has no entry named %r" % want
            return entry, None
        if sid == "registry":
            page = fetch_registry(query=want, limit=MAX_LIMIT, timeout=timeout, fetch=fetch)
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
    reported, not hidden."""
    fetch = _resolve_fetcher(fetch)
    sid = str(source or "").strip().lower()
    fn = _FETCHERS.get(sid)
    if fn is None:
        return {"ok": False, "source": sid, "entries": [], "total": 0,
                "error": "unknown source %r (known: %s)"
                         % (source, ", ".join(sorted(_FETCHERS)))}
    try:
        return fn(query=query, limit=limit, timeout=timeout, fetch=fetch)
    except urllib.error.HTTPError as e:
        return {"ok": False, "source": sid, "entries": [], "total": 0,
                "error": "%s answered HTTP %s" % (sid, e.code)}
    except urllib.error.URLError as e:
        return {"ok": False, "source": sid, "entries": [], "total": 0,
                "error": "%s is unreachable (%s)" % (sid, e.reason)}
    except Exception as e:  # shape changes, timeouts, bad JSON
        return {"ok": False, "source": sid, "entries": [], "total": 0,
                "error": "%s: %s" % (sid, e)}
