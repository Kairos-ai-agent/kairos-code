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
"""

from __future__ import annotations

import json
import os
import re
import tempfile
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


def layer_path(scope: str, *, project_path: Optional[Path] = None,
               user_dir: Optional[Path] = None) -> Path:
    """The ``mcp.yaml`` a scope writes to.

    ``user_dir`` overrides the ``.kairos`` directory itself (the same parameter
    :func:`kairos.mcp_client.load_configs` takes), which is how tests keep their
    hands off a real ``~/.kairos``.
    """
    if scope == "user":
        base = Path(user_dir) if user_dir else Path.home() / ".kairos"
        return base / "mcp.yaml"
    if scope == "project":
        if not project_path:
            raise ValueError("scope='project' needs a project_path")
        return Path(project_path) / ".kairos" / "mcp.yaml"
    raise ValueError(f"unknown scope {scope!r}: expected one of {list(SCOPES)}")


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


def install_mcp(
    name: str,
    *,
    scope: str = "user",
    project_path: Optional[Path] = None,
    registry: RegistryLike = None,
    user_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Add (or reset) one server in one layer of ``mcp.yaml``.

    Only that server's key is touched: every other entry and every comment in the
    file is left as it was. Idempotent — installing the same server twice writes
    nothing the second time and reports ``changed: False``. The write is atomic.

    Returns ``{"ok", "changed", "path", "entry", "config"}``: the registry entry,
    and the body that landed in the file.
    """
    entry = registry_entry(name, registry=registry)
    body = yaml_body(entry)
    path = layer_path(scope, project_path=project_path, user_dir=user_dir)
    text, changed = _add(path, name, body)
    if changed:
        _atomic_write(path, text)
    return {"ok": True, "changed": changed, "path": str(path),
            "entry": entry, "config": body}


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
