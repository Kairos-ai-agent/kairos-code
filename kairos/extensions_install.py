"""Install / uninstall a market MCP server into an ``mcp.yaml`` layer.

The registry (``kairos/extensions/mcps.json``) names the servers the user can add,
but "installing" one used to mean copying a stanza out of the docs into
``~/.kairos/mcp.yaml`` by hand — which is exactly the kind of step that ends with
a broken YAML file and no idea which line is wrong. This module is the write half
of the registry: :func:`install_mcp` adds one server key to one layer,
:func:`uninstall_mcp` removes it, and nothing else about the file changes.

Two properties are deliberate:

* **Textual.** ``yaml.safe_dump`` of the whole document would drop every comment
  in the user's file and reflow the entries they wrote themselves. Only the target
  block is rewritten, so the rest of the file survives byte for byte — comments
  included. (The registry and ``mcp_client`` keep reading it with a plain YAML
  parse; this module never needs to.)
* **Atomic.** The new document goes to a temporary file in the same directory and
  is moved into place with :func:`os.replace`, so a crash or a full disk cannot
  leave a half-written config where the user's servers used to be.

Precedence, as everywhere else in Kairos: bundled plugin defaults < user
(``~/.kairos/mcp.yaml``) < project (``<project>/.kairos/mcp.yaml``).

Two more writers live at the bottom of this module, for the two marketplaces
that are not MCP servers: :func:`install_plugin` converts a Claude plugin into a
plugin directory (the conversion is
:mod:`kairos.extensions.claude_plugin`, the destination and the atomicity are
here) and :func:`install_skill` writes one ``SKILL.md`` into the user's skills
scope. They keep the same two properties by other means — a staged tree swapped
into place, and no write at all when the result would be identical.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

import yaml

#: Where the curated registry lives (repo checkout or installed package).
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent / "extensions" / "mcps.json"

#: The two layers a user can write to.
SCOPES: Tuple[str, ...] = ("user", "project")

#: Keys are inserted at this indent, matching every ``mcp.yaml`` in the wild.
ENTRY_INDENT = 2

_MAPPING_KEY = "mcp_servers"

#: A server name is a YAML key we generate — keep it a plain slug so it can never
#: need quoting and can never inject a second key.
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Header written into a file this module creates. Never added to a file the user
#: already has (their comments are theirs).
_NEW_FILE_HEADER = [
    "# Kairos MCP servers.",
    "#",
    "# The extensions page adds one key here per installed server and removes it on",
    "# uninstall. Everything else in this file, comments included, is untouched.",
]

RegistryLike = Union[None, str, Path, Mapping[str, Any]]


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def _load_registry_file(path: Path) -> Dict[str, Dict[str, Any]]:
    """``{name: entry}`` from a registry JSON file; {} when unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _index_entries(data)


def _index_entries(data: Any) -> Dict[str, Dict[str, Any]]:
    if isinstance(data, Mapping):
        servers = data.get("servers")
        if isinstance(servers, list):
            return _index_entries(servers)
        out: Dict[str, Dict[str, Any]] = {}
        for name, entry in data.items():
            if isinstance(entry, Mapping):
                out[str(name)] = dict(entry)
        return out
    if isinstance(data, (list, tuple)):
        out = {}
        for entry in data:
            if isinstance(entry, Mapping) and entry.get("name"):
                out[str(entry["name"])] = dict(entry)
        return out
    return {}


def registry_index(registry: RegistryLike = None) -> Dict[str, Dict[str, Any]]:
    """The registry as ``{name: entry}``.

    ``registry`` may be ``None`` (the shipped file), a path to a registry JSON, a
    parsed registry (``{"servers": [...]}``), or a mapping keyed by server name.
    """
    if registry is None:
        return _load_registry_file(DEFAULT_REGISTRY_PATH)
    if isinstance(registry, (str, Path)):
        return _load_registry_file(Path(registry))
    if isinstance(registry, (Mapping, list, tuple)):
        return _index_entries(registry)
    raise TypeError(f"unsupported registry: {type(registry).__name__}")


def registry_entry(name: str, registry: RegistryLike = None) -> Dict[str, Any]:
    """The registry entry for ``name``.

    Raises :class:`ValueError` when the name is not in the registry — the caller
    asked to install something we cannot describe, and guessing a command for it
    would be worse than saying so.
    """
    _check_name(name)
    entry = registry_index(registry).get(name)
    if entry is None:
        raise ValueError(f"unknown MCP server {name!r}: it is not in the registry")
    return dict(entry)


def _check_name(name: str) -> str:
    text = str(name or "").strip()
    if not _VALID_NAME.match(text):
        raise ValueError(f"invalid MCP server name: {name!r}")
    return text


# ---------------------------------------------------------------------------
# registry entry -> the body of a ``mcp_servers:`` key
# ---------------------------------------------------------------------------


def yaml_body(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``mcp.yaml`` body for one registry entry.

    A bundled server is written as ``bundled: <name>`` — the same spelling the
    shipped defaults use — so it resolves to the command that works in *this*
    install (a Python module here, a built-in flag in a packaged build) instead of
    baking one interpreter's path into the user's config. An upstream server keeps
    the command the registry advertises, and its credentials are written as
    ``${VAR}`` references read from the environment at connect time — never a
    literal secret in a config file.
    """
    name = str(entry.get("name") or "")
    transport = str(entry.get("transport") or "stdio").strip().lower()
    env_keys = [str(k) for k in (entry.get("env_keys") or []) if k]
    body: Dict[str, Any] = {}

    if entry.get("bundled"):
        from kairos.mcp_local_servers import resolve_bundled

        if resolve_bundled(name) is not None:
            body["bundled"] = name
        else:  # flagged bundled but not actually served here: keep it explicit
            body["command"] = str(entry.get("command") or "")
            if entry.get("args"):
                body["args"] = list(entry["args"])
    elif transport in ("http", "sse"):
        body["transport"] = transport
        if entry.get("url"):
            body["url"] = str(entry["url"])
        headers = dict(entry.get("headers") or {})
        for key in env_keys:
            headers.setdefault(key, "${%s}" % key)
        if headers:
            body["headers"] = headers
    else:
        if entry.get("command"):
            body["command"] = str(entry["command"])
        if entry.get("args"):
            body["args"] = list(entry["args"])
        if env_keys:
            body["env"] = {key: "${%s}" % key for key in env_keys}

    body["enabled"] = True
    return body


# ---------------------------------------------------------------------------
# layer paths
# ---------------------------------------------------------------------------


def scope_dir(scope: str, *, project_path: Optional[Path] = None,
              user_dir: Optional[Path] = None) -> Path:
    """The ``.kairos`` directory a scope writes to.

    ``user_dir`` overrides that directory itself (the same parameter
    :func:`kairos.mcp_client.load_configs` takes), which is how tests keep their
    hands off a real ``~/.kairos``.
    """
    if scope == "user":
        return Path(user_dir) if user_dir else Path.home() / ".kairos"
    if scope == "project":
        if not project_path:
            raise ValueError("scope='project' needs a project_path")
        return Path(project_path) / ".kairos"
    raise ValueError(f"unknown scope {scope!r}: expected one of {list(SCOPES)}")


def layer_path(scope: str, *, project_path: Optional[Path] = None,
               user_dir: Optional[Path] = None) -> Path:
    """The ``mcp.yaml`` a scope writes to."""
    return scope_dir(scope, project_path=project_path, user_dir=user_dir) / "mcp.yaml"


# ---------------------------------------------------------------------------
# file surgery
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    """The file's exact text; "" when it does not exist.

    Decoded with ``surrogateescape`` so a file we did not write re-encodes to the
    same bytes.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ""
    return raw.decode("utf-8", errors="surrogateescape")


def _newline_of(text: str) -> str:
    """The carriage return (or not) this document uses for *new* lines."""
    return "\r" if "\r\n" in text else ""


def _line_body(line: str) -> str:
    """A line without a CRLF's carriage return.

    Lines come from ``text.split("\\n")`` so a CRLF file leaves the ``\\r`` at the
    end of every line; it is an artefact of the file's line style, never content,
    and every check below must see the line without it.
    """
    return line[:-1] if line.endswith("\r") else line


def _contentish(line: str) -> bool:
    """A line that is neither blank nor a comment — i.e. real YAML content."""
    stripped = _line_body(line).strip()
    return bool(stripped) and not stripped.startswith("#")


def _indent_of(line: str) -> int:
    body = _line_body(line)
    return len(body) - len(body.lstrip(" "))


def _key_of(line: str) -> Optional[str]:
    """The mapping key on a ``key: …`` line, else ``None``."""
    body = _line_body(line)
    if not _contentish(body):
        return None
    match = re.match(r"^([ \t]*)([^\s#:][^:]*?)\s*:(?:[ \t].*)?$", body)
    if not match:
        return None
    return match.group(2).strip().strip("'\"") or None


def _mapping_header(lines: List[str], key: str = _MAPPING_KEY) -> Optional[int]:
    for index, line in enumerate(lines):
        if _indent_of(line) == 0 and _key_of(line) == key:
            return index
    return None


def _find_entry(lines: List[str], header: int,
                name: str) -> Optional[Tuple[int, int]]:
    """``(start, end)`` line indices of the ``name:`` block under ``header``."""
    entry_indent: Optional[int] = None
    start: Optional[int] = None
    for index in range(header + 1, len(lines)):
        line = lines[index]
        if not _contentish(line):
            continue
        indent = _indent_of(line)
        if indent == 0:  # left the mcp_servers mapping without finding it
            return None
        if entry_indent is None:
            entry_indent = indent
        if indent == entry_indent and _key_of(line) == name:
            start = index
            break
    if start is None or entry_indent is None:
        return None

    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not _contentish(line):
            continue
        if _indent_of(line) <= entry_indent:
            end = index
            break
    # Blank lines and block-level comments after the entry belong to whatever
    # follows it, not to the entry: dropping them on uninstall would eat the
    # user's file.
    while end > start + 1 and not _contentish(lines[end - 1]):
        end -= 1
    return start, end


def _insert_at(lines: List[str], header: int) -> int:
    """Where a new entry goes: after the mapping's last existing entry."""
    last_child: Optional[int] = None
    for index in range(header + 1, len(lines)):
        line = lines[index]
        if not _contentish(line):
            continue
        if _indent_of(line) == 0:
            break
        last_child = index
    return header + 1 if last_child is None else last_child + 1


def _render_entry(name: str, body: Mapping[str, Any],
                  newline: str = "") -> List[str]:
    """The lines of one ``  name:`` block, with the caller's line endings."""
    pad = " " * ENTRY_INDENT
    dumped = yaml.safe_dump(dict(body), default_flow_style=False,
                            allow_unicode=True, sort_keys=False, width=4096)
    out = [f"{pad}{name}:"]
    for line in dumped.splitlines():
        out.append(f"{pad}  {line}")
    return [line + newline for line in out]


def _empty_mapping_header(line: str) -> str:
    """``mcp_servers: {}`` -> ``mcp_servers:`` (we are about to fill it in).

    The line's own carriage return is kept, so a CRLF file stays CRLF.
    """
    body = _line_body(line)
    cr = line[len(body):]
    head, _, tail = body.partition(":")
    if tail.strip() not in ("", "{}"):
        raise ValueError(
            "this mcp.yaml holds an inline `mcp_servers` mapping; edit it by hand")
    return head + ":" + cr


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace``.

    A failure anywhere before the rename leaves the target untouched and removes
    the temporary file — there is no moment at which a reader (or a crash) can
    find a half-written config.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8", errors="surrogateescape")
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                    dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parsed_servers(text: str) -> Dict[str, Any]:
    """The ``mcp_servers`` mapping the loader would see."""
    if not text.strip():
        return {}
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, Mapping):
        return {}
    servers = data.get(_MAPPING_KEY)
    return dict(servers) if isinstance(servers, Mapping) else {}


def _add(path: Path, name: str,
         body: Mapping[str, Any]) -> Tuple[str, bool]:
    """The new text and whether it changed — the install half of the surgery."""
    text = _read_text(path)
    lines = text.split("\n") if text else []
    nope = (text, False)
    block = _render_entry(name, body, _newline_of(text))
    header = _mapping_header(lines)

    if header is None:
        # No mcp_servers key at all: start the section (a brand-new file gets the
        # explanatory header; a file the user already owns does not).
        newline = _newline_of(text)
        eol = newline + "\n"
        section = "\n".join([_MAPPING_KEY + ":" + newline] + block)
        if not text:
            return ("".join(line + eol for line in _NEW_FILE_HEADER)
                    + section + eol), True
        prefix = text if text.endswith("\n") else text + eol
        if not prefix.endswith(eol + eol):
            prefix += eol  # one blank line between their content and the section
        return prefix + section + eol, True

    found = _find_entry(lines, header, name)
    if found is not None:
        start, end = found
        if _parsed_servers(text).get(name) == dict(body):
            return nope  # already installed with exactly this body
        new_lines = lines[:start] + block + lines[end:]
    else:
        lines[header] = _empty_mapping_header(lines[header])
        at = _insert_at(lines, header)
        new_lines = lines[:at] + block + lines[at:]
    return "\n".join(new_lines), True


def _remove(path: Path, name: str) -> Tuple[str, bool, Dict[str, Any]]:
    """The new text, whether it changed, and the body that was removed."""
    text = _read_text(path)
    lines = text.split("\n") if text else []
    header = _mapping_header(lines)
    if header is None:
        return text, False, {}
    found = _find_entry(lines, header, name)
    if found is None:
        return text, False, {}
    removed = _parsed_servers(text).get(name) or {}
    start, end = found
    return "\n".join(lines[:start] + lines[end:]), True, dict(removed)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def install_entry(
    entry: Mapping[str, Any],
    *,
    name: Optional[str] = None,
    scope: str = "user",
    project_path: Optional[Path] = None,
    user_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add (or reset) one *already normalised* entry in one layer of ``mcp.yaml``.

    This is the path a marketplace install takes, and it is the same path a
    curated install takes: the only difference between the two is where the entry
    came from. Keeping one implementation means a remote server cannot install
    itself in a way the curated ones do not — no second code path to keep honest.

    Same guarantees as :func:`install_mcp`: only this key is touched, the write is
    atomic, and a second identical install reports ``changed: False``.
    """
    key = str(name or entry.get("name") or "")
    _check_name(key)
    body = yaml_body(entry)
    path = layer_path(scope, project_path=project_path, user_dir=user_dir)
    text, changed = _add(path, key, body)
    if changed:
        _atomic_write(path, text)
    return {"ok": True, "changed": changed, "path": str(path),
            "entry": dict(entry), "config": body}


def install_mcp(
    name: str,
    *,
    scope: str = "user",
    project_path: Optional[Path] = None,
    registry: RegistryLike = None,
    user_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add (or reset) one server from the curated registry into one layer.

    Only that server's key is touched: every other entry and every comment in the
    file is left as it was. Idempotent — installing the same server twice writes
    nothing the second time and reports ``changed: False``. The write is atomic.

    Returns ``{"ok", "changed", "path", "entry", "config"}``: the registry entry,
    and the body that landed in the file.
    """
    return install_entry(registry_entry(name, registry=registry), name=name,
                         scope=scope, project_path=project_path, user_dir=user_dir)


def uninstall_mcp(
    name: str,
    *,
    scope: str = "user",
    project_path: Optional[Path] = None,
    user_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Remove one server from one layer. A no-op (``changed: False``) if absent."""
    _check_name(name)
    path = layer_path(scope, project_path=project_path, user_dir=user_dir)
    text, changed, removed = _remove(path, name)
    if changed:
        _atomic_write(path, text)
    return {"ok": True, "changed": changed, "path": str(path), "entry": removed}


# ---------------------------------------------------------------------------
# Plugins and skills
#
# The MCP writers above change one key inside one YAML file. These two write a
# *tree*, which fails differently: there is no comment to preserve, but a
# half-written plugin directory is a plugin that half works, and a file deleted
# between two renames is a plugin that is not there at all. So the same two
# properties are kept, by other means — the whole tree is built somewhere else
# and swapped into place, and a second identical install writes nothing.
# ---------------------------------------------------------------------------

#: Where plugin directories live, per scope. Mirrors
#: :class:`kairos.plugins.PluginManager`, which loads ``~/.kairos/plugins/``.
PLUGIN_DIR = "plugins"

#: Where skills live. ``kairos.skills.SkillsLoader`` reads
#: ``<.kairos>/skills/<name>/SKILL.md`` — the directory is the skill's name.
SKILL_DIR = "skills"
SKILL_FILE = "SKILL.md"

#: A SKILL.md has to start with frontmatter: the loader skips files that do not,
#: with a warning nobody sees.
_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*(\n|\Z)", re.DOTALL)


def plugin_dir(scope: str = "user", *, project_path: Optional[Path] = None,
               user_dir: Optional[Path] = None) -> Path:
    """The plugin directory a scope writes into."""
    return scope_dir(scope, project_path=project_path, user_dir=user_dir) / PLUGIN_DIR


def skills_dir(scope: str = "user", *, project_path: Optional[Path] = None,
               user_dir: Optional[Path] = None) -> Path:
    """The skills directory a scope writes into."""
    return scope_dir(scope, project_path=project_path, user_dir=user_dir) / SKILL_DIR


def _trees_equal(left: Path, right: Path) -> bool:
    """Whether two directories hold the same files with the same bytes."""
    def index(root: Path) -> Dict[str, Path]:
        out: Dict[str, Path] = {}
        for path in root.rglob("*"):
            if path.is_file():
                out[str(path.relative_to(root)).replace("\\", "/")] = path
        return out

    a, b = index(left), index(right)
    if set(a) != set(b):
        return False
    for rel, path in a.items():
        try:
            if path.read_bytes() != b[rel].read_bytes():
                return False
        except OSError:
            return False
    return True


def _swap_dir(staged: Path, dest: Path) -> None:
    """Move ``staged`` onto ``dest``, putting ``dest`` back if the move fails.

    A directory cannot be replaced while it is non-empty on Windows, so the old
    one is renamed aside, the new one takes its place, and only then is the old
    one removed. If the second rename fails the first is undone: the plugin
    directory is either the old tree or the new one, never missing.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        os.replace(staged, dest)
        return
    aside = dest.with_name("%s.old-%s" % (dest.name, uuid.uuid4().hex[:8]))
    os.replace(dest, aside)
    try:
        os.replace(staged, dest)
    except BaseException:
        os.replace(aside, dest)
        raise
    shutil.rmtree(aside, ignore_errors=True)


def install_plugin(
    entry: Mapping[str, Any],
    *,
    name: Optional[str] = None,
    scope: str = "user",
    project_path: Optional[Path] = None,
    user_dir: Optional[Path] = None,
    fetch: Optional[Any] = None,
) -> Dict[str, Any]:
    """Install one Claude plugin by converting it into a scope's plugin directory.

    The conversion itself is :mod:`kairos.extensions.claude_plugin` — this
    function owns only *where* it lands and *how*: the tree is built in a staging
    directory next to the destination, compared with what is already there, and
    swapped in only if it differs. Installing the same plugin twice therefore
    reports ``changed: False`` and touches nothing.

    A refused conversion (no determinable licence, nothing to install, an
    unfetchable location) returns ``ok: False`` **and writes nothing**: the
    staging directory is discarded. ``warnings`` is always the full list, so a
    plugin that installed with files skipped says which ones.
    """
    from kairos.extensions import claude_plugin

    key = _check_name(str(name or entry.get("name") or ""))
    dest = plugin_dir(scope, project_path=project_path, user_dir=user_dir) / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=key + ".", suffix=".staging",
                                    dir=str(dest.parent)))
    try:
        converted, warnings = claude_plugin.convert(entry, staging, fetch=fetch,
                                                   root=dest)
        if converted is None:
            return {"ok": False, "changed": False, "path": None,
                    "installed_path": None, "warnings": list(warnings),
                    "entry": dict(entry)}
        if dest.exists() and _trees_equal(staging, dest):
            return {"ok": True, "changed": False, "path": str(dest),
                    "installed_path": str(dest), "warnings": list(warnings),
                    "entry": dict(entry)}
        _swap_dir(staging, dest)
        return {"ok": True, "changed": True, "path": str(dest),
                "installed_path": str(dest), "warnings": list(warnings),
                "entry": dict(entry)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install_skill(
    entry: Mapping[str, Any],
    *,
    name: Optional[str] = None,
    scope: str = "user",
    project_path: Optional[Path] = None,
    user_dir: Optional[Path] = None,
    content: Optional[str] = None,
    fetch: Optional[Any] = None,
) -> Dict[str, Any]:
    """Write one skill's ``SKILL.md`` into a scope's skills directory.

    ``content`` is used when the caller already holds the file; otherwise it is
    read from the entry's ``raw_url`` (the jsDelivr URL the marketplace
    recorded) through the call-time-resolved fetcher.

    Two refusals, both because writing would leave something that looks
    installed and is not: an empty file, and a file with no YAML frontmatter —
    ``kairos.skills.SkillsLoader`` skips those, so a skill installed that way
    would sit on disk and never load.

    Unlike the plugin converter, a ``${CLAUDE_SKILL_DIR}`` reference in a skill
    body is *not* a reason to refuse: a skill is text the model reads, and the
    loader in this project already carries dozens of adapted skills that mention
    it. It is reported as a warning instead, so the reference is not a surprise.
    """
    from kairos.extensions import claude_plugin

    key = _check_name(str(name or entry.get("name") or ""))
    text = content if content is not None else claude_plugin.fetch_skill_text(entry, fetch)
    text = str(text or "")
    if not text.strip():
        raise ValueError("%r has an empty SKILL.md" % key)
    if not _FRONTMATTER.match(text):
        raise ValueError(
            "%r has no YAML frontmatter in its SKILL.md, so the skills loader "
            "would skip it — refusing to write a file that never loads" % key)

    path = skills_dir(scope, project_path=project_path, user_dir=user_dir) / key / SKILL_FILE
    if _read_text(path) == text:
        return {"ok": True, "changed": False, "path": str(path),
                "installed_path": str(path), "warnings": [], "entry": dict(entry)}

    _atomic_write(path, text)
    warnings: List[str] = []
    variables = claude_plugin.claude_only_variables(text)
    if variables:
        warnings.append(
            "%s mentions %s, which only Claude Code resolves; the skill loads, "
            "but that reference will not" % (key, ", ".join(variables)))
    return {"ok": True, "changed": True, "path": str(path),
            "installed_path": str(path), "warnings": warnings,
            "entry": dict(entry)}
