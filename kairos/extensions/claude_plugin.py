"""Convert a Claude Code plugin into a Kairos plugin directory.

A Claude plugin is not a package you install — it is a *convention*. Its
``.claude-plugin/plugin.json`` declares a name, a description and an author and
nothing else; the components are found by where they live::

    commands/*.md          slash commands
    agents/*.md            sub-agent roles
    skills/**/SKILL.md     skills
    hooks/hooks.json       lifecycle hooks
    .mcp.json              MCP servers

That is the same convention :mod:`kairos.plugins` loads (its module docstring
lists exactly these surfaces), which is why the four bundled ports
(``code-review``, ``code-simplifier``, ``commit-commands``, ``feature-dev``) work
here unchanged, and why a marketplace plugin can be converted by *copying* rather
than by transpiling.

Three things make this more than a copy:

**The plugin-root variable.** ``${CLAUDE_PLUGIN_ROOT}`` appears inside plugin
files — a hook that runs ``bash ${CLAUDE_PLUGIN_ROOT}/scripts/x.sh``, a command
that reads ``@${CLAUDE_PLUGIN_ROOT}/templates/report.md``. Claude Code expands it
to the plugin's own directory; nothing here does. Every occurrence is rewritten
to the installed plugin's directory. A file that depends on some *other*
Claude-only variable (``${CLAUDE_SKILL_DIR}`` and friends) cannot be made to
work, so it is **skipped and named in the warnings** — never silently dropped,
because a command that references a missing file is worse than a command that was
never installed, and the user can only act on what they are told.

**The licence.** This project imports third-party content only when it is
permissively licensed. A plugin that ships no ``LICENSE``, or whose licence text
is not one this project can carry (a copyleft one, or something unrecognisable),
is **refused** — ``convert`` returns ``(None, [reason])`` and nothing is written.
"The licence is probably fine" is not a basis for redistributing someone's work.

**The listing.** Components are enumerated with GitHub's git-trees API rather
than jsDelivr's file API. Measured from this network: jsDelivr's listing for
``anthropics/claude-plugins-official`` returned 337 files where the repository
tree has 683 — an incomplete listing installs "successfully" with half the
commands missing. The trees API answered the same repository in one request with
``truncated: false``.

The MCP half deserves a note of its own: a Claude plugin's ``.mcp.json`` is
Claude's shape (``{"mcpServers": {...}}``) and :mod:`kairos.plugins` loads
``mcp.yaml`` instead. Both are written — the original file verbatim, so nothing
upstream is lost, and the translated ``mcp.yaml``, so the servers can actually be
loaded — and the ``mcp`` capability is declared only when the translation
produced something.
"""

from __future__ import annotations

import json
import re
import textwrap
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from kairos.extensions import market_sources as ms

#: Where a Claude plugin keeps its manifest, relative to the plugin root.
MANIFEST_REL = ".claude-plugin/plugin.json"

#: Claude's MCP config file at the plugin root.
MCP_REL = ".mcp.json"

#: The Kairos plugin manifest (:data:`kairos.plugins.PLUGIN_MANIFEST`).
PLUGIN_MANIFEST = "kairos-plugin.yaml"

#: Directories whose contents are a component, by convention.
COMPONENT_DIRS: Tuple[str, ...] = ("agents", "commands", "hooks", "skills")

#: Licence file names, most common first.
LICENSE_NAMES: Tuple[str, ...] = ("LICENSE", "LICENSE.md", "LICENSE.txt",
                                 "LICENCE", "LICENCE.md", "COPYING", "COPYING.md")

#: The variable Claude Code expands to the plugin's own directory. The
#: dollar-brace form is the only one that appears in practice.
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"

#: Any *other* Claude-only variable: unrewritable, so files containing one are
#: skipped rather than shipped broken.
_CLAUDE_ONLY = re.compile(r"\$\{CLAUDE_(?!PLUGIN_ROOT\})[A-Z0-9_]+\}")

#: Licence markers this project may import (permissive only).
_PERMISSIVE_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("APACHE LICENSE", "Apache-2.0"),
    ("MIT LICENSE", "MIT"),
    ("PERMISSION IS HEREBY GRANTED, FREE OF CHARGE", "MIT"),
    ("REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS", "BSD"),
    ("ISC LICENSE", "ISC"),
    ("MOZILLA PUBLIC LICENSE", "MPL-2.0"),
    ("THIS IS FREE AND UNENCUMBERED SOFTWARE RELEASED INTO THE PUBLIC DOMAIN",
     "Unlicense"),
    ("CREATIVE COMMONS CC0", "CC0-1.0"),
)

#: Licence markers that mean "no": this project cannot carry these.
_REFUSED_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL"),
    ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL"),
    ("GNU GENERAL PUBLIC LICENSE", "GPL"),
    ("ALL RIGHTS RESERVED", "proprietary"),
)

#: Bounds. A plugin is a handful of small text files; anything else is a sign
#: that the listing picked up more than the plugin.
MAX_FILES = 400
MAX_FILE_BYTES = 512 * 1024

#: Capability order, mirroring :data:`kairos.plugins.KNOWN_CAPABILITIES`. Kept
#: as a local constant so this module does not import the plugin system just to
#: spell a list.
CAPABILITY_ORDER: Tuple[str, ...] = ("skills", "agents", "hooks", "mcp", "commands")


# --------------------------------------------------------------------------- location


def _location(entry: Any) -> Tuple[Optional[str], Optional[str], str, str]:
    """``(repo, ref, path, problem)`` for one entry.

    A plugin can only be converted from a location this build can fetch: a
    GitHub repository, a revision (``ref`` preferred, then ``sha``), and a
    directory inside it. Anything else yields ``problem``, which is then the
    reason the conversion refused — never a half-fetch from another host.
    """
    if not isinstance(entry, dict):
        return None, None, "", "the entry is not an object"
    repo = str(entry.get("repo") or "").strip()
    if not repo or "/" not in repo:
        return None, None, "", "the entry names no GitHub repository to fetch from"
    ref = str(entry.get("ref") or "").strip() or str(entry.get("sha") or "").strip() or None
    path = str(entry.get("path") or "").strip().strip("/")
    return repo, ref, path, ""


def plugin_url(entry: Any) -> str:
    """Where the plugin lives upstream — recorded in ``ATTRIBUTION.md``."""
    repo, ref, path, _ = _location(entry)
    if repo is None:
        return str(entry.get("homepage") or "") if isinstance(entry, dict) else ""
    url = "https://github.com/%s" % repo
    if ref:
        url += "/tree/%s" % ref
    if path:
        url += "/%s" % path
    return url


def _manifest_url(entry: Dict[str, Any]) -> str:
    repo, ref, path, _ = _location(entry)
    parts = [p for p in (path, MANIFEST_REL) if p]
    return ms.jsdelivr(str(repo), ref, "/".join(parts))


def _file_url(entry: Dict[str, Any], rel: str) -> str:
    repo, ref, path, _ = _location(entry)
    parts = [p for p in (path, rel) if p]
    return ms.jsdelivr(str(repo), ref, "/".join(parts))


# --------------------------------------------------------------------------- fetching


def fetch_plugin_manifest(entry: Dict[str, Any], fetch: Optional[Callable[..., Any]] = None,
                          *, timeout: float = 20.0) -> Dict[str, Any]:
    """The plugin's ``.claude-plugin/plugin.json``.

    Fetched through jsDelivr: ``raw.githubusercontent.com`` does not answer from
    this network, and jsDelivr serves the same file out of its own cache.
    Raises on failure — the caller decides whether a plugin without a
    readable manifest is worth continuing with.
    """
    repo, _ref, _path, problem = _location(entry)
    if repo is None:
        raise ValueError(problem)
    fetch = ms._resolve_fetcher(fetch)
    url = _manifest_url(entry)
    raw = ms._cached("claude-plugin:manifest:%s" % url,
                     lambda: fetch(url, timeout=timeout))
    if not isinstance(raw, dict):
        raise ValueError("plugin.json at %s is not an object" % url)
    return raw


#: A repository's tarball. Complete and unthrottled, unlike the two alternatives.
#: The URL *and* the fetch of it live in :mod:`kairos.extensions.market_sources`
#: (``ms.ARCHIVE_URL`` / ``ms._repo_archive``), so a marketplace listing and a
#: plugin install share one download and one parser instead of two that could
#: disagree.
ARCHIVE_URL = ms.ARCHIVE_URL


def _resolve_fetchers(fetch: Optional[Callable[..., Any]],
                      archive_fetch: Optional[Callable[..., Any]]
                      ) -> Tuple[Callable[..., Any], Callable[..., Any]]:
    """The ``(file, archive)`` transports to use for this call.

    Resolved at call time, like every other transport in this package. An injected
    ``fetch`` is reused for the archive when no explicit ``archive_fetch`` was
    passed, so a test that takes the network out of a run takes it out of *both*
    paths — an archive path that quietly reached for the real transport would turn
    an offline test into a live one on any machine that has network.
    """
    if archive_fetch is not None:
        return ms._resolve_fetcher(fetch), archive_fetch
    if fetch is not None:
        return ms._resolve_fetcher(fetch), fetch
    return ms._resolve_fetcher(None), ms._http_bytes


def _archive(entry: Dict[str, Any], fetch: Optional[Callable[..., Any]],
             timeout: float) -> Dict[str, bytes]:
    """The plugin's files as ``{relative path: bytes}``, from its repository tarball.

    The fetch itself is :func:`kairos.extensions.market_sources._repo_archive`,
    which is the one implementation of "read a GitHub repository without the
    API": it is measured to be both complete and unthrottled, where

    * jsDelivr's file API is missing whole plugin directories for this
      marketplace — ``claude-security`` (42 files, 0 through the CDN),
      ``code-modernization`` (29, 0), ``cwc-makers`` (6, 0) — so installing from
      that listing would report success and write an empty plugin;
    * the git-trees API is authoritative but an anonymous caller gets 60 requests
      an hour, which is how a real install failed with "403 rate limit exceeded".

    One 3 MB download returns the listing *and* every file's contents, so an
    install costs one request instead of one per file. Members are read into
    memory and never extracted, so nothing can land outside ``dest``. What is
    left here is the plugin's own part: the files under its directory.
    """
    repo, ref, path, problem = _location(entry)
    if repo is None:
        raise ValueError(problem)
    _file_fetch, archive_fetch = _resolve_fetchers(fetch, None)
    owner, _sep, name = repo.partition("/")
    files = ms._repo_archive(owner, name, ref or "HEAD", archive_fetch,
                             timeout=timeout)

    prefix = (path + "/") if path else ""
    if prefix:
        files = {rel[len(prefix):]: body for rel, body in files.items()
                 if rel.startswith(prefix)}
    if not files:
        raise ValueError("the archive of %s holds nothing under %r"
                         % (repo, path or "."))
    return files


def _tree(entry: Dict[str, Any], fetch: Optional[Callable[..., Any]],
          timeout: float) -> Tuple[List[Dict[str, Any]], bool]:
    """``(files, truncated)`` — the plugin's files, relative to its root.

    Reads the repository archive first (:func:`_archive`), falling back to GitHub's
    git-trees API only when the archive cannot be read. ``truncated`` is surfaced
    so a huge repository cannot silently lose files instead of failing; a complete
    archive always reports ``False``.
    """
    try:
        archived = _archive(entry, fetch, timeout)
    except Exception as exc:  # noqa: BLE001 - the listing below is the fallback
        archive_problem = "%s: %s" % (type(exc).__name__, exc)
    else:
        return ([{"path": rel, "size": len(body)} for rel, body in sorted(archived.items())],
                False)

    repo, ref, path, problem = _location(entry)
    if repo is None:
        raise ValueError(problem)
    revision = ref or "HEAD"
    url = "https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (
        repo, urllib.parse.quote(revision, safe=""))
    fetch = ms._resolve_fetcher(fetch)
    try:
        raw = ms._cached("claude-plugin:tree:%s" % url,
                         lambda: fetch(url, timeout=timeout))
    except Exception as exc:  # noqa: BLE001 - both roads are now closed; say both
        raise ValueError(
            "the files of %s could not be listed: no archive (%s) and no listing (%s)"
            % (repo, archive_problem, exc)) from exc
    tree = raw.get("tree") if isinstance(raw, dict) else None
    if not isinstance(tree, list):
        raise ValueError("unexpected repository tree shape from %s" % url)

    prefix = (path + "/") if path else ""
    files: List[Dict[str, Any]] = []
    for item in tree:
        if not isinstance(item, dict):
            continue
        full = str(item.get("path") or "")
        if prefix and not full.startswith(prefix):
            continue
        rel = full[len(prefix):] if prefix else full
        if not rel or item.get("type") != "blob":
            continue
        files.append({"path": rel, "size": item.get("size") or 0})
    return files, bool(raw.get("truncated"))


def fetch_plugin_files(entry: Dict[str, Any], fetch: Optional[Callable[..., Any]] = None,
                       *, timeout: float = 20.0) -> List[Dict[str, Any]]:
    """Every file in the plugin as ``{"path", "size"}``, paths relative to its root."""
    files, _truncated = _tree(entry, fetch, timeout)
    return files


def fetch_plugin_file(entry: Dict[str, Any], rel: str,
                      fetch: Optional[Callable[..., Any]] = None,
                      *, timeout: float = 20.0) -> str:
    """One file's text: from the repository archive when it has it, else jsDelivr.

    By the time ``convert`` walks the file list the archive is already cached, so
    reading a file from it costs nothing and saves one request per file. Text
    only: everything copied is text.
    """
    try:
        body = _archive(entry, fetch, timeout).get(rel)
    except Exception:  # noqa: BLE001 - the CDN below is the fallback
        body = None
    if body is not None:
        return body.decode("utf-8", "replace")
    fetch = ms._resolve_fetcher(fetch, ms._http_text)
    url = _file_url(entry, rel)
    body = ms._cached("claude-plugin:file:%s" % url,
                      lambda: fetch(url, timeout=timeout))
    if not isinstance(body, str):
        raise ValueError("expected text from %s, got %s" % (url, type(body).__name__))
    return body


def fetch_skill_text(entry: Any, fetch: Optional[Callable[..., Any]] = None,
                     *, timeout: float = 20.0) -> str:
    """The raw ``SKILL.md`` of a skill entry, through jsDelivr.

    A skill entry carries the URL the marketplace already worked out
    (``raw_url``), so this is the one fetch a skill install needs: the file *is*
    the skill.
    """
    url = str(entry.get("raw_url") or "").strip() if isinstance(entry, dict) else ""
    if not url:
        raise ValueError("the entry names no file to fetch")
    fetch = ms._resolve_fetcher(fetch, ms._http_text)
    body = ms._cached("skill:text:%s" % url, lambda: fetch(url, timeout=timeout))
    if not isinstance(body, str):
        raise ValueError("expected text from %s, got %s" % (url, type(body).__name__))
    return body


# --------------------------------------------------------------------------- licence


def licence_kind(text: str) -> Optional[str]:
    """The permissive licence a text looks like, or ``None``.

    Recognised by its own words rather than by a file name: a file called
    ``LICENSE`` that contains a proprietary notice is not a licence to
    redistribute, and a file called ``COPYING`` that contains the Apache text
    is.
    """
    upper = str(text or "").upper()
    if not upper.strip():
        return None
    for marker, _label in _REFUSED_MARKERS:
        if marker in upper:
            return None
    for marker, name in _PERMISSIVE_MARKERS:
        if marker in upper:
            return name
    return None


def refused_licence(text: str) -> Optional[str]:
    """The licence this project *cannot* carry, when the text says so."""
    upper = str(text or "").upper()
    for marker, name in _REFUSED_MARKERS:
        if marker in upper:
            return name
    return None


def _find_licence(files: List[Dict[str, Any]]) -> Optional[str]:
    """The plugin-relative path of its licence file, if it ships one."""
    candidates: List[str] = []
    for item in files:
        rel = str(item.get("path") or "")
        base = rel.rsplit("/", 1)[-1].upper()
        if base in LICENSE_NAMES:
            candidates.append(rel)
    if not candidates:
        return None
    # A root-level LICENSE is the plugin's own; one nested in a vendored
    # directory belongs to that directory.
    candidates.sort(key=lambda p: (p.count("/"), len(p)))
    return candidates[0]


# --------------------------------------------------------------------------- rewriting


def rewrite_plugin_root(text: str, root: str) -> str:
    """Point ``${CLAUDE_PLUGIN_ROOT}`` at the installed plugin's directory."""
    return str(text).replace(PLUGIN_ROOT_VAR, str(root).replace("\\", "/"))


def claude_only_variables(text: str) -> List[str]:
    """Every ``${CLAUDE_*}`` variable in ``text`` that is not the plugin root."""
    return sorted(set(_CLAUDE_ONLY.findall(str(text or ""))))


# --------------------------------------------------------------------------- manifest


def _yaml_text(description: str, name: str, version: str, author: str,
               capabilities: List[str]) -> str:
    """``kairos-plugin.yaml`` in the shape the bundled plugins use.

    Written as text rather than dumped so the folded description and the key
    order match the four bundled ports byte for byte in style. The result is
    parsed back before it is written; if anything about the description makes
    the hand-built text ambiguous, the caller falls back to a dump.
    """
    body = " ".join(str(description or "").split())
    wrapped = textwrap.wrap(body, width=96, break_long_words=False) or [""]
    lines = [
        "name: %s" % name,
        "version: %s" % version,
        "description: >-",
    ]
    lines += ["  " + line for line in wrapped]
    lines += [
        "author: %s" % author,
        "capabilities: [%s]" % ", ".join(capabilities),
        'compatible: ">=0.1.0"',
        "enabled: true",
        "source: claude-plugins-official/%s" % name,
    ]
    return "\n".join(lines) + "\n"


def manifest_fields(entry: Dict[str, Any], manifest: Dict[str, Any],
                    capabilities: List[str]) -> Dict[str, Any]:
    """The fields of the Kairos manifest, from the upstream manifest where it has them."""
    author = manifest.get("author")
    author_name = (str((author or {}).get("name") or "") if isinstance(author, dict)
                   else str(author or ""))
    return {
        "name": str(manifest.get("name") or entry.get("name") or "").strip(),
        "version": str(manifest.get("version") or entry.get("version") or "1.0.0"),
        "description": str(manifest.get("description") or entry.get("description") or ""),
        "author": author_name or str(entry.get("author") or "") or "unknown",
        "capabilities": list(capabilities),
    }


def _manifest_text(entry: Dict[str, Any], manifest: Dict[str, Any],
                   capabilities: List[str]) -> str:
    fields = manifest_fields(entry, manifest, capabilities)
    text = _yaml_text(fields["description"], fields["name"], fields["version"],
                      fields["author"], fields["capabilities"])
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("name") == fields["name"]:
        return text
    # The description contained something that made the folded block ambiguous.
    # A dumped document is uglier and always parses; the plugin has to load.
    return yaml.safe_dump(fields, default_flow_style=False, allow_unicode=True,
                          sort_keys=False, width=4096)


def attribution_text(entry: Dict[str, Any], manifest: Dict[str, Any],
                     manifest_error: Optional[str]) -> str:
    """``ATTRIBUTION.md`` in the same shape the bundled ports carry."""
    name = str(entry.get("name") or manifest.get("name") or "plugin")
    author = str(entry.get("author") or "")
    if not author:
        raw = manifest.get("author")
        author = (str((raw or {}).get("name") or "") if isinstance(raw, dict)
                  else str(raw or "")) or "its author"
    original = (json.dumps(manifest, ensure_ascii=False) if manifest
                else "unavailable (%s)" % (manifest_error or "not fetched"))
    return (
        "# %s\n"
        "\n"
        "Imported from the Claude Code plugin marketplace "
        "(`claude-plugins-official/%s`), distributed by %s under the\n"
        "license in `LICENSE`. Kairos carries it as an installed plugin so the same\n"
        "skills and commands are available here.\n"
        "\n"
        "Upstream: %s\n"
        "Original manifest: %s\n"
        % (name, name, author, plugin_url(entry), original)
    )


# --------------------------------------------------------------------------- mcp


def mcp_yaml(mcp_json: Any) -> Optional[str]:
    """Claude's ``.mcp.json`` as the ``mcp.yaml`` this project loads.

    ``{"mcpServers": {name: {command|url, args, env, type}}}`` becomes
    ``mcp_servers:`` with the same entries — the key
    :func:`kairos.mcp_client._load_yaml_config` reads. Values are copied, so a
    ``${VAR}`` reference stays a reference (the project's rule: a config file
    names the credential, it never holds one). Returns ``None`` when there is
    nothing translatable, which is also the signal not to claim the ``mcp``
    capability.
    """
    servers = mcp_json.get("mcpServers") if isinstance(mcp_json, dict) else None
    if not isinstance(servers, dict) or not servers:
        return None

    body: Dict[str, Any] = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        item: Dict[str, Any] = {}
        url = str(cfg.get("url") or "").strip()
        command = str(cfg.get("command") or "").strip()
        if url:
            item["transport"] = "sse" if str(cfg.get("type") or "").lower() == "sse" else "http"
            item["url"] = url
            headers = cfg.get("headers")
            if isinstance(headers, dict) and headers:
                item["headers"] = {str(k): str(v) for k, v in headers.items()}
        elif command:
            item["command"] = command
            if isinstance(cfg.get("args"), list) and cfg["args"]:
                item["args"] = [str(a) for a in cfg["args"]]
            env = cfg.get("env")
            if isinstance(env, dict) and env:
                item["env"] = {str(k): str(v) for k, v in env.items()}
        else:
            # Neither a command nor a URL: a stanza that cannot start. Copying
            # it would add a server to the user's config that never runs.
            continue
        item["enabled"] = True
        body[str(name)] = item
    if not body:
        return None
    return yaml.safe_dump({"mcp_servers": body}, default_flow_style=False,
                          allow_unicode=True, sort_keys=False, width=4096)


# --------------------------------------------------------------------------- convert


def _is_component(rel: str) -> bool:
    if rel == MCP_REL:
        return True
    head = rel.split("/", 1)[0]
    return head in COMPONENT_DIRS and rel != head


def convert(entry: Dict[str, Any], dest: Path,
            fetch: Optional[Callable[..., Any]] = None, *,
            root: Optional[Path] = None, timeout: float = 20.0,
            ) -> Tuple[Optional[Path], List[str]]:
    """Write the plugin at ``dest`` and return ``(path, warnings)``.

    ``root`` is what ``${CLAUDE_PLUGIN_ROOT}`` is rewritten to; it defaults to
    ``dest``. The atomic installer passes the *final* directory while staging in
    a temporary one, so a plugin that is swapped into place later still contains
    paths that point at where it will live.

    Returns ``(None, warnings)`` when the plugin is refused — no licence that can
    be determined, nothing to install, or a location that cannot be fetched. In
    that case **nothing** is written: a partial plugin directory is a plugin that
    half works.
    """
    if not isinstance(entry, dict):
        return None, ["the entry is not an object, so there is nothing to convert"]

    warnings: List[str] = []
    name = str(entry.get("name") or "").strip()
    if not name:
        return None, ["the entry has no name, so the plugin directory cannot be named"]

    repo, _ref, _path, problem = _location(entry)
    if repo is None:
        return None, ["%s: %s" % (name, problem)]

    dest = Path(dest)
    installed_root = Path(root) if root else dest

    # 1. What is actually there.
    try:
        files, truncated = _tree(entry, fetch, timeout)
    except Exception as exc:
        return None, ["%s: its file listing could not be read (%s)"
                      % (name, _reason(exc))]
    if not files:
        return None, ["%s: the repository directory is empty" % name]
    if truncated:
        warnings.append(
            "the repository tree came back truncated, so files beyond GitHub's "
            "limit are missing from this install")

    # 2. The manifest. Recorded verbatim in ATTRIBUTION.md; not required to run.
    manifest: Dict[str, Any] = {}
    manifest_error: Optional[str] = None
    try:
        manifest = fetch_plugin_manifest(entry, fetch, timeout=timeout)
    except Exception as exc:
        manifest_error = _reason(exc)
        warnings.append("%s: its plugin.json could not be read (%s)"
                        % (name, manifest_error))

    # 3. The licence, before anything is written.
    licence_rel = _find_licence(files)
    if licence_rel is None:
        return None, ["%s: it ships no LICENSE file, so its licence cannot be "
                      "determined — this project only imports permissively "
                      "licensed content" % name]
    try:
        licence_text = fetch_plugin_file(entry, licence_rel, fetch, timeout=timeout)
    except Exception as exc:
        return None, ["%s: its licence (%s) could not be read (%s), so the licence "
                      "cannot be determined" % (name, licence_rel, _reason(exc))]
    refused = refused_licence(licence_text)
    if refused:
        return None, ["%s: its licence looks like %s, which this project cannot "
                      "import (permissive licences only)" % (name, refused)]
    kind = licence_kind(licence_text)
    if kind is None:
        return None, ["%s: the text of %s is not a licence this project "
                      "recognises, so it cannot be carried" % (name, licence_rel)]

    # 4. The components, filtered to what a plugin actually contributes.
    components = [f for f in files if _is_component(str(f.get("path") or ""))]
    if len(components) > MAX_FILES:
        warnings.append("%s: %d files were in the plugin; only the first %d were "
                        "copied" % (name, len(components), MAX_FILES))
        components = components[:MAX_FILES]
    if not components:
        return None, ["%s: it has no commands, agents, skills, hooks or MCP "
                      "servers, so there is nothing to install" % name]

    written: List[str] = []
    mcp_written = False
    for item in components:
        rel = str(item.get("path") or "")
        size = int(item.get("size") or 0)
        if size > MAX_FILE_BYTES:
            warnings.append("%s: %s is %d bytes and was not copied"
                            % (name, rel, size))
            continue
        try:
            body = fetch_plugin_file(entry, rel, fetch, timeout=timeout)
        except Exception as exc:
            warnings.append("%s: %s could not be read (%s)"
                            % (name, rel, _reason(exc)))
            continue

        if rel == MCP_REL:
            # Claude's MCP file: kept verbatim *and* translated, because the
            # plugin loader reads mcp.yaml and a file nothing reads contributes
            # nothing.
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as exc:
                warnings.append("%s: %s is not valid JSON (%s) and was not copied"
                                % (name, rel, exc))
                continue
            translated = mcp_yaml(parsed)
            if translated is None:
                warnings.append("%s: %s declares no MCP server that can be "
                                "started, so it was not copied" % (name, rel))
                continue
            _write(dest / MCP_REL, body)
            _write(dest / "mcp.yaml", translated)
            mcp_written = True
            written.append(rel)
            continue

        claude_only = claude_only_variables(body)
        if claude_only:
            # Shipped as-is this file would reference something only Claude Code
            # resolves. Skipped, and named: silently installing it is how a
            # user ends up with a command that fails at run time.
            warnings.append(
                "%s: %s depends on %s, which only Claude Code resolves, so it "
                "was skipped" % (name, rel, ", ".join(claude_only)))
            continue

        _write(dest / rel, rewrite_plugin_root(body, installed_root))
        written.append(rel)

    if not written:
        return None, warnings + ["%s: none of its files could be converted" % name]

    # 5. Declare only what landed. The four bundled ports work this way: their
    # capabilities are the directories they actually carry.
    capabilities = [cap for cap in CAPABILITY_ORDER if _has(cap, written, mcp_written)]

    # 6. The licence travels with the plugin — that is the condition for
    # carrying it at all.
    _write(dest / "LICENSE", licence_text)

    # 7. Provenance.
    _write(dest / "ATTRIBUTION.md", attribution_text(entry, manifest, manifest_error))
    _write(dest / PLUGIN_MANIFEST, _manifest_text(entry, manifest, capabilities))

    return dest, warnings


def _has(capability: str, written: List[str], mcp_written: bool) -> bool:
    if capability == "mcp":
        return mcp_written
    if capability == "hooks":
        return any(rel.startswith("hooks/") for rel in written)
    return any(rel.startswith(capability + "/") for rel in written)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _reason(exc: BaseException) -> str:
    """A short, non-empty reason for a failure."""
    text = str(exc).strip()
    return text or type(exc).__name__
